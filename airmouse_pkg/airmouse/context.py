"""
airmouse.context — v8 App-Context Awareness 🧭
==============================================

Coarse "what app is the user in?" detection from the active window title,
plus per-context behavioural profiles and hotkey recommendations.

Detection (:meth:`detect_app_context`) is a case-insensitive substring
scan over priority-ordered keyword tables.  The FIRST table (highest
priority) with any keyword hit wins, so multi-keyword titles resolve to
the earliest priority table — e.g. ``"Chrome — YouTube"`` → BROWSER
(browser beats video).  Priority order (documented, highest first):

    1. BROWSER       chrome, firefox, edge, safari, opera, brave, browser, tab
    2. VIDEO         youtube, vlc, netflix, player, video, movie, plex, mpv
    3. EDITOR        code, notepad, sublime, vim, emacs, editor, word, docs, writer
    4. TERMINAL      terminal, bash, zsh, powershell, cmd, console, shell
    5. FILE_MANAGER  explorer, finder, nautilus, files, dolphin
    6. DIALOG        dialog, settings, preferences, confirm, properties
    7. DESKTOP       desktop, empty
    8. (no hit)      UNKNOWN

Profiles (:class:`ContextProfile.profile_for`) are pure per-context data
maps (context-neutral hotkey defaults); :meth:`ContextProfile.recommend`
returns the params dict a caller should merge into a plan for a given
intent type + context ({} when no sensible recommendation exists).

Pure stdlib, headless, deterministic.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional, Tuple, Union

try:  # package-relative (normal import path)
    from .interfaces import AppContext, ContextState, IntentType, ScreenTarget, now_ts
except ImportError:  # pragma: no cover - direct file execution fallback
    from airmouse.interfaces import (AppContext, ContextState, IntentType,
                                     ScreenTarget, now_ts)

__all__ = [
    "CONTEXT_KEYWORDS",
    "CONTEXT_PRIORITY",
    "PROFILES",
    "detect_app_context",
    "ContextProfile",
    "ContextEngine",
]

# ---------------------------------------------------------------------------
# Keyword tables (priority order — see module docstring)
# ---------------------------------------------------------------------------

#: Priority-ordered (context, keywords) tables; first hit wins.
CONTEXT_PRIORITY: Tuple[Tuple[AppContext, Tuple[str, ...]], ...] = (
    (
        AppContext.BROWSER,
        ("chrome", "firefox", "edge", "safari", "opera", "brave", "browser", "tab"),
    ),
    (
        AppContext.VIDEO,
        ("youtube", "vlc", "netflix", "player", "video", "movie", "plex", "mpv"),
    ),
    (
        AppContext.EDITOR,
        ("code", "notepad", "sublime", "vim", "emacs", "editor", "word",
         "docs", "writer"),
    ),
    (
        AppContext.TERMINAL,
        ("terminal", "bash", "zsh", "powershell", "cmd", "console", "shell"),
    ),
    (
        AppContext.FILE_MANAGER,
        ("explorer", "finder", "nautilus", "files", "dolphin"),
    ),
    (
        AppContext.DIALOG,
        ("dialog", "settings", "preferences", "confirm", "properties"),
    ),
    (
        AppContext.DESKTOP,
        ("desktop", "empty"),
    ),
)

#: Flat lookup: AppContext -> keywords (derived from CONTEXT_PRIORITY).
CONTEXT_KEYWORDS: Dict[AppContext, Tuple[str, ...]] = {
    ctx: kw for ctx, kw in CONTEXT_PRIORITY
}


def detect_app_context(window_title: str) -> AppContext:
    """Classify an active window title into an :class:`AppContext`.

    Case-insensitive substring match against the priority-ordered tables;
    the earliest-priority table with a hit wins.  Empty / unmatched titles
    → ``AppContext.UNKNOWN``.  Never raises.
    """
    try:
        title = str(window_title or "").lower()
    except Exception:
        return AppContext.UNKNOWN
    if not title.strip():
        return AppContext.UNKNOWN
    for ctx, keywords in CONTEXT_PRIORITY:
        for kw in keywords:
            if kw in title:
                return ctx
    return AppContext.UNKNOWN


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------

#: Per-context behaviour profiles (pure data; context-neutral hotkeys).
PROFILES: Dict[AppContext, Dict[str, object]] = {
    AppContext.BROWSER: {
        "back_hotkey": ["alt", "left"],
        "forward_hotkey": ["alt", "right"],
        "reload_hotkey": ["f5"],
        "new_tab_hotkey": ["ctrl", "t"],
    },
    AppContext.VIDEO: {
        "play_pause_key": "space",
        "seek_back": "left",
        "seek_forward": "right",
    },
    AppContext.EDITOR: {
        "paste_hotkey": ["ctrl", "v"],
        "copy_hotkey": ["ctrl", "c"],
        "save_hotkey": ["ctrl", "s"],
    },
    AppContext.TERMINAL: {
        "paste_hotkey": ["ctrl", "shift", "v"],
        "copy_hotkey": ["ctrl", "shift", "c"],
    },
    AppContext.FILE_MANAGER: {},
    AppContext.DESKTOP: {},
    AppContext.DIALOG: {},
    AppContext.UNKNOWN: {},
}


class ContextProfile:
    """Context → behaviour-profile lookup and intent recommendations."""

    @staticmethod
    def profile_for(ctx: Union[AppContext, str]) -> Dict[str, object]:
        """Pure data profile for a context ({} for unknown contexts)."""
        ctx = _coerce_ctx(ctx)
        return dict(PROFILES.get(ctx, {}))

    @staticmethod
    def recommend(
        intent_type: Union[IntentType, str],
        ctx: Union[AppContext, str],
    ) -> Dict[str, object]:
        """Recommended params for ``intent_type`` in ``ctx`` ({} = none).

        Examples:  BACK in BROWSER → {"hotkey": ["alt","left"]};
        PLAY in VIDEO → {"key": "space"}; BACK in UNKNOWN → {}.
        """
        ctx = _coerce_ctx(ctx)
        try:
            itype = intent_type
            if isinstance(itype, str):
                itype = IntentType(str(itype).lower())
        except Exception:
            return {}
        profile = PROFILES.get(ctx, {})
        if itype == IntentType.BACK:
            if ctx == AppContext.BROWSER:
                return {"hotkey": list(profile.get("back_hotkey", []))}
            if ctx == AppContext.VIDEO:
                return {"key": profile.get("seek_back", "left")}
            return {}
        if itype == IntentType.FORWARD:
            if ctx == AppContext.BROWSER:
                return {"hotkey": list(profile.get("forward_hotkey", []))}
            if ctx == AppContext.VIDEO:
                return {"key": profile.get("seek_forward", "right")}
            return {}
        if itype in (IntentType.PLAY, IntentType.PAUSE):
            if ctx == AppContext.VIDEO:
                return {"key": profile.get("play_pause_key", "space")}
            return {}
        if itype == IntentType.PASTE:
            hotkey = profile.get("paste_hotkey")
            return {"hotkey": list(hotkey)} if hotkey else {}
        if itype == IntentType.COPY:
            hotkey = profile.get("copy_hotkey")
            return {"hotkey": list(hotkey)} if hotkey else {}
        if itype == IntentType.SWITCH_WINDOW:
            # alt+tab is context-neutral; only recommend where sensible.
            if ctx not in (AppContext.DIALOG, AppContext.DESKTOP):
                return {"hotkey": ["alt", "tab"]}
            return {}
        return {}


def _coerce_ctx(ctx: Union[AppContext, str]) -> AppContext:
    """Accept an AppContext, its value or name (UNKNOWN on garbage)."""
    if isinstance(ctx, AppContext):
        return ctx
    try:
        return AppContext(str(getattr(ctx, "value", ctx)).lower())
    except Exception:
        return AppContext.UNKNOWN


# ═════════════════════════════════════════════════════════════════════════════
# v10 — Context Engine (§8)
# ═════════════════════════════════════════════════════════════════════════════


class ContextEngine:
    """Local context state machine (v10 §8).

    One thread-safe object holding everything the system knows about
    "where the user is": focused application/window, browser state,
    gaze target, selection, recent action/target, active mode.

    Contextual commands resolve here::

        "click that"  -> current_gaze_target   (gaze → click)
        "close it"    -> focused window        (window ref)
        "open this"   -> selected object       (selection)

    Every update accepts an explicit ``now`` for determinism; a stale
    gaze target expires after ``gaze_ttl`` seconds (default 2.0) so
    "click that" never fires on ancient eye positions.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        cfg = dict(config or {})
        self.gaze_ttl = float(cfg.get("gaze_ttl", 2.0))
        self.recent_ttl = float(cfg.get("recent_ttl", 30.0))
        self._state = ContextState()
        self._gaze_at = -1e9
        self._recent_at = -1e9
        self._selection_at = -1e9
        self._lock = threading.RLock()

    # -- updates ---------------------------------------------------------------

    def update_window(self, title: str, application: str = "",
                      now: Optional[float] = None) -> None:
        """Focused window/application changed (screen perception calls)."""
        now = float(now if now is not None else now_ts())
        with self._lock:
            self._state.focused_window = str(title or "")
            self._state.focused_application = str(application or "") or \
                str(title or "")
            self._state.app_context = detect_app_context(str(title or ""))
            self._state.timestamp = now

    def update_browser(self, browser: str = "", tab_title: str = "",
                       url: str = "", now: Optional[float] = None) -> None:
        """Browser-bridge state changed (bridge polls call this)."""
        now = float(now if now is not None else now_ts())
        with self._lock:
            if browser:
                self._state.active_browser = str(browser)
            if tab_title:
                self._state.active_tab_title = str(tab_title)
            if url:
                self._state.current_url = str(url)
            self._state.timestamp = now

    def update_gaze_target(self, target: Optional["ScreenTarget"],
                           now: Optional[float] = None) -> None:
        """The target currently under the gaze point (fusion calls)."""
        now = float(now if now is not None else now_ts())
        with self._lock:
            self._state.current_gaze_target = target
            self._gaze_at = now
            self._state.timestamp = now

    def set_selection(self, target: Optional["ScreenTarget"],
                      now: Optional[float] = None) -> None:
        now = float(now if now is not None else now_ts())
        with self._lock:
            self._state.selected_object = target
            self._selection_at = now

    def record_action(self, action: str,
                      target: Optional["ScreenTarget"] = None,
                      now: Optional[float] = None) -> None:
        """The action engine records what it just did (recent history)."""
        now = float(now if now is not None else now_ts())
        with self._lock:
            self._state.recent_action = str(action or "")
            if target is not None:
                self._state.recent_target = target
            self._recent_at = now
            self._state.timestamp = now

    def set_mode(self, mode: str, now: Optional[float] = None) -> None:
        now = float(now if now is not None else now_ts())
        with self._lock:
            self._state.active_mode = str(mode or "hand")
            self._state.timestamp = now

    def update_screen(self, screen_w: int, screen_h: int) -> None:
        with self._lock:
            self._state.current_screen = (int(screen_w), int(screen_h))

    def set_browser_targets(self, targets: List["ScreenTarget"],
                            now: Optional[float] = None) -> None:
        now = float(now if now is not None else now_ts())
        with self._lock:
            self._state.browser_targets = list(targets or [])
            self._state.timestamp = now

    # -- resolution ---------------------------------------------------------------

    def resolve_reference(self, ref: str,
                          now: Optional[float] = None) -> Optional["ScreenTarget"]:
        """Resolve a deictic reference ("that"/"it"/"window"…) against
        live context.  Gaze targets expire after ``gaze_ttl``; recent
        targets after ``recent_ttl``.  Never raises; returns None when
        nothing sensible exists (the caller must NOT invent coordinates).
        """
        now = float(now if now is not None else now_ts())
        with self._lock:
            r = (ref or "").strip().lower()
            # stale-out the gaze target first
            if (now - self._gaze_at) > self.gaze_ttl:
                self._state.current_gaze_target = None
            if (now - self._recent_at) > self.recent_ttl:
                self._state.recent_target = None
            resolved = self._state.resolve_reference(r)
            # "browser target" phrasing: fall back to browser targets
            if resolved is None and r and self._state.browser_targets:
                needle = r
                for t in self._state.browser_targets:
                    if needle in (t.text or "").lower():
                        return t
            return resolved

    def snapshot(self) -> ContextState:
        """Thread-safe copy of the current context state."""
        with self._lock:
            return self._state

    @property
    def state(self) -> ContextState:
        """Live state reference (read-only users; use snapshot to copy)."""
        return self._state

    def reset(self) -> None:
        with self._lock:
            self._state = ContextState()
            self._gaze_at = self._recent_at = self._selection_at = -1e9
