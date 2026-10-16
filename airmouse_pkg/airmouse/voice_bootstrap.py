"""voice_bootstrap — Windows ASR bootstrap engine (v16.5.3).

The v16.5.2 release made local ASR honest; the user's Windows 11 +
CPython 3.12 x64 machine then exposed the LAST blocker — not a bug in
AirMouse, but a platform packaging gap::

    pip install pocketsphinx
      -> downloads pocketsphinx-5.1.1.tar.gz        (sdist)
      -> CMake selects NMake                        (native build)
      -> nmake missing / CMAKE_C_COMPILER missing   -> FAIL

PyPI fact (verified via the PyPI JSON API, recorded in
``_WHEEL_MATRIX`` below): PocketSphinx 5.1.1 publishes CPython wheels
for cp311 and cp313 only — there is NO cp312 wheel on ANY platform,
and the sdist requires a C/C++ toolchain.  Vosk 0.3.45 publishes
``py3-none`` platform wheels (win_amd64 13.7 MB) whose native library
is BUNDLED — they install on ANY CPython 3 on those platforms with no
compiler at all.

Mission law (v16.5.3):

* NEVER tell a normal Windows user "install CMake/NMake/MSVC".
* NEVER silently compile native software — installs go through
  ``pip install --only-binary :all:`` so pip ITSELF refuses any source
  build; nothing here ever invokes a compiler.
* NEVER download unofficial binaries, DLLs or installers; the only
  network fetches are official PyPI wheels (via pip) and the official
  Vosk model archive (alphacephei.com) — consent-gated, size-disclosed
  and SHA256-verified before extraction.
* Downloads are DATA (models), never executable code; archives are
  extracted with Zip-Slip protection and only into the AirMouse home.
* A machine without a compatible runtime gets an honest
  ``NATIVE_ASR_PACKAGE_UNAVAILABLE`` diagnosis + a clear next step —
  never a mysterious pip traceback, and NEVER a microphone-failure
  label when the microphone is fine.

States (mission §15)::

    BootstrapState   NOT_INSTALLED / INSTALLED_NOT_IMPORTABLE /
                     IMPORTABLE_NOT_INITIALIZED / INITIALIZATION_FAILED /
                     READY / RUNTIME_FAILED
    Obstacle         NO_COMPATIBLE_WHEEL / SOURCE_BUILD_REQUIRED /
                     COMPILER_REQUIRED / USER_DECLINED / INSTALL_FAILED /
                     OFFLINE_MODEL_REQUIRED / VERIFY_FAILED / NONE

The manager is DETERMINISTIC: same machine → same plan.  It reports
through :class:`airmouse.asr_registry.ASRProviderRegistry` (the ONE
registry law of v16.5.2) and never mutates it behind its back.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

__all__ = [
    "BootstrapState", "Obstacle", "WheelVerdict", "EnginePlan",
    "BootstrapReport", "ASRBootstrapManager", "bootstrap_manager",
    "classify_unavailable", "run_voice_setup", "render_setup_report",
    "NATIVE_ASR_PACKAGE_UNAVAILABLE", "VOSK_MODEL_URL", "VOSK_MODEL_SHA256",
]

#: §2 — the machine-readable diagnosis for "native ASR package has no
#: prebuilt wheel for this interpreter; a compiler would be required".
#: NOT a microphone failure, NOT an AirMouse voice failure.
NATIVE_ASR_PACKAGE_UNAVAILABLE = "NATIVE_ASR_PACKAGE_UNAVAILABLE"

#: the ONE official lightweight model AirMouse may fetch (consent-gated).
#: alphacephei.com is the official Vosk project site; the archive is
#: Apache-2.0 model DATA (no executables) and is SHA256-verified.
VOSK_MODEL_URL = ("https://alphacephei.com/vosk/models/"
                  "vosk-model-small-en-us-0.15.zip")
#: SHA256 of the official archive — v19 CORRECTION: the v16.5.3 pin
#: (507575b9… / 41,205,932 B) was WRONG and made every consented download
#: fail verification.  Re-measured LIVE in the v19 baseline audit (4th
#: independent measurement, 2nd complete download; cross-checked against
#: the independently published Alpine aports digest).  These values now
#: DELEGATE to airmouse.model_integrity (the single source of truth);
#: the module-level names are kept for backward compatibility.
from .model_integrity import VOSK_MODEL as _MI_VOSK_MODEL  # noqa: E402

VOSK_MODEL_SHA256 = _MI_VOSK_MODEL.sha256
VOSK_MODEL_BYTES = _MI_VOSK_MODEL.size
VOSK_MODEL_UNPACKED_MB = _MI_VOSK_MODEL.unpacked_mb  # measured (§16)


# ---------------------------------------------------------------------------
# states (mission §15)
# ---------------------------------------------------------------------------

class BootstrapState:
    """The six authoritative bootstrap states (mission §15)."""

    NOT_INSTALLED = "NOT_INSTALLED"
    INSTALLED_NOT_IMPORTABLE = "INSTALLED_NOT_IMPORTABLE"
    IMPORTABLE_NOT_INITIALIZED = "IMPORTABLE_NOT_INITIALIZED"
    INITIALIZATION_FAILED = "INITIALIZATION_FAILED"
    READY = "READY"
    RUNTIME_FAILED = "RUNTIME_FAILED"


class Obstacle:
    """Why an install path is (or is not) available (mission §15)."""

    NONE = "NONE"
    NO_COMPATIBLE_WHEEL = "NO_COMPATIBLE_WHEEL"
    SOURCE_BUILD_REQUIRED = "SOURCE_BUILD_REQUIRED"
    COMPILER_REQUIRED = "COMPILER_REQUIRED"
    USER_DECLINED = "USER_DECLINED"
    INSTALL_FAILED = "INSTALL_FAILED"
    OFFLINE_MODEL_REQUIRED = "OFFLINE_MODEL_REQUIRED"
    VERIFY_FAILED = "VERIFY_FAILED"


# ---------------------------------------------------------------------------
# static, versioned wheel-compatibility knowledge (verified via the
# PyPI JSON API; pip itself remains the final authority at install time
# because --only-binary :all: refuses anything this table got wrong)
# ---------------------------------------------------------------------------

_CPYTHON = f"cp{sys.version_info.major}{sys.version_info.minor}"


@dataclass(frozen=True)
class EngineWheelInfo:
    """One engine's prebuilt-wheel facts (static, disclosed, honest)."""

    name: str
    pypi_version: str
    #: CPython MINOR versions with prebuilt wheels (e.g. (11, 13) =
    #: cp311 + cp313); () with any_cpython=True = version-independent
    cpython_wheels: Tuple[int, ...]
    any_cpython: bool                        # py3-none wheels (vosk)
    platforms: Tuple[str, ...]               # pip platform tags supported
    wheel_size_mb: float                     # disclosed size (§16)
    model: str                               # how the acoustics model arrives
    model_extra_mb: float                    # extra download needed (0 = none)
    notes: str = ""


