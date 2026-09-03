"""
Screen Perception v7 — layered screen understanding producing a ScreenModel
===========================================================================

Turns the raw desktop into a unified :class:`airmouse.interfaces.ScreenModel`
(a list of semantic :class:`airmouse.interfaces.ScreenTarget` entries plus the
active-window context) so the fusion/intent layers can reason about WHAT is
under the gaze point, not just WHERE it is.

Layered architecture (checked top -> bottom, first hit wins per layer)
---------------------------------------------------------------------
=========================  ==================================================
Provider                   What it contributes
=========================  ==================================================
AccessibilityProvider      ACTIVE WINDOW target (title + app name, WINDOW,
                           actionable) via xdotool / ctypes / osascript
OCRProvider                OPTIONAL text targets (BUTTON/UNKNOWN) — **off by
                           default**, explicit opt-in via config (see privacy)
GeometryProvider           deterministic region decomposition (center focus
                           zone + 4 corners + 4 edge strips) — ALWAYS
                           available; the coordinate-fallback safety net
=========================  ==================================================

PRIVACY
-------
Everything in this module runs **locally**:

* nothing ever leaves the machine — no network calls, no screenshots are
  transmitted, no telemetry;
* the only OS interactions are a best-effort active-window title lookup and
  (only if the user explicitly opts in via ``OCRProvider(config={"enabled":
  True})``) a local screen grab + local tesseract OCR;
* when any dependency is missing (xdotool, pytesseract, PIL, tesseract
  binary) the affected provider simply reports ``available == False`` and
  yields an empty list — the engine NEVER raises.

Decoupling point
----------------
``ScreenPerceptionEngine(..., context_resolver=fn)`` expects
``fn(active_window_title) -> AppContext``.  The default ``None`` resolver
yields :attr:`AppContext.UNKNOWN`; the context agent (v8) plugs its real
resolver in here without touching this module.

House style: lazy/guarded imports, graceful degradation everywhere,
importable and fully usable headless.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import threading
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

try:  # package-relative (normal import path)
    from .interfaces import (
        AppContext,
        ScreenModel,
        ScreenTarget,
        ScreenTargetType,
        now_ts,
    )
except ImportError:  # pragma: no cover - direct file execution fallback
    from airmouse.interfaces import (
        AppContext,
        ScreenModel,
        ScreenTarget,
        ScreenTargetType,
        now_ts,
    )

__all__ = [
    "parse_app_name",
    "iou",
    "dedupe_targets",
    "GeometryProvider",
    "AccessibilityProvider",
    "OCRProvider",
    "ScreenPerceptionEngine",
]

#: Subprocess timeout for accessibility lookups (seconds) — best effort only.
_AX_TIMEOUT = 0.4

#: IoU threshold above which two targets are considered the same object.
_DEDUPE_IOU = 0.85

_ContextResolver = Callable[[str], AppContext]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def parse_app_name(title: str) -> str:
    """Best-effort application name parsed from a window title.

    Most desktops title windows ``"<document> <sep> <AppName>"``; common
    separators are tried and the LAST non-empty segment is returned.  With no
    separator the whole (stripped) title is returned.  Never raises.
    """
    text = str(title or "").strip()
    if not text:
        return ""
    for sep in (" — ", " – ", " - ", " :: ", " | "):
        if sep in text:
            parts = [p.strip() for p in text.split(sep) if p.strip()]
            if len(parts) >= 2:
                return parts[-1]
    return text


def iou(a: Tuple[float, float, float, float],
        b: Tuple[float, float, float, float]) -> float:
    """Intersection-over-union of two ``(x, y, w, h)`` boxes in [0, 1]."""
    try:
        ax, ay, aw, ah = (float(v) for v in a)
        bx, by, bw, bh = (float(v) for v in b)
    except Exception:
        return 0.0
    ix0, iy0 = max(ax, bx), max(ay, by)
    ix1, iy1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0.0:
        return 0.0
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0.0 else 0.0


def dedupe_targets(targets: Sequence[ScreenTarget],
                   iou_threshold: float = _DEDUPE_IOU) -> List[ScreenTarget]:
    """Merge duplicate targets: IoU > threshold keeps the higher-confidence one.

    Deterministic: candidates are visited in descending-confidence order
    (stable for equal confidences) and kept unless they heavily overlap an
    already-kept target.  The returned list is in that confidence order.
    """
    ordered = sorted([t for t in targets if t is not None],
                     key=lambda t: -float(getattr(t, "confidence", 0.0)))
    kept: List[ScreenTarget] = []
    for t in ordered:
        bbox = getattr(t, "bbox", (0.0, 0.0, 0.0, 0.0))
        if all(iou(bbox, k.bbox) <= iou_threshold for k in kept):
            kept.append(t)
    return kept


# ---------------------------------------------------------------------------
# Guarded platform backends for the active window
# ---------------------------------------------------------------------------

def _title_windows() -> str:
    """Foreground window title via ctypes user32 (Windows only). Never raises."""
    try:
        import ctypes  # guarded: only meaningful on Windows
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(max(1, length + 1))
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return str(buf.value or "").strip()
    except Exception:
        return ""


def _title_macos(timeout: float) -> str:
    """Frontmost application name via osascript (macOS only). Never raises."""
    try:
        out = subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to get name of '
             'first application process whose frontmost is true'],
            capture_output=True, text=True, timeout=float(timeout))
        return (out.stdout or "").strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def _geometry_linux(wid: str, timeout: float) -> Optional[Tuple[float, float, float, float]]:
    """Window geometry via ``xdotool getwindowgeometry --shell``. Never raises."""
    try:
        out = subprocess.run(["xdotool", "getwindowgeometry", "--shell", str(wid)],
                             capture_output=True, text=True, timeout=float(timeout))
        values: Dict[str, str] = {}
        for line in (out.stdout or "").splitlines():
            if "=" in line:
                key, _, val = line.partition("=")
                values[key.strip()] = val.strip()
        x = float(values.get("X", 0.0))
        y = float(values.get("Y", 0.0))
        w = float(values.get("WIDTH", 0.0))
        h = float(values.get("HEIGHT", 0.0))
        if w <= 0.0 or h <= 0.0:
            return None
        return (x, y, w, h)
    except Exception:
        return None


def _geometry_windows() -> Optional[Tuple[float, float, float, float]]:
    """Foreground window geometry via ctypes GetWindowRect. Never raises."""
    try:
        import ctypes  # guarded: only meaningful on Windows
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None

        class _RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        rect = _RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        w = float(rect.right - rect.left)
        h = float(rect.bottom - rect.top)
        if w <= 0.0 or h <= 0.0:
            return None
        return (float(rect.left), float(rect.top), w, h)
    except Exception:
        return None


def _active_window_title() -> str:
    """Platform-aware active window title. Empty string when unavailable."""
    sysname = platform.system()
    try:
        if sysname == "Linux":
            if shutil.which("xdotool") is None:
                return ""  # headless / not installed — degrade silently
            out = subprocess.run(["xdotool", "getactivewindow", "getwindowname"],
                                 capture_output=True, text=True,
                                 timeout=_AX_TIMEOUT)
            return (out.stdout or "").strip() if out.returncode == 0 else ""
        if sysname == "Windows":
            return _title_windows()
        if sysname == "Darwin":
            if shutil.which("osascript") is None:
                return ""
            return _title_macos(_AX_TIMEOUT)
    except Exception:
        return ""
    return ""


# ---------------------------------------------------------------------------
# Layer 3 (always-on): deterministic geometry decomposition
# ---------------------------------------------------------------------------

class GeometryProvider:
    """Deterministic screen-region decomposition — the coordinate fallback.

    Partitions the desktop into nine zones and yields one ScreenTarget each:

    * ``geo:center``      — the middle 30% × 30% focus zone, type UNKNOWN,
                            **actionable True** (safe default click zone);
    * ``geo:corner_{tl,tr,bl,br}`` — 25% × 25% corner zones, APP_REGION,
                            actionable False;
    * ``geo:edge_{top,bottom,left,right}`` — 10%-thick edge strips,
                            APP_REGION, actionable False.

    ALWAYS ``available``; output is deterministic for a given screen size
    (built once in the constructor, returned as a fresh list per update).
    Guarantees the engine always has *something* under any screen coordinate.
    """

    name = "geometry"

    def __init__(self, screen_w: int, screen_h: int) -> None:
        try:
            self.screen_w = max(1, int(screen_w))
        except Exception:
            self.screen_w = 1
        try:
            self.screen_h = max(1, int(screen_h))
        except Exception:
            self.screen_h = 1
        self.available: bool = True
        self._targets: List[ScreenTarget] = self._build()

    # -- zone construction -----------------------------------------------------

    def _build(self) -> List[ScreenTarget]:
        w, h = float(self.screen_w), float(self.screen_h)
        stamp = now_ts()

        def tgt(tid: str, bbox: Tuple[float, float, float, float],
                ttype: ScreenTargetType, actionable: bool, conf: float,
                text: str = "") -> ScreenTarget:
            return ScreenTarget(id=tid, type=ttype, bbox=bbox, text=text,
                                confidence=conf, application="",
                                actionable=actionable, source="geometry",
                                timestamp=stamp)

        return [
            tgt("geo:center", (0.35 * w, 0.35 * h, 0.30 * w, 0.30 * h),
                ScreenTargetType.UNKNOWN, True, 0.55, "center"),
            tgt("geo:corner_tl", (0.0, 0.0, 0.25 * w, 0.25 * h),
                ScreenTargetType.APP_REGION, False, 0.40),
            tgt("geo:corner_tr", (0.75 * w, 0.0, 0.25 * w, 0.25 * h),
                ScreenTargetType.APP_REGION, False, 0.40),
            tgt("geo:corner_bl", (0.0, 0.75 * h, 0.25 * w, 0.25 * h),
                ScreenTargetType.APP_REGION, False, 0.40),
            tgt("geo:corner_br", (0.75 * w, 0.75 * h, 0.25 * w, 0.25 * h),
                ScreenTargetType.APP_REGION, False, 0.40),
            tgt("geo:edge_top", (0.0, 0.0, w, 0.10 * h),
                ScreenTargetType.APP_REGION, False, 0.40),
            tgt("geo:edge_bottom", (0.0, 0.90 * h, w, 0.10 * h),
                ScreenTargetType.APP_REGION, False, 0.40),
            tgt("geo:edge_left", (0.0, 0.0, 0.10 * w, h),
                ScreenTargetType.APP_REGION, False, 0.40),
            tgt("geo:edge_right", (0.90 * w, 0.0, 0.10 * w, h),
                ScreenTargetType.APP_REGION, False, 0.40),
        ]

    def update(self, now: Optional[float] = None) -> List[ScreenTarget]:
        """Return the nine deterministic geometry zones (fresh list each call)."""
        return list(self._targets)


# ---------------------------------------------------------------------------
# Layer 1: accessibility — active window (platform best-effort)
# ---------------------------------------------------------------------------

class AccessibilityProvider:
    """Active-window target via the platform accessibility path.

    * Linux  — ``xdotool getactivewindow getwindowname`` (+ geometry via
      ``getwindowgeometry --shell``), guarded, ``timeout`` seconds each;
    * Windows — ctypes ``user32.GetForegroundWindow`` + ``GetWindowTextW``
      (+ ``GetWindowRect`` for geometry), guarded;
    * macOS — guarded ``osascript`` (frontmost application name).

    When the tool is missing (headless servers, restricted sandboxes) the
    provider reports ``available == False`` and ``update()`` yields ``[]`` —
    never raises.  The successful active-window title is cached in
    :attr:`last_title` where the engine's context hook picks it up.
    """

    name = "accessibility"

    def __init__(self,
                 screen_w: Optional[int] = None,
                 screen_h: Optional[int] = None,
                 timeout: float = _AX_TIMEOUT) -> None:
        self.screen_w = screen_w
        self.screen_h = screen_h
        try:
            self.timeout = max(0.05, float(timeout))
        except Exception:
            self.timeout = _AX_TIMEOUT
        self.last_title: str = ""
        self.available: bool = self._probe()

    # -- capability probe --------------------------------------------------------

    def _probe(self) -> bool:
        """True when the platform's accessibility tool looks usable."""
        sysname = platform.system()
        try:
            if sysname == "Linux":
                return shutil.which("xdotool") is not None
            if sysname == "Windows":
                import ctypes  # guarded
                return ctypes.windll.user32 is not None
            if sysname == "Darwin":
                return shutil.which("osascript") is not None
        except Exception:
            return False
        return False

    @staticmethod
    def active_window_title() -> str:
        """Static best-effort foreground window title ('' when unavailable).

        Reuses the same guarded platform logic as :meth:`update` so callers
        (HUD, voice layer, tests) can query the title without a provider
        instance.  Never raises.
        """
        return _active_window_title()

    def _window_bbox(self) -> Optional[Tuple[float, float, float, float]]:
        """Best-effort foreground window geometry; None when unknown."""
        sysname = platform.system()
        if sysname == "Linux":
            try:
                out = subprocess.run(["xdotool", "getactivewindow"],
                                     capture_output=True, text=True,
                                     timeout=self.timeout)
                wid = (out.stdout or "").strip()
                if wid and out.returncode == 0:
                    return _geometry_linux(wid, self.timeout)
            except Exception:
                return None
            return None
        if sysname == "Windows":
            return _geometry_windows()
        return None  # macOS: no cheap geometry path — fall back below

    def update(self, now: Optional[float] = None) -> List[ScreenTarget]:
        """Return one WINDOW ScreenTarget for the active window, or []."""
        if not self.available:
            return []
        try:
            title = self.active_window_title()
        except Exception:
            title = ""
        if not title:
            return []
        self.last_title = title
        try:
            bbox = self._window_bbox()
        except Exception:
            bbox = None
        if bbox is None:
            bbox = (0.0, 0.0, float(self.screen_w or 0), float(self.screen_h or 0))
        return [ScreenTarget(
            id="ax:active_window",
            type=ScreenTargetType.WINDOW,
            bbox=bbox,
            text=title,
            confidence=0.8,
            application=parse_app_name(title),
            actionable=True,
            source="accessibility",
            timestamp=float(now if now is not None else now_ts()),
        )]


