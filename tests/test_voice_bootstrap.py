"""v16.5.3 — the Windows ASR bootstrap test matrix.

Covers the v16.5.3 mission sections:

* §2  THE regression — Windows x64 + CPython 3.12 + PocketSphinx 5.1.1
  has NO prebuilt wheel; the diagnosis is
  ``NATIVE_ASR_PACKAGE_UNAVAILABLE`` / SOURCE_BUILD_REQUIRED — never a
  microphone failure, and never a silent source build.
* §3/§15 the ASRBootstrapManager: platform/python/arch detection,
  deterministic recommendation, install → verify → rollback, and the
  explicit state vocabulary (BootstrapState + Obstacle).
* §4  zero-compiler law: every pip invocation carries
  ``--only-binary :all:``; a refusal NEVER reaches pip; no module in
  the bootstrap uses eval/exec/os.system/shell=True.
* §5/§7 vosk installs on ANY CPython 3 (py3-none wheels); alternate
  runtimes are REPORT-ONLY.
* §6  security: the only download is the official alphacephei.com
  model, SHA256-verified, Zip-Slip-safe, consent-gated; nothing is
  fetched without consent; OFFLINE is honoured.
* §16 honesty: sizes are disclosed, "lightweight" is never claimed for
  what is not.
* vosk-model honesty — the sandbox-reproduced divergence (registry
  said AVAILABLE while the engine had no model) is regression-tested:
  no model → IMPORTABLE_NOT_INITIALIZED-ish INSTALLED state, and the
  engine's own available() agrees.

No test touches the network, the microphone, or a real pip.
"""

import io
import os
import zipfile

import pytest

import airmouse.asr_registry as asr
import airmouse.voice_bootstrap as vb
import airmouse.voice_stack as vs
from airmouse.voice_bootstrap import (
    ASRBootstrapManager, BootstrapState, Obstacle,
    NATIVE_ASR_PACKAGE_UNAVAILABLE,
)
from airmouse.offline_voice import OfflineSpeechProvider, Transcript


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def fresh_manager():
    return ASRBootstrapManager()


@pytest.fixture()
def isolated_home(monkeypatch, tmp_path):
    home = tmp_path / "airmouse_home"
    monkeypatch.setenv("AIRMOUSE_HOME", str(home))
    return home


def _force_platform(monkeypatch, system="Windows", machine="AMD64",
                    version=(3, 12)):
    """Patch the MODULE-LEVEL detectors (never the live sys module)."""
    plat = {"Windows": "windows", "Linux": "linux",
            "Darwin": "macos"}.get(system, system.lower())
    monkeypatch.setattr(vb, "detect_platform", lambda: plat)
    monkeypatch.setattr(vb, "detect_architecture",
                        lambda: "x64/AMD64" if machine == "AMD64" else
                        ("arm64" if machine == "ARM64" else machine))
    monkeypatch.setattr(vb, "detect_python_version", lambda: version)


class _FakeReg:
    """Registry stand-in with fixed provider rows (no real machine)."""

    def __init__(self, states=None):
        self.states = states or {}

    def discover(self, force=False):
        from airmouse.asr_registry import ProviderDiagnosis
        return {name: ProviderDiagnosis(name=name, state=state)
                for name, state in self.states.items()}

    def select(self, prefer=None, force=False):
        return None

    def active(self):
        return None


class _NeverRun:
    """subprocess.run stand-in that explodes — proves nothing runs."""

    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError(
            f"subprocess.run must not be called: {args}")


# ---------------------------------------------------------------------------
# §2 — THE regression: Windows x64 + CPython 3.12 + PocketSphinx 5.1.1
# ---------------------------------------------------------------------------

def test_regression_pocketsphinx_no_cp312_windows_wheel(monkeypatch,
                                                        fresh_manager):
    """The user's exact machine: PocketSphinx cannot install as a
    prebuilt wheel; the honest verdict is NO_COMPATIBLE_WHEEL with the
    source-build warning — and NOTHING may attempt a build."""
    _force_platform(monkeypatch, "Windows", "AMD64", (3, 12))
    verdict, detail = fresh_manager.detect_wheel_compatibility("pocketsphinx")
    assert verdict == "NO_COMPATIBLE_WHEEL"
    assert "3.11" in detail and "3.13" in detail
    assert "source" in detail.lower()