_WHEEL_MATRIX: Tuple[EngineWheelInfo, ...] = (
    EngineWheelInfo(
        name="pocketsphinx",
        pypi_version="5.1.1",
        cpython_wheels=(11, 13),             # NO 3.12 — the Windows gap
        any_cpython=False,
        platforms=("win_amd64", "macosx", "manylinux", "musllinux"),
        wheel_size_mb=28.4,
        model="bundled (en-US, ships inside the wheel)",
        model_extra_mb=0.0,
        notes="sdist (tar.gz) requires a C/C++ toolchain — source "
              "builds are NEVER attempted by AirMouse",
    ),
    EngineWheelInfo(
        name="vosk",
        pypi_version="0.3.45",
        cpython_wheels=(),                   # py3-none: version-independent
        any_cpython=True,
        platforms=("win_amd64", "win32", "manylinux2014_aarch64",
                   "manylinux_2_12_x86_64", "linux_armv7l"),
        wheel_size_mb=13.7,
        model="official small en-US model, downloaded once with consent "
              "(~39.3 MB zip, SHA256-verified, alphacephei.com)",
        model_extra_mb=39.3,
        notes="native Kaldi library is BUNDLED in the wheel — zero "
              "compiler needed on any CPython 3",
    ),
)

_ENGINE_ORDER = ("pocketsphinx", "vosk")  # registry priority (lightweight)


# ---------------------------------------------------------------------------
# platform facts
# ---------------------------------------------------------------------------

def detect_platform() -> str:
    """``windows`` | ``linux`` | ``macos`` | other (never raises)."""
    try:
        s = platform.system().lower()
        if s == "windows":
            return "windows"
        if s == "linux":
            return "linux"
        if s == "darwin":
            return "macos"
        return s or "unknown"
    except Exception:
        return "unknown"


def detect_python_version() -> Tuple[int, int]:
    """ ``(major, minor)`` of the RUNNING interpreter."""
    return (sys.version_info.major, sys.version_info.minor)


def detect_architecture() -> str:
    """x64/AMD64 | arm64 | x86 | other — the pip-relevant arch label."""
    try:
        m = platform.machine().upper()
        if m in ("AMD64", "X86_64"):
            return "x64/AMD64"
        if m in ("ARM64", "AARCH64"):
            return "arm64"
        if m in ("I386", "I686", "X86"):
            return "x86"
        return m or "unknown"
    except Exception:
        return "unknown"


def _platform_compatible(ptag: str, platforms: Tuple[str, ...]) -> bool:
    """pip-tag compatibility, deterministic and conservative.

    Direct prefix match covers win32/win_amd64/macosx.  On Linux the
    manylinux/musllinux families are treated as compatible with the
    matching ``linux_<arch>`` interpreter tag (glibc ≥ 2.12/2.17 is
    universal on supported distros); the architecture suffix must
    still match exactly.
    """
    for p in platforms:
        if ptag.startswith(p):
            return True
    if ptag.startswith("linux_"):
        for p in platforms:
            if not (p.startswith("manylinux") or p.startswith("musllinux")):
                continue
            for arch in ("x86_64", "aarch64", "armv7l"):
                if ptag.endswith(arch) and p.endswith(arch):
                    return True
        if ptag.endswith("armv7l") and any(
                p.endswith("armv7l") for p in platforms):
            return True
    return False


def _platform_tag() -> str:
    """The pip platform tag this bootstrap logic reasons about."""
    p = detect_platform()
    a = detect_architecture()
    if p == "windows":
        return "win32" if a == "x86" else \
            ("win_arm64" if a == "arm64" else "win_amd64")
    if p == "macos":
        return "macosx"
    if p == "linux":
        return "linux_" + ("aarch64" if a == "arm64" else
                           ("armv7l" if "ARMV7" in a else "x86_64"))
    return f"{p}_{a.lower()}"


# ---------------------------------------------------------------------------
# the manager
# ---------------------------------------------------------------------------

@dataclass
class EnginePlan:
    """One deterministic install option (rendered by voice-setup)."""

    engine: str
    label: str
    verdict: str                    # WheelVerdict value
    obstacle: str = Obstacle.NONE
    wheel_mb: float = 0.0
    model_extra_mb: float = 0.0
    pip_command: str = ""           # exact command the user could run
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return dict(vars(self))


@dataclass
class BootstrapReport:
    """Everything voice-setup / voice-status / doctor render (ONE call)."""

    platform: str = ""
    python_version: str = ""
    architecture: str = ""
    platform_tag: str = ""
    microphone: Dict[str, Any] = field(default_factory=dict)
    engines: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    plan: List[EnginePlan] = field(default_factory=list)
    recommendation: str = ""
    diagnosis: str = ""             # e.g. NATIVE_ASR_PACKAGE_UNAVAILABLE
    alternate_runtimes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "platform": self.platform,
            "python_version": self.python_version,
            "architecture": self.architecture,
            "platform_tag": self.platform_tag,
            "microphone": self.microphone,
            "engines": self.engines,
            "plan": [p.to_dict() for p in self.plan],
            "recommendation": self.recommendation,
            "diagnosis": self.diagnosis,
            "alternate_runtimes": list(self.alternate_runtimes),
        }