# ---------------------------------------------------------------------------
# Layer 2: OCR — OPTIONAL, DISABLED BY DEFAULT (privacy + optional deps)
# ---------------------------------------------------------------------------

class OCRProvider:
    """Local text recognition layer — opt-in ONLY, disabled by default.

    **Why disabled by default:** OCR implies grabbing the screen content,
    which is a privacy-sensitive operation, and it also requires optional
    dependencies (pytesseract + Pillow + a local tesseract binary).  The
    provider therefore ships dormant: ``available`` is False until the user
    explicitly enables it via ``OCRProvider(config={"enabled": True})`` AND
    the dependencies are importable.  Intended for explicit opt-in only.

    PRIVACY: when enabled, the screenshot is grabbed locally (PIL.ImageGrab)
    and recognised by the LOCAL tesseract binary.  No network calls are ever
    made; no pixels or text leave the machine; nothing is cached to disk.
    """

    name = "ocr"

    DEFAULT_CONFIG: Dict[str, Any] = {
        "enabled": False,          # master switch — off by default (privacy)
        "language": "eng",         # tesseract language model
        "min_confidence": 0.60,    # drop words below this recognition conf
        "max_targets": 40,         # cap targets per update
        "button_words": ("submit", "ok", "cancel", "save", "next", "done",
                         "search", "buy", "login", "sign in", "send"),
    }

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config: Dict[str, Any] = dict(self.DEFAULT_CONFIG)
        if config:
            try:
                self.config.update({k: v for k, v in config.items()
                                    if v is not None})
            except Exception:
                pass  # broken config -> documented defaults
        self.available: bool = self._probe()

    def _probe(self) -> bool:
        """True only when explicitly enabled AND tesseract stack is usable."""
        if not self.config.get("enabled"):
            return False
        try:
            import pytesseract  # optional dependency, guarded
            from PIL import Image
            if not hasattr(Image, "open"):  # real availability probe
                return False
            pytesseract.get_tesseract_version()  # raises if binary missing
            return True
        except Exception:
            return False

    def update(self, now: Optional[float] = None) -> List[ScreenTarget]:
        """Return text targets, or [] when disabled/unavailable. Never raises."""
        if not self.available:
            return []
        try:
            import pytesseract
            from PIL import ImageGrab  # local screen grab only
        except Exception:
            self.available = False
            return []
        try:
            img = ImageGrab.grab()
            data = pytesseract.image_to_data(
                img, lang=str(self.config.get("language", "eng")),
                output_type=pytesseract.Output.DICT)
        except Exception:
            return []  # grab/recognize hiccup — degrade silently

        try:
            min_conf = float(self.config.get("min_confidence", 0.60)) * 100.0
            max_targets = int(self.config.get("max_targets", 40))
            button_words = tuple(self.config.get("button_words", ()))
        except Exception:
            return []

        # group words into lines by tesseract's page/block/par/line indices
        lines: Dict[Tuple[int, int, int, int], List[Dict[str, Any]]] = {}
        texts = data.get("text") or []
        for idx in range(len(texts)):
            try:
                conf = float(data["conf"][idx])
            except Exception:
                continue
            word = str(data["text"][idx] or "").strip()
            if not word or conf < min_conf:
                continue
            try:
                key = (int(data["page_num"][idx]), int(data["block_num"][idx]),
                       int(data["par_num"][idx]), int(data["line_num"][idx]))
                entry = {"word": word, "conf": conf,
                         "left": int(data["left"][idx]),
                         "top": int(data["top"][idx]),
                         "width": int(data["width"][idx]),
                         "height": int(data["height"][idx])}
            except Exception:
                continue
            lines.setdefault(key, []).append(entry)

        stamp = float(now if now is not None else now_ts())
        targets: List[ScreenTarget] = []
        for _key, words in lines.items():
            text = " ".join(w["word"] for w in words).strip()
            if not text:
                continue
            x0 = min(w["left"] for w in words)
            y0 = min(w["top"] for w in words)
            x1 = max(w["left"] + w["width"] for w in words)
            y1 = max(w["top"] + w["height"] for w in words)
            low = text.lower()
            is_button = any(b in low for b in button_words)
            confidence = min(0.95, max(w["conf"] for w in words) / 100.0)
            targets.append(ScreenTarget(
                id="ocr:line-{}".format(len(targets)),
                type=ScreenTargetType.BUTTON if is_button else ScreenTargetType.UNKNOWN,
                bbox=(float(x0), float(y0), float(x1 - x0), float(y1 - y0)),
                text=text,
                confidence=confidence,
                application="",
                actionable=is_button,
                source="ocr",
                timestamp=stamp,
            ))
            if len(targets) >= max_targets:
                break
        return targets


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------

