"""tests/test_model_integrity.py — the v19 model integrity pipeline.

Covers the reconstructed 12-stage pipeline (temp→fsync→bytecount→SHA256→
SHA512→CRC→structure→zip-slip→exec/symlink→staged→validate→ATOMIC+rollback),
the v19 Phase 6 model-state machine, concurrency hygiene, and the adversarial
cases the mission demands (zip bomb, zip-slip, truncation, wrong digest,
executable/symlink members, disk-full, concurrent installs, rollback).

The REAL official archive (41,205,931 B, SHA256 30f26242…) is used when
``.test-assets/vosk-model.zip`` exists next to the repo root (downloaded and
verified live during the v19 baseline audit); those tests SKIP otherwise so
the suite stays offline-deterministic.
"""

from __future__ import annotations

import hashlib
import io
import os
import struct
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

import airmouse.model_integrity as mi

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_ARCHIVE = REPO_ROOT / ".test-assets" / "vosk-model.zip"
REAL_SHA256 = ("30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367"
               "f99ff41780942498")


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Point the AirMouse home at a throwaway directory."""
    monkeypatch.setenv("AIRMOUSE_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


# ---------------------------------------------------------------------------
# synthetic archive builders (deterministic, no network)
# ---------------------------------------------------------------------------

def make_spec(raw: bytes, *, root: str = "test-model-0.1",
              members: int = 4,
              required=("am/final.mdl",)) -> mi.ModelSpec:
    return mi.ModelSpec(
        name=root, url="https://example.invalid/models/x.zip", size=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
        sha512=hashlib.sha512(raw).hexdigest(), root=root, members=members,
        unpacked_mb=0.1, provider="example.invalid (test)",
        license="TEST", engine="test", required_members=tuple(required))


def build_archive(*, root: str = "test-model-0.1",
                  extra_members: int = 0,
                  content: bytes = b"pretend kaldi model") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{root}/", "")
        zf.writestr(f"{root}/am/final.mdl", content)
        zf.writestr(f"{root}/conf/model.conf", b"--sample-frequency=16000")
        zf.writestr(f"{root}/graph/words.txt", b"<unk> 0\n")
        for i in range(extra_members):
            zf.writestr(f"{root}/extra_{i}.txt", b"x")
    return buf.getvalue()


def pipeline_for(tmp_path: Path, spec: mi.ModelSpec,
                 **kw) -> mi.ModelPipeline:
    return mi.ModelPipeline(spec=spec, dest=str(tmp_path / "models" / "m"),
                            **kw)


# ---------------------------------------------------------------------------
# happy paths
# ---------------------------------------------------------------------------

def test_valid_archive_installs_and_is_ready(tmp_path):
    raw = build_archive()
    p = pipeline_for(tmp_path, make_spec(raw))
    state, msg = p.install_from_bytes(raw)
    assert state == mi.ModelState.MODEL_READY
    assert "verified" in msg
    assert (tmp_path / "models" / "m" / "am" / "final.mdl").exists()
    st, detail = p.state()
    assert st == mi.ModelState.MODEL_READY
    # 12-stage hygiene: no temp/archive/staging/lock leftovers
    parent = tmp_path / "models"
    assert [e.name for e in parent.iterdir()] == ["m"]


def test_state_machine_honest_when_missing(tmp_path):
    raw = build_archive()
    p = pipeline_for(tmp_path, make_spec(raw))
    st, detail = p.state()
    assert st == mi.ModelState.MODEL_MISSING
    assert "no model installed" in detail


def test_corrupted_tree_detected_as_corrupted(tmp_path):
    raw = build_archive()
    p = pipeline_for(tmp_path, make_spec(raw))
    p.install_from_bytes(raw)
    (tmp_path / "models" / "m" / "am" / "final.mdl").unlink()
    st, _ = p.state()
    assert st == mi.ModelState.MODEL_CORRUPTED


def test_download_required_when_user_declines(tmp_path):
    raw = build_archive()
    p = pipeline_for(tmp_path, make_spec(raw))
    called = []

    def _fetch(url, progress):  # must NEVER be reached
        called.append(url)
        return raw

    state, msg = p.download_and_install(consent=lambda q: False, fetch=_fetch)
    assert state == mi.ModelState.MODEL_DOWNLOAD_REQUIRED
    assert "declined" in msg
    assert called == []                      # never downloaded silently
    assert "manual" in msg.lower()


def test_offline_is_download_required_without_network(tmp_path):
    raw = build_archive()
    p = pipeline_for(tmp_path, make_spec(raw))
    called = []
    state, msg = p.download_and_install(
        offline=True, fetch=lambda u, pr: called.append(u) or raw)
    assert state == mi.ModelState.MODEL_DOWNLOAD_REQUIRED
    assert called == []
    assert "offline" in msg.lower()


def test_observer_sees_the_lifecycle(tmp_path):
    raw = build_archive()
    p = pipeline_for(tmp_path, make_spec(raw))
    seen = []
    p.observe = seen.append
    p.install_from_bytes(raw)
    assert mi.ModelState.MODEL_VERIFYING in seen
    assert mi.ModelState.MODEL_INSTALLING in seen
    # DOWNLOADING is emitted by download_and_install, not install_from_bytes
    assert mi.ModelState.MODEL_DOWNLOADING not in seen


def test_download_and_install_observer_includes_downloading(tmp_path):
    raw = build_archive()
    p = pipeline_for(tmp_path, make_spec(raw))
    seen = []
    p.observe = seen.append
    p.download_and_install(consent=lambda q: True, fetch=lambda u, pr: raw)
    assert seen[0] == mi.ModelState.MODEL_DOWNLOADING
    assert mi.ModelState.MODEL_READY in (
        mi.ModelState.MODEL_READY,)  # final state asserted by return below
    state, _ = p.install_from_bytes.__self__.state() if False else p.state()
    assert state == mi.ModelState.MODEL_READY


def test_status_record_is_honest_and_complete(tmp_path):
    raw = build_archive()
    p = pipeline_for(tmp_path, make_spec(raw))
    rec = p.status()
    for key in ("name", "state", "detail", "installed", "source", "provider",
                "license", "engine", "expected_size", "sha256", "sha512",
                "compatible"):
        assert key in rec, key
    assert rec["installed"] is False
    assert rec["state"] == mi.ModelState.MODEL_MISSING.value
    assert rec["license"] == "TEST"
    p.install_from_bytes(raw)
    rec = p.status()
    assert rec["installed"] is True
    assert rec["state"] == mi.ModelState.MODEL_READY.value


def test_install_from_file_manual_offline_path(tmp_path):
    raw = build_archive()
    p = pipeline_for(tmp_path, make_spec(raw))
    archive = tmp_path / "manual.zip"
    archive.write_bytes(raw)
    state, _ = p.install_from_file(str(archive))
    assert state == mi.ModelState.MODEL_READY


def test_already_ready_short_circuits(tmp_path):
    raw = build_archive()
    p = pipeline_for(tmp_path, make_spec(raw))
    p.install_from_bytes(raw)
    reached = []

    def _fetch(url, progress):
        reached.append(url)
        return raw

    state, msg = p.download_and_install(consent=lambda q: True,
                                        fetch=_fetch)
    assert state == mi.ModelState.MODEL_READY
    assert "already present" in msg
    assert reached == []          # a verified model is never re-fetched


# ---------------------------------------------------------------------------
# adversarial: every rejection leaves the system unchanged
# ---------------------------------------------------------------------------

def test_size_mismatch_rejected(tmp_path):
    import dataclasses
    raw = build_archive()
    spec = dataclasses.replace(make_spec(raw), size=len(raw) + 1)
    p = pipeline_for(tmp_path, spec)
    with pytest.raises(mi.ModelIntegrityError) as ei:
        p.install_from_bytes(raw)
    assert "size mismatch" in ei.value.reason
    assert not (tmp_path / "models" / "m").exists()


def test_sha256_mismatch_rejected(tmp_path):
    raw = build_archive()
    spec = make_spec(raw)
    evil = bytearray(raw)
    evil[len(raw) // 2] ^= 0xFF
    evil = bytes(evil)
    spec = mi.ModelSpec(**{**spec.__dict__, "size": len(evil)})
    p = pipeline_for(tmp_path, spec)
    with pytest.raises(mi.ModelIntegrityError) as ei:
        p.install_from_bytes(evil)
    assert "SHA256 mismatch" in ei.value.reason
    assert not (tmp_path / "models" / "m").exists()


def test_sha512_mismatch_rejected(tmp_path):
    raw = build_archive()
    spec = make_spec(raw)
    evil = bytearray(raw)
    evil[len(raw) // 2] ^= 0xFF
    evil = bytes(evil)
    spec = mi.ModelSpec(**{**spec.__dict__,
                           "size": len(evil),
                           "sha256": hashlib.sha256(evil).hexdigest()})
    p = pipeline_for(tmp_path, spec)
    with pytest.raises(mi.ModelIntegrityError) as ei:
        p.install_from_bytes(evil)
    assert "SHA512 mismatch" in ei.value.reason
    assert not (tmp_path / "models" / "m").exists()


def test_truncated_archive_rejected(tmp_path):
    raw = build_archive()
    spec = make_spec(raw)
    truncated = raw[: len(raw) - 5]
    spec = mi.ModelSpec(**{**spec.__dict__, "size": len(truncated),
                           "sha256": hashlib.sha256(truncated).hexdigest(),
                           "sha512": hashlib.sha512(truncated).hexdigest()})
    p = pipeline_for(tmp_path, spec)
    with pytest.raises(mi.ModelIntegrityError):
        p.install_from_bytes(truncated)
    assert not (tmp_path / "models" / "m").exists()


def _zip_with_evil_member(member: str, *, create_system: bytes = b""):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("test-model-0.1/am/final.mdl", b"m")
        zf.writestr(member, b"evil")
    return buf.getvalue()


@pytest.mark.parametrize("member", [
    "../evil.txt",
    "/abs/evil.txt",
    "C:\\evil.txt",
    "test-model-0.1\\..\\..\\evil.txt",
    "test-model-0.1/../../evil.txt",
])
def test_zip_slip_members_refused(tmp_path, member):
    raw = _zip_with_evil_member(member)
    spec = make_spec(raw, members=2)
    p = pipeline_for(tmp_path, spec)
    with pytest.raises(mi.ModelIntegrityError) as ei:
        p.install_from_bytes(raw)
    assert "refused" in ei.value.reason or "zip-slip" in ei.value.reason
    assert not (tmp_path / "models" / "m").exists()
    assert not (tmp_path / "evil.txt").exists()


def test_executable_member_refused(tmp_path):
    raw = _zip_with_evil_member("test-model-0.1/payload.exe")
    spec = make_spec(raw, members=2)
    p = pipeline_for(tmp_path, spec)
    with pytest.raises(mi.ModelIntegrityError) as ei:
        p.install_from_bytes(raw)
    assert "executable" in ei.value.reason
    assert not (tmp_path / "models" / "m").exists()


def test_executable_permission_bit_refused(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("test-model-0.1/am/final.mdl", b"m")
        info = zipfile.ZipInfo("test-model-0.1/run.sh")
        info.external_attr = 0o755 << 16
        zf.writestr(info, b"#!/bin/sh\nevil")
    raw = buf.getvalue()
    spec = make_spec(raw, members=2)
    p = pipeline_for(tmp_path, spec)
    with pytest.raises(mi.ModelIntegrityError) as ei:
        p.install_from_bytes(raw)
    assert "executable" in ei.value.reason
    assert not (tmp_path / "models" / "m").exists()


def test_symlink_member_refused(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("test-model-0.1/am/final.mdl", b"m")
        info = zipfile.ZipInfo("test-model-0.1/link")
        info.external_attr = (0o120777 << 16)
        zf.writestr(info, "/etc/passwd")
    raw = buf.getvalue()
    spec = make_spec(raw, members=2)
    p = pipeline_for(tmp_path, spec)
    with pytest.raises(mi.ModelIntegrityError) as ei:
        p.install_from_bytes(raw)
    assert "symlink" in ei.value.reason
    assert not (tmp_path / "models" / "m").exists()


def test_zip_bomb_ratio_refused(tmp_path):
    # 10 MiB of zeros compresses to a few KiB → ratio far above 1000:1
    bomb = struct.pack("<1000000i", *([0] * 1_000_000))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("test-model-0.1/am/final.mdl", bomb)
        zf.writestr("test-model-0.1/conf/model.conf", b"c")
        zf.writestr("test-model-0.1/graph/words.txt", b"w")
    raw = buf.getvalue()
    spec = make_spec(raw, members=3)
    p = pipeline_for(tmp_path, spec)
    with pytest.raises(mi.ModelIntegrityError) as ei:
        p.install_from_bytes(raw)
    assert "bomb" in ei.value.reason
    assert not (tmp_path / "models" / "m").exists()


def test_wrong_root_refused(tmp_path):
    raw = build_archive(root="totally-different")
    spec = make_spec(raw)
    p = pipeline_for(tmp_path, spec)
    with pytest.raises(mi.ModelIntegrityError) as ei:
        p.install_from_bytes(raw)
    assert "layout" in ei.value.reason
    assert not (tmp_path / "models" / "m").exists()


def test_member_count_mismatch_refused(tmp_path):
    raw = build_archive(extra_members=3)
    spec = make_spec(raw)          # spec still expects 4 members
    p = pipeline_for(tmp_path, spec)
    with pytest.raises(mi.ModelIntegrityError) as ei:
        p.install_from_bytes(raw)
    assert "member count" in ei.value.reason
    assert not (tmp_path / "models" / "m").exists()


def test_crc_corruption_rejected(tmp_path):
    # ZIP_STORED so the stored bytes ARE the content bytes — flipping one
    # byte inside the model content guarantees a member CRC mismatch
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("test-model-0.1/am/final.mdl", b"pretend kaldi model")
        zf.writestr("test-model-0.1/conf/model.conf", b"--freq=16000")
        zf.writestr("test-model-0.1/graph/words.txt", b"<unk> 0\n")
    raw = bytearray(buf.getvalue())
    pos = bytes(raw).find(b"pretend kaldi model")
    assert pos > 0
    raw[pos + 3] ^= 0xFF
    corrupted = bytes(raw)
    spec = make_spec(corrupted)
    p = pipeline_for(tmp_path, spec)
    with pytest.raises(mi.ModelIntegrityError) as ei:
        p.install_from_bytes(corrupted)
    assert "CRC" in ei.value.reason or "Error" in ei.value.detail \
        or "zip" in ei.value.reason.lower()
    assert not (tmp_path / "models" / "m").exists()


# ---------------------------------------------------------------------------
# rollback + concurrency + resource hygiene
# ---------------------------------------------------------------------------

def test_failed_postinstall_verifies_rollback_restores_previous(tmp_path,
                                                                monkeypatch):
    raw = build_archive()
    p = pipeline_for(tmp_path, make_spec(raw))
    p.install_from_bytes(raw)
    previous = (tmp_path / "models" / "m" / "conf" / "model.conf").read_text()

    good = build_archive(extra_members=0)
    spec = make_spec(good)
    p2 = mi.ModelPipeline(spec=spec,
                          dest=str(tmp_path / "models" / "m"))
    # make the post-install verification fail AFTER the atomic rename
    monkeypatch.setattr(p2, "verify_existing",
                        lambda: (False, "simulated engine refusal"))
    with pytest.raises(mi.ModelIntegrityError) as ei:
        p2.install_from_bytes(good)
    assert "post-install" in ei.value.reason
    # ROLLBACK: the previous model is back, intact
    assert (tmp_path / "models" / "m" / "am" / "final.mdl").exists()
    assert (tmp_path / "models" / "m" / "conf" / "model.conf"
            ).read_text() == previous


def test_disk_full_leaves_system_unchanged(tmp_path, monkeypatch):
    raw = build_archive()
    spec = make_spec(raw)
    p = pipeline_for(tmp_path, spec)

    real_fsync = os.fsync

    def full_fsync(fd):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "fsync", full_fsync)
    with pytest.raises(mi.ModelIntegrityError) as ei:
        p.install_from_bytes(raw)
    assert "disk full" in ei.value.reason
    monkeypatch.setattr(os, "fsync", real_fsync)
    parent = tmp_path / "models"
    leftovers = [e.name for e in parent.iterdir()]
    assert leftovers == []       # temp archive cleaned, nothing installed


def test_live_lock_refuses_second_install(tmp_path, monkeypatch):
    raw = build_archive()
    p = pipeline_for(tmp_path, make_spec(raw))
    p._acquire_lock()
    try:
        p2 = pipeline_for(tmp_path, make_spec(raw))
        # fake a live writer: current pid + fresh timestamp
        with open(p2._lock(), "w", encoding="utf-8") as fh:
            fh.write(f"{os.getpid()}\n{__import__('time').time()}\n")
        with pytest.raises(mi.ModelIntegrityError) as ei:
            p2.install_from_bytes(raw)
        assert "another installation" in ei.value.reason
    finally:
        p._release_lock()


def test_stale_lock_is_reaped(tmp_path):
    raw = build_archive()
    p = pipeline_for(tmp_path, make_spec(raw))
    os.makedirs(tmp_path / "models", exist_ok=True)
    # dead pid + ancient timestamp → stale
    with open(p._lock(), "w", encoding="utf-8") as fh:
        fh.write("999999\n0\n")
    state, _ = p.install_from_bytes(raw)
    assert state == mi.ModelState.MODEL_READY


def test_stale_staging_dirs_are_reaped(tmp_path):
    raw = build_archive()
    p = pipeline_for(tmp_path, make_spec(raw))
    stale = tmp_path / "models" / "m.staging-999999"
    stale.mkdir(parents=True)
    (stale / "junk").write_text("left behind by a dead run")
    p.install_from_bytes(raw)
    assert not stale.exists()
    assert (tmp_path / "models" / "m" / "am" / "final.mdl").exists()


def test_verified_model_never_silently_replaced(tmp_path):
    raw = build_archive()
    p = pipeline_for(tmp_path, make_spec(raw))
    p.install_from_bytes(raw)
    marker = (tmp_path / "models" / "m" / "am" / "final.mdl")
    before = marker.stat().st_mtime_ns
    # a second successful install still goes through verify + atomic
    # switch (never in-place mutation), and ends READY
    p2 = mi.ModelPipeline(spec=make_spec(raw), dest=p.dest)
    state, _ = p2.install_from_bytes(raw)
    assert state == mi.ModelState.MODEL_READY
    assert marker.stat().st_mtime_ns >= before
    assert (tmp_path / "models" / "m" / "am" / "final.mdl").exists()
    leftovers = [e.name for e in (tmp_path / "models").iterdir()]
    assert leftovers == ["m"]     # backup removed after success


# ---------------------------------------------------------------------------
# the REAL official archive (skips when the asset is absent)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not REAL_ARCHIVE.exists(),
                    reason="real archive not present (offline determinism)")
def test_real_official_archive_installs_ready():
    raw = REAL_ARCHIVE.read_bytes()
    assert len(raw) == 41_205_931
    assert hashlib.sha256(raw).hexdigest() == REAL_SHA256
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        dest = os.path.join(td, "vosk")
        p = mi.ModelPipeline(spec=mi.VOSK_MODEL, dest=dest)
        state, msg = p.install_from_bytes(raw)
        assert state == mi.ModelState.MODEL_READY
        # the single top-level folder BECOMES dest (models/vosk semantics)
        assert os.path.isfile(os.path.join(dest, "am", "final.mdl"))
        st, _ = p.state()
        assert st == mi.ModelState.MODEL_READY


@pytest.mark.skipif(not REAL_ARCHIVE.exists(),
                    reason="real archive not present (offline determinism)")
def test_real_archive_wrong_digest_refused():
    raw = bytearray(REAL_ARCHIVE.read_bytes())
    raw[-1] ^= 0xFF                       # single flipped byte
    evil = bytes(raw)
    spec = mi.ModelSpec(**{**mi.VOSK_MODEL.__dict__, "size": len(evil)})
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = mi.ModelPipeline(spec=spec, dest=os.path.join(td, "vosk"))
        with pytest.raises(mi.ModelIntegrityError) as ei:
            p.install_from_bytes(evil)
        assert "SHA256 mismatch" in ei.value.reason
        assert not os.path.exists(os.path.join(td, "vosk"))


@pytest.mark.skipif(not REAL_ARCHIVE.exists(),
                    reason="real archive not present (offline determinism)")
def test_real_archive_via_voice_bootstrap_delegation(isolated_home):
    """The full delegation path over the REAL archive.  When the vosk
    engine is importable this ends READY; when it is not, the honest
    engine gate refuses and REMOVES the model (never a fake READY)."""
    from airmouse import voice_bootstrap as vb
    raw = REAL_ARCHIVE.read_bytes()
    mgr = vb.ASRBootstrapManager()
    ok, msg = mgr.install_vosk_model(consent=lambda q: True,
                                     fetch=lambda u, p: raw)
    engine_present = True
    try:
        import vosk  # noqa: F401
    except Exception:            # noqa: BLE001 — engine not installed here
        engine_present = False
    if engine_present:
        assert ok is True
        assert "READY" in msg
        # the single top-level folder becomes models/vosk itself
        assert (Path(isolated_home) / "models" / "vosk" / "am" /
                "final.mdl").exists()
    else:
        assert ok is False
        assert "VERIFY_FAILED" in msg
        assert "engine failed to initialize" in msg
        assert not (Path(isolated_home) / "models" / "vosk").exists()


# ---------------------------------------------------------------------------
# hygiene: the module itself stays inside the security law
# ---------------------------------------------------------------------------

def test_model_integrity_source_has_no_exec_vectors():
    src = Path(mi.__file__).read_text(encoding="utf-8")
    for evil in ("eval(", "exec(", "os.system", "shell=True",
                 "__import__", "pickle.loads"):
        assert evil not in src, evil
    assert "alphacephei.com" in mi.VOSK_MODEL.url
    assert mi.VOSK_MODEL.provider.startswith("alphacephei.com")


def test_fetcher_only_ever_receives_the_pinned_url(tmp_path):
    """The pipeline hands the fetcher EXACTLY the pinned official URL —
    never a redirect target, never anything else."""
    raw = build_archive()
    p = pipeline_for(tmp_path, make_spec(raw))
    seen = []

    def _spy_fetch(url, progress):
        seen.append(url)
        return raw

    p.download_and_install(consent=lambda q: True, fetch=_spy_fetch)
    assert seen == [p.spec.url]


def test_real_url_pin_matches_ground_truth_record():
    """The pin equals the digest measured live during the v19 baseline
    audit (4th independent measurement) and cross-checked against the
    independently published Alpine aports digest."""
    assert mi.VOSK_MODEL.size == 41_205_931
    assert mi.VOSK_MODEL.sha256 == REAL_SHA256
    assert len(mi.VOSK_MODEL.sha512) == 128
    assert mi.VOSK_MODEL.sha512.startswith("d56243ba")
    assert mi.VOSK_MODEL.sha512.endswith("f53bc5a")
    assert mi.VOSK_MODEL.members == 20
    assert mi.VOSK_MODEL.root == "vosk-model-small-en-us-0.15"


# ---------------------------------------------------------------------------
# regression (Windows CI): os.kill(pid, 0) is a POSIX liveness probe — on
# Windows signal 0 IS CTRL_C_EVENT, so reaping a lock whose pid was alive
# sent a real Ctrl+C to the process group and interrupted the suite.
# _pid_alive() must probe without ever signaling.
# ---------------------------------------------------------------------------

def test_pid_alive_never_signals_and_answers_definitely():
    pid = mi._pid_alive
    # pid 0 / negative / garbage → not alive, no exception, no signal
    assert pid(0) is False
    assert pid(-1) is False
    # OUR OWN live pid must be reported alive on every platform —
    # this is exactly the case that used to raise KeyboardInterrupt
    # on Windows (GenerateConsoleCtrlEvent to our own group)
    assert pid(os.getpid()) is True
    # a process we KNOW was allocated and has exited must be dead.  A
    # made-up pid cannot be used here: Windows pid allocation is an
    # implementation detail (the "pids are multiples of 4" folklore does
    # not hold on modern runners — OpenProcess happily answered for a
    # made-up odd pid), so we prove the contract with a real lifecycle.
    import subprocess
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    for _ in range(5):                       # tolerate instant pid recycle
        if pid(proc.pid) is False:
            break
        time.sleep(0.05)
    assert pid(proc.pid) is False, \
        "an exited process must not be reported alive"


def test_reaper_refuses_fresh_lock_of_live_process(tmp_path):
    raw = build_archive()
    p = pipeline_for(tmp_path, make_spec(raw))
    os.makedirs(tmp_path / "models", exist_ok=True)
    # OUR pid + fresh timestamp → genuinely busy → refuse honestly
    with open(p._lock(), "w", encoding="utf-8") as fh:
        fh.write(f"{os.getpid()}\n{__import__('time').time()}\n")
    with pytest.raises(mi.ModelIntegrityError, match="another installation"):
        p.install_from_bytes(raw)
    # and the lock file survives (we did not steal it, we did not signal it)
    assert os.path.exists(p._lock())