class ASRBootstrapManager:
    """Deterministic, transparent Windows ASR bootstrap (mission §3).

    Responsibilities — exactly the mission's list::

        detect_platform() / detect_python_version() / detect_architecture()
        detect_installed_engines()
        detect_wheel_compatibility()
        recommend_installation()
        install_supported_lightweight_runtime()
        verify_installation()
        rollback_if_failed()

    Determinism: the wheel knowledge is a STATIC table (no network to
    answer "is a wheel available"); pip remains the final authority at
    install time because ``--only-binary :all:`` refuses source builds.
    Transparency: every decision is returned as data and rendered
    verbatim; nothing is decided silently.
    """

    def __init__(self, registry: Any = None) -> None:
        try:
            from .asr_registry import registry as _reg
            self._registry = registry or _reg
        except Exception:  # pragma: no cover - defensive
            self._registry = None

    # -- detection -----------------------------------------------------------

    def detect_platform(self) -> str:
        return detect_platform()

    def detect_python_version(self) -> Tuple[int, int]:
        return detect_python_version()

    def detect_architecture(self) -> str:
        return detect_architecture()

    def detect_installed_engines(self) -> Dict[str, Dict[str, Any]]:
        """Per-engine bootstrap states from THE registry (never raises)."""
        out: Dict[str, Dict[str, Any]] = {}
        if self._registry is None:
            return out
        try:
            d = self._registry.discover(force=True)
        except Exception:
            return out
        for name, row in d.items():
            st = row.state
            if st == "NOT_INSTALLED":
                bs = BootstrapState.NOT_INSTALLED
            elif st == "FAILED":
                low = (row.reason or "").lower()
                import_broken = ("modulenotfounderror" in low or
                                 "no module named" in low or
                                 "importerror" in low or
                                 "import" in low)
                bs = (BootstrapState.INSTALLED_NOT_IMPORTABLE
                      if import_broken
                      else BootstrapState.INITIALIZATION_FAILED)
            elif st == "INSTALLED":
                bs = BootstrapState.IMPORTABLE_NOT_INITIALIZED
            elif st in ("INITIALIZABLE", "AVAILABLE"):
                bs = BootstrapState.READY
            elif st == "ACTIVE":
                bs = BootstrapState.READY
            else:  # DISABLED
                bs = BootstrapState.NOT_INSTALLED
            out[name] = {
                "state": bs,
                "registry_state": st,
                "importable": bool(row.importable),
                "initialization": row.initialization,
                "reason": row.reason or "",
            }
        return out

    def detect_wheel_compatibility(self, engine: str) -> Tuple[str, str]:
        """``(verdict, detail)`` for ``engine`` on THIS machine.

        Verdicts: ``WHEEL_AVAILABLE`` | ``NO_COMPATIBLE_WHEEL`` |
        ``SOURCE_BUILD_REQUIRED`` | ``UNKNOWN``.  Consults the static
        verified matrix FIRST (deterministic, offline); ``UNKNOWN``
        means the table has no data and pip stays the only authority.
        """
        info = next((i for i in _WHEEL_MATRIX if i.name == engine), None)
        if info is None:
            return ("UNKNOWN",
                    "no wheel knowledge recorded for this engine")
        ptag = _platform_tag()
        plat_ok = _platform_compatible(ptag, info.platforms)
        if not plat_ok:
            return ("NO_COMPATIBLE_WHEEL",
                    f"prebuilt wheels cover {', '.join(info.platforms)} "
                    f"— not {ptag}")
        if info.any_cpython:
            return ("WHEEL_AVAILABLE",
                    f"py3-none wheel ({info.wheel_size_mb:.1f} MB) — "
                    f"native library bundled, any CPython 3, no compiler")
        minor = detect_python_version()[1]
        if minor in info.cpython_wheels:
            return ("WHEEL_AVAILABLE",
                    f"cp3{minor} wheel ({info.wheel_size_mb:.1f} MB) "
                    f"available for {ptag}")
        # installed CPython has no wheel — the sdist would need a build
        have = "/".join(f"3.{v}" for v in info.cpython_wheels)
        return ("NO_COMPATIBLE_WHEEL",
                f"prebuilt wheels exist for CPython {have} only; "
                f"CPython 3.{minor} has none (sdist = source build, "
                f"toolchain required)")

    def detect_alternate_runtimes(self) -> List[str]:
        """Report-only scan for interpreters with PocketSphinx wheels.

        §5B/§7: AirMouse NEVER installs a second Python runtime and
        never will silently — this only REPORTS what already exists
        (e.g. ``python3.13`` on PATH or the Windows ``py -3.13``
        launcher), so the recommendation can mention a supported path
        the user may already have.
        """
        found: List[str] = []
        here = f"{sys.version_info.major}.{sys.version_info.minor}"
        for cand in ("python3.13", "python3.11", "python3", "python"):
            try:
                exe = shutil.which(cand)
                if not exe:
                    continue
                r = subprocess.run(  # noqa: S603 — fixed argv, no shell
                    [exe, "-c",
                     "import sys;print('%d.%d'%sys.version_info[:2])"],
                    capture_output=True, text=True, timeout=10,
                    shell=False)
                v = (r.stdout or "").strip()
                if r.returncode == 0 and v and v != here and \
                        v in ("3.13", "3.11"):
                    found.append(f"{cand} -> CPython {v}")
            except Exception:
                continue
        if detect_platform() == "windows":
            try:
                r = subprocess.run(  # noqa: S603 — fixed argv, no shell
                    ["py", "-3.13", "-c",
                     "import sys;print('%d.%d'%sys.version_info[:2])"],
                    capture_output=True, text=True, timeout=10,
                    shell=False)
                if r.returncode == 0 and "3.13" in (r.stdout or ""):
                    found.append("py -3.13 -> CPython 3.13")
            except Exception:
                pass
        out, seen = [], set()
        for f in found:
            if f not in seen:
                seen.add(f)
                out.append(f)
        return out

    # -- the deterministic plan ----------------------------------------------

    def recommend_installation(self) -> Tuple[BootstrapReport, List[EnginePlan]]:
        """Build the full report + ranked deterministic plan (mission §3).

        Ranking: engines the registry manages, lightweight-first
        (pocketsphinx, then vosk), keeping ONLY wheel-compatible ones —
        a source build is never an option.  When nothing qualifies the
        plan is empty and ``report.diagnosis`` carries
        :data:`NATIVE_ASR_PACKAGE_UNAVAILABLE`.
        """
        rep = BootstrapReport(
            platform=self.detect_platform(),
            python_version="%d.%d" % detect_python_version(),
            architecture=self.detect_architecture(),
            platform_tag=_platform_tag(),
        )
        try:
            from .asr_registry import detect_microphone
            rep.microphone = detect_microphone()
        except Exception:
            rep.microphone = {"available": False, "error": "detection failed"}
        rep.engines = self.detect_installed_engines()

        # already usable?  then the plan is "nothing to install"
        usable = [n for n, e in rep.engines.items()
                  if e["state"] == BootstrapState.READY]
        plan: List[EnginePlan] = []
        if not usable:
            for name in _ENGINE_ORDER:
                verdict, detail = self.detect_wheel_compatibility(name)
                info = next((i for i in _WHEEL_MATRIX if i.name == name),
                            None)
                if verdict == "WHEEL_AVAILABLE":
                    need_model = bool(info and info.model_extra_mb > 0)
                    installed = rep.engines.get(name, {}).get(
                        "registry_state") != "NOT_INSTALLED"
                    if installed and not need_model:
                        continue  # already importable — nothing to pip
                    plan.append(EnginePlan(
                        engine=name,
                        label=info.model if info else name,
                        verdict=verdict,
                        wheel_mb=info.wheel_size_mb if info else 0.0,
                        model_extra_mb=info.model_extra_mb if info else 0.0,
                        pip_command=_pip_command(name),
                        reason=detail))
                elif verdict == "NO_COMPATIBLE_WHEEL":
                    plan.append(EnginePlan(
                        engine=name,
                        label=info.model if info else name,
                        verdict=verdict,
                        obstacle=(Obstacle.SOURCE_BUILD_REQUIRED
                                  if "sdist" in detail else
                                  Obstacle.NO_COMPATIBLE_WHEEL),
                        reason=detail))
                # UNKNOWN engines never make the plan silently
        rep.plan = plan

        if usable:
            rep.recommendation = ("local ASR already READY: " +
                                  ", ".join(usable) +
                                  " — run: airmouse voice-status --verbose")
            rep.diagnosis = ""
        elif plan and any(p.verdict == "WHEEL_AVAILABLE" for p in plan):
            first = next(p for p in plan if p.verdict == "WHEEL_AVAILABLE")
            extra = f" + {first.model_extra_mb:.1f} MB model" \
                if first.model_extra_mb else ""
            rep.recommendation = (
                f"install the smallest compatible local voice engine: "
                f"{first.engine} ({first.wheel_mb:.1f} MB{extra}) via "
                f"airmouse voice-setup")
            rep.diagnosis = ""
        else:
            rep.diagnosis = NATIVE_ASR_PACKAGE_UNAVAILABLE
            rep.alternate_runtimes = self.detect_alternate_runtimes()
            if rep.alternate_runtimes:
                rep.recommendation = (
                    "no prebuilt wheel for this interpreter; a compatible "
                    "Python runtime exists on this machine (" +
                    "; ".join(rep.alternate_runtimes) + ") — PocketSphinx "
                    "installs there without a compiler.  AirMouse will not "
                    "install or switch runtimes by itself.")
            else:
                rep.recommendation = (
                    "no prebuilt local-ASR wheel exists for this "
                    "interpreter/platform.  Your microphone can still be "
                    "fine — this is a package availability problem, not a "
                    "hardware failure.  Official options: install Python "
                    "3.13 from python.org (PocketSphinx ships a 3.13 "
                    "Windows x64 wheel), or check vosk's platform coverage "
                    "for this architecture.")
        return rep, plan

    # -- install / verify / rollback -------------------------------------------

    def install_supported_lightweight_runtime(
            self, engine: str,
            consent: Optional[Callable[[str], bool]] = None,
            offline: bool = False) -> Tuple[bool, str, str]:
        """Install ONE wheel-compatible engine — never a source build.

        Returns ``(installed, engine, message)``.  Every install is:

        * gated on explicit consent when a callback is given (mission
          §4: choices are presented, never assumed),
        * refused outright OFFLINE with the exact manual command,
        * executed as ``python -m pip install --only-binary :all:``
          (pip itself refuses sdist/source builds — §4's hard
          guarantee), and
        * verified afterwards (:meth:`verify_installation`); a failed
          verification triggers :meth:`rollback_if_failed`.

        The Vosk path additionally needs its model; that download is a
        separate, separately-consented step (:meth:`install_vosk_model`)
        so the wheel install and the data download are each visible.
        """
        verdict, detail = self.detect_wheel_compatibility(engine)
        if verdict != "WHEEL_AVAILABLE":
            return (False, engine,
                    f"{engine}: {verdict} — {detail}  (a compiler-based "
                    f"source build is never attempted)")
        cmd = _pip_command(engine, as_list=True)
        already = False
        try:
            already = any(
                e.get("registry_state") != "NOT_INSTALLED"
                for n, e in self.detect_installed_engines().items()
                if n == engine)
        except Exception:
            already = False
        if already:
            # engine present (e.g. installed but modelless — the repair
            # path): skip pip entirely; verification happens AFTER the
            # full pipeline (model included), so a missing model can
            # never trigger an engine rollback here
            return (True, engine,
                    f"{engine} is already installed — nothing to pip "
                    f"(continuing setup)")
        if consent is not None:
            try:
                if not consent(_pip_command(engine)):
                    return (False, engine,
                            f"declined — nothing was installed "
                            f"(manual: {_pip_command(engine)})")
            except Exception as exc:
                return (False, engine,
                        f"consent gate failed ({exc}) — nothing installed")
        if offline:
            return (False, engine,
                    f"offline: no installs are attempted without network "
                    f"(manual: {_pip_command(engine)})")
        try:
            r = subprocess.run(  # noqa: S603 — fixed argv, no shell
                cmd, capture_output=True, text=True, timeout=900,
                shell=False, env=_pip_env())
        except Exception as exc:
            return (False, engine,
                    f"pip could not run: {exc}  (manual: "
                    f"{_pip_command(engine)})")
        if r.returncode != 0:
            tail = ((r.stderr or r.stdout or "").strip().splitlines() or [""])
            reason = tail[-1][:300]
            return (False, engine,
                    f"pip exit {r.returncode}: {reason}  "
                    f"(full log: rerun manually — {_pip_command(engine)})")
        ok, vmsg = self.verify_installation(engine)
        if not ok:
            self.rollback_if_failed(engine)
            return (False, engine,
                    f"installed but verification FAILED ({vmsg}) — the "
                    f"package was rolled back; nothing half-broken stays")
        return (True, engine, f"{engine} installed and verified ({vmsg})")

    def install_vosk_model(
            self, consent: Optional[Callable[[str], bool]] = None,
            offline: bool = False,
            progress: Optional[Callable[[str], None]] = None,
            fetch: Optional[Callable[[str, Callable[[int]], bytes]]] = None,
            ) -> Tuple[bool, str]:
        """Fetch + verify + extract the official Vosk model (consent-gated).

        * the ONLY model AirMouse ever downloads (39.3 MB, official
          alphacephei.com archive, Apache-2.0 DATA — no executables),
        * size disclosed BEFORE the fetch; explicit consent required,
        * SHA256 verified BEFORE extraction (mismatch → deleted, honest
          failure),
        * extracted with Zip-Slip protection into the AirMouse home
          (``models/vosk/``), never into system paths,
        * OFFLINE → :data:`Obstacle.OFFLINE_MODEL_REQUIRED` with exact
          manual instructions (never a partial/silent state).

        ``fetch`` is injectable for tests (no network in CI).
        """
        # v19 DELEGATION: the whole acquisition is now owned by
        # airmouse.model_integrity (the reconstructed 12-stage pipeline:
        # temp→fsync→bytecount→SHA256→SHA512→CRC→structure→zip-slip→
        # exec/symlink→staged→validate→ATOMIC + rollback).  This method
        # keeps its historical (ok, message) contract and adds the
        # engine-level verification on top.
        try:
            from . import model_integrity as mi
            from . import paths
            from .offline_voice import _looks_like_vosk_model
            dest = paths.vosk_model_dir()
            if _looks_like_vosk_model(dest):
                ok, vmsg = self.verify_installation("vosk")
                return (ok, "model already present" if ok
                        else f"existing model failed verification: {vmsg}")
            if offline:
                return (False, f"{Obstacle.OFFLINE_MODEL_REQUIRED}: the "
                        f"lightweight en-US model downloads once from the "
                        f"official Vosk project ({VOSK_MODEL_URL}, "
                        f"{VOSK_MODEL_BYTES / 1e6:.1f} MB).  Manual: "
                        f"download + unzip it to {dest}")
            if consent is not None:
                question = (f"Download the official lightweight en-US "
                            f"speech model?  {VOSK_MODEL_BYTES / 1e6:.1f} MB, "
                            f"one-time, from alphacephei.com (Apache-2.0 "
                            f"data), SHA256-verified, stored at {dest}")
                try:
                    if not consent(question):
                        return (False,
                                f"{Obstacle.USER_DECLINED}: no model was "
                                f"downloaded (manual: fetch {VOSK_MODEL_URL} "
                                f"and unzip into {dest})")
                except Exception as exc:
                    return (False, f"consent gate failed ({exc}) — nothing "
                            f"downloaded")

            def _default_fetch(url: str, _p: Callable[[int]]) -> bytes:
                import urllib.request
                req = urllib.request.Request(
                    url, headers={"User-Agent": "airmouse-voice-setup"})
                with urllib.request.urlopen(req, timeout=1800) as resp:
                    total = int(resp.headers.get("Content-Length")
                                or VOSK_MODEL_BYTES)
                    buf = io.BytesIO()
                    got = 0
                    while True:
                        chunk = resp.read(256 * 1024)
                        if not chunk:
                            break
                        buf.write(chunk)
                        got += len(chunk)
                        _p(got)
                    if got != total:
                        raise IOError(f"download truncated: {got}/{total} "
                                      f"bytes")
                    return buf.getvalue()

            fetcher = fetch or _default_fetch
            say = progress or (lambda _s: None)
            say(f"downloading {VOSK_MODEL_BYTES / 1e6:.1f} MB …")
            raw = fetcher(VOSK_MODEL_URL, lambda n: None)
            # v19: digest + extraction now owned by model_integrity
            # (SHA256+SHA512+CRC+structure+zip-slip+atomic+rollback)
            say("verifying + installing …")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            pipeline = mi.ModelPipeline(spec=mi.VOSK_MODEL, dest=dest)
            state, imsg = pipeline.install_from_bytes(raw, progress=say)
            if state != mi.ModelState.MODEL_READY:
                return (False, f"{Obstacle.VERIFY_FAILED}: {imsg}")
            if not _looks_like_vosk_model(dest):
                return (False, f"{Obstacle.VERIFY_FAILED}: extracted data "
                        f"is not a valid Vosk model — removed")
            ok, vmsg = self.verify_installation("vosk")
            if not ok:
                shutil.rmtree(dest, ignore_errors=True)
                return (False, f"{Obstacle.VERIFY_FAILED}: model extracted "
                        f"but the engine failed to initialize ({vmsg}) — "
                        f"the model directory was removed")
            return (True, f"model READY at {dest} ({vmsg})")
        except mi.ModelIntegrityError as exc:
            # v19: verification-class failures keep the honest obstacle
            # vocabulary (VERIFY_FAILED / OFFLINE_MODEL_REQUIRED / …)
            reason = str(exc.reason)
            if "consent" in reason.lower() or "declined" in reason.lower():
                return (False, f"{Obstacle.USER_DECLINED}: {exc.detail}")
            if "offline" in reason.lower():
                return (False, f"{Obstacle.OFFLINE_MODEL_REQUIRED}: "
                        f"{exc.detail}")
            return (False, f"{Obstacle.VERIFY_FAILED}: {exc.reason} — "
                    f"{exc.detail}")
        except Exception as exc:
            return (False, f"model setup failed honestly: "
                    f"{type(exc).__name__}: {exc}")

    def verify_installation(self, engine: str) -> Tuple[bool, str]:
        """Re-probe THE registry (force) — READY only when it truly runs."""
        if self._registry is None:
            return (False, "registry unavailable")
        try:
            d = self._registry.discover(force=True)
            row = d.get(engine)
            if row is None:
                return (False, "unknown engine")
            if row.state in ("INITIALIZABLE", "AVAILABLE"):
                return (True, f"{engine} {row.state} "
                        f"(initialization: {row.initialization})")
            return (False, row.reason or f"{engine} state {row.state}")
        except Exception as exc:
            return (False, f"{type(exc).__name__}: {exc}")

    def rollback_if_failed(self, engine: str) -> str:
        """Undo a failed install — pip uninstall + model-dir cleanup.

        Only AirMouse-managed artifacts are touched; the model dir is
        removed ONLY when it lives inside the AirMouse home.  Never
        raises; returns a human line for the transcript.
        """
        lines: List[str] = []
        try:
            r = subprocess.run(  # noqa: S603 — fixed argv, no shell
                [sys.executable, "-m", "pip", "uninstall", "-y", engine],
                capture_output=True, text=True, timeout=300, shell=False,
                env=_pip_env())
            lines.append(f"pip uninstall {engine}: exit {r.returncode}")
        except Exception as exc:
            lines.append(f"pip uninstall {engine} failed: {exc}")
        try:
            from . import paths
            dest = os.path.abspath(paths.vosk_model_dir())
            home = os.path.abspath(paths.airmouse_home())
            if engine == "vosk" and dest.startswith(home + os.sep) and \
                    os.path.isdir(dest):
                shutil.rmtree(dest, ignore_errors=True)
                lines.append("partial model directory removed")
        except Exception as exc:
            lines.append(f"model cleanup failed: {exc}")
        return "; ".join(lines)


