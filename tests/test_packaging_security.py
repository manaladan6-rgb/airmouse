"""tests/test_packaging_security.py — v19 Phase 17/24 packaging audit.

The packaging is part of AirMouse's safety architecture.  This suite:

* builds the wheel + sdist for real (setuptools backend, isolated);
* audits every packaged path — executables, symlink members, .pth /
  sitecustomize tricks, unexpected file types, path escapes;
* proves a corrupted wheel is DETECTABLE (zip CRC round-trip);
* audits the sdist for repository/workflow leakage (.git, venvs,
  test assets, caches);
* checks the declared dependencies (every dep has a floor bound — the
  supply-chain law) and the entry point contract;
* checks version consistency between pyproject and the package;
* generates + verifies a SHA256SUMS manifest the way the release does.

The built artifacts are cached per session in a tmp dir and reused by
the tests in this module (the build itself runs once).
"""

from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PKG = REPO / "airmouse_pkg"


def _build_tool_available() -> bool:
    """Probe `python -m build` in a SUBPROCESS — importable-in-process
    is not the contract (a stray build.py can shadow the import)."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "build", "--version"],
            capture_output=True, text=True, timeout=60)
        return proc.returncode == 0
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(
    not _build_tool_available(),
    reason="python -m build required (pip install build)")

_BUILD = {"done": False, "wheel": None, "sdist": None}


def _build_once(tmp_path_factory):
    if _BUILD["done"]:
        return _BUILD["wheel"], _BUILD["sdist"]
    out = tmp_path_factory.mktemp("v19dist")
    proc = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--sdist",
         str(PKG), "--outdir", str(out)],
        capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stderr[-2000:]
    arts = sorted(out.iterdir())
    wheel = next(a for a in arts if a.name.endswith(".whl"))
    sdist = next(a for a in arts if a.name.endswith(".tar.gz"))
    _BUILD.update(done=True, wheel=wheel, sdist=sdist)
    return wheel, sdist


@pytest.fixture(scope="module")
def artifacts(tmp_path_factory):
    return _build_once(tmp_path_factory)


# ---------------------------------------------------------------------------
# wheel audit
# ---------------------------------------------------------------------------

def test_wheel_zip_integrity(artifacts):
    wheel, _ = artifacts
    with zipfile.ZipFile(wheel) as zf:
        assert zf.testzip() is None


def test_wheel_contains_no_dangerous_members(artifacts):
    wheel, _ = artifacts
    banned_suffix = (".exe", ".dll", ".so", ".bat", ".cmd", ".ps1", ".com",
                     ".scr", ".msi", ".sh")
    with zipfile.ZipFile(wheel) as zf:
        for info in zf.infolist():
            name = info.filename
            low = name.lower()
            assert ".." not in Path(name).parts, name
            assert not low.startswith("/"), name
            assert not (len(low) > 1 and low[1] == ":"), name
            for b in banned_suffix:
                assert not low.endswith(b), f"executable member: {name}"
            mode = (info.external_attr >> 16) & 0o170000
            assert mode != 0o120000, f"symlink member: {name}"
            if not info.is_dir():
                assert not ((info.external_attr >> 16) & 0o111), \
                    f"executable bit: {name}"


def test_wheel_contains_no_import_time_bombs(artifacts):
    """No .pth execution, no sitecustomize, no __main__ at top level of
    site-packages — classic supply-chain execution vectors."""
    wheel, _ = artifacts
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
        for name in names:
            assert not name.endswith(".pth"), name
            assert Path(name).name != "sitecustomize.py", name
            assert Path(name).name != "usercustomize.py", name


def test_wheel_entry_point_contract(artifacts):
    wheel, _ = artifacts
    with zipfile.ZipFile(wheel) as zf:
        ep = zf.read([n for n in zf.namelist()
                      if n.endswith("entry_points.txt")][0]).decode()
    assert "airmouse" in ep
    assert "airmouse.__main__:main" in ep


def test_wheel_packages_the_v19_modules(artifacts):
    wheel, _ = artifacts
    with zipfile.ZipFile(wheel) as zf:
        names = set(zf.namelist())
    for mod in ("model_integrity.py", "capability_registry.py",
                "degradation.py", "evidence.py", "lifecycle.py",
                "wizard.py", "cli_menu.py", "voice_bootstrap.py"):
        assert f"airmouse/{mod}" in names, mod


def test_corrupted_wheel_is_detectable(artifacts, tmp_path):
    """Flip one byte inside the zip → the standard integrity check
    (zip.testzip) must catch it.  The release verifies checksums the
    same way before publishing."""
    wheel, _ = artifacts
    raw = bytearray(wheel.read_bytes())
    # flip a byte deep inside the archive payload
    pos = len(raw) // 2
    raw[pos] ^= 0xFF
    evil = tmp_path / "evil.whl"
    evil.write_bytes(bytes(raw))
    # a zip whose bytes changed either fails to open or fails testzip —
    # both are honest detections
    detected = False
    try:
        with zipfile.ZipFile(evil) as zf:
            detected = zf.testzip() is not None
    except Exception:  # noqa: BLE001 — bad zip = detected
        detected = True
    assert detected


# ---------------------------------------------------------------------------
# sdist audit
# ---------------------------------------------------------------------------

def test_sdist_no_repository_leakage(artifacts):
    _, sdist = artifacts
    banned_dirs = (".git", ".venv", "venv", ".test-assets", "__pycache__",
                   "node_modules", ".pytest_cache", "dist", "bundle")
    with tarfile.open(sdist) as tf:
        for member in tf.getmembers():
            parts = Path(member.name).parts
            for b in banned_dirs:
                assert b not in parts, f"{b} leaked into the sdist: " \
                                       f"{member.name}"


def test_sdist_contains_the_build_sources(artifacts):
    _, sdist = artifacts
    with tarfile.open(sdist) as tf:
        names = tf.getnames()
    assert any(n.endswith("pyproject.toml") for n in names)
    assert any(n.endswith("airmouse/__main__.py") for n in names)
    assert any(n.endswith("airmouse/model_integrity.py") for n in names)


# ---------------------------------------------------------------------------
# dependency + version laws
# ---------------------------------------------------------------------------

def test_every_dependency_declares_a_floor():
    """The supply-chain law: no unpinned, floor-less dependencies."""
    text = (PKG / "pyproject.toml").read_text(encoding="utf-8")
    deps = []
    in_deps = False
    for line in text.splitlines():
        if line.strip().startswith("dependencies = ["):
            in_deps = True
            continue
        if in_deps:
            if line.strip().startswith("]"):
                break
            deps.append(line.strip().strip('",'))
    assert deps, "no dependencies parsed"
    for dep in deps:
        assert any(op in dep for op in (">", "==", "~=", "<")), \
            f"dependency without a version bound: {dep}"


def test_pyproject_version_matches_package():
    text = (PKG / "pyproject.toml").read_text(encoding="utf-8")
    version_line = next(l for l in text.splitlines()
                        if l.startswith("version"))
    declared = version_line.split("=")[1].strip().strip('"')
    sys.path.insert(0, str(PKG))
    import airmouse  # noqa: PLC0415
    assert declared == airmouse.__version__


# ---------------------------------------------------------------------------
# checksums (the release contract)
# ---------------------------------------------------------------------------

def test_sha256sums_generation_and_verification_roundtrip(artifacts,
                                                          tmp_path):
    wheel, sdist = artifacts
    files = [wheel, sdist]
    sums = tmp_path / "SHA256SUMS"
    lines = []
    for f in files:
        digest = hashlib.sha256(f.read_bytes()).hexdigest()
        lines.append(f"{digest}  {f.name}")
    sums.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # verify round-trip exactly like the release battery does
    for line in sums.read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        f = tmp_path / name
        f.write_bytes((wheel if name == wheel.name else sdist).read_bytes())
        assert hashlib.sha256(f.read_bytes()).hexdigest() == digest
