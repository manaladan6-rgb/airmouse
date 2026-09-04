"""
airmouse.setup_wizard — first-run setup + honest environment report
(v15.1 hardening).

`run_setup()` walks eleven fixed steps and NEVER raises on environment
problems — every probe is guarded and degrades into a step status:

    DONE             verified working here, now
    SKIPPED          optional / not applicable (with the exact fix)
    ACTION_REQUIRED  needs hardware/permission — plain-language next step
    FAILED           attempted and failed (with remediation)

Non-interactive mode (``interactive=False``) does everything that is
safe automatically and NEVER installs packages silently: missing core
dependencies become a SKIPPED step whose remediation carries the exact
pip command.  The ONLY subprocess this module may ever run is
``[sys.executable, "-m", "pip", "install",
"--disable-pip-version-check", <pypi-name>]`` with a 600 s timeout —
and only after explicit consent (``install_ok=True``, ``assume_yes``,
or an interactive Y answer).  There is no other network or download
code; the setup itself is local-only.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import importlib.util  # noqa: F401  (imports the util submodule explicitly —
#                       bare ``import importlib`` does NOT load importlib.util,
#                       which made find_spec probes silently fail)
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import IO, Callable, Dict, List, Optional, Tuple

from . import persistence
from .persistence import (STORE_NAMES, airmouse_home, atomic_write_json,
                          ensure_dirs, get_store)

__all__ = ["SetupStep", "SetupReport", "run_setup",
           "setup_complete", "mark_setup_complete", "CORE_DEPENDENCIES"]

#: (import name, PyPI name) for the REQUIRED core stack — duplicated
#: locally on purpose so the wizard has no dependency on any other
#: module that may be built in parallel.
CORE_DEPENDENCIES: Tuple[Tuple[str, str], ...] = (
    ("numpy", "numpy"),
    ("cv2", "opencv-python"),
    ("mediapipe", "mediapipe"),
    ("pynput", "pynput"),
)

#: optional ASR/voice extras — detected, reported, NEVER installed
#: without the same consent gate as core deps (and never required).
OPTIONAL_EXTRAS: Tuple[Tuple[str, str], ...] = (
    ("SpeechRecognition", "SpeechRecognition"),
    ("sounddevice", "sounddevice"),
    ("pyaudio", "pyaudio"),
    ("pytesseract", "pytesseract"),
    ("pyttsx3", "pyttsx3"),
)

_INSTALL_TIMEOUT = 600  # seconds — the one allowed subprocess

STEP_IDS = ("env", "deps", "storage", "config", "cameras", "microphones",
            "browsers", "asr", "permissions", "smoke", "marker")


# ---------------------------------------------------------------------------
# report dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SetupStep:
    """One setup check and its honest outcome."""
    id: str
    title: str
    status: str            # DONE | SKIPPED | ACTION_REQUIRED | FAILED
    detail: str = ""
    remediation: str = ""


@dataclass
class SetupReport:
    """All steps of a setup run."""
    steps: List[SetupStep] = field(default_factory=list)

    def remaining(self) -> List[SetupStep]:
        """Steps that still need the user (or hardware) attention."""
        return [s for s in self.steps
                if s.status in ("SKIPPED", "ACTION_REQUIRED", "FAILED")]

    def get(self, step_id: str) -> Optional[SetupStep]:
        for s in self.steps:
            if s.id == step_id:
                return s
        return None

    def format(self) -> str:
        lines = ["AirMouse setup report",
                 "=" * 60]
        for n, s in enumerate(self.steps, 1):
            lines.append(f"{n:>2}. [{s.status:<15}] {s.title} — {s.detail}"
                         .rstrip())
            if s.remediation:
                lines.append(f"      fix: {s.remediation}")
        lines.append("")
        lines.append("What remains to test (needs you + hardware):")
        lines.append("  * Camera: start AirMouse (run `airmouse`) and watch "
                     "the camera window — your hand should be tracked; "
                     "wave to confirm.")
        lines.append("  * Microphone: with AirMouse running, say \"click\" "
                     "— the HUD should react (voice features are optional).")
        lines.append("  * Guided check: run `airmouse --self-test` and "
                     "confirm the report shows no FAIL lines.")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# guarded probe helpers
# ---------------------------------------------------------------------------

def _import_optional(name: str):
    """Import a module or return None — never raises."""
    try:
        return importlib.import_module(name)
    except Exception:
        return None


def _module_present(name: str) -> bool:
    """True when the module exists on disk (no import side effects)."""
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def _missing_required_deps() -> List[str]:
    """Import names of REQUIRED core dependencies not present on disk."""
    return [import_name for import_name, _pypi in CORE_DEPENDENCIES
            if not _module_present(import_name)]


def _pip_install(pypi_name: str) -> None:
    """The ONE allowed subprocess: pip install of one PyPI package.

    Consent is checked by the caller.  No shell, no other commands.
    """
    subprocess.run(
        [sys.executable, "-m", "pip", "install",
         "--disable-pip-version-check", pypi_name],
        timeout=_INSTALL_TIMEOUT,
    )


def _pip_command(names: List[str]) -> str:
    """Copy-pasteable remediation command for the given import names."""
    pypi = [dict(CORE_DEPENDENCIES).get(n, n) for n in names]
    return (f'"{sys.executable}" -m pip install '
            f"--disable-pip-version-check {' '.join(pypi)}").strip()


def _detect_browsers() -> Dict[str, str]:
    """Lightweight Chrome/Edge/Chromium detection (paths only, no launch)."""
    found: Dict[str, str] = {}

    def _try(key: str, path: str) -> None:
        if key not in found and path and os.path.isfile(path):
            found[key] = path

    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)",
                                       r"C:\Program Files (x86)")
    local_appdata = os.environ.get("LocalAppData",
                                   os.path.join(os.path.expanduser("~"),
                                                "AppData", "Local"))
    for base in (program_files, program_files_x86, local_appdata):
        _try("chrome", os.path.join(base, "Google", "Chrome", "Application",
                                    "chrome.exe"))
        _try("edge", os.path.join(base, "Microsoft", "Edge", "Application",
                                  "msedge.exe"))
    for exe, key in (("chrome", "chrome"), ("google-chrome", "chrome"),
                     ("google-chrome-stable", "chrome"),
                     ("msedge", "edge"), ("chromium", "chromium"),
                     ("chromium-browser", "chromium")):
        path = shutil.which(exe)
        if path:
            _try(key, path)
    return found


def _utcnow_iso() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# individual steps (each returns exactly one SetupStep, never raises)
# ---------------------------------------------------------------------------

def _step_env() -> SetupStep:
    try:
        v = sys.version_info
        py = f"{v.major}.{v.minor}.{v.micro}"
        missing = _missing_required_deps()
        present = len(CORE_DEPENDENCIES) - len(missing)
        detail = (f"Python {py} on {platform.system()} "
                  f"{platform.machine()}; core deps {present}/"
                  f"{len(CORE_DEPENDENCIES)} present")
        if missing:
            detail += f" (missing: {', '.join(missing)})"
        return SetupStep("env", "Environment", "DONE", detail)
    except Exception as exc:
        return SetupStep("env", "Environment", "FAILED",
                         f"could not inspect environment: "
                         f"{type(exc).__name__}")


def _step_deps(interactive: bool, assume_yes: bool,
               install_ok: Optional[bool],
               input_fn: Callable[[str], str]) -> SetupStep:
    try:
        missing = _missing_required_deps()
        if not missing:
            return SetupStep("deps", "Core packages", "DONE",
                             f"all {len(CORE_DEPENDENCIES)} core "
                             f"dependencies present")
        consent = assume_yes if install_ok is None else bool(install_ok)
        if interactive and not assume_yes:
            # interactive run: always confirm out loud, even when the
            # caller pre-approved installs programmatically
            try:
                answer = input_fn("Install missing required packages? [Y/N] ")
            except Exception:
                answer = ""
            consent = answer.strip().lower() in ("y", "yes")
        if not consent:
            return SetupStep(
                "deps", "Core packages", "SKIPPED",
                f"missing required packages: {', '.join(missing)} "
                f"(not installed without your consent)",
                remediation=_pip_command(missing))
        installed: List[str] = []
        failures: List[str] = []
        for import_name in missing:
            pypi = dict(CORE_DEPENDENCIES).get(import_name, import_name)
            try:
                _pip_install(pypi)
                installed.append(import_name)
            except Exception as exc:
                failures.append(f"{import_name} "
                                f"({type(exc).__name__}: {exc})")
        importlib.invalidate_caches()
        still = _missing_required_deps()
        if still:
            return SetupStep(
                "deps", "Core packages", "FAILED",
                f"installed: {', '.join(installed) or 'none'}; "
                f"still missing after install: {', '.join(still)}"
                + (f"; install errors: {'; '.join(failures)}"
                   if failures else ""),
                remediation=_pip_command(still))
        return SetupStep("deps", "Core packages", "DONE",
                         f"installed: {', '.join(installed)}")
    except Exception as exc:
        return SetupStep("deps", "Core packages", "FAILED",
                         f"dependency check failed: {type(exc).__name__}: "
                         f"{exc}")


def _step_storage() -> SetupStep:
    try:
        home = ensure_dirs()
        created = []
        for name in STORE_NAMES:
            store = get_store(name)
            if not store.exists():
                store.save({})
                created.append(name)
        detail = f"storage ready at {home}"
        if created:
            detail += f"; initialised stores: {', '.join(created)}"
        return SetupStep("storage", "Local storage", "DONE", detail)
    except Exception as exc:
        return SetupStep("storage", "Local storage", "FAILED",
                         f"could not prepare storage: "
                         f"{type(exc).__name__}: {exc}",
                         remediation="check write permissions on your "
                                     "home directory")


def _step_config() -> SetupStep:
    try:
        with persistence.config_path_scope() as cfg_path:
            if os.path.exists(cfg_path):
                return SetupStep("config", "Configuration", "DONE",
                                 f"config found at {cfg_path} "
                                 f"(not modified)")
            from .config import Config
            Config().save_defaults()
            if os.path.exists(cfg_path):
                return SetupStep("config", "Configuration", "DONE",
                                 f"created default config at {cfg_path}")
            return SetupStep(
                "config", "Configuration", "ACTION_REQUIRED",
                "no config file and the default template could not be "
                "written — AirMouse runs on built-in defaults",
                remediation="check write permissions on the AirMouse "
                            "home directory")
    except Exception as exc:
        return SetupStep("config", "Configuration", "FAILED",
                         f"config check failed: {type(exc).__name__}: {exc}")


def _step_cameras() -> SetupStep:
    cv2 = _import_optional("cv2")
    if cv2 is None:
        return SetupStep(
            "cameras", "Camera", "ACTION_REQUIRED",
            "OpenCV is not installed, so the camera cannot be probed",
            remediation=_pip_command(["cv2"]))
    try:
        cap = cv2.VideoCapture(0)
        opened = bool(cap.isOpened())
        try:
            cap.release()
        except Exception:
            pass
    except Exception as exc:
        return SetupStep("cameras", "Camera", "ACTION_REQUIRED",
                         f"camera probe failed: {type(exc).__name__}: {exc}")
    if opened:
        return SetupStep("cameras", "Camera", "DONE",
                         "camera detected at index 0")
    return SetupStep(
        "cameras", "Camera", "ACTION_REQUIRED",
        "no camera detected at index 0 (headless machine, or the webcam "
        "is unplugged/disabled?)",
        remediation="connect a webcam (or enable camera access in your "
                    "OS privacy settings), then run `airmouse` and watch "
                    "the camera window")


def _step_microphones() -> SetupStep:
    sd = _import_optional("sounddevice")
    if sd is None:
        return SetupStep(
            "microphones", "Microphone", "SKIPPED",
            "optional microphone support (sounddevice) not installed — "
            "voice features stay optional; everything else works",
            remediation=_pip_command(["sounddevice"]))
    try:
        devices = sd.query_devices()
        inputs = [d for d in devices
                  if isinstance(d, dict) and d.get("max_input_channels", 0) > 0]
    except Exception as exc:
        return SetupStep("microphones", "Microphone", "SKIPPED",
                         f"audio subsystem not usable here "
                         f"({type(exc).__name__}) — optional")
    if inputs:
        return SetupStep("microphones", "Microphone", "DONE",
                         f"{len(inputs)} input device(s) found")
    return SetupStep(
        "microphones", "Microphone", "ACTION_REQUIRED",
        "no microphone detected (headless machine, or mic disabled?)",
        remediation="connect/enable a microphone in your OS settings, "
                    'then say "click" while AirMouse is running')


def _step_browsers() -> SetupStep:
    try:
        found = _detect_browsers()
        if found:
            return SetupStep("browsers", "Browser control", "DONE",
                             f"system browser(s): {', '.join(sorted(found))}"
                             "; simulated bridge also always available")
        return SetupStep("browsers", "Browser control", "DONE",
                         "no system browser found — the simulated browser "
                         "bridge is always available, so browser commands "
                         "still work offline")
    except Exception as exc:
        return SetupStep("browsers", "Browser control", "FAILED",
                         f"browser detection failed: {type(exc).__name__}")


def _step_asr() -> SetupStep:
    try:
        present = [name for name, _pypi in OPTIONAL_EXTRAS
                   if _module_present(name)]
        if present:
            return SetupStep("asr", "Voice extras (optional)", "DONE",
                             f"optional extras present: "
                             f"{', '.join(present)}")
        return SetupStep(
            "asr", "Voice extras (optional)", "SKIPPED",
            "optional ASR extras not installed — never forced; command "
            "voice control works without them via the offline engine",
            remediation="optional: " + _pip_command(
                [name for name, _pypi in OPTIONAL_EXTRAS]))
    except Exception as exc:
        return SetupStep("asr", "Voice extras (optional)", "FAILED",
                         f"extras check failed: {type(exc).__name__}")


def _step_permissions() -> SetupStep:
    try:
        # the real import probe (pynput needs a display server on Linux;
        # it raises on headless machines — that is honest, not an error)
        importlib.import_module("pynput")
    except Exception as exc:
        return SetupStep(
            "permissions", "Keyboard/mouse control", "ACTION_REQUIRED",
            f"input automation is not available here "
            f"({type(exc).__name__}) — expected on headless machines; "
            "this is NOT an error",
            remediation="on Windows it works out of the box — just run "
                        "`airmouse`; on Linux a display server is "
                        "required; on macOS grant Accessibility "
                        "permission")
    return SetupStep("permissions", "Keyboard/mouse control", "DONE",
                     "keyboard/mouse automation available")


def _step_smoke() -> SetupStep:
    try:
        from .selftest import run_self_test
        results = run_self_test(intelligence=False)
        failures = [r.component for r in results if not r.ok]
        if failures:
            return SetupStep(
                "smoke", "Smoke test", "FAILED",
                f"failing self-checks: {', '.join(failures)}",
                remediation="run `airmouse --self-test` for the full "
                            "report")
        return SetupStep("smoke", "Smoke test", "DONE",
                         f"{len(results)} self-checks passed "
                         f"(hardware checks honestly deferred)")
    except Exception as exc:
        return SetupStep("smoke", "Smoke test", "FAILED",
                         f"self-test could not run: "
                         f"{type(exc).__name__}: {exc}",
                         remediation="run `airmouse --self-test` and "
                                     "share the output if you need help")


def _step_marker() -> SetupStep:
    try:
        path = mark_setup_complete()
        return SetupStep("marker", "Finish", "DONE",
                         f"setup marker written: {path}")
    except Exception as exc:
        return SetupStep("marker", "Finish", "FAILED",
                         f"could not write the setup marker: "
                         f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# marker helpers
# ---------------------------------------------------------------------------

def setup_complete() -> bool:
    """True when a previous setup run left its marker in the home dir."""
    return os.path.exists(os.path.join(airmouse_home(), ".setup_complete"))


def mark_setup_complete() -> str:
    """Write the ``.setup_complete`` marker (atomically); returns path."""
    home = ensure_dirs()
    version = "unknown"
    try:
        from . import __version__ as _version
        version = _version
    except Exception:
        pass
    path = os.path.join(home, ".setup_complete")
    atomic_write_json(path, {"marked_at": _utcnow_iso(),
                             "version": version})
    return path


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------

def run_setup(interactive: bool = True, assume_yes: bool = False,
              install_ok: Optional[bool] = None,
              out: IO[str] = sys.stdout,
              input_fn: Callable[[str], str] = input) -> SetupReport:
    """Run all setup steps; never raises on environment problems.

    Consent rule: packages are installed ONLY when ``install_ok=True``
    (or ``assume_yes=True``, or an interactive Y answer).  Without
    consent the step is SKIPPED with the exact pip command as
    remediation — never a silent install.
    """
    steps: List[SetupStep] = []

    def say(msg: str) -> None:
        try:
            out.write(msg + "\n")
            out.flush()
        except Exception:
            pass

    def finish(step: SetupStep, progress: str) -> SetupStep:
        steps.append(step)
        say(f"  {progress}... {step.status}"
            + (f" ({step.detail})" if step.detail else ""))
        return step

    say("AirMouse setup — checking your system (nothing is installed "
        "without your consent)")
    finish(_step_env(), "Checking Python and OS")
    finish(_step_deps(interactive, assume_yes, install_ok, input_fn),
           "Checking core packages")
    finish(_step_storage(), "Preparing local storage")
    finish(_step_config(), "Checking configuration")
    finish(_step_cameras(), "Looking for a camera")
    finish(_step_microphones(), "Looking for a microphone")
    finish(_step_browsers(), "Looking for a browser")
    finish(_step_asr(), "Checking optional voice extras")
    finish(_step_permissions(), "Checking keyboard/mouse access")
    finish(_step_smoke(), "Running the smoke test")
    finish(_step_marker(), "Finishing")
    say("Done. See the report below for anything that still needs you.")
    say("")
    return SetupReport(steps=steps)