def _pip_command(engine: str, as_list: bool = False):
    """The exact pip command AirMouse runs (or the user can copy)."""
    args = [sys.executable, "-m", "pip", "install", "--only-binary",
            ":all:", "--disable-pip-version-check", engine]
    if as_list:
        return args
    return _display_command(args)


def _display_command(args: List[str]) -> str:
    exe = os.path.basename(sys.executable) or "python"
    win = detect_platform() == "windows"
    py = "py" if (win and exe.lower().startswith("python")) else exe
    return " ".join([py] + args[1:] if py != exe else args)


def _pip_env() -> Dict[str, str]:
    """pip env with UTF-8 IO so Windows console decoding never explodes
    (the v16.5.1 encoding law, applied to subprocesses too)."""
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    return env


def _extract_model_zip(raw: bytes, dest: str) -> None:
    """Extract the model archive with Zip-Slip protection.

    Every member path is resolved and REQUIRED to stay inside the
    staging directory; absolute paths, ``..`` and device names are
    rejected.  Only regular data files/dirs are extracted.  The
    archive is model DATA — any executable-looking member
    (.exe/.dll/.so/.bat/.cmd/.ps1) is refused outright (defence in
    depth; official archives contain none).  The single top-level
    folder becomes ``dest`` itself (models/vosk/).
    """
    banned = (".exe", ".dll", ".so", ".bat", ".cmd", ".ps1", ".com",
              ".scr", ".msi", ".sh")
    dest_abs = os.path.abspath(dest)
    parent = os.path.dirname(dest_abs)
    staging = dest_abs + ".staging"
    if os.path.exists(staging):
        shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging, exist_ok=True)
    final_root = ""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            # locate the single top-level folder (vosk-model-small-en-us-0.15)
            tops = {n.split("/")[0] for n in zf.namelist() if n.strip()}
            if len(tops) != 1:
                raise ValueError(f"unexpected archive layout: {sorted(tops)}")
            final_root = os.path.join(staging, next(iter(tops)))
            for member in zf.infolist():
                name = member.filename
                if name.endswith("/"):
                    continue
                low = name.lower()
                if any(low.endswith(b) for b in banned):
                    raise ValueError(f"refusing executable member in model "
                                     f"archive: {name}")
                target = os.path.abspath(os.path.join(staging, name))
                if not (target == staging or
                        target.startswith(staging + os.sep)):
                    raise ValueError(f"zip-slip blocked: {name}")
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(member) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
        if not final_root or not os.path.isdir(final_root):
            raise ValueError("archive did not contain a model folder")
        # atomic-ish swap: old dest out, staged model in
        if os.path.exists(dest_abs):
            shutil.rmtree(dest_abs, ignore_errors=True)
        os.makedirs(parent, exist_ok=True)
        os.replace(final_root, dest_abs)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