class ScreenPerceptionEngine:
    """Fuses the perception layers into one cached :class:`ScreenModel`.

    Usage::

        engine = ScreenPerceptionEngine(1920, 1080)
        model = engine.update()             # cheap when called < 0.5 s apart
        hit = engine.target_at(960, 540)    # smallest actionable containing
        near = engine.nearest(970, 550)     # nearest target center
        say  = engine.describe_target(hit)  # "screen region center"

    Parameters
    ----------
    screen_w, screen_h : desktop pixel size.
    config : optional dict; keys: ``refresh_interval`` (default 0.5 s) and an
        optional nested ``"ocr"`` dict forwarded to :class:`OCRProvider`.
    providers : optional list replacing the default order
        ``[AccessibilityProvider, OCRProvider, GeometryProvider]``
        (dependency injection for tests).  **Fallback guarantee:** if the
        supplied list contains no GeometryProvider, one is appended — even
        with zero usable providers the engine still yields geometry targets.
    context_resolver : optional ``fn(active_window_title) -> AppContext``
        hook (the v8 context agent supplies the real one later; default None
        -> :attr:`AppContext.UNKNOWN`).  Resolver exceptions degrade to
        UNKNOWN — never raise.
    """

    DEFAULT_CONFIG: Dict[str, Any] = {
        "refresh_interval": 0.5,   # seconds a cached ScreenModel stays valid
    }

    def __init__(self,
                 screen_w: int,
                 screen_h: int,
                 config: Optional[Dict[str, Any]] = None,
                 providers: Optional[Sequence[Any]] = None,
                 context_resolver: Optional[_ContextResolver] = None) -> None:
        try:
            self.screen_w = max(1, int(screen_w))
        except Exception:
            self.screen_w = 1
        try:
            self.screen_h = max(1, int(screen_h))
        except Exception:
            self.screen_h = 1

        self.config: Dict[str, Any] = dict(self.DEFAULT_CONFIG)
        if config:
            try:
                self.config.update({k: v for k, v in config.items()
                                    if v is not None})
            except Exception:
                pass

        if providers is None:
            ocr_cfg = self.config.get("ocr")
            ocr_cfg = ocr_cfg if isinstance(ocr_cfg, dict) else None
            self.providers: List[Any] = [
                AccessibilityProvider(self.screen_w, self.screen_h),
                OCRProvider(ocr_cfg),
                GeometryProvider(self.screen_w, self.screen_h),
            ]
        else:
            self.providers = list(providers)
            if not any(isinstance(p, GeometryProvider) for p in self.providers):
                # Coordinate-fallback guarantee: geometry is ALWAYS present.
                self.providers.append(GeometryProvider(self.screen_w,
                                                       self.screen_h))

        self.context_resolver = context_resolver
        self._model: Optional[ScreenModel] = None
        self._last_refresh: float = float("-inf")
        self._lock = threading.RLock()

    # -- model refresh -----------------------------------------------------------

    def update(self, now: Optional[float] = None,
               force: bool = False) -> ScreenModel:
        """Collect all layers and return the merged :class:`ScreenModel`.

        Calls within ``refresh_interval`` seconds return the CACHED model
        object (cheap for per-frame callers).  ``force=True`` or
        :meth:`invalidate` bypasses the cache.  Provider explosions are
        swallowed (that layer contributes nothing for this tick).
        """
        ts = float(now_ts() if now is None else now)
        with self._lock:
            interval = float(self.config.get("refresh_interval",
                                             self.DEFAULT_CONFIG["refresh_interval"]))
            if (not force and self._model is not None
                    and (ts - self._last_refresh) < interval):
                return self._model

            collected: List[ScreenTarget] = []
            for provider in self.providers:
                try:
                    if not bool(getattr(provider, "available", True)):
                        continue
                    batch = provider.update(ts)
                except Exception:
                    continue  # a broken layer never kills the engine
                if batch:
                    collected.extend(batch)

            merged = dedupe_targets(collected)

            title = self._active_title(merged)
            context = self._resolve_context(title)

            model = ScreenModel(
                targets=merged,
                screen_w=self.screen_w,
                screen_h=self.screen_h,
                context=context,
                active_window_title=title,
                timestamp=ts,
            )
            self._model = model
            self._last_refresh = ts
            return model

    def invalidate(self) -> None:
        """Drop the cached model so the next update() re-collects."""
        with self._lock:
            self._model = None
            self._last_refresh = float("-inf")

    def _active_title(self, merged: List[ScreenTarget]) -> str:
        """Active-window title: provider ``last_title`` first, then targets."""
        for provider in self.providers:
            try:
                title = getattr(provider, "last_title", "") or ""
            except Exception:
                title = ""
            if title:
                return str(title)
        for t in merged:
            if getattr(t, "source", "") == "accessibility" and t.text:
                return t.text
        return ""

    def _resolve_context(self, title: str) -> AppContext:
        """Run the (optional) context resolver hook, guarded."""
        resolver = self.context_resolver
        if resolver is None:
            return AppContext.UNKNOWN
        try:
            result = resolver(title)
            return result if isinstance(result, AppContext) else AppContext.UNKNOWN
        except Exception:
            return AppContext.UNKNOWN

    # -- queries (delegates to the current model) ---------------------------------

    @property
    def model(self) -> Optional[ScreenModel]:
        """Last cached ScreenModel (None before the first update())."""
        return self._model

    def _ensure_model(self) -> Optional[ScreenModel]:
        return self._model if self._model is not None else self.update()

    def target_at(self, px: float, py: float) -> Optional[ScreenTarget]:
        """Smallest actionable target containing (px, py); None when none."""
        model = self._ensure_model()
        return model.target_at(px, py) if model is not None else None

    def nearest(self, px: float, py: float,
                max_dist: float = 120.0) -> Optional[ScreenTarget]:
        """Nearest target center within ``max_dist`` px; None when none."""
        model = self._ensure_model()
        return model.nearest(px, py, max_dist) if model is not None else None

    def find_by_text(self, text: str) -> Optional[ScreenTarget]:
        """First target whose text contains ``text`` (case-insensitive)."""
        model = self._ensure_model()
        return model.find_by_text(text) if model is not None else None

    # -- human phrases -------------------------------------------------------------

    def describe_target(self,
                        target: Optional[ScreenTarget] = None,
                        point: Optional[Tuple[float, float]] = None) -> str:
        """Human-readable phrase for a target — ALWAYS returns something.

        Examples: ``"Submit button"``, ``"Window: Firefox"``,
        ``"screen region center"``.  With no semantic target it falls back to
        a coordinates description (``point=`` given) or ``"unknown screen
        region"`` — never empty, never raises.
        """
        if target is not None:
            try:
                text = (getattr(target, "text", "") or "").strip()
                ttype = getattr(target, "type", ScreenTargetType.UNKNOWN)
                app = (getattr(target, "application", "") or "").strip()
                tid = str(getattr(target, "id", "") or "")
                source = str(getattr(target, "source", "") or "")

                if source == "geometry" or tid.startswith("geo:"):
                    return "screen region {}".format(
                        tid.split("geo:", 1)[-1].replace("_", " "))
                if ttype is ScreenTargetType.WINDOW:
                    return "Window: {}".format(app or text or "unknown")
                if ttype is ScreenTargetType.BUTTON:
                    return "{} button".format(text) if text else "button"
                if text:
                    if ttype is ScreenTargetType.UNKNOWN:
                        return text
                    return "{} {}".format(text, ttype.value)
                if ttype is not ScreenTargetType.UNKNOWN:
                    return "{} (unlabeled)".format(ttype.value)
                return "unlabeled screen element"
            except Exception:
                return "unknown screen region"
        if point is not None:
            try:
                return "screen point ({:.0f}, {:.0f})".format(
                    float(point[0]), float(point[1]))
            except Exception:
                pass
        return "unknown screen region"

    # -- observability ------------------------------------------------------------

    def availability(self) -> Dict[str, bool]:
        """Map of provider name -> available flag (for HUD/diagnostics)."""
        return {str(getattr(p, "name", "?")): bool(getattr(p, "available", True))
                for p in self.providers}