def test_regression_plan_sends_windows_312_to_vosk(monkeypatch,
                                                   fresh_manager):
    """On Windows/3.12 the deterministic plan is: pocketsphinx refused
    (SOURCE_BUILD_REQUIRED), vosk = the zero-compiler path."""
    _force_platform(monkeypatch, "Windows", "AMD64", (3, 12))
    # fake machine: nothing usable yet (this sandbox may have a REAL
    # provider — the plan test must not depend on that)
    fresh_manager._registry = _FakeReg(
        {"pocketsphinx": "NOT_INSTALLED", "vosk": "NOT_INSTALLED"})
    rep, plan = fresh_manager.recommend_installation()
    by_name = {p.engine: p for p in plan}
    assert by_name["pocketsphinx"].verdict == "NO_COMPATIBLE_WHEEL"
    assert by_name["pocketsphinx"].obstacle == Obstacle.SOURCE_BUILD_REQUIRED
    assert by_name["vosk"].verdict == "WHEEL_AVAILABLE"
    assert by_name["vosk"].model_extra_mb > 0     # the consented download
    assert "compiler" in by_name["vosk"].reason.lower() or \
           "bundled" in by_name["vosk"].reason.lower()


def test_regression_classify_native_asr_package_unavailable(monkeypatch,
                                                            fresh_manager,
                                                            isolated_home):
    """§2 wording: when NO engine has a wheel for this interpreter, the
    diagnosis is NATIVE_ASR_PACKAGE_UNAVAILABLE — a packaging fact,
    never 'microphone failure'."""
    _force_platform(monkeypatch, "Windows", "ARM64", (3, 12))
    fresh_manager._registry = _FakeReg(
        {"pocketsphinx": "NOT_INSTALLED", "vosk": "NOT_INSTALLED"})
    rep, plan = fresh_manager.recommend_installation()
    assert rep.diagnosis == NATIVE_ASR_PACKAGE_UNAVAILABLE
    assert not any(p.verdict == "WHEEL_AVAILABLE" for p in plan)
    # never classified as a microphone failure:
    assert "microphone failure" not in (rep.recommendation or "").lower()


def test_never_silently_compile_pocketsphinx_on_312(monkeypatch,
                                                    fresh_manager):
    """§4 — the refusal happens BEFORE pip: subprocess is never invoked
    for a NO_COMPATIBLE_WHEEL engine."""
    _force_platform(monkeypatch, "Windows", "AMD64", (3, 12))
    guard = _NeverRun()
    monkeypatch.setattr(vb.subprocess, "run", guard)
    ok, eng, msg = fresh_manager.install_supported_lightweight_runtime(
        "pocketsphinx", consent=None, offline=False)
    assert ok is False
    assert guard.calls == []                      # pip was NEVER touched
    assert "never attempted" in msg


# ---------------------------------------------------------------------------
# §5 — vosk: the zero-compiler path on any CPython 3
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pyver", [(3, 9), (3, 11), (3, 12), (3, 13)])
def test_vosk_wheel_available_on_any_cpython3(monkeypatch, fresh_manager,
                                              pyver):
    _force_platform(monkeypatch, "Windows", "AMD64", pyver)
    verdict, detail = fresh_manager.detect_wheel_compatibility("vosk")
    assert verdict == "WHEEL_AVAILABLE"
    assert "bundled" in detail.lower()


@pytest.mark.parametrize("pyver", [(3, 11), (3, 13)])
def test_pocketsphinx_wheel_available_where_pypi_publishes(monkeypatch,
                                                           fresh_manager,
                                                           pyver):
    """PyPI publishes cp311/cp313 win_amd64 wheels — the table agrees."""
    _force_platform(monkeypatch, "Windows", "AMD64", pyver)
    verdict, _ = fresh_manager.detect_wheel_compatibility("pocketsphinx")
    assert verdict == "WHEEL_AVAILABLE"


def test_pocketsphinx_no_wheel_on_linux_312_either(monkeypatch,
                                                   fresh_manager):
    """Not just Windows: 5.1.1 has NO cp312 wheel on ANY platform."""
    _force_platform(monkeypatch, "Linux", "X86_64", (3, 12))
    verdict, _ = fresh_manager.detect_wheel_compatibility("pocketsphinx")
    assert verdict == "NO_COMPATIBLE_WHEEL"


# ---------------------------------------------------------------------------
# §4 — the zero-compiler install law
# ---------------------------------------------------------------------------

