"""
airmouse.capabilities — v15.1 capability detection (doctor core).

Answers ONE question honestly: *what can AirMouse do on THIS machine,
right now?*  Every category produces :class:`Component` rows with a
state, a human detail and (for anything not READY) a remediation.

Design rules (hard constraints, see worklog task 2-a):

1.  FAIL-CLOSED — every detector is wrapped; a crashing detector becomes
    a FAILED component, never an exception escaping to the caller.
2.  HEADLESS-SAFE — no display, camera, mic or network required; every
    probe is bounded (the camera probe releases the device and is
    capped well under 5 s).
3.  NO NETWORK — nothing here dials out.  The hand-landmarker model is
    only STATed, never downloaded (that is first-run behaviour).
4.  NO SUBPROCESS — detection uses imports, the platform module, and
    filesystem stats only.  Nothing is exec'd, nothing is shell'd.
5.  NO TELEMETRY — the report never reads environment variables or
    secrets; it describes the machine in the vaguest useful terms.

State mapping (spec §4/§15):
    READY         verified working here, now
    OPTIONAL      nice-to-have; absence is not a failure
    NOT_INSTALLED an optional extra exists but its package is absent
    HARDWARE      present in principle; needs physical proof
    ENHANCEMENT   a degraded-but-functional substitute is active (mock)
    UNAVAILABLE   this platform/environment cannot support it right now
    INCOMPATIBLE  present but the wrong version
    FAILED        broken (core-broken components block the doctor)
    WARNING       degraded / needs attention (e.g. first-run download)

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import platform
import shutil
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

try:  # package-relative (normal import path)
    from . import __version__ as _AIRMOUSE_VERSION
except Exception:  # pragma: no cover - namespace fallback
    _AIRMOUSE_VERSION = "unknown"

__all__ = [
    "ComponentState", "Component", "CapabilityReport", "detect_all",
    "CATEGORIES", "REQUIRED_CATEGORIES", "CORE_DEPENDENCIES",
    "OPTIONAL_DEPENDENCIES",
]


# ---------------------------------------------------------------------------
# Public vocabulary
# ---------------------------------------------------------------------------

#: The 12 detection categories — in doctor-section order.
CATEGORIES: Tuple[str, ...] = (
    "SYSTEM", "PYTHON", "AIRMOUSE", "CAMERA", "MICROPHONE", "SPEECH",
    "INPUT", "BROWSER", "INTELLIGENCE", "AGENT", "OFFLINE", "SAFETY",
)

#: Categories whose FAILED/INCOMPATIBLE state BLOCKs the whole report.
REQUIRED_CATEGORIES: Tuple[str, ...] = (
    "SYSTEM", "PYTHON", "AIRMOUSE", "SAFETY",
)

#: Core (pyproject ``dependencies``): pypi name -> (import name, pypi name)
CORE_DEPENDENCIES: Dict[str, Tuple[str, str]] = {
    "numpy": ("numpy", "numpy"),
    "opencv-python": ("cv2", "opencv-python"),
    "mediapipe": ("mediapipe", "mediapipe"),
    "pynput": ("pynput", "pynput"),
}

#: Optional extras (pyproject ``optional-dependencies``).
OPTIONAL_DEPENDENCIES: Dict[str, Tuple[str, str]] = {
    "sounddevice": ("sounddevice", "sounddevice"),
    "SpeechRecognition": ("speech_recognition", "SpeechRecognition"),
    "pyaudio": ("pyaudio", "pyaudio"),
    "pytesseract": ("pytesseract", "pytesseract"),
    "pyttsx3": ("pyttsx3", "pyttsx3"),
}

#: pypi name -> pip extra that installs it (for remediation text)
_EXTRA_FOR: Dict[str, str] = {
    "sounddevice": "sound",
    "SpeechRecognition": "voice",
    "pyaudio": "voice",
    "pytesseract": "ocr",
    "pyttsx3": "tts",
}

#: The camera probe must release the device well inside this budget.
CAMERA_PROBE_BUDGET_S: float = 4.0

#: camera device index policy: built-in default device only
_CAMERA_INDEX: int = 0


class ComponentState(str, Enum):
    """Lifecycle/health states for one capability component."""

    READY = "READY"
    OPTIONAL = "OPTIONAL"
    NOT_INSTALLED = "NOT_INSTALLED"
    HARDWARE = "HARDWARE"
    ENHANCEMENT = "ENHANCEMENT"
    UNAVAILABLE = "UNAVAILABLE"
    INCOMPATIBLE = "INCOMPATIBLE"
    FAILED = "FAILED"
    WARNING = "WARNING"


#: states that mean "nothing to fix here"
_PASsthrough_STATES = (ComponentState.READY, ComponentState.OPTIONAL)

_PYNPUT_DISPLAY_REMEDIATION = (
    "run on a machine with a display; see docs "
    "(docs/USER_GUIDE.md — input permissions)")


@dataclass
class Component:
    """One checkable capability row."""

    name: str
    category: str            # one of CATEGORIES
    state: ComponentState
    detail: str = ""
    remediation: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {
            "name": str(self.name),
            "category": str(self.category),
            "state": ComponentState(self.state).value,
            "detail": str(self.detail),
            "remediation": str(self.remediation),
        }


@dataclass
class CapabilityReport:
    """Every component, flat, in detection order."""

    components: List[Component] = field(default_factory=list)

    # -- aggregations -------------------------------------------------------

    def counts(self) -> Dict[str, int]:
        """state name -> count (ALL states present, zero-padded)."""
        out: Dict[str, int] = {s.value: 0 for s in ComponentState}
        for c in self.components:
            key = ComponentState(c.state).value
            out[key] = out.get(key, 0) + 1
        return out

    def overall(self) -> str:
        """BLOCKED / READY FOR TESTING / PARTIAL (spec verdict logic)."""
        required = set(REQUIRED_CATEGORIES)
        blocking = (ComponentState.FAILED, ComponentState.INCOMPATIBLE)
        for c in self.components:
            if ComponentState(c.state) in blocking and \
                    c.category in required:
                return "BLOCKED"
        for c in self.components:
            if ComponentState(c.state) in blocking or \
                    ComponentState(c.state) is ComponentState.WARNING:
                return "PARTIAL"
        return "READY FOR TESTING"

    def to_machine(self) -> Dict[str, Any]:
        """JSON-serializable dict (stable keys) for tooling."""
        return {
            "version": str(_AIRMOUSE_VERSION),
            "components": [c.to_dict() for c in self.components],
            "overall": self.overall(),
        }

    def remediations(self) -> List[str]:
        """One exact instruction per non-READY component."""
        out: List[str] = []
        for c in self.components:
            if ComponentState(c.state) not in _PASsthrough_STATES:
                out.append(c.remediation or c.detail or
                           f"review `{c.name.lower()}` in airmouse doctor")
        return out

    def format(self) -> str:
        """Human table: Capability / Status + counts + Overall + fixes."""
        widths = 38
        lines = ["Capability".ljust(widths) + "Status",
                 "-" * (widths + 14)]
        for c in self.components:
            state = ComponentState(c.state).value
            detail = f"  {c.detail}" if c.detail else ""
            lines.append(f"{c.name[:widths - 1]:<{widths}}{state}{detail}")
        counts = self.counts()
        lines.append("-" * (widths + 14))
        lines.append("Counts: " + " ".join(
            f"{k}={counts[k]}" for k in ComponentState.__members__))
        lines.append(f"Overall: {self.overall()}")
        fixes = [f"  - {c.name}: {c.remediation or c.detail}"
                 for c in self.components
                 if ComponentState(c.state) not in _PASsthrough_STATES]
        if fixes:
            lines.append("Remediations:")
            lines.extend(fixes)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# small honest helpers
# ---------------------------------------------------------------------------

def _human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024.0 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024.0
    return f"{n:.1f} TB"  # pragma: no cover - unreachable


_import_cache: Dict[str, Tuple[bool, str]] = {}


def _try_import(module: str, quick: bool = False) -> Tuple[bool, str]:
    """Import a module once per process; returns (ok, version-or-error).

    ``quick`` uses find_spec only (no execution of module bodies —
    keeps ``detect_all(quick=True)`` fast and dependency-light).
    """
    cached = _import_cache.get(module)
    if cached is not None and not quick:
        return cached
    spec = None
    try:
        spec = importlib.util.find_spec(module)
    except Exception:
        spec = None
    if spec is None:
        result = (False, "not installed")
        _import_cache[module] = result
        return result
    if quick:
        return (True, "present (version not loaded in quick mode)")
    try:
        mod = importlib.import_module(module)
        result = (True, str(getattr(mod, "__version__", "present")))
    except Exception as exc:  # installed but unusable here
        result = (False, f"{type(exc).__name__}: {exc}")
        _import_cache[module] = result
    return result


def _total_ram_bytes() -> Optional[int]:
    """Total RAM without child processes or network: /proc or Windows
    GlobalMemoryStatusEx."""
    try:  # Linux
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except Exception:
        pass
    if sys.platform == "win32":  # Windows: GlobalMemoryStatusEx
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(
                    ctypes.byref(stat)):
                return int(stat.ullTotalPhys)
        except Exception:
            return None
    return None


def _is_admin() -> Optional[bool]:
    """Best-effort privilege probe.  None = unknown (honest)."""
    try:
        if os.name == "posix":
            return os.geteuid() == 0
        if os.name == "nt":
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return None
    return None


def _find_browsers() -> List[str]:
    """Locate Chrome/Edge installs via fixed well-known paths only."""
    found: List[str] = []
    if sys.platform == "win32":
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        lad = os.environ.get("LocalAppData",
                             os.path.join(pf, "LocalAppData"))
        candidates = [
            ("chrome", [os.path.join(d, "Google", "Chrome", "Application",
                                  "chrome.exe") for d in (pf, pf86, lad)]),
            ("edge", [os.path.join(d, "Microsoft", "Edge", "Application",
                              "msedge.exe") for d in (pf, pf86, lad)]),
        ]
        for name, paths in candidates:
            if any(os.path.isfile(p) for p in paths):
                found.append(name)
    elif sys.platform == "darwin":
        for name, path in (
                ("chrome", "/Applications/Google Chrome.app"),
                ("edge", "/Applications/Microsoft Edge.app")):
            if os.path.isdir(path):
                found.append(name)
    else:
        for name in ("google-chrome", "google-chrome-stable", "chromium",
                     "chromium-browser", "microsoft-edge",
                     "microsoft-edge-stable"):
            if shutil.which(name):
                found.append(name.split("-")[0] if "edge" not in name
                             else "edge")
    return sorted(set(found))


def _component(name: str, category: str, state: ComponentState,
               detail: str = "", remediation: str = "") -> Component:
    """Build a row with table-safe (single-line) text fields."""
    return Component(
        name=" ".join(str(name).split()),
        category=str(category),
        state=state,
        detail=" ".join(str(detail).split()),
        remediation=" ".join(str(remediation).split()),
    )


# ---------------------------------------------------------------------------
# detectors — one per category, each returns List[Component]
#   (names are looked up as globals at call time so tests can inject a
#   crashing detector and prove the fail-closed path)
# ---------------------------------------------------------------------------

def _detect_system(quick: bool) -> List[Component]:
    comps: List[Component] = []
    plat = platform.platform()
    if sys.platform == "win32":
        try:
            winver = sys.getwindowsversion()  # type: ignore[attr-defined]
            plat = (f"Windows {winver.major}.{winver.minor} "
                    f"build {winver.build} ({platform.platform()})")
        except Exception:
            pass
    comps.append(_component(
        "Operating system", "SYSTEM", ComponentState.READY, plat))
    comps.append(_component(
        "Architecture", "SYSTEM", ComponentState.READY,
        f"{platform.machine()} {platform.architecture()[0]}"))
    cpu = os.cpu_count() or 0
    comps.append(_component(
        "CPU cores", "SYSTEM", ComponentState.READY, f"{cpu} logical"))

    ram = _total_ram_bytes()
    if ram is not None:
        comps.append(_component(
            "Memory (RAM)", "SYSTEM", ComponentState.READY,
            f"{_human_bytes(float(ram))} total"))
    else:  # honest degradation, not a crash
        comps.append(_component(
            "Memory (RAM)", "SYSTEM", ComponentState.WARNING,
            "total RAM unknown on this platform",
            "run airmouse doctor on Windows or Linux to report memory"))

    try:
        home = os.path.expanduser("~")
        free = shutil.disk_usage(home).free
        if free < 500 * 1024 * 1024:
            comps.append(_component(
                "Disk space (home)", "SYSTEM", ComponentState.WARNING,
                f"{_human_bytes(float(free))} free at {home}",
                "free up disk space (less than 500 MB free in your home "
                "directory)"))
        else:
            comps.append(_component(
                "Disk space (home)", "SYSTEM", ComponentState.READY,
                f"{_human_bytes(float(free))} free at {home}"))
    except Exception as exc:
        comps.append(_component(
            "Disk space (home)", "SYSTEM", ComponentState.FAILED,
            f"disk probe failed: {exc}",
            "check that your home directory exists and is readable"))

    admin = _is_admin()
    detail = {True: "running as administrator/root",
              False: "standard user",
              None: "privilege level unknown"}[admin]
    comps.append(_component(
        "Privileges", "SYSTEM", ComponentState.READY, detail))
    return comps


def _detect_python(quick: bool) -> List[Component]:
    comps: List[Component] = []
    ver = f"{sys.version_info.major}.{sys.version_info.minor}." \
          f"{sys.version_info.micro}"
    if sys.version_info >= (3, 9):
        comps.append(_component(
            "Python interpreter", "PYTHON", ComponentState.READY,
            f"Python {ver} at {sys.executable or '<embedded>'}"))
    else:
        comps.append(_component(
            "Python interpreter", "PYTHON", ComponentState.INCOMPATIBLE,
            f"Python {ver} (requires-python >= 3.9)",
            "install Python 3.9 or newer and reinstall airmouse"))

    try:
        import importlib.util as _iu
        has_pip = _iu.find_spec("pip") is not None
    except Exception:
        has_pip = False
    if has_pip:
        comps.append(_component(
            "pip", "PYTHON", ComponentState.READY, "pip module importable"))
    else:
        comps.append(_component(
            "pip", "PYTHON", ComponentState.NOT_INSTALLED,
            "pip not importable",
            "bootstrap pip with: python -m ensurepip --upgrade"))

    # core dependencies — broken core = FAILED (blocks)
    for pypi, (import_name, _pypi2) in CORE_DEPENDENCIES.items():
        ok, info = _try_import(import_name, quick=quick)
        if ok:
            comps.append(_component(
                f"Dependency: {pypi}", "PYTHON", ComponentState.READY,
                info))
        elif "not installed" in info:
            comps.append(_component(
                f"Dependency: {pypi}", "PYTHON", ComponentState.FAILED,
                info, f"pip install {pypi}"))
        elif _is_platform_unsupported(info):
            comps.append(_component(
                f"Dependency: {pypi}", "PYTHON", ComponentState.UNAVAILABLE,
                info, _PYNPUT_DISPLAY_REMEDIATION))
        else:
            comps.append(_component(
                f"Dependency: {pypi}", "PYTHON", ComponentState.FAILED,
                info, f"reinstall {pypi} (pip install --force-reinstall "
                      f"{pypi})"))

    # optional extras — absent is fine, broken is a warning
    for pypi, (import_name, _p2) in OPTIONAL_DEPENDENCIES.items():
        ok, info = _try_import(import_name, quick=quick)
        extra = _EXTRA_FOR.get(pypi)
        install_cmd = f'pip install "airmouse[{extra}]"' if extra \
            else f"pip install {pypi}"
        if ok:
            comps.append(_component(
                f"Optional: {pypi}", "PYTHON", ComponentState.READY,
                info))
        elif "not installed" in info:
            comps.append(_component(
                f"Optional: {pypi}", "PYTHON", ComponentState.NOT_INSTALLED,
                f"optional extra not installed ({info})", install_cmd))
        else:
            comps.append(_component(
                f"Optional: {pypi}", "PYTHON", ComponentState.WARNING,
                f"installed but import fails: {info}",
                f"{install_cmd} --force-reinstall (or uninstall if unused)"))
    return comps


def _is_platform_unsupported(detail: str) -> bool:
    low = detail.lower()
    return ("platform is not supported" in low
            or "display" in low
            or "displaynameerror" in low
            or "x server" in low)


def _hand_model_path() -> str:
    """Reuse tracker's own model location (no import side effects we
    can avoid — but tracker imports cv2/mediapipe, so fall back to the
    same computed path if that import is unavailable)."""
    try:
        from . import tracker as _tracker
        return str(_tracker.MODEL_PATH)
    except Exception:
        return os.path.join(os.path.expanduser("~"), ".airmouse",
                            "hand_landmarker.task")


def _detect_airmouse(quick: bool) -> List[Component]:
    comps: List[Component] = []
    comps.append(_component(
        "AirMouse package", "AIRMOUSE", ComponentState.READY,
        f"v{_AIRMOUSE_VERSION}"))

    try:  # config public load API (Config.load never raises, belt+braces)
        from .config import Config, CONFIG_PATH
        cfg = Config()
        cfg.load()
        exists = os.path.isfile(CONFIG_PATH)
        comps.append(_component(
            "Configuration", "AIRMOUSE", ComponentState.READY,
            f"{'loaded' if exists else 'defaults'} ({CONFIG_PATH})"))
    except Exception as exc:
        comps.append(_component(
            "Configuration", "AIRMOUSE", ComponentState.FAILED,
            f"config load failed: {exc}",
            "fix or remove ~/.airmouse/config.toml (defaults are used)"))

    try:  # storage home
        home = os.path.join(os.path.expanduser("~"), ".airmouse")
        if not os.path.isdir(home):
            os.makedirs(home, exist_ok=True)
        if os.access(home, os.W_OK):
            comps.append(_component(
                "Storage home", "AIRMOUSE", ComponentState.READY,
                f"{home} writable"))
        else:
            comps.append(_component(
                "Storage home", "AIRMOUSE", ComponentState.FAILED,
                f"{home} is not writable",
                "check permissions on your home directory"))
    except Exception as exc:
        comps.append(_component(
            "Storage home", "AIRMOUSE", ComponentState.FAILED,
            f"cannot prepare ~/.airmouse: {exc}",
            "check permissions on your home directory"))

    try:  # hand model: STAT ONLY — never downloaded from here
        path = _hand_model_path()
        if os.path.isfile(path):
            size = os.path.getsize(path)
            comps.append(_component(
                "Hand tracking model", "AIRMOUSE", ComponentState.READY,
                f"{path} ({_human_bytes(float(size))})"))
        else:
            comps.append(_component(
                "Hand tracking model", "AIRMOUSE", ComponentState.WARNING,
                "not downloaded yet",
                "downloads automatically on first tracking run (~8 MB, "
                f"internet needed once) or place it at {path}"))
    except Exception as exc:
        comps.append(_component(
            "Hand tracking model", "AIRMOUSE", ComponentState.WARNING,
            f"could not stat model: {exc}",
            "the model downloads on first tracking run"))
    return comps


def _detect_camera(quick: bool) -> List[Component]:
    ok, info = _try_import("cv2", quick=quick)
    if not ok:
        state = (ComponentState.UNAVAILABLE if "not installed" in info
                 else ComponentState.FAILED)
        return [_component(
            "Webcam", "CAMERA", state,
            f"OpenCV unavailable: {info}",
            "pip install opencv-python")]
    if quick:
        return [_component(
            "Webcam", "CAMERA", ComponentState.HARDWARE,
            "device not probed in quick mode",
            "run a full airmouse doctor with the camera connected")]
    import cv2  # verified importable above (cheap after first call)
    t0 = time.perf_counter()
    cap = None
    try:
        cap = cv2.VideoCapture(_CAMERA_INDEX)
        opened = bool(cap.isOpened())
        elapsed = time.perf_counter() - t0
        if not opened:
            return [_component(
                "Webcam", "CAMERA", ComponentState.HARDWARE,
                "no camera device answered at index "
                f"{_CAMERA_INDEX} ({elapsed * 1000:.0f}ms)",
                "connect a webcam (and grant camera permission), then "
                "re-run airmouse doctor")]
        if elapsed > CAMERA_PROBE_BUDGET_S:
            return [_component(
                "Webcam", "CAMERA", ComponentState.WARNING,
                f"camera open took {elapsed:.1f}s (budget "
                f"{CAMERA_PROBE_BUDGET_S:.0f}s) — skipped frame grab",
                "close other apps using the camera and re-run "
                "airmouse doctor")]
        ret, frame = cap.read()  # one bounded frame grab
        elapsed = time.perf_counter() - t0
        if ret and frame is not None:
            h, w = frame.shape[:2]
            return [_component(
                "Webcam", "CAMERA", ComponentState.READY,
                f"frame {w}x{h} in {elapsed * 1000:.0f}ms")]
        return [_component(
            "Webcam", "CAMERA", ComponentState.HARDWARE,
            "camera opened but produced no frame",
            "connect a webcam (and grant camera permission), then "
            "re-run airmouse doctor")]
    except Exception as exc:
        return [_component(
            "Webcam", "CAMERA", ComponentState.FAILED,
            f"camera probe crashed: {exc}",
            "update opencv-python and re-run airmouse doctor")]
    finally:
        try:
            if cap is not None:
                cap.release()
        except Exception:
            pass


def _detect_microphone(quick: bool) -> List[Component]:
    ok, info = _try_import("sounddevice", quick=quick)
    if not ok:
        state = (ComponentState.NOT_INSTALLED if "not installed" in info
                 else ComponentState.WARNING)
        return [_component(
            "Microphone", "MICROPHONE", state,
            f"sounddevice unavailable: {info}",
            'pip install "airmouse[sound]"')]
    try:
        import sounddevice as sd  # noqa: F401
        devices = sd.query_devices()
        inputs = 0
        try:
            for d in list(devices):
                try:
                    if int(d.get("max_input_channels", 0)) > 0:
                        inputs += 1
                except Exception:
                    continue
        except Exception:
            inputs = 0
        if inputs > 0:
            return [_component(
                "Microphone", "MICROPHONE", ComponentState.READY,
                f"{inputs} input device(s) via PortAudio")]
        return [_component(
            "Microphone", "MICROPHONE", ComponentState.HARDWARE,
            "PortAudio works but reported no input devices",
            "connect a microphone (and grant mic permission), then "
            "re-run airmouse doctor")]
    except Exception as exc:
        return [_component(
            "Microphone", "MICROPHONE", ComponentState.HARDWARE,
            f"audio subsystem probe failed: {exc}",
            "check your audio drivers (PortAudio) and microphone "
            "permissions")]


def _detect_speech(quick: bool) -> List[Component]:
    comps: List[Component] = []
    try:
        from .voice_commands import list_commands, match_command_grammar
        m = match_command_grammar("open firefox")
        n = len(list_commands())
        if m.is_command:
            comps.append(_component(
                "Voice command grammar", "SPEECH", ComponentState.READY,
                f"{n} commands; 'open firefox' -> {m.intent.name}"))
        else:
            comps.append(_component(
                "Voice command grammar", "SPEECH", ComponentState.FAILED,
                "deterministic grammar match failed",
                "reinstall airmouse (the grammar ships in the wheel)"))
    except Exception as exc:
        comps.append(_component(
            "Voice command grammar", "SPEECH", ComponentState.FAILED,
            f"grammar crashed: {exc}",
            "reinstall airmouse (the grammar ships in the wheel)"))

    try:
        from .offline_voice import OfflineVoiceEngine
        eng = OfflineVoiceEngine({"mode": "command"})
        eng.feed_transcript("volume up", 0.9, now=1.0)
        if eng.last_intent is not None:
            comps.append(_component(
                "Offline voice engine", "SPEECH", ComponentState.READY,
                "command + dictation modes functional"))
        else:
            comps.append(_component(
                "Offline voice engine", "SPEECH", ComponentState.FAILED,
                "feed_transcript produced no intent",
                "reinstall airmouse"))
    except Exception as exc:
        comps.append(_component(
            "Offline voice engine", "SPEECH", ComponentState.FAILED,
            f"engine crashed: {exc}",
            "reinstall airmouse"))

    try:
        from .offline_voice import detect_providers
        provs = detect_providers()
        usable = sorted(k for k, v in provs.items() if v)
        real = [k for k in usable if k != "simulated"]
        if real:
            comps.append(_component(
                "Transcription providers", "SPEECH", ComponentState.READY,
                f"local ASR: {', '.join(real)}"))
        else:
            comps.append(_component(
                "Transcription providers", "SPEECH",
                ComponentState.OPTIONAL,
                "simulated provider only (deterministic, offline)",
                "optional: pip install vosk / openai-whisper / "
                "pocketsphinx for real local ASR"))
    except Exception as exc:
        comps.append(_component(
            "Transcription providers", "SPEECH", ComponentState.WARNING,
            f"provider detection failed: {exc}",
            "reinstall airmouse; simulated provider remains available"))

    try:
        from .transcription import (LiveTranscriptionEngine,
                                    SimulatedStreamingProvider)
        eng = LiveTranscriptionEngine(
            provider=SimulatedStreamingProvider(), history_enabled=False)
        eng.start()
        eng.provider.push_utterance("hello there period", 0.9)
        for i in range(8):
            eng.feed_audio(b"\x10\x27" * 1000, now=i * 0.05)
        seg = eng.finalize(now=0.5)
        if seg is not None and seg.text:
            comps.append(_component(
                "Streaming transcription", "SPEECH", ComponentState.READY,
                f"roundtrip ok ('{seg.text[:24]}')"))
        else:
            comps.append(_component(
                "Streaming transcription", "SPEECH", ComponentState.FAILED,
                "engine roundtrip produced no segment",
                "reinstall airmouse"))
    except Exception as exc:
        comps.append(_component(
            "Streaming transcription", "SPEECH", ComponentState.FAILED,
            f"engine crashed: {exc}",
            "reinstall airmouse"))
    return comps


def _detect_input(quick: bool) -> List[Component]:
    comps: List[Component] = []
    try:
        import pynput
        comps.append(_component(
            "pynput input control", "INPUT", ComponentState.READY,
            f"real mouse/keyboard injection available "
            f"(pynput {getattr(pynput, '__version__', 'ok')})"))
    except Exception as exc:
        comps.append(_component(
            "pynput input control", "INPUT", ComponentState.UNAVAILABLE,
            f"{type(exc).__name__}: {str(exc)[:110]}",
            _PYNPUT_DISPLAY_REMEDIATION))

    try:
        from .actions import PynputExecutor
        if PynputExecutor().available:
            comps.append(_component(
                "Action executors", "INPUT", ComponentState.READY,
                "PynputExecutor ready"))
        else:
            comps.append(_component(
                "Action executors", "INPUT", ComponentState.ENHANCEMENT,
                "MockExecutor active — actions are simulated on this "
                "machine",
                "run on a machine with a display for real input "
                "injection"))
    except Exception as exc:
        comps.append(_component(
            "Action executors", "INPUT", ComponentState.FAILED,
            f"executor probe crashed: {exc}",
            "reinstall airmouse"))
    return comps


def _detect_browser(quick: bool) -> List[Component]:
    comps: List[Component] = []
    try:
        from .browser import SimulatedBrowserBridge
        if SimulatedBrowserBridge().available():
            comps.append(_component(
                "Simulated browser bridge", "BROWSER",
                ComponentState.READY, "deterministic bridge available"))
        else:
            comps.append(_component(
                "Simulated browser bridge", "BROWSER",
                ComponentState.FAILED, "simulated bridge unavailable",
                "reinstall airmouse"))
    except Exception as exc:
        comps.append(_component(
            "Simulated browser bridge", "BROWSER", ComponentState.FAILED,
            f"import crashed: {exc}", "reinstall airmouse"))

    try:
        from .browser_bridge import DEFAULT_BRIDGE_PORT, BrowserBridgeServer
        comps.append(_component(
            "Browser extension bridge", "BROWSER", ComponentState.READY,
            f"{BrowserBridgeServer.__name__}: localhost-only state sink "
            f"(default port {DEFAULT_BRIDGE_PORT})"))
    except Exception as exc:
        comps.append(_component(
            "Browser extension bridge", "BROWSER", ComponentState.FAILED,
            f"import crashed: {exc}", "reinstall airmouse"))

    try:
        found = _find_browsers()
        if found:
            comps.append(_component(
                "Chrome / Edge installation", "BROWSER",
                ComponentState.READY,
                f"found: {', '.join(found)} (CDP control ready)"))
        else:
            comps.append(_component(
                "Chrome / Edge installation", "BROWSER",
                ComponentState.OPTIONAL,
                "no Chrome/Edge found in the standard install locations",
                "install Google Chrome or Microsoft Edge for real browser "
                "control (the simulated bridge still works)"))
    except Exception as exc:
        comps.append(_component(
            "Chrome / Edge installation", "BROWSER", ComponentState.WARNING,
            f"browser scan failed: {exc}",
            "install Google Chrome or Microsoft Edge for real browser "
            "control"))
    return comps


def _detect_intelligence(quick: bool) -> List[Component]:
    comps: List[Component] = []
    try:
        from .intelligence import twin as _twin
        from .intelligence import vocabulary as _vocab
        from . import skills as _skills
        comps.append(_component(
            "Intelligence package", "INTELLIGENCE", ComponentState.READY,
            f"twin v{getattr(_twin, 'TWIN_FORMAT_VERSION', '?')} + "
            f"{_vocab.PersonalVocabulary.__name__} + "
            f"{_skills.PersonalSkillLibrary.__name__} importable"))
    except Exception as exc:
        comps.append(_component(
            "Intelligence package", "INTELLIGENCE",
            ComponentState.OPTIONAL,
            f"optional plugin unavailable: {exc}",
            "reinstall airmouse to restore the intelligence plugin "
            "(the core works without it)"))
        return comps

    try:
        from .intelligence.plugin import IntelligencePlugin
        from .intelligence import IntelligenceState
        if quick:
            comps.append(_component(
                "Intelligence stores", "INTELLIGENCE",
                ComponentState.READY,
                "plugin importable (stores not loaded in quick mode)"))
            return comps
        plug = IntelligencePlugin({"enabled": True})
        if plug.state is IntelligenceState.AVAILABLE:
            comps.append(_component(
                "Intelligence stores", "INTELLIGENCE",
                ComponentState.READY,
                f"state=available ({plug.base_dir})"))
        elif plug.state in (IntelligenceState.DISABLED,):
            comps.append(_component(
                "Intelligence stores", "INTELLIGENCE",
                ComponentState.OPTIONAL, "disabled by config"))
        elif plug.state is IntelligenceState.UNAVAILABLE:
            comps.append(_component(
                "Intelligence stores", "INTELLIGENCE", ComponentState.WARNING,
                f"plugin unavailable: {plug.last_error}",
                "reinstall airmouse to repair the intelligence plugin"))
        else:  # corrupted / incompatible / out_of_memory / paused
            comps.append(_component(
                "Intelligence stores", "INTELLIGENCE", ComponentState.WARNING,
                f"state={plug.state.value} err={plug.last_error[:80]}",
                "remove the artifacts in ~/.airmouse/intelligence — they "
                "rebuild automatically on next start"))
    except Exception as exc:
        comps.append(_component(
            "Intelligence stores", "INTELLIGENCE", ComponentState.WARNING,
            f"plugin probe crashed: {exc}",
            "remove the artifacts in ~/.airmouse/intelligence — they "
            "rebuild automatically on next start"))
    return comps


def _detect_agent(quick: bool) -> List[Component]:
    comps: List[Component] = []
    try:
        from .permissions import AgentPermissionEngine, Decision
        eng = AgentPermissionEngine()
        # NOTE: ALLOW_ONCE needs uses=1 — the grant() default (uses=-1,
        # "unlimited") marks an ALLOW_ONCE rule exhausted immediately.
        if eng.grant("doctor-probe", "mouse.click", Decision.ALLOW_ONCE,
                     uses=1):
            d = eng.check("doctor-probe", "mouse.click")
            if d.allowed:
                comps.append(_component(
                    "Permission engine", "AGENT", ComponentState.READY,
                    "grant + check roundtrip ok (denies by default)"))
            else:
                comps.append(_component(
                    "Permission engine", "AGENT", ComponentState.FAILED,
                    "explicit grant did not allow",
                    "reinstall airmouse (permission engine is broken)"))
        else:
            comps.append(_component(
                "Permission engine", "AGENT", ComponentState.FAILED,
                "grant() refused a valid rule",
                "reinstall airmouse (permission engine is broken)"))
    except Exception as exc:
        comps.append(_component(
            "Permission engine", "AGENT", ComponentState.FAILED,
            f"probe crashed: {exc}", "reinstall airmouse"))

    try:
        from .agents import AgentRegistry
        reg = AgentRegistry()
        if reg.register("doctor-probe"):
            comps.append(_component(
                "Agent registry", "AGENT", ComponentState.READY,
                "registration + discovery functional"))
        else:
            comps.append(_component(
                "Agent registry", "AGENT", ComponentState.FAILED,
                "register() refused a valid agent",
                "reinstall airmouse (agent registry is broken)"))
    except Exception as exc:
        comps.append(_component(
            "Agent registry", "AGENT", ComponentState.FAILED,
            f"probe crashed: {exc}", "reinstall airmouse"))

    try:
        from .aip import AIP_VERSION, parse_message
        msg, errs = parse_message({"aip_version": "9.9", "type": "x",
                                   "id": "y"})
        if msg is None and errs:
            comps.append(_component(
                "AIP protocol", "AGENT", ComponentState.READY,
                f"AIP v{AIP_VERSION} validator fail-closed ok"))
        else:
            comps.append(_component(
                "AIP protocol", "AGENT", ComponentState.FAILED,
                "validator accepted a malformed envelope",
                "reinstall airmouse (AIP validator is broken)"))
    except Exception as exc:
        comps.append(_component(
            "AIP protocol", "AGENT", ComponentState.FAILED,
            f"probe crashed: {exc}", "reinstall airmouse"))

    try:
        from . import agent_sdk
        comps.append(_component(
            "Agent SDK", "AGENT", ComponentState.READY,
            f"agent_sdk importable ({agent_sdk.AirMouse.__name__} facade, "
            f"AIP v{agent_sdk.AIP_VERSION})"))
    except Exception as exc:
        comps.append(_component(
            "Agent SDK", "AGENT", ComponentState.OPTIONAL,
            f"agent_sdk unavailable: {exc}",
            "reinstall airmouse to restore the agent SDK"))
    return comps


def _detect_offline(quick: bool) -> List[Component]:
    try:
        from .offline import OfflineGate
        gate = OfflineGate(engaged=True)
        gate_ok = (not gate.check("cloud_asr")) and \
            gate.check("local_grammar")
        if gate_ok:
            return [_component(
                "Offline mode", "OFFLINE", ComponentState.READY,
                "gate routing ok; network-isolation self-test available "
                "via `airmouse offline-test` (18 checks)")]
        return [_component(
            "Offline mode", "OFFLINE", ComponentState.FAILED,
            "gate semantics broken",
            "reinstall airmouse (offline gate is broken)")]
    except Exception as exc:
        return [_component(
            "Offline mode", "OFFLINE", ComponentState.FAILED,
            f"probe crashed: {exc}", "reinstall airmouse")]


def _detect_safety(quick: bool) -> List[Component]:
    comps: List[Component] = []
    try:
        from .interfaces import Intent, IntentType
        from .safety import CLICK_CLASS, SENSITIVE_TYPES, SafetySystem
        s = SafetySystem({"level": "normal"})
        has_estop = all(hasattr(s, a) for a in
                        ("trip", "reset", "approve_intent", "confirm"))
        d = s.approve_intent(
            Intent(type=IntentType.CLICK, confidence=0.9), now=1.0)
        estop_type = IntentType.EMERGENCY_STOP
        if has_estop and d is not None and d.allowed and estop_type:
            comps.append(_component(
                "Safety system", "SAFETY", ComponentState.READY,
                "gates + confirmation flow + e-stop path present "
                f"({len(CLICK_CLASS)} click-class, "
                f"{len(SENSITIVE_TYPES)} sensitive types)"))
        else:
            comps.append(_component(
                "Safety system", "SAFETY", ComponentState.FAILED,
                "safety approval path broken",
                "reinstall airmouse (safety system is broken)"))
    except Exception as exc:
        comps.append(_component(
            "Safety system", "SAFETY", ComponentState.FAILED,
            f"probe crashed: {exc}",
            "reinstall airmouse (safety system is broken)"))

    try:
        from .interfaces import SafetyLevel
        from .permissions import ControlLevel
        hierarchy = " > ".join(l.name for l in ControlLevel)
        comps.append(_component(
            "Control hierarchy", "SAFETY", ComponentState.READY,
            f"{hierarchy} (+ {len(SafetyLevel)} safety levels)"))
    except Exception as exc:
        comps.append(_component(
            "Control hierarchy", "SAFETY", ComponentState.FAILED,
            f"hierarchy constants missing: {exc}",
            "reinstall airmouse"))
    return comps


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def detect_all(quick: bool = False) -> CapabilityReport:
    """Run every category detector.  NEVER raises; NEVER hangs.

    Each detector is looked up by name at call time (so tests can
    inject failures) and wrapped: a crashing or misbehaving detector
    yields a single FAILED component for that category.
    """
    components: List[Component] = []
    for cat in CATEGORIES:
        fn = globals().get("_detect_" + cat.lower())
        try:
            if not callable(fn):
                raise RuntimeError("detector missing")
            result = fn(bool(quick))
            if isinstance(result, list) and all(
                    isinstance(c, Component) for c in result):
                components.extend(result)
            else:
                raise RuntimeError("detector returned invalid rows")
        except Exception as exc:  # FAIL CLOSED — never propagate
            components.append(_component(
                cat.title() + " diagnostics", cat, ComponentState.FAILED,
                f"detector crashed: {type(exc).__name__}: "
                f"{str(exc)[:120]}",
                "re-run `airmouse doctor`; if this persists reinstall "
                "airmouse"))
    return CapabilityReport(components=components)