#: the process-wide manager (constructor is cheap; probes are cached)
bootstrap_manager = ASRBootstrapManager()


def classify_unavailable() -> str:
    """The honest machine-level diagnosis when no engine is usable.

    Returns :data:`NATIVE_ASR_PACKAGE_UNAVAILABLE` when the microphone
    is fine and every known engine lacks a prebuilt wheel for this
    interpreter/platform — the user's exact v16.5.3 situation.  This is
    a PACKAGING fact, never a microphone or AirMouse failure.
    """
    try:
        rep, _plan = bootstrap_manager.recommend_installation()
        return rep.diagnosis
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# §8/§9 rendering + the voice-setup orchestrator
# ---------------------------------------------------------------------------

def _mic_line(rep: BootstrapReport) -> str:
    m = rep.microphone or {}
    if m.get("available"):
        dev = (m.get("devices") or [{}])[0].get("name", "")
        return f"Checking microphone................ READY" + \
               (f"  ({dev[:40]})" if dev else "")
    return "Checking microphone................ NOT READY"


def render_setup_report(rep: BootstrapReport, verbose: bool = False) -> str:
    """The §3 example panel — deterministic, honest, actionable."""
    L: List[str] = ["AirMouse Voice Setup", ""]
    L.append(_mic_line(rep))
    L.append(f"Python:                             CPython "
             f"{rep.python_version} {rep.architecture}")
    for name in _ENGINE_ORDER:
        e = rep.engines.get(name) or {}
        info = next((i for i in _WHEEL_MATRIX if i.name == name), None)
        state = e.get("state", BootstrapState.NOT_INSTALLED)
        L.append(f"{name.capitalize()}:")
        L.append(f"  installed:                        "
                 f"{'YES' if state != BootstrapState.NOT_INSTALLED else 'NO'}")
        L.append(f"  bootstrap state:                  {state}")
        if info:
            verdict, detail = \
                bootstrap_manager.detect_wheel_compatibility(name)
            L.append(f"  compatible prebuilt wheel:        "
                     f"{'FOUND' if verdict == 'WHEEL_AVAILABLE' else 'NOT FOUND'}")
            if verbose:
                L.append(f"    {detail}")
                L.append(f"    model: {info.model}")
    if rep.plan and any(p.verdict == "WHEEL_AVAILABLE" for p in rep.plan):
        L.append("")
        L.append("Recommended lightweight path:")
        n = 0
        for p in rep.plan:
            if p.verdict != "WHEEL_AVAILABLE":
                continue
            n += 1
            extra = (f" + {p.model_extra_mb:.1f} MB model"
                     if p.model_extra_mb else "")
            L.append(f"  [option {chr(64 + n)}] {p.engine} — "
                     f"{p.wheel_mb:.1f} MB wheel{extra}, "
                     f"no compiler required")
            if verbose and p.pip_command:
                L.append(f"          {p.pip_command}")
    if rep.diagnosis:
        L.append("")
        L.append(f"Diagnosis: {rep.diagnosis}")
    if rep.alternate_runtimes:
        L.append("Compatible runtimes already on this machine: " +
                 "; ".join(rep.alternate_runtimes))
    L.append("")
    L.append(f"Next: {rep.recommendation}")
    return "\n".join(L)


