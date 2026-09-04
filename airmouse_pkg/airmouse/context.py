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

from typing import Dict, List, Optional, Tuple, Union

try:  # package-relative (normal import path)
    from .interfaces import AppContext, IntentType
except ImportError:  # pragma: no cover - direct file execution fallback
    from airmouse.interfaces import AppContext, IntentType

__all__ = [
    "CONTEXT_KEYWORDS",
    "CONTEXT_PRIORITY",
    "PROFILES",
    "detect_app_context",
    "ContextProfile",
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
