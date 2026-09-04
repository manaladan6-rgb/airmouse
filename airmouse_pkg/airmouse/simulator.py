"""
airmouse.simulator — deterministic computer simulator (v15 §26).

A tiny virtual "computer" able to test agent workflows WITHOUT
physical hardware (§26):

    windows · apps · browser (tabs/pages/buttons/forms) · text ·
    files · clipboard · navigation · failures · UI changes

Deterministic: same script -> same final state.  Powers the §27
failure-injection suite and the §24 explainability demos.

Nothing here touches a real display; the simulator is also the
"fake computer environment" promised to developers (§25).

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import copy
import enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

MAX_WINDOWS = 16
MAX_TABS = 24
MAX_FILES = 64
MAX_CLIP = 4096
MAX_LOG = 200
MAX_NAME = 80


@dataclass
class SimButton:
    label: str = ""
    visible: bool = True
    clicked: bool = False
    x: float = 0.0
    y: float = 0.0


@dataclass
class SimForm:
    fields: Dict[str, str] = field(default_factory=dict)


@dataclass
class SimPage:
    url: str = ""
    title: str = ""
    buttons: List[SimButton] = field(default_factory=list)
    forms: List[SimForm] = field(default_factory=list)
    text: str = ""

    def button(self, label: str) -> Optional[SimButton]:
        return next((b for b in self.buttons if b.label == label), None)


@dataclass
class SimWindow:
    title: str = ""
    app: str = ""
    focused: bool = False
    visible: bool = True
    text: str = ""
    buttons: List[SimButton] = field(default_factory=list)
    forms: List[SimForm] = field(default_factory=list)

    def button(self, label: str) -> Optional[SimButton]:
        return next((b for b in self.buttons if b.label == label), None)


@dataclass
class SimFile:
    name: str = ""
    content: str = ""
    deleted: bool = False


class Simulator:
    """The §26 virtual computer (deterministic)."""

    def __init__(self) -> None:
        self.windows: List[SimWindow] = []
        self.tabs: List[SimPage] = []
        self.active_tab: int = -1
        self.files: Dict[str, SimFile] = {}
        self.clipboard: str = ""
        self.log: List[str] = []
        self.crashed_apps: set = set()
        self.fail_mode: Optional[str] = None     # §27 injection hook

    # ── construction helpers ────────────────────────────────────────────

    def add_window(self, title: str, app: str = "app",
                   buttons: Optional[List[str]] = None,
                   text: str = "", focus: bool = False) -> SimWindow:
        if len(self.windows) >= MAX_WINDOWS:
            self.windows.pop(0)
        win = SimWindow(title=title[:MAX_NAME], app=app[:MAX_NAME],
                        text=text[:2000],
                        buttons=[SimButton(label=b) for b in
                                 (buttons or [])[:32]])
        self.windows.append(win)
        if focus or len(self.windows) == 1:
            self.focus_window(title)
        self.log.append(f"window+ {title}")
        return win

    def focus_window(self, title: str) -> bool:
        found = False
        for w in self.windows:
            w.focused = (w.title == title and w.visible)
            found = found or w.focused
        if found:
            self.log.append(f"focus {title}")
        return found

    def close_window(self, title: str) -> bool:
        for i, w in enumerate(self.windows):
            if w.title == title:
                w.visible = False
                w.focused = False
                self.log.append(f"window- {title}")
                return True
        return False

    def add_tab(self, url: str, title: str,
                buttons: Optional[List[str]] = None,
                forms: Optional[List[str]] = None,
                text: str = "") -> SimPage:
        if len(self.tabs) >= MAX_TABS:
            self.tabs.pop(0)
            if self.active_tab > 0:
                self.active_tab -= 1
        page = SimPage(url=url[:MAX_NAME], title=title[:MAX_NAME],
                       text=text[:2000],
                       buttons=[SimButton(label=b) for b in
                                (buttons or [])[:32]],
                       forms=[SimForm(fields={f: ""}) for f in
                              (forms or [])[:12]])
        self.tabs.append(page)
        self.active_tab = len(self.tabs) - 1
        self.log.append(f"tab+ {url}")
        return page

    def navigate(self, url: str, title: str = "") -> bool:
        """Navigate the ACTIVE tab (or open the first one)."""
        if self.fail_mode == "network":
            self.log.append(f"navigate FAILED(network) {url}")
            return False
        if not self.tabs:
            self.add_tab(url, title or url)
            return True
        page = self.tabs[self.active_tab]
        page.url = url[:MAX_NAME]
        if title:
            page.title = title[:MAX_NAME]
        self.log.append(f"navigate {url}")
        return True

    def switch_tab(self, index: int) -> bool:
        if 0 <= index < len(self.tabs):
            self.active_tab = index
            self.log.append(f"tab switch {index}")
            return True
        return False

    def click_button(self, label: str) -> bool:
        """Click a visible button on the focused window OR active tab."""
        if self.fail_mode == "clicks":
            self.log.append(f"click FAILED(injected) {label}")
            return False
        surface = self._active_surface()
        if surface is None:
            return False
        btn = surface.button(label)
        if btn is None or not btn.visible:
            self.log.append(f"click MISS {label}")
            return False
        btn.clicked = True
        self.log.append(f"click {label}")
        return True

    def type_text(self, target_field: str, text: str) -> bool:
        surface = self._active_surface()
        if surface is None:
            return False
        for form in surface.forms:
            if target_field in form.fields:
                form.fields[target_field] = text[:MAX_CLIP]
                self.log.append(f"type {target_field}")
                return True
        self.log.append(f"type MISS {target_field}")
        return False

    # ── files / clipboard ───────────────────────────────────────────────

    def write_file(self, name: str, content: str) -> bool:
        if len(self.files) >= MAX_FILES and name not in self.files:
            return False
        self.files[name[:MAX_NAME]] = SimFile(name=name[:MAX_NAME],
                                              content=content[:20000])
        self.log.append(f"file+ {name}")
        return True

    def read_file(self, name: str) -> Optional[str]:
        f = self.files.get(name)
        if f is None or f.deleted:
            return None
        self.log.append(f"file read {name}")
        return f.content

    def delete_file(self, name: str) -> bool:
        f = self.files.get(name)
        if f is None:
            return False
        f.deleted = True
        self.log.append(f"file- {name}")
        return True

    def set_clipboard(self, text: str) -> None:
        self.clipboard = str(text)[:MAX_CLIP]
        self.log.append("clip set")

    def copy_selection(self) -> str:
        surface = self._active_surface()
        text = getattr(surface, "text", "") if surface else ""
        self.clipboard = text[:MAX_CLIP]
        self.log.append("clip copy")
        return self.clipboard

    def paste(self, field: str) -> bool:
        return self.type_text(field, self.clipboard)

    # ── failure + UI-change simulation (§26/§27) ────────────────────────

    def inject_failure(self, mode: str) -> None:
        """Modes: network | clicks | crash:<app> | hide:<button> |
        move:<button> | close:<window> | stale_dom | ocr_fail |
        a11y_fail | permission | timeout | conflict | malformed."""
        self.fail_mode = str(mode)[:40]
        if mode.startswith("crash:"):
            self.crashed_apps.add(mode.split(":", 1)[1])
        if mode.startswith("hide:"):
            btn = self._find_button(mode.split(":", 1)[1])
            if btn:
                btn.visible = False
        if mode.startswith("close:"):
            self.close_window(mode.split(":", 1)[1])
        self.log.append(f"inject {mode}")

    def clear_failures(self) -> None:
        self.fail_mode = None
        self.crashed_apps.clear()

    def change_ui(self, old_label: str, new_label: str) -> bool:
        """§26 'UI changes': relabel a button (targets must re-resolve)."""
        btn = self._find_button(old_label)
        if btn is None:
            return False
        btn.label = new_label[:MAX_NAME]
        self.log.append(f"ui change {old_label}->{new_label}")
        return True

    # ── observation (§9 OBSERVE over the simulated world) ───────────────

    def observe(self) -> Dict[str, Any]:
        focused = next((w for w in self.windows if w.focused and w.visible),
                       None)
        page = self.tabs[self.active_tab] if 0 <= self.active_tab < \
            len(self.tabs) else None
        return {
            "active_application": focused.app if focused else
                (page and "browser" or "desktop"),
            "active_window": focused.title if focused else
                (page.title if page else ""),
            "browser": "sim-browser" if page else "",
            "tabs": [p.title for p in self.tabs],
            "visible_ui_targets": [b.label for b in
                                   (focused.buttons if focused else
                                    page.buttons if page else [])
                                   if b.visible],
            "files": sorted(self.files),
            "clipboard_len": len(self.clipboard),
            "sensor_health": "ok",
        }

    # ── verification helper ─────────────────────────────────────────────

    def verify(self, expectation: Dict[str, Any]) -> Tuple[bool, str]:
        """Deterministic expectation check, e.g.
        {"button_clicked": "Send"} / {"file_exists": "a.txt"} /
        {"field_equals": ("name", "Ada")} / {"url": "https://…"}."""
        for kind, arg in expectation.items():
            if kind == "button_clicked":
                btn = self._find_button(arg)
                if btn is None or not btn.clicked:
                    return False, f"button not clicked: {arg}"
            elif kind == "file_exists":
                f = self.files.get(arg)
                if f is None or f.deleted:
                    return False, f"file missing: {arg}"
            elif kind == "field_equals":
                name, value = arg
                surface = self._active_surface()
                got = None
                if surface:
                    for form in surface.forms:
                        if name in form.fields:
                            got = form.fields[name]
                if got != value:
                    return False, f"field {name} != expected"
            elif kind == "url":
                page = self.tabs[self.active_tab] if self.tabs else None
                if page is None or page.url != arg:
                    return False, f"url != {arg}"
            elif kind == "window_visible":
                w = next((w for w in self.windows if w.title == arg), None)
                if w is None or not w.visible:
                    return False, f"window not visible: {arg}"
        return True, "all expectations met"

    # ── internals ────────────────────────────────────────────────────────

    def _active_surface(self):
        focused = next((w for w in self.windows if w.focused and w.visible),
                       None)
        if focused is not None:
            return focused
        if 0 <= self.active_tab < len(self.tabs):
            return self.tabs[self.active_tab]
        return None

    def _find_button(self, label: str) -> Optional[SimButton]:
        for surface in list(self.windows) + list(self.tabs):
            btn = surface.button(label)
            if btn is not None:
                return btn
        return None