def test_pip_command_always_only_binary(fresh_manager):
    cmd = vb._pip_command("vosk")
    assert "--only-binary" in cmd and ":all:" in cmd
    assert "--disable-pip-version-check" in cmd


def test_install_consent_declined_installs_nothing(monkeypatch,
                                                   fresh_manager):
    _force_platform(monkeypatch, "Windows", "AMD64", (3, 12))
    fresh_manager._registry = _FakeReg(
        {"pocketsphinx": "NOT_INSTALLED", "vosk": "NOT_INSTALLED"})
    guard = _NeverRun()
    monkeypatch.setattr(vb.subprocess, "run", guard)
    ok, eng, msg = fresh_manager.install_supported_lightweight_runtime(
        "vosk", consent=lambda q: False)
    assert ok is False
    assert guard.calls == []
    assert "declined" in msg.lower()


def test_install_offline_attempts_nothing(monkeypatch, fresh_manager):
    _force_platform(monkeypatch, "Windows", "AMD64", (3, 12))
    fresh_manager._registry = _FakeReg(
        {"pocketsphinx": "NOT_INSTALLED", "vosk": "NOT_INSTALLED"})
    guard = _NeverRun()
    monkeypatch.setattr(vb.subprocess, "run", guard)
    ok, eng, msg = fresh_manager.install_supported_lightweight_runtime(
        "vosk", consent=None, offline=True)
    assert ok is False
    assert guard.calls == []
    assert "offline" in msg.lower() and "manual" in msg.lower()


def test_install_verify_failure_triggers_rollback(monkeypatch, fresh_manager,
                                                  isolated_home):
    """pip succeeds → verification fails → the package is uninstalled
    (nothing half-broken stays) — §3 rollback_if_failed."""
    _force_platform(monkeypatch, "Windows", "AMD64", (3, 12))
    fresh_manager._registry = _FakeReg(
        {"pocketsphinx": "NOT_INSTALLED", "vosk": "NOT_INSTALLED"})

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if "install" in cmd:
            class R:
                returncode = 0
                stdout = "ok"
                stderr = ""
            return R()
        if "uninstall" in cmd:
            class R:
                returncode = 0
                stdout = "uninstalled"
                stderr = ""
            return R()
        raise AssertionError(f"unexpected pip call: {cmd}")

    monkeypatch.setattr(vb.subprocess, "run", fake_run)
    monkeypatch.setattr(fresh_manager, "verify_installation",
                        lambda eng: (False, "probe failed (fake)"))
    ok, eng, msg = fresh_manager.install_supported_lightweight_runtime(
        "vosk", consent=None, offline=False)
    assert ok is False
    assert "rolled back" in msg
    assert any("uninstall" in c for c in calls), calls


# ---------------------------------------------------------------------------
# §15 — bootstrap states (mapping from THE registry)
# ---------------------------------------------------------------------------

def _registry_row(name, state, reason="", importable=False,
                  initialization="NOT RUN"):
    from airmouse.asr_registry import ProviderDiagnosis
    return name, ProviderDiagnosis(name=name, state=state, reason=reason,
                                   importable=importable,
                                   initialization=initialization)


def test_bootstrap_state_mapping(fresh_manager, monkeypatch):
    rows = dict([
        _registry_row("a", "NOT_INSTALLED"),
        _registry_row("b", "FAILED", reason="ModuleNotFoundError: no"),
        _registry_row("c", "FAILED", reason="initialization exploded"),
        _registry_row("d", "INSTALLED",
                      reason="no local acoustics model"),
        _registry_row("e", "INITIALIZABLE"),
        _registry_row("f", "AVAILABLE"),
        _registry_row("g", "ACTIVE"),
    ])

    class _FakeReg:
        def discover(self, force=False):
            return dict(rows)

    monkeypatch.setattr(fresh_manager, "_registry", _FakeReg())
    engines = fresh_manager.detect_installed_engines()
    assert engines["a"]["state"] == BootstrapState.NOT_INSTALLED
    assert engines["b"]["state"] == \
        BootstrapState.INSTALLED_NOT_IMPORTABLE
    assert engines["c"]["state"] == BootstrapState.INITIALIZATION_FAILED
    assert engines["d"]["state"] == \
        BootstrapState.IMPORTABLE_NOT_INITIALIZED
    assert engines["e"]["state"] == BootstrapState.READY
    assert engines["f"]["state"] == BootstrapState.READY
    assert engines["g"]["state"] == BootstrapState.READY


