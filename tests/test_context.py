"""Tests for airmouse.context (v8 app-context detection + profiles)."""
from __future__ import annotations

import pytest

from airmouse.context import (
    CONTEXT_KEYWORDS,
    ContextProfile,
    detect_app_context,
)
from airmouse.interfaces import AppContext, IntentType


# ── detection ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("title,ctx", [
    ("Google Chrome", AppContext.BROWSER),
    ("Mozilla Firefox", AppContext.BROWSER),
    ("Microsoft Edge", AppContext.BROWSER),
    ("New Tab - Brave", AppContext.BROWSER),
    ("VLC media player", AppContext.VIDEO),
    ("YouTube", AppContext.VIDEO),
    ("Netflix - player", AppContext.VIDEO),
    ("Visual Studio Code", AppContext.EDITOR),
    ("Untitled - Notepad", AppContext.EDITOR),
    ("~/.bashrc - Vim", AppContext.EDITOR),
    ("GNOME Terminal", AppContext.TERMINAL),
    ("user@host: ~ — bash", AppContext.TERMINAL),
    ("Windows PowerShell", AppContext.TERMINAL),
    ("File Explorer", AppContext.FILE_MANAGER),
    ("Finder", AppContext.FILE_MANAGER),
    ("Nautilus", AppContext.FILE_MANAGER),
    ("Settings", AppContext.DIALOG),
    ("Confirm delete — Properties", AppContext.DIALOG),
    ("Desktop", AppContext.DESKTOP),
])
def test_detect_each_context(title, ctx):
    assert detect_app_context(title) is ctx


def test_case_insensitive_detection():
    assert detect_app_context("CHROME") is AppContext.BROWSER
    assert detect_app_context("FiReFoX") is AppContext.BROWSER
    assert detect_app_context("VLC") is AppContext.VIDEO
    assert detect_app_context("QUANTUM") is AppContext.UNKNOWN  # no keyword hit


def test_multi_keyword_priority_browser_beats_video():
    assert detect_app_context("Chrome — YouTube") is AppContext.BROWSER


def test_multi_keyword_priority_editor_beats_terminal():
    # "code" (editor) is checked before "console" (terminal)
    assert detect_app_context("VS Code — console") is AppContext.EDITOR


def test_empty_and_unknown_titles():
    assert detect_app_context("") is AppContext.UNKNOWN
    assert detect_app_context("   ") is AppContext.UNKNOWN
    assert detect_app_context("Quantum Spreadsheet 3000") is AppContext.UNKNOWN
    assert detect_app_context(None) is AppContext.UNKNOWN


def test_keyword_table_covers_priority_table():
    # CONTEXT_KEYWORDS is derived from CONTEXT_PRIORITY 1:1
    from airmouse.context import CONTEXT_PRIORITY
    assert CONTEXT_KEYWORDS == {ctx: kw for ctx, kw in CONTEXT_PRIORITY}
    for ctx, keywords in CONTEXT_KEYWORDS.items():
        assert isinstance(keywords, tuple) and keywords


# ── profiles ─────────────────────────────────────────────────────────────────

def test_profiles_non_empty_for_rich_contexts():
    prof = ContextProfile()
    for ctx in (AppContext.BROWSER, AppContext.VIDEO, AppContext.EDITOR):
        assert prof.profile_for(ctx), ctx
    assert prof.profile_for(AppContext.BROWSER)["back_hotkey"] == ["alt", "left"]


def test_terminal_paste_profile():
    prof = ContextProfile()
    assert prof.profile_for(AppContext.TERMINAL)["paste_hotkey"] == \
        ["ctrl", "shift", "v"]


def test_profiles_empty_for_plain_contexts():
    prof = ContextProfile()
    for ctx in (AppContext.FILE_MANAGER, AppContext.DESKTOP,
                AppContext.DIALOG, AppContext.UNKNOWN):
        assert prof.profile_for(ctx) == {}


def test_profile_accepts_strings():
    prof = ContextProfile()
    assert prof.profile_for("browser")["reload_hotkey"] == ["f5"]
    assert prof.profile_for("nonsense") == {}


# ── recommendations ──────────────────────────────────────────────────────────

def test_recommend_back_in_browser():
    assert ContextProfile.recommend(IntentType.BACK, AppContext.BROWSER) == \
        {"hotkey": ["alt", "left"]}


def test_recommend_forward_in_browser():
    assert ContextProfile.recommend(IntentType.FORWARD, AppContext.BROWSER) == \
        {"hotkey": ["alt", "right"]}


def test_recommend_back_unknown_empty():
    assert ContextProfile.recommend(IntentType.BACK, AppContext.UNKNOWN) == {}


def test_recommend_play_pause_video():
    assert ContextProfile.recommend(IntentType.PLAY, AppContext.VIDEO) == \
        {"key": "space"}
    assert ContextProfile.recommend(IntentType.PAUSE, AppContext.VIDEO) == \
        {"key": "space"}


def test_recommend_paste_context_sensitive():
    assert ContextProfile.recommend(IntentType.PASTE, AppContext.TERMINAL) == \
        {"hotkey": ["ctrl", "shift", "v"]}
    assert ContextProfile.recommend(IntentType.PASTE, AppContext.EDITOR) == \
        {"hotkey": ["ctrl", "v"]}
    assert ContextProfile.recommend(IntentType.PASTE, AppContext.UNKNOWN) == {}


def test_recommend_accepts_strings_and_garbage():
    assert ContextProfile.recommend("back", "browser") == \
        {"hotkey": ["alt", "left"]}
    assert ContextProfile.recommend("nonsense", "browser") == {}