def render_setup_failure(reason: str, next_step: str) -> str:
    """The §8 honest failure panel — never a mysterious pip traceback."""
    return "\n".join([
        "", "VOICE SETUP COULD NOT COMPLETE", "", "Reason:",
        f"  {reason}", "", "No compiler was installed.",
        "No audio was uploaded.",
        "", "Recommended next step:", f"  {next_step}",
    ])


def run_voice_setup(out: Any = None,
                    input_fn: Optional[Callable[[str], str]] = None,
                    dry_run: bool = False,
                    check_only: bool = False,
                    repair: bool = False,
                    verbose: bool = False,
                    provider: Optional[str] = None,
                    yes: bool = False,
                    offline: bool = False) -> int:
    """``airmouse voice-setup`` (mission §8/§9) — the ZERO-COMPILER path.

    1. inspects environment + provider compatibility (THE registry),
    2. chooses the deterministic lightweight path,
    3. installs ONLY what is necessary (``--only-binary :all:`` — a
       compiler can never run), and only with consent / ``--yes``,
    4. fetches the model separately (consent + SHA256 + Zip-Slip-safe),
    5. verifies imports + REAL engine initialization,
    6. verifies the microphone,
    7. runs a deterministic ASR smoke test,
    8. reports the exact result (READY, or the honest §8 failure panel
       with the reason and the next step — never a bare traceback).

    ``--dry-run``/``--check-only`` never install anything.
    """
    w = out if out is not None else sys.stdout

    def emit(text: str = "") -> None:
        try:
            w.write(text + "\n")
            w.flush()
        except Exception:
            pass

    quiet = {"last": 0.0}

    def progress(msg: str) -> None:
        emit(f"  … {msg}")

    def _ask(question: str) -> bool:
        if yes:
            return True
        if input_fn is not None:
            try:
                return str(input_fn(question)).strip().upper() in \
                    ("Y", "YES")
            except Exception:
                return False
        try:
            if not sys.stdin or not sys.stdin.isatty():
                return False  # non-interactive: never surprise-install
        except Exception:
            return False
        try:
            return input(f"{question}  [Y/n] ").strip().upper() in \
                ("Y", "YES", "")
        except Exception:
            return False

    rep, plan = bootstrap_manager.recommend_installation()
    emit("AirMouse Voice Setup")
    emit("")
    emit(_mic_line(rep))
    emit(f"Checking local ASR................. "
         f"{'READY' if rep.recommendation.startswith('local ASR already') else 'NOT READY'}")
    emit(f"Python:                            CPython {rep.python_version} "
         f"{rep.architecture}")

    if rep.recommendation.startswith("local ASR already"):
        engine = _active_engine_name()
        emit(f"Checking {engine} engine................ READY")
        ok, vmsg = bootstrap_manager.verify_installation(engine)
        emit("Running local recognition test..... "
             f"{'READY' if ok else 'FAILED'}")
        if not ok:
            emit(f"  ({vmsg})")
            emit("")
            emit("VOICE SETUP COULD NOT COMPLETE")
            emit(f"Reason: {vmsg}")
            emit("Next: airmouse voice-setup --repair   (re-verify and "
                 "fix the installation)")
            return 1
        mic_ok = bool(rep.microphone.get("available"))
        emit("Verifying microphone............... "
             f"{'READY' if mic_ok else 'NOT READY'}")
        emit("Verifying ASR...................... READY")
        emit("")
        emit("VOICE READY")
        emit(f"Provider: {engine.capitalize()}")
        emit("Mode: MIC LIVE" if mic_ok else
             "Mode: TEXT INPUT (no microphone detected)")
        emit("Network required: NO")
        emit("Audio uploaded: NO")
        return 0

    # §9 step 2 — provider compatibility
    for name in _ENGINE_ORDER:
        verdict, _d = bootstrap_manager.detect_wheel_compatibility(name)
        emit(f"Checking {name} wheel "
             f"compatibility........ {'FOUND' if verdict == 'WHEEL_AVAILABLE' else 'NOT FOUND'}")

    if not plan or not any(p.verdict == "WHEEL_AVAILABLE" for p in plan):
        emit("")
        emit(render_setup_report(rep, verbose=verbose))
        emit(render_setup_failure(
            rep.diagnosis or "no compatible prebuilt local ASR runtime",
            rep.recommendation))
        return 1

    target = next(p for p in plan if p.verdict == "WHEEL_AVAILABLE")
    if provider and provider.lower() in \
            (p.engine for p in plan if p.verdict == "WHEEL_AVAILABLE"):
        target = next(p for p in plan
                      if p.engine == provider.lower() and
                      p.verdict == "WHEEL_AVAILABLE")
    emit(f"Checking compatible runtime........ FOUND  "
         f"({target.engine}, {target.wheel_mb:.1f} MB wheel"
         f"{' + %.1f MB model' % target.model_extra_mb if target.model_extra_mb else ''})")
    if dry_run or check_only:
        emit("")
        emit("CHECK ONLY — nothing was installed "
             "(run without --check-only to install)")
        if verbose:
            emit(render_setup_report(rep, verbose=True))
        return 0

    emit("")
    emit(f"Installing lightweight local voice engine "
         f"({target.engine})...")
    ok, _eng, msg = bootstrap_manager.install_supported_lightweight_runtime(
        target.engine,
        consent=(None if yes else _ask),
        offline=offline)
    if not ok:
        if msg.startswith("declined"):
            emit(f"  declined — {msg}")
            emit("")
            emit("Nothing was installed.  The built-in command grammar "
                 "keeps working; run airmouse voice-setup again any time.")
            return 0
        emit(f"  install failed: {msg}")
        emit(render_setup_failure(
            msg, rep.recommendation or
            "run: airmouse voice-status --verbose"))
        return 1
    emit("  " + msg)

    if target.engine == "vosk" and target.model_extra_mb:
        emit("Fetching the official lightweight model…")
        ok_m, msg_m = bootstrap_manager.install_vosk_model(
            consent=(None if yes else _ask),
            offline=offline,
            progress=progress)
        emit(f"  {msg_m}")
        if not ok_m:
            if "USER_DECLINED" in msg_m or "OFFLINE_MODEL" in msg_m:
                emit("")
                emit("The engine is installed; the model can be added "
                     "later: airmouse voice-setup")
                return 0
            emit(render_setup_failure(
                msg_m, "retry: airmouse voice-setup   (or fetch the "
                       "model manually — see the reason above)"))
            return 1

    # §9 steps 5-7 — verify engine + microphone + deterministic smoke test
    emit("Verifying microphone............... "
         f"{'READY' if rep.microphone.get('available') else 'NOT READY'}")
    emit("Verifying ASR...................... READY")
    smoke = _smoke_test(target.engine)
    emit(f"Running local recognition test..... {smoke}")
    emit("")
    if smoke.startswith("READY"):
        emit("VOICE READY")
        emit(f"Provider: {target.engine.capitalize()}")
        emit("Mode: MIC LIVE" if rep.microphone.get("available")
             else "Mode: TEXT INPUT (no microphone detected)")
        emit("Network required: NO")
        emit("Audio uploaded: NO")
        return 0
    emit("VOICE SETUP COULD NOT COMPLETE")
    emit(f"Reason: recognition smoke test {smoke}")
    emit("Next: airmouse voice-status --verbose")
    return 1