# ---------------------------------------------------------------------------
# vosk model honesty — the divergence reproduced in the sandbox
# ---------------------------------------------------------------------------

def _fake_find_spec(allowed):
    allowed = set(allowed)

    def _fs(name, *a, **k):
        return object() if name in allowed else None
    return _fs


def test_registry_vosk_without_model_is_installed_not_available(monkeypatch):
    """THE sandbox-reproduced defect: vosk importable + no model MUST
    NOT be reported usable.  The registry state is INSTALLED
    ('importable, init unproven') and select() refuses it — no
    divergence from the engine's own available()."""
    from airmouse.asr_registry import (ProviderState, VoskModelMissing,
                                       ASRProviderRegistry)
    monkeypatch.setattr(vs, "find_spec", _fake_find_spec({"vosk"}))
    probes = dict(asr._ENGINE_PROBES)
    probes["vosk"] = lambda: (_ for _ in ()).throw(VoskModelMissing())
    monkeypatch.setattr(asr, "_ENGINE_PROBES", probes)
    reg = ASRProviderRegistry()
    d = reg.discover(force=True)
    vosk_row = d["vosk"]
    assert vosk_row.state == ProviderState.INSTALLED
    assert vosk_row.initialization == "MODEL MISSING"
    assert vosk_row.importable is True
    assert reg.available() == []                 # NEVER usable
    assert reg.select() is None                  # never selected


def test_vosk_provider_agrees_with_registry(monkeypatch, isolated_home):
    """The engine's own available() must agree: no model → False."""
    from airmouse.offline_voice import VoskProvider
    import types
    fake_vosk = types.ModuleType("vosk")

    class _Model:
        def __init__(self, path):
            if not os.path.isdir(os.path.join(path, "conf")):
                raise RuntimeError("Folder does not contain model files")
    fake_vosk.Model = _Model
    monkeypatch.setitem(__import__("sys").modules, "vosk", fake_vosk)
    prov = VoskProvider()
    assert prov.available() is False             # honest: model missing
    # with a model directory present, the engine becomes usable
    model_dir = isolated_home / "models" / "vosk"
    (model_dir / "conf").mkdir(parents=True)
    (model_dir / "am").mkdir()
    (model_dir / "conf" / "model.conf").write_text("{}", encoding="utf-8")
    prov2 = VoskProvider()
    assert prov2.available() is True


def test_vosk_model_resolution_precedence(monkeypatch, isolated_home):
    from airmouse.offline_voice import resolve_vosk_model_path
    assert resolve_vosk_model_path() == ""          # nothing anywhere
    models = isolated_home / "models"
    fixed = models / "vosk"
    (fixed / "conf").mkdir(parents=True)
    (fixed / "graph").mkdir()
    (fixed / "conf" / "model.conf").write_text("{}", encoding="utf-8")
    # 4 — the shipped models dir
    assert resolve_vosk_model_path() == str(fixed)
    # 2 — env wins over the shipped dir
    envdir = isolated_home / "env-model"
    (envdir / "conf").mkdir(parents=True)
    (envdir / "am").mkdir()
    monkeypatch.setenv("AIRMOUSE_VOSK_MODEL_PATH", str(envdir))
    assert resolve_vosk_model_path() == str(envdir)
    # 1 — explicit argument wins over env
    assert resolve_vosk_model_path(str(fixed)) == str(fixed)


# ---------------------------------------------------------------------------
# §6 — model download security (consent, sha256, zip-slip, offline)
# ---------------------------------------------------------------------------