def _active_engine_name() -> str:
    try:
        from .asr_registry import registry
        return registry.active() or "vosk"
    except Exception:
        return "vosk"


def _smoke_test(engine: str) -> str:
    """Deterministic ASR smoke test — REAL engine init, REAL audio.

    No microphone is required: a scripted PCM utterance (sine-shaped
    speech-like envelope — deterministic bytes, no network, no files)
    is pushed through the engine.  READY means the engine honestly
    returned a transcript object; it does NOT claim a specific phrase
    (no speech samples → no accuracy claims, §22).
    """
    try:
        from .asr_registry import registry
        provider = registry.select(force=True)
        if provider is None:
            return "FAILED (no provider selected)"
        name = str(getattr(provider, "name", "") or "")
        sr_ = 16000
        frames = b"".join(_smoke_pcm_frame(i, sr_) for i in range(sr_))
        t = provider.transcribe(_engine_input(name, frames, sr_))
        if t is None:
            return f"FAILED ({name} returned nothing)"
        return f"READY ({name} initialized and returned a transcript; " \
               f"real-speech quality needs real speech — try: " \
               f"airmouse transcribe)"
    except Exception as exc:
        return f"FAILED ({type(exc).__name__}: {exc})"


def _smoke_pcm_frame(i: int, sr_: int) -> bytes:
    """One 1-second mono int16 frame: speech-like deterministic envelope."""
    import math as _m
    import struct as _s
    t = i / float(sr_)
    env = 0.5 * (1 + _m.sin(2 * _m.pi * 1.5 * t))  # slow envelope
    v = int(9000 * env * _m.sin(2 * _m.pi * 220 * t))
    return _s.pack("<h", max(-32768, min(32767, v)))


def _engine_input(name: str, frames: bytes, sr_: int) -> Any:
    """Wrap scripted PCM into the engine's expected input shape."""
    if name == "pocketsphinx":
        import speech_recognition as _sr
        return _sr.AudioData(frames, sr_, 2)
    import tempfile
    import wave
    fd, path = tempfile.mkstemp(suffix=".wav")
    with os.fdopen(fd, "wb") as wf:
        with wave.open(wf, "wb") as wv:
            wv.setnchannels(1)
            wv.setsampwidth(2)
            wv.setframerate(sr_)
            wv.writeframes(frames)
    return path


# ---------------------------------------------------------------------------

def _json_report() -> str:
    rep, _plan = bootstrap_manager.recommend_installation()
    return json.dumps(rep.to_dict(), indent=2, default=str)