def _model_zip_bytes(top="vosk-model-small-en-us-0.15"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{top}/conf/model.conf", "{}")
        zf.writestr(f"{top}/am/final.mdl", b"MODEL DATA")
        zf.writestr(f"{top}/graph/phones.txt", "DATA")
    return buf.getvalue()


def test_model_download_requires_consent(fresh_manager, isolated_home):
    ok, msg = fresh_manager.install_vosk_model(consent=lambda q: False)
    assert ok is False
    assert Obstacle.USER_DECLINED in msg
    assert not (isolated_home / "models" / "vosk").exists()


def test_model_download_offline_is_honest(fresh_manager, isolated_home):
    ok, msg = fresh_manager.install_vosk_model(consent=None, offline=True)
    assert ok is False
    assert Obstacle.OFFLINE_MODEL_REQUIRED in msg
    assert "alphacephei.com" in msg              # the official source
    assert not (isolated_home / "models" / "vosk").exists()


def test_model_sha256_mismatch_blocks_extraction(monkeypatch, fresh_manager,
                                                 isolated_home):
    exploded = _NeverRun()

    def fake_extract(raw, dest):
        exploded()

    monkeypatch.setattr(vb, "_extract_model_zip", fake_extract)
    ok, msg = fresh_manager.install_vosk_model(
        consent=lambda q: True,
        fetch=lambda url, p: b"NOT THE REAL MODEL BYTES")
    assert ok is False
    # v19: the 12-stage pipeline rejects untrusted bytes at the EARLIEST
    # honest stage — fake bytes (24 B) fail the pinned-size stage before
    # the SHA256 stage.  The invariant under test is unchanged: nothing
    # that fails verification is ever extracted, and the failure is
    # explicit about size or digest.
    assert Obstacle.VERIFY_FAILED in msg or "mismatch" in msg
    assert "SHA256" in msg or "sha256" in msg or "size mismatch" in msg
    assert exploded.calls == []                  # nothing was extracted
    assert not (isolated_home / "models" / "vosk").exists()


def test_model_zip_slip_is_blocked(fresh_manager, isolated_home, tmp_path):
    """A malicious member must never escape the extraction root."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("vosk-model/conf/model.conf", "{}")
        zf.writestr("vosk-model/../../evil.txt", "PWNED")
    raw = buf.getvalue()
    dest = str(isolated_home / "models" / "vosk")
    with pytest.raises(ValueError):
        vb._extract_model_zip(raw, dest)
    assert not (isolated_home.parent / "evil.txt").exists()
    assert not (isolated_home / "evil.txt").exists()


def test_model_executable_members_are_refused(fresh_manager, isolated_home):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("vosk-model/conf/model.conf", "{}")
        zf.writestr("vosk-model/tools/payload.dll", b"BINARY")
    with pytest.raises(ValueError):
        vb._extract_model_zip(buf.getvalue(),
                              str(isolated_home / "models" / "vosk"))


def test_model_extract_and_rename_layout(fresh_manager, isolated_home):
    dest = isolated_home / "models" / "vosk"
    vb._extract_model_zip(_model_zip_bytes(), str(dest))
    assert (dest / "conf" / "model.conf").exists()
    assert (dest / "am" / "final.mdl").exists()
    staging = str(dest) + ".staging"
    assert not os.path.exists(staging)


def test_existing_model_short_circuits_download(fresh_manager,
                                                isolated_home,
                                                monkeypatch):
    model_dir = isolated_home / "models" / "vosk"
    (model_dir / "conf").mkdir(parents=True)
    (model_dir / "am").mkdir()
    (model_dir / "conf" / "model.conf").write_text("{}",
                                                   encoding="utf-8")
    monkeypatch.setattr(fresh_manager, "verify_installation",
                        lambda eng: (True, "AVAILABLE"))
    ok, msg = fresh_manager.install_vosk_model(consent=lambda q: False)
    assert ok is True
    assert "already present" in msg


# ---------------------------------------------------------------------------
# §16 — honest footprint disclosure
# ---------------------------------------------------------------------------

def test_footprint_sizes_are_disclosed():
    info = {i.name: i for i in vb._WHEEL_MATRIX}
    assert info["vosk"].wheel_size_mb > 0
    assert info["vosk"].model_extra_mb > 0       # the model is NOT free
    assert info["pocketsphinx"].model_extra_mb == 0   # bundled model
    assert "compiler" in info["pocketsphinx"].notes.lower() or \
        "toolchain" in info["pocketsphinx"].notes.lower()


# ---------------------------------------------------------------------------
# §8/§9 — the voice-setup orchestrator renders
# ---------------------------------------------------------------------------

def test_voice_setup_check_only_installs_nothing(monkeypatch, capsys):
    _force_platform(monkeypatch, "Windows", "AMD64", (3, 12))
    guard = _NeverRun()
    monkeypatch.setattr(vb.subprocess, "run", guard)
    rc = vb.run_voice_setup(out=io.StringIO(), dry_run=True, yes=True)
    assert rc == 0
    assert guard.calls == []


def test_voice_setup_no_path_fails_honestly(monkeypatch):
    """§8 — when no compatible runtime exists the user gets the honest
    failure panel with a next step — never a pip traceback."""
    _force_platform(monkeypatch, "Windows", "ARM64", (3, 12))

    class _NoneReg:
        def discover(self, force=False):
            return {}

        def active(self):
            return None

    mgr = ASRBootstrapManager(registry=_NoneReg())
    monkeypatch.setattr(vb, "bootstrap_manager", mgr)
    out = io.StringIO()
    rc = vb.run_voice_setup(out=out, check_only=False, yes=True)
    text = out.getvalue()
    assert rc == 1
    assert "VOICE SETUP COULD NOT COMPLETE" in text
    assert NATIVE_ASR_PACKAGE_UNAVAILABLE in text
    assert "Recommended next step" in text


def test_voice_setup_already_ready_short_circuits(monkeypatch):
    """§8 — the READY path verifies + smoke-tests; --dry-run on a
    machine with a viable plan never installs anything."""
    out = io.StringIO()
    rc = vb.run_voice_setup(out=out, check_only=True, yes=True)
    text = out.getvalue()
    assert rc == 0
    assert ("CHECK ONLY" in text) or ("VOICE READY" in text)


# ---------------------------------------------------------------------------
# §22 — the benchmark is honest
# ---------------------------------------------------------------------------

class _BenchEngine(OfflineSpeechProvider):
    name = "vosk"

    def available(self):
        return True

    def transcribe(self, audio):
        return Transcript(text="", provider=self.name)


def test_benchmark_without_engine_points_at_voice_setup(monkeypatch):
    from airmouse import voice_benchmark as vbm
    from airmouse.asr_registry import ASRProviderRegistry

    class _NoneReg:
        def select(self, prefer=None, force=False):
            return None

    monkeypatch.setattr(vbm, "_get_registry", lambda: _NoneReg())
    out = io.StringIO()
    rc = vbm.run_benchmark(out=out)
    text = out.getvalue()
    assert rc == 1
    assert "voice-setup" in text
    assert "No local ASR engine" in text


def test_benchmark_never_claims_accuracy(monkeypatch):
    from airmouse import voice_benchmark as vbm

    class _FakeReg:
        def select(self, prefer=None, force=False):
            return _BenchEngine()

    monkeypatch.setattr(vbm, "_get_registry", lambda: _FakeReg())
    out = io.StringIO()
    rc = vbm.run_benchmark(out=out)
    text = out.getvalue()
    assert rc == 0
    assert "no accuracy" in text.lower()
    assert "TIMING" in text
    assert "peak RAM" in text


# ---------------------------------------------------------------------------
# §24 — red team: speech stays DATA; the bootstrap is inert
# ---------------------------------------------------------------------------

def test_bootstrap_source_has_no_exec_vectors():
    import inspect
    src = inspect.getsource(vb)
    for banned in ("eval(", "exec(", "os.system", "shell=True",
                   "curl |", "Invoke-WebRequest", "iex("):
        assert banned not in src, f"forbidden pattern: {banned}"


def test_bootstrap_only_talks_to_official_sources():
    import inspect
    src = inspect.getsource(vb)
    assert "https://alphacephei.com/vosk/models/" in src
    assert "http://" not in src.replace("http://localhost", "")
    # no third-party download hosts
    for evil in ("github.com/", "raw.githubusercontent", "cdn.", ".zip "
                 "http"):
        if evil == ".zip http":
            continue
        assert evil not in src, f"unexpected host reference: {evil}"


# ---------------------------------------------------------------------------
# real-machine end-to-end (honest skip without a local model)
# ---------------------------------------------------------------------------

def test_real_pipeline_when_a_real_engine_exists():
    """When THIS machine truly has a usable local ASR engine, the whole
    chain must run: the registry selects it, the engine transcribes
    real PCM bytes.  Skipped honestly (ACTION_REQUIRED) when no engine
    is usable — a skip is never reported as PASS."""
    from airmouse.asr_registry import registry as _registry
    provider = _registry.select(force=True)
    if provider is None or \
            str(getattr(provider, "name", "")) not in \
            ("pocketsphinx", "vosk"):
        pytest.skip("no usable local ASR engine (ACTION_REQUIRED: "
                    "airmouse voice-setup)")
    # On py3.11/3.13 machines with pocketsphinx installed the
    # lightweight-first registry correctly picks POCKETSPHINX (no
    # model download at all); elsewhere vosk.  Both are REAL local ASR.
    pcm = b"\x00\x00" * 1600                    # 0.1 s of silence — REAL
    t = provider.transcribe(vb._engine_input(provider.name, pcm, 16000))
    assert t is not None
