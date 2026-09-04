"""
airmouse.browser — v10 Semantic Browser Control 🌐
===================================================

Mission §11–§13: the local browser-control system, built in three layers::

    §11  transport        BrowserBridge (protocol)
                          ├─ SimulatedBrowserBridge   deterministic scripted state
                          └─ CDPBrowserBridge         Chrome/Edge DevTools Protocol
                                                      (urllib + raw socket, 0.5 s)
    §12  semantics        BrowserTargetMapper + SemanticBrowserResolver
                          "click the login button" → a concrete BrowserElement
    §13  verification     BrowserActionVerifier (before/after state diff)

Design rules
------------
1.  **Pure stdlib, headless.**  Only urllib + a minimal RFC 6455 websocket
    client for the (optional) CDP transport.  No cv2 / mediapipe / pynput
    at import time; the module imports with networking disabled.
2.  **Page content is NEVER trusted.**  Every element carries
    ``untrusted=True``; text collected from a page is DATA used for
    matching, never a command.  The resolver only matches its own fixed
    template grammar — an utterance that originates from page text can
    never become anything other than the literal action the USER asked
    for.  The CDP adapter evaluates only FIXED JavaScript snippets built
    from OUR parameters (JSON-encoded numbers/strings) — never page strings.
3.  **Never raises, never blocks.**  Bridge methods return False / None on
    failure; every network call has a timeout and is wrapped in try/except.
4.  **Deterministic.**  Every relevant method accepts an explicit ``now``
    timestamp; the simulated bridge is a scripted state machine for tests
    and offline demos (works with networking fully disabled).

Quick usage
-----------

    from airmouse.browser import (BrowserController, SimulatedBrowserBridge,
                                  SemanticBrowserResolver, BrowserTargetMapper)

    ctrl = BrowserController(config={"enabled": True}, bridge=SimulatedBrowserBridge())
    ctrl.start()
    state = ctrl.poll(now=1.0)                    # updates mapper + bus + context
    resolver = SemanticBrowserResolver(ctrl.mapper)
    res = resolver.resolve("click the login button")
    out = ctrl.execute(res, now=1.1)              # {"status": "executed", ...}

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import logging
import os
import re
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

try:  # package-relative (normal import path)
    from .interfaces import (Event, EventKind, Modality, ScreenTarget,
                             ScreenTargetType, now_ts)
except ImportError:  # pragma: no cover - direct file execution fallback
    from airmouse.interfaces import (Event, EventKind, Modality, ScreenTarget,
                                     ScreenTargetType, now_ts)

logger = logging.getLogger("airmouse.browser")

__all__ = [
    "BrowserElement", "BrowserState", "BrowserBridge",
    "SimulatedBrowserBridge", "CDPBrowserBridge",
    "element_to_screen_target", "BrowserTargetMapper",
    "BrowserResolution", "SemanticBrowserResolver",
    "BrowserActionVerifier", "BrowserController",
    "BROWSER_ACTIONS",
    "discover_browser_executable", "launch_browser",
    "pinned_ws_parts",
]


# ═════════════════════════════════════════════════════════════════════════════
# Data model (§11)
# ═════════════════════════════════════════════════════════════════════════════


@dataclass
class BrowserElement:
    """One interactive element collected from a page (§11).

    ``bbox`` is ``(x, y, w, h)`` — NORMALIZED viewport space 0..1 when
    ``is_px`` is False (the default, what the extension reports) or
    absolute pixels when ``is_px`` is True.

    ``untrusted`` is ALWAYS True for anything derived from page content:
    page text is data for deterministic matching, never a command.
    """

    id: str
    role: str = "text"             # button | link | input | tab | heading | text
    text: str = ""
    tag: str = ""
    bbox: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    is_px: bool = False
    actionable: bool = True
    value: str = ""                # input value (masked upstream for passwords)
    href: str = ""
    confidence: float = 1.0
    untrusted: bool = True
    browser: str = ""              # owning browser name ("" → generic "browser")


@dataclass
class BrowserState:
    """A full snapshot of the browser as reported by a bridge (§11)."""

    browser: str = "chrome"
    tabs: List[Dict[str, Any]] = field(default_factory=list)
    active_tab_id: str = ""
    url: str = ""
    title: str = ""
    focused_element_id: str = ""
    elements: List[BrowserElement] = field(default_factory=list)
    timestamp: float = field(default_factory=now_ts)

    def active_tab(self) -> Dict[str, Any]:
        """The active tab dict ({} when tabs are unknown).  Never raises."""
        try:
            for tab in self.tabs:
                if isinstance(tab, dict) and str(tab.get("id", "")) == \
                        str(self.active_tab_id):
                    return dict(tab)
            if self.tabs and isinstance(self.tabs[0], dict):
                return dict(self.tabs[0])
            return {}
        except Exception:
            return {}


class BrowserBridge:
    """PROTOCOL for browser transports (§11).

    Implementations MUST be non-blocking when the browser is unavailable
    and MUST never raise from public methods — failures are reported as
    ``False`` / ``None``.  The base class only fixes the shape.
    """

    def available(self) -> bool:
        """True when the transport can talk to a browser right now."""
        raise NotImplementedError

    def poll_state(self, now: Optional[float] = None) -> Optional[BrowserState]:
        """Latest BrowserState snapshot, or None when unavailable."""
        raise NotImplementedError

    def click_element(self, element_id: str) -> bool:
        raise NotImplementedError

    def focus_element(self, element_id: str) -> bool:
        raise NotImplementedError

    def type_text(self, text: str) -> bool:
        raise NotImplementedError

    def navigate(self, url: str) -> bool:
        raise NotImplementedError

    def new_tab(self) -> bool:
        raise NotImplementedError

    def close_tab(self, tab_id: str) -> bool:
        raise NotImplementedError

    def switch_tab(self, tab_id: str) -> bool:
        raise NotImplementedError

    def scroll(self, amount: float) -> bool:
        raise NotImplementedError

    def go_back(self) -> bool:
        raise NotImplementedError

    def go_forward(self) -> bool:
        raise NotImplementedError

    def refresh(self) -> bool:
        raise NotImplementedError


# ═════════════════════════════════════════════════════════════════════════════
# Simulated bridge (§11) — deterministic test double
# ═════════════════════════════════════════════════════════════════════════════


def _default_demo_state(now: Optional[float] = None) -> BrowserState:
    """The scripted demo page used when no initial state is given."""
    elements = [
        BrowserElement(id="input-search", role="input", text="Search",
                       tag="input", bbox=(0.05, 0.06, 0.30, 0.05),
                       actionable=True, value=""),
        BrowserElement(id="btn-login", role="button", text="Login",
                       tag="button", bbox=(0.05, 0.16, 0.12, 0.06),
                       actionable=True),
        BrowserElement(id="btn-signup", role="button", text="Sign Up",
                       tag="button", bbox=(0.20, 0.16, 0.12, 0.06),
                       actionable=True),
        BrowserElement(id="link-downloads", role="link", text="Downloads",
                       tag="a", bbox=(0.05, 0.28, 0.15, 0.04),
                       actionable=True, href="/downloads"),
        BrowserElement(id="link-home", role="link", text="Home",
                       tag="a", bbox=(0.05, 0.36, 0.10, 0.04),
                       actionable=True, href="/home"),
        BrowserElement(id="link-youtube", role="link", text="YouTube",
                       tag="a", bbox=(0.05, 0.44, 0.13, 0.04),
                       actionable=True, href="https://www.youtube.com/"),
    ]
    tabs = [
        {"id": "tab-1", "title": "Demo Portal - AirMouse Test Page",
         "url": "https://demo.airmouse.local/home"},
        {"id": "tab-2", "title": "YouTube", "url": "https://www.youtube.com/"},
    ]
    return BrowserState(browser="chrome", tabs=tabs, active_tab_id="tab-1",
                        url="https://demo.airmouse.local/home",
                        title="Demo Portal - AirMouse Test Page",
                        focused_element_id="", elements=elements,
                        timestamp=float(now) if now is not None else now_ts())


class SimulatedBrowserBridge(BrowserBridge):
    """Deterministic scripted browser (§11) for tests and offline demos.

    State changes are observable for verification: every action records
    ``last_action`` and appends to ``action_history``; clicks append to
    ``clicked_elements``; link clicks navigate (url changes); navigation
    keeps a real back/forward history.
    """

    def __init__(self, state: Optional[BrowserState] = None,
                 now: Optional[float] = None) -> None:
        self._state: BrowserState = (
            copy.deepcopy(state) if state is not None
            else _default_demo_state(now=now))
        self.clicked_elements: List[str] = []
        self.action_history: List[Dict[str, Any]] = []
        self.last_action: Dict[str, Any] = {}
        self.refresh_count: int = 0
        self.scroll_position: float = 0.0
        self._tab_seq = max([1] + [
            self._tab_number(t.get("id", ""))
            for t in self._state.tabs if isinstance(t, dict)])
        url = str(self._state.url or "")
        self._url_history: List[str] = [url]
        self._url_pos: int = 0
        # per-tab navigation history + previous-active tracking (real
        # browser semantics: closing a tab restores the previous tab)
        self._tab_hist: Dict[str, Tuple[List[str], int]] = {}
        self._prev_active_tab_id: str = ""
        self._tab_hist[str(self._state.active_tab_id or "")] = \
            (list(self._url_history), self._url_pos)

    @staticmethod
    def _tab_number(tab_id: Any) -> int:
        try:
            return int(str(tab_id or "").replace("tab-", ""))
        except Exception:
            return 0

    # -- helpers ---------------------------------------------------------------

    def _record(self, action_type: str, ok: bool, **extra: Any) -> None:
        entry: Dict[str, Any] = {"type": action_type, "ok": bool(ok)}
        entry.update(extra)
        self.last_action = entry
        self.action_history.append(dict(entry))

    def _find_element(self, element_id: str) -> Optional[BrowserElement]:
        eid = str(element_id or "")
        for el in self._state.elements:
            if el.id == eid:
                return el
        return None

    def _sync_active_tab(self) -> None:
        """Mirror url/title into the active tab dict (consistency)."""
        for tab in self._state.tabs:
            if isinstance(tab, dict) and \
                    str(tab.get("id", "")) == str(self._state.active_tab_id):
                tab["url"] = str(self._state.url or "")
                tab["title"] = str(self._state.title or "")
                return

    def _push_url(self, url: str) -> None:
        url = str(url or "")
        self._url_history = self._url_history[:self._url_pos + 1] + [url]
        self._url_pos = len(self._url_history) - 1
        self._state.url = url
        self._sync_active_tab()
        self._save_tab_history()

    def _set_url_by_history(self, pos: int) -> None:
        self._url_pos = pos
        self._state.url = str(self._url_history[pos] or "")
        self._sync_active_tab()
        self._save_tab_history()

    def _save_tab_history(self) -> None:
        """Persist the active tab's (history, position) for later restore."""
        try:
            aid = str(self._state.active_tab_id or "")
            if aid:
                self._tab_hist[aid] = (list(self._url_history),
                                       int(self._url_pos))
        except Exception:
            pass

    # -- test helpers -----------------------------------------------------------

    def push_element(self, element: BrowserElement) -> None:
        """Append an element to the simulated page (tests)."""
        try:
            if isinstance(element, BrowserElement):
                self._state.elements.append(element)
        except Exception:
            pass

    def push_tab(self, tab: Dict[str, Any],
                 activate: bool = False) -> None:
        """Append a tab dict {'id','title','url'} (tests)."""
        try:
            tab = dict(tab)
            self._tab_seq += 1
            tab.setdefault("id", f"tab-{self._tab_seq}")
            self._state.tabs.append(tab)
            if activate:
                self.set_active_tab(str(tab["id"]))
        except Exception:
            pass

    def set_active_tab(self, tab_id: str) -> None:
        """Directly activate a tab WITHOUT recording an action (tests).

        Saves the outgoing tab's navigation history and loads the
        incoming tab's own history (fresh tabs start at their url).
        """
        try:
            tid = str(tab_id or "")
            for tab in self._state.tabs:
                if isinstance(tab, dict) and str(tab.get("id", "")) == tid:
                    cur = str(self._state.active_tab_id or "")
                    if cur and cur != tid:
                        self._save_tab_history()
                        self._prev_active_tab_id = cur
                    self._state.active_tab_id = tid
                    self._state.url = str(tab.get("url", "") or "")
                    self._state.title = str(tab.get("title", "") or "")
                    hist, pos = self._tab_hist.get(tid,
                                                   ([self._state.url], 0))
                    if not hist:
                        hist, pos = [self._state.url], 0
                    self._url_history = list(hist)
                    self._url_pos = max(0, min(int(pos),
                                               len(self._url_history) - 1))
                    self._state.url = \
                        str(self._url_history[self._url_pos] or "") \
                        or self._state.url
                    return
        except Exception:
            pass

    # -- bridge protocol ----------------------------------------------------------

    def available(self) -> bool:
        return True

    def poll_state(self, now: Optional[float] = None) -> Optional[BrowserState]:
        try:
            snapshot = copy.deepcopy(self._state)
            snapshot.timestamp = (float(now) if now is not None else now_ts())
            return snapshot
        except Exception:
            return None

    def click_element(self, element_id: str) -> bool:
        try:
            el = self._find_element(element_id)
            if el is None:
                self._record("click", False, element_id=str(element_id or ""))
                return False
            self.clicked_elements.append(el.id)
            self._state.focused_element_id = el.id
            ok = True
            if el.role == "link" and el.href:
                base = str(self._state.url or "https://demo.airmouse.local/")
                self._push_url(urllib.parse.urljoin(base, el.href))
            self._record("click", ok, element_id=el.id)
            return ok
        except Exception:
            return False

    def focus_element(self, element_id: str) -> bool:
        try:
            el = self._find_element(element_id)
            if el is None:
                self._record("focus", False,
                             element_id=str(element_id or ""))
                return False
            self._state.focused_element_id = el.id
            self._record("focus", True, element_id=el.id)
            return True
        except Exception:
            return False

    def type_text(self, text: str) -> bool:
        try:
            text = str(text or "")
            el = self._find_element(self._state.focused_element_id)
            ok = el is not None and el.role == "input"
            if ok and el is not None:
                el.value = (el.value or "") + text
            self._record("type", ok, text=text)
            return ok
        except Exception:
            return False

    def navigate(self, url: str) -> bool:
        try:
            url = str(url or "").strip()
            if not url:
                self._record("navigate", False, url="")
                return False
            self._push_url(url)
            self._state.title = url
            self._sync_active_tab()
            self._record("navigate", True, url=url)
            return True
        except Exception:
            return False

    def new_tab(self) -> bool:
        try:
            self._tab_seq += 1
            tab = {"id": f"tab-{self._tab_seq}", "title": "New Tab",
                   "url": "about:blank"}
            self._state.tabs.append(tab)
            self.set_active_tab(tab["id"])
            self._record("new_tab", True, tab_id=tab["id"])
            return True
        except Exception:
            return False

    def close_tab(self, tab_id: str) -> bool:
        try:
            tid = str(tab_id or "")
            before = len(self._state.tabs)
            self._state.tabs = [
                t for t in self._state.tabs
                if not (isinstance(t, dict) and str(t.get("id", "")) == tid)]
            ok = len(self._state.tabs) < before
            if ok and str(self._state.active_tab_id) == tid:
                # real-browser semantics: prefer the previously active tab
                restore = ""
                prev = str(self._prev_active_tab_id or "")
                for tab in self._state.tabs:
                    if isinstance(tab, dict) and \
                            str(tab.get("id", "")) == prev:
                        restore = prev
                        break
                if not restore and self._state.tabs and \
                        isinstance(self._state.tabs[-1], dict):
                    restore = str(self._state.tabs[-1].get("id", ""))
                if restore:
                    self.set_active_tab(restore)
                else:
                    self._state.active_tab_id = ""
                    self._state.url = ""
                    self._state.title = ""
            self._record("close_tab", ok, tab_id=tid)
            return ok
        except Exception:
            return False

    def switch_tab(self, tab_id: str) -> bool:
        try:
            tid = str(tab_id or "")
            for tab in self._state.tabs:
                if isinstance(tab, dict) and str(tab.get("id", "")) == tid:
                    self.set_active_tab(tid)
                    self._record("switch_tab", True, tab_id=tid)
                    return True
            self._record("switch_tab", False, tab_id=tid)
            return False
        except Exception:
            return False

    def scroll(self, amount: float) -> bool:
        try:
            amount = float(amount)
            self.scroll_position = max(0.0, self.scroll_position + amount)
            self._record("scroll", True, amount=amount,
                         position=self.scroll_position)
            return True
        except Exception:
            return False

    def go_back(self) -> bool:
        try:
            if self._url_pos > 0:
                self._set_url_by_history(self._url_pos - 1)
                self._record("back", True, url=self._state.url)
                return True
            self._record("back", False)
            return False
        except Exception:
            return False

    def go_forward(self) -> bool:
        try:
            if self._url_pos + 1 < len(self._url_history):
                self._set_url_by_history(self._url_pos + 1)
                self._record("forward", True, url=self._state.url)
                return True
            self._record("forward", False)
            return False
        except Exception:
            return False

    def refresh(self) -> bool:
        try:
            self.refresh_count += 1
            self._record("refresh", True, count=self.refresh_count)
            return True
        except Exception:
            return False


# ═════════════════════════════════════════════════════════════════════════════
# CDP bridge (§11) — real Chrome/Edge DevTools Protocol adapter
# ═════════════════════════════════════════════════════════════════════════════

# FIXED JavaScript snippets — the ONLY code ever evaluated by this bridge.
# Placeholders are filled exclusively with OUR parameters (integers and
# JSON-encoded strings).  Page content NEVER flows into an expression.

_CDP_COLLECT_JS = (
    "(() => {"
    "const vw = window.innerWidth || document.documentElement.clientWidth || 1;"
    "const vh = window.innerHeight || document.documentElement.clientHeight || 1;"
    "const sel = 'a[href],button,input,select,textarea,[role=button],"
    "[role=link],[role=tab],[role=heading],h1,h2,h3';"
    "const nodes = Array.from(document.querySelectorAll(sel)).slice(0, 200);"
    "const out = nodes.map((e, i) => {"
    "const r = e.getBoundingClientRect();"
    "const tag = (e.tagName || '').toLowerCase();"
    "let role = tag === 'button' ? 'button' : tag === 'a' ? 'link' :"
    "(tag === 'input' || tag === 'textarea' || tag === 'select') ? 'input' :"
    "/^h[1-6]$/.test(tag) ? 'heading' : 'text';"
    "const ar = (e.getAttribute('role') || '').toLowerCase();"
    "if (ar === 'button' || ar === 'link' || ar === 'tab' || ar === 'heading')"
    " role = ar;"
    "const txt = ((e.innerText || e.value || e.getAttribute('aria-label') || '')"
    " + '').trim().slice(0, 120);"
    "const isPw = tag === 'input' && (e.type || '') === 'password';"
    "return {id: 'cdp-' + i, role: role, text: txt, tag: tag,"
    "bbox: [Math.max(0, r.left / vw), Math.max(0, r.top / vh),"
    "Math.max(0, r.width / vw), Math.max(0, r.height / vh)],"
    "actionable: !!(tag === 'a' || tag === 'button' || tag === 'input' ||"
    "tag === 'select' || tag === 'textarea' || ar),"
    "value: isPw ? '' : ((e.value || '') + '').slice(0, 120),"
    "href: ((e.getAttribute && e.getAttribute('href')) || '').slice(0, 300)};"
    "});"
    "const active = document.activeElement;"
    "let focused = '';"
    "if (active) { const idx = nodes.indexOf(active);"
    "if (idx >= 0) focused = 'cdp-' + idx; }"
    "return JSON.stringify({elements: out, focused: focused});"
    "})()"
)

_CDP_CLICK_JS = (
    "(() => {"
    "const sel = 'a[href],button,input,select,textarea,[role=button],"
    "[role=link],[role=tab],[role=heading],h1,h2,h3';"
    "const e = document.querySelectorAll(sel)[IDX];"
    "if (!e) return false;"
    "try { e.scrollIntoView({block: 'center'}); } catch (err) {}"
    "e.click();"
    "return true;"
    "})()"
)

_CDP_FOCUS_JS = (
    "(() => {"
    "const sel = 'a[href],button,input,select,textarea,[role=button],"
    "[role=link],[role=tab],[role=heading],h1,h2,h3';"
    "const e = document.querySelectorAll(sel)[IDX];"
    "if (!e) return false;"
    "if (typeof e.focus === 'function') e.focus();"
    "return document.activeElement === e;"
    "})()"
)

_CDP_TYPE_JS = (
    "(() => {"
    "const e = document.activeElement;"
    "if (!e) return false;"
    "const v = TEXT;"
    "if ('value' in e) {"
    "e.value += v;"
    "try { e.dispatchEvent(new Event('input', {bubbles: true})); }"
    "catch (err) {}"
    "return true;"
    "}"
    "return false;"
    "})()"
)

_CDP_SCROLL_JS = "window.scrollBy(0, AMOUNT); true"
_CDP_BACK_JS = "history.back(); true"
_CDP_FORWARD_JS = "history.forward(); true"


class _CdpWebSocket:
    """Minimal RFC 6455 client for DevTools websockets (stdlib sockets).

    Used only to deliver FIXED CDP commands built from our parameters.
    Every failure surfaces as an Exception which callers catch — nothing
    here ever escapes :class:`CDPBrowserBridge`.
    """

    _GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

    def __init__(self, host: str, port: int, path: str,
                 timeout: float = 0.5) -> None:
        self._sock = socket.create_connection((host, int(port)),
                                              timeout=timeout)
        self._sock.settimeout(timeout)
        self._buf = b""
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self._sock.sendall(request.encode("utf-8"))
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise IOError("websocket handshake EOF")
            head += chunk
        head, _, rest = head.partition(b"\r\n\r\n")
        status = head.split(b"\r\n", 1)[0]
        if b" 101 " not in status:
            raise IOError("websocket upgrade refused")
        expect = base64.b64encode(
            hashlib.sha1((key + self._GUID).encode("ascii")).digest()
        ).decode("ascii")
        headers = head.decode("latin-1", "replace").lower()
        if expect.lower() not in headers:
            # Accept-key mismatch → not a real DevTools websocket.
            raise IOError("websocket accept key mismatch")
        self._buf = rest
        self._msg_seq = 0

    # -- framing ---------------------------------------------------------------

    def _read_exact(self, n: int) -> bytes:
        buf = self._buf
        while len(buf) < n:
            chunk = self._sock.recv(min(65536, n - len(buf)))
            if not chunk:
                raise IOError("websocket EOF")
            buf += chunk
        self._buf = buf[n:]
        return buf[:n]

    def _send_frame(self, opcode: int, data: bytes) -> None:
        mask = os.urandom(4)
        header = bytearray([0x80 | opcode])
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self._sock.sendall(bytes(header) + mask + masked)

    def _recv_frame(self) -> Tuple[int, bytes]:
        head = self._read_exact(2)
        opcode = head[0] & 0x0F
        length = head[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._read_exact(8))[0]
        payload = self._read_exact(length) if length else b""
        return opcode, payload

    # -- messages ----------------------------------------------------------------

    def call(self, method: str, params: Optional[Dict[str, Any]] = None,
             timeout: float = 1.0) -> Optional[Dict[str, Any]]:
        """Send one CDP command and wait (bounded) for its response."""
        self._msg_seq += 1
        msg_id = self._msg_seq
        self._send_frame(0x1, json.dumps(
            {"id": msg_id, "method": method, "params": dict(params or {})}
        ).encode("utf-8"))
        deadline = time.monotonic() + max(0.05, float(timeout))
        while time.monotonic() < deadline:
            try:
                opcode, payload = self._recv_frame()
            except socket.timeout:
                break
            except OSError:
                break
            if opcode == 0x8:
                raise IOError("websocket closed by peer")
            if opcode == 0x9:               # ping → pong
                self._send_frame(0xA, payload)
                continue
            if opcode not in (0x1, 0x2):
                continue
            try:
                msg = json.loads(payload.decode("utf-8", "replace"))
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("id") == msg_id:
                return msg
        return None

    def close(self) -> None:
        try:
            self._sock.close()
        except Exception:
            pass


class CDPBrowserBridge(BrowserBridge):
    """Real adapter over the Chrome/Edge DevTools Protocol (§11).

    Transport: HTTP ``http://127.0.0.1:<port>/json`` via urllib (0.5 s
    timeout) plus a minimal stdlib websocket client for commands that
    require a debugging session (Runtime.evaluate, Input.dispatchMouseEvent,
    Page.navigate / Page.reload).

    Security: only FIXED JS snippets are evaluated, filled from OUR
    parameters (integers, JSON-encoded strings).  Strings that came from
    page content are NEVER evaluated.  Every public method is wrapped and
    returns False / None on any failure.
    """

    def __init__(self, port: int = 9222, host: str = "127.0.0.1",
                 timeout: float = 0.5, offline: bool = False,
                 browser: str = "chrome") -> None:
        self._port = int(port)
        # HARD RULE: the DevTools discovery endpoint is only ever fetched on
        # loopback (§11 security).  A non-loopback host is coerced to
        # 127.0.0.1 with a logged reason — never contacted.
        self._host = _pin_loopback_host(host)
        self._timeout = max(0.1, float(timeout))
        self._offline = bool(offline)
        self._browser_name = str(browser or "chrome")
        self._avail_cached = False
        self._avail_until = 0.0
        self._tab_ws: Dict[str, str] = {}
        self._ws_cache: Dict[str, _CdpWebSocket] = {}
        self._last_elements: List[BrowserElement] = []
        self._element_index: Dict[str, int] = {}
        self._last_active_id: str = ""
        self.last_action: Dict[str, Any] = {}
        self.action_history: List[Dict[str, Any]] = []

    # -- plumbing ----------------------------------------------------------------

    def _http_json(self, path: str, method: str = "GET") -> Optional[Any]:
        try:
            # discovery fetch is pinned to loopback — hard rule, not a default
            url = (f"http://{_pin_loopback_host(self._host)}:"
                   f"{self._port}{path}")
            req = urllib.request.Request(url, method=method)
            req.add_header("Connection", "close")
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read(1_000_000)
            return json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            return None

    def _record(self, action_type: str, ok: bool, **extra: Any) -> None:
        entry: Dict[str, Any] = {"type": action_type, "ok": bool(ok)}
        entry.update(extra)
        self.last_action = entry
        self.action_history.append(dict(entry))

    @staticmethod
    def _safe_target_id(tab_id: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]", "", str(tab_id or ""))

    # -- websocket session -------------------------------------------------------

    def _drop_ws(self, tab_id: str) -> None:
        ws = self._ws_cache.pop(tab_id, None)
        if ws is not None:
            ws.close()

    def _refresh_tabs_http(self) -> List[Dict[str, Any]]:
        data = self._http_json("/json/list") or self._http_json("/json")
        if not isinstance(data, list):
            return []
        pages = [t for t in data
                 if isinstance(t, dict) and t.get("type") == "page"]
        self._tab_ws = {
            str(t.get("id", "")): str(t.get("webSocketDebuggerUrl", ""))
            for t in pages
            if t.get("id") and t.get("webSocketDebuggerUrl")}
        return [{"id": str(t.get("id", "")),
                 "title": str(t.get("title", "")),
                 "url": str(t.get("url", ""))} for t in pages]

    def _ws_for(self, tab_id: str) -> Optional[_CdpWebSocket]:
        ws = self._ws_cache.get(tab_id)
        if ws is not None:
            return ws
        ws_url = self._tab_ws.get(tab_id, "")
        if not ws_url:
            self._refresh_tabs_http()
            ws_url = self._tab_ws.get(tab_id, "")
        if not ws_url:
            return None
        # SECURITY: only connect when the reported host is loopback.  A
        # DevTools response claiming another host is not ours to drive —
        # refuse it with a logged reason instead of connecting.
        pinned = pinned_ws_parts(ws_url, self._host, self._port)
        if pinned is None:
            return None
        host, port, path = pinned
        try:
            ws = _CdpWebSocket(host, port, path, timeout=self._timeout)
        except Exception:
            return None
        self._ws_cache[tab_id] = ws
        return ws

    def _call_tab(self, tab_id: str, method: str,
                  params: Optional[Dict[str, Any]] = None,
                  wait: Optional[float] = None) -> Optional[Dict[str, Any]]:
        try:
            ws = self._ws_for(tab_id)
            if ws is None:
                return None
            resp = ws.call(method, params,
                           timeout=wait or max(0.5, self._timeout * 2))
            if resp is None:
                self._drop_ws(tab_id)
            return resp
        except Exception:
            self._drop_ws(tab_id)
            return None

    @staticmethod
    def _eval_value(resp: Optional[Dict[str, Any]]) -> Any:
        try:
            if not isinstance(resp, dict):
                return None
            result = resp.get("result")
            if not isinstance(result, dict):
                return None
            inner = result.get("result")
            if not isinstance(inner, dict):
                return None
            return inner.get("value")
        except Exception:
            return None

    def _eval_ok(self, tab_id: str, expression: str) -> bool:
        resp = self._call_tab(tab_id, "Runtime.evaluate",
                              {"expression": expression,
                               "returnByValue": True})
        return bool(self._eval_value(resp))

    def _active_tab_id(self) -> str:
        if self._last_active_id:
            return self._last_active_id
        tabs = self._refresh_tabs_http()
        return str(tabs[0]["id"]) if tabs else ""

    # -- bridge protocol ----------------------------------------------------------

    def available(self) -> bool:
        """False immediately when the offline gate is on, else True only
        when the /json endpoint answers (cached for 2 s to stay cheap)."""
        try:
            if self._offline:
                return False
            nowm = time.monotonic()
            if nowm < self._avail_until:
                return self._avail_cached
            reachable = (self._http_json("/json/list") is not None
                         or self._http_json("/json") is not None)
            self._avail_cached = bool(reachable)
            self._avail_until = nowm + 2.0
            return self._avail_cached
        except Exception:
            return False

    def poll_state(self, now: Optional[float] = None) -> Optional[BrowserState]:
        try:
            if not self.available():
                return None
            tabs = self._refresh_tabs_http()
            if not tabs:
                return None
            active = tabs[0]
            active_id = str(active.get("id", ""))
            self._last_active_id = active_id
            elements: List[BrowserElement] = []
            focused = ""
            self._element_index = {}
            collected = self._evaluate_collect(active_id)
            if isinstance(collected, dict):
                focused = str(collected.get("focused", "") or "")
                raw_list = collected.get("elements")
                if isinstance(raw_list, list):
                    for i, raw in enumerate(raw_list):
                        if not isinstance(raw, dict):
                            continue
                        el = BrowserElement(
                            id=str(raw.get("id", f"cdp-{i}")),
                            role=str(raw.get("role", "text")),
                            text=str(raw.get("text", ""))[:200],
                            tag=str(raw.get("tag", "")),
                            bbox=self._bbox4(raw.get("bbox")),
                            is_px=False,
                            actionable=bool(raw.get("actionable", True)),
                            value=str(raw.get("value", ""))[:120],
                            href=str(raw.get("href", ""))[:300],
                            confidence=0.8,
                            untrusted=True,
                            browser=self._browser_name)
                        elements.append(el)
                        self._element_index[el.id] = i
            self._last_elements = elements
            return BrowserState(browser=self._browser_name, tabs=tabs,
                                active_tab_id=active_id,
                                url=str(active.get("url", "")),
                                title=str(active.get("title", "")),
                                focused_element_id=focused,
                                elements=elements,
                                timestamp=float(now) if now is not None
                                else now_ts())
        except Exception:
            return None

    @staticmethod
    def _bbox4(raw: Any) -> Tuple[float, float, float, float]:
        try:
            if isinstance(raw, (list, tuple)) and len(raw) == 4:
                return (float(raw[0]), float(raw[1]),
                        float(raw[2]), float(raw[3]))
        except Exception:
            pass
        return (0.0, 0.0, 0.0, 0.0)

    def _evaluate_collect(self, tab_id: str) -> Optional[Dict[str, Any]]:
        try:
            resp = self._call_tab(tab_id, "Runtime.evaluate",
                                  {"expression": _CDP_COLLECT_JS,
                                   "returnByValue": True})
            value = self._eval_value(resp)
            if isinstance(value, str):
                value = json.loads(value)
            return value if isinstance(value, dict) else None
        except Exception:
            return None

    def _element_by_id(self, element_id: str) -> Optional[BrowserElement]:
        eid = str(element_id or "")
        for el in self._last_elements:
            if el.id == eid:
                return el
        return None

    # -- mutations (all best-effort, never raise) ---------------------------------

    def click_element(self, element_id: str) -> bool:
        try:
            idx = self._element_index.get(str(element_id or ""))
            tab = self._active_tab_id()
            ok = False
            if idx is not None and tab:
                ok = self._eval_ok(tab, _CDP_CLICK_JS.replace("IDX",
                                                              str(int(idx))))
            self._record("click", ok, element_id=str(element_id or ""))
            return ok
        except Exception:
            return False

    def focus_element(self, element_id: str) -> bool:
        try:
            idx = self._element_index.get(str(element_id or ""))
            tab = self._active_tab_id()
            ok = False
            if idx is not None and tab:
                ok = self._eval_ok(tab, _CDP_FOCUS_JS.replace("IDX",
                                                              str(int(idx))))
            self._record("focus", ok, element_id=str(element_id or ""))
            return ok
        except Exception:
            return False

    def type_text(self, text: str) -> bool:
        try:
            tab = self._active_tab_id()
            ok = False
            if tab:
                expr = _CDP_TYPE_JS.replace("TEXT",
                                            json.dumps(str(text or "")))
                ok = self._eval_ok(tab, expr)
            self._record("type", ok)
            return ok
        except Exception:
            return False

    def navigate(self, url: str) -> bool:
        try:
            url = str(url or "").strip()
            if not url:
                self._record("navigate", False, url="")
                return False
            ok = False
            tab = self._active_tab_id()
            if tab:
                resp = self._call_tab(tab, "Page.navigate", {"url": url})
                ok = isinstance(resp, dict)
            if not ok:  # HTTP fallback: open in a new tab (best effort)
                path = "/json/new?" + urllib.parse.quote(url, safe="")
                data = (self._http_json(path, method="PUT")
                        or self._http_json(path, method="GET"))
                ok = isinstance(data, dict)
            self._record("navigate", ok, url=url)
            return ok
        except Exception:
            return False

    def new_tab(self) -> bool:
        try:
            path = "/json/new?about:blank"
            data = (self._http_json(path, method="PUT")
                    or self._http_json(path, method="GET"))
            ok = isinstance(data, dict)
            self._record("new_tab", ok)
            return ok
        except Exception:
            return False

    def close_tab(self, tab_id: str) -> bool:
        try:
            safe = self._safe_target_id(tab_id)
            if not safe:
                self._record("close_tab", False, tab_id=str(tab_id or ""))
                return False
            data = (self._http_json(f"/json/close/{safe}", method="PUT")
                    or self._http_json(f"/json/close/{safe}", method="GET"))
            ok = data is not None
            self._record("close_tab", ok, tab_id=safe)
            return ok
        except Exception:
            return False

    def switch_tab(self, tab_id: str) -> bool:
        try:
            safe = self._safe_target_id(tab_id)
            if not safe:
                self._record("switch_tab", False, tab_id=str(tab_id or ""))
                return False
            data = (self._http_json(f"/json/activate/{safe}", method="PUT")
                    or self._http_json(f"/json/activate/{safe}",
                                       method="GET"))
            ok = data is not None
            self._record("switch_tab", ok, tab_id=safe)
            return ok
        except Exception:
            return False

    def scroll(self, amount: float) -> bool:
        try:
            amount = float(amount)
            ok = False
            tab = self._active_tab_id()
            if tab:
                resp = self._call_tab(tab, "Input.dispatchMouseEvent", {
                    "type": "mouseWheel", "x": 200, "y": 200,
                    "deltaX": 0, "deltaY": amount})
                ok = isinstance(resp, dict)
            if not ok and tab:
                ok = self._eval_ok(tab, _CDP_SCROLL_JS.replace(
                    "AMOUNT", json.dumps(amount)))
            self._record("scroll", ok, amount=amount)
            return ok
        except Exception:
            return False

    def go_back(self) -> bool:
        try:
            tab = self._active_tab_id()
            ok = bool(tab) and self._eval_ok(tab, _CDP_BACK_JS)
            self._record("back", ok)
            return ok
        except Exception:
            return False

    def go_forward(self) -> bool:
        try:
            tab = self._active_tab_id()
            ok = bool(tab) and self._eval_ok(tab, _CDP_FORWARD_JS)
            self._record("forward", ok)
            return ok
        except Exception:
            return False

    def refresh(self) -> bool:
        try:
            tab = self._active_tab_id()
            ok = False
            if tab:
                resp = self._call_tab(tab, "Page.reload",
                                      {"ignoreCache": False})
                ok = isinstance(resp, dict)
            self._record("refresh", ok)
            return ok
        except Exception:
            return False


# ═════════════════════════════════════════════════════════════════════════════
# Browser launcher + loopback pinning (§11 last mile)
# ═════════════════════════════════════════════════════════════════════════════

#: loopback hosts a DevTools endpoint may claim (anything else is refused)
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")

#: executable names probed with shutil.which, in discovery order
_BROWSER_EXE_CANDIDATES: Tuple[str, ...] = (
    "google-chrome", "google-chrome-stable", "chromium",
    "chromium-browser", "chrome", "msedge",
)


def _pin_loopback_host(host: Any) -> str:
    """Coerce a discovery host to loopback (hard rule, logged).

    ``127.0.0.1`` / ``localhost`` / ``::1`` pass through (normalized to
    ``127.0.0.1`` for HTTP); anything else is refused and replaced with
    ``127.0.0.1`` — a DevTools endpoint on a non-loopback host is never
    contacted, it is logged and ignored.
    """
    try:
        h = str(host or "127.0.0.1").strip().strip("[]").lower()
    except Exception:
        return "127.0.0.1"
    if h in ("127.0.0.1", "localhost", "::1", ""):
        return "127.0.0.1"
    logger.warning(
        "browser: pinning DevTools discovery host %r to 127.0.0.1 "
        "(non-loopback hosts are never contacted)", h)
    return "127.0.0.1"


def pinned_ws_parts(ws_url: str, default_host: str = "127.0.0.1",
                    default_port: int = 9222
                    ) -> Optional[Tuple[str, int, str]]:
    """Parse a DevTools ``webSocketDebuggerUrl`` PINNED to loopback.

    Returns ``(host, port, path)`` only when the URL's host is
    ``127.0.0.1`` or ``localhost`` (a missing host falls back to the
    pinned default).  Any other host — e.g. a hostile/misconfigured
    ``ws://evil.example.com:9222/...`` — yields ``None`` with a logged
    reason, and the caller must NOT connect.
    """
    try:
        parts = urllib.parse.urlsplit(str(ws_url or ""))
    except Exception:
        logger.warning("browser: refusing unparsable webSocketDebuggerUrl")
        return None
    scheme = (parts.scheme or "").strip().lower()
    if scheme not in ("ws", "wss"):
        logger.warning("browser: refusing webSocketDebuggerUrl with "
                       "non-ws scheme %r", scheme or "<none>")
        return None
    host = (parts.hostname or "").strip().strip("[]").lower()
    if not host:
        host = _pin_loopback_host(default_host)
    if host not in _LOOPBACK_HOSTS:
        logger.warning(
            "browser: refusing non-loopback webSocketDebuggerUrl host %r "
            "(only 127.0.0.1/localhost are accepted)", host)
        return None
    try:
        port = int(parts.port) if parts.port else int(default_port)
    except (TypeError, ValueError):
        logger.warning("browser: refusing webSocketDebuggerUrl with "
                       "invalid port %r", parts.port)
        return None
    return (host, port, parts.path or "/")


def _devtools_ready(port: int, timeout: float = 0.5) -> bool:
    """True when http://127.0.0.1:<port>/json/version answers (loopback)."""
    try:
        url = f"http://127.0.0.1:{int(port)}/json/version"
        req = urllib.request.Request(url)
        req.add_header("Connection", "close")
        with urllib.request.urlopen(req, timeout=max(0.1, float(timeout))) \
                as resp:
            data = json.loads(resp.read(65_536).decode("utf-8", "replace"))
        return isinstance(data, dict) and bool(data)
    except Exception:
        return False


def _windows_browser_paths() -> List[str]:
    """Typical Windows install paths (ProgramFiles envs, like §doctor)."""
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    lad = os.environ.get(
        "LocalAppData", os.path.join(os.path.expanduser("~"),
                                     "AppData", "Local"))
    out: List[str] = []
    for base in (pf, pf86, lad):
        out.append(os.path.join(base, "Google", "Chrome", "Application",
                                "chrome.exe"))
        out.append(os.path.join(base, "Microsoft", "Edge", "Application",
                                "msedge.exe"))
        out.append(os.path.join(base, "Chromium", "Application",
                                "chrome.exe"))
    return out


_MAC_BROWSER_PATHS: Tuple[str, ...] = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)


def discover_browser_executable(browser_path: Optional[str] = None) -> str:
    """Locate a Chrome/Chromium/Edge executable; '' when none is found.

    Discovery order: explicit ``browser_path`` (must exist on disk or be
    resolvable on PATH — an explicit-but-missing path is an honest
    failure, NOT silently replaced) → ``shutil.which`` over
    :data:`_BROWSER_EXE_CANDIDATES` → Windows typical install paths
    (ProgramFiles/ProgramFiles(x86)/LocalAppData envs) → macOS
    /Applications bundles.  Never raises.
    """
    try:
        if browser_path:
            p = str(browser_path)
            if os.path.isfile(p):
                return p
            w = shutil.which(p)
            if w and os.path.isfile(w):
                return w
            return ""       # explicit but missing → honest failure
        for name in _BROWSER_EXE_CANDIDATES:
            w = shutil.which(name)
            if w and os.path.isfile(w):
                return w
        if os.name == "nt":
            for p in _windows_browser_paths():
                if os.path.isfile(p):
                    return p
        elif os.name == "posix":
            import platform as _platform
            if _platform.system() == "Darwin":
                for p in _MAC_BROWSER_PATHS:
                    if os.path.isfile(p):
                        return p
    except Exception:
        return ""
    return ""


def launch_browser(port: int = 9222,
                   browser_path: Optional[str] = None,
                   user_data_dir: Optional[str] = None,
                   timeout_s: float = 10.0,
                   headless: bool = False) -> Dict[str, Any]:
    """Launch a local Chrome/Chromium/Edge with the DevTools port open.

    The last mile of §11: until now a user had to start the browser by
    hand with ``--remote-debugging-port``; this does it for them.

    Behaviour
    ---------
    - executable discovery via :func:`discover_browser_executable`
      (explicit path → PATH candidates → Windows/macOS well-known paths);
    - spawned with an argv list (NO shell): ``--remote-debugging-port``,
      ``--no-first-run``, ``--no-default-browser-check`` and a
      ``--user-data-dir`` that is ALWAYS an isolated throwaway profile
      under the temp dir unless the caller explicitly passes one — the
      user's real profile is never touched by default;
    - ``headless=True`` (tests/CI) appends ``--headless=new``; the default
      is a real, visible browser (this is for real control);
    - readiness = ``http://127.0.0.1:<port>/json/version`` answering
      within ``timeout_s`` (polled; loopback is a hard rule);
    - if something already answers on the port first, it is reused and
      nothing is spawned.

    Returns ``{"ok": bool, "port": int, "browser": <path or "">}`` plus
    ``"error"`` (str) on failure and ``"pid"`` (int) when we spawned a
    process.  Never raises.
    """
    result: Dict[str, Any] = {"ok": False, "port": int(port), "browser": ""}
    try:
        port = int(port)
    except (TypeError, ValueError):
        result["error"] = "invalid_port"
        return result
    if not (0 < port < 65536):
        result["error"] = "invalid_port"
        return result

    # Already-running DevTools endpoint on this port → reuse honestly
    # (the user may have started their own browser with
    # --remote-debugging-port; nothing is spawned then).
    if _devtools_ready(port, timeout=0.5):
        result["browser"] = discover_browser_executable()   # best-effort
        result["ok"] = True
        return result

    exe = discover_browser_executable(browser_path)
    if not exe:
        result["error"] = "browser_not_found"
        return result
    result["browser"] = exe

    if user_data_dir:
        profile = str(user_data_dir)
    else:
        # isolated throwaway profile — NEVER the user's real profile
        profile = os.path.join(tempfile.gettempdir(),
                               f"airmouse-browser-profile-{port}")
    argv = [
        exe,
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
        f"--user-data-dir={profile}",
    ]
    if headless:
        argv.append("--headless=new")
    try:
        proc = subprocess.Popen(
            argv, shell=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL)
        result["pid"] = int(proc.pid)
    except Exception as exc:
        result["error"] = f"launch_failed: {exc}"
        return result

    deadline = time.monotonic() + max(0.5, float(timeout_s))
    while time.monotonic() < deadline:
        if _devtools_ready(port, timeout=0.5):
            result["ok"] = True
            return result
        time.sleep(0.15)
    result["error"] = "devtools_not_ready"
    return result


# ═════════════════════════════════════════════════════════════════════════════
# Screen-target mapping (§12)
# ═════════════════════════════════════════════════════════════════════════════

_ROLE_TO_TARGET_TYPE = {
    "button": ScreenTargetType.BUTTON,
    "link": ScreenTargetType.LINK,
    "input": ScreenTargetType.TEXT_FIELD,
    "tab": ScreenTargetType.BROWSER_CONTROL,
}


def element_to_screen_target(el: BrowserElement,
                             screen_w: int = 1920,
                             screen_h: int = 1080) -> ScreenTarget:
    """Convert a :class:`BrowserElement` into the v7 ScreenTarget contract.

    Normalized bboxes are scaled to ``screen_w × screen_h`` pixels; bboxes
    flagged ``is_px`` pass through unchanged.  Never raises.
    """
    try:
        x, y, w, h = el.bbox
        if not el.is_px:
            x, y, w, h = (x * screen_w, y * screen_h,
                          w * screen_w, h * screen_h)
        return ScreenTarget(
            id=f"browser:{el.id}",
            type=_ROLE_TO_TARGET_TYPE.get(el.role, ScreenTargetType.UNKNOWN),
            bbox=(float(x), float(y), float(w), float(h)),
            text=str(el.text or ""),
            confidence=max(0.0, min(1.0, float(el.confidence))),
            application=str(el.browser or "browser"),
            actionable=bool(el.actionable),
            source="dom")
    except Exception:
        return ScreenTarget(id="browser:unknown", source="dom")


class BrowserTargetMapper:
    """Holds the latest :class:`BrowserState` and answers target queries.

    All lookups are deterministic (document order) and never raise.
    Element-level finders return :class:`BrowserElement`; the public
    ``find_by_*`` per the mission contract return :class:`ScreenTarget`.
    """

    def __init__(self, screen_w: int = 1920,
                 screen_h: int = 1080) -> None:
        self._screen_w = int(screen_w)
        self._screen_h = int(screen_h)
        self._state: Optional[BrowserState] = None
        self._targets: List[ScreenTarget] = []

    # -- state ------------------------------------------------------------------

    def update(self, state: Optional[BrowserState]) -> None:
        """Store the latest state and rebuild the ScreenTarget list."""
        try:
            self._state = state
            if state is None:
                self._targets = []
                return
            self._targets = [
                element_to_screen_target(el, self._screen_w, self._screen_h)
                for el in state.elements]
            if state.browser:
                for t in self._targets:
                    t.application = state.browser
        except Exception:
            self._state = state
            self._targets = []

    @property
    def state(self) -> Optional[BrowserState]:
        return self._state

    def targets(self) -> List[ScreenTarget]:
        return list(self._targets)

    # -- element-level lookups (used by the resolver) ----------------------------

    def find_element_by_text(self, needle: str) -> Optional[BrowserElement]:
        n = str(needle or "").strip().lower()
        if not n or self._state is None:
            return None
        for el in self._state.elements:
            if n in str(el.text or "").lower():
                return el
        return None

    def find_element_by_ordinal(self, n: int,
                                role: Optional[str] = None) -> Optional[BrowserElement]:
        """1-based ordinal among ACTIONABLE elements (optionally by role)."""
        if self._state is None:
            return None
        try:
            n = int(n)
        except Exception:
            return None
        if n < 1:
            return None
        role = str(role).lower() if role else None
        candidates = [el for el in self._state.elements
                      if el.actionable and (role is None or el.role == role)]
        if n > len(candidates):
            return None
        return candidates[n - 1]

    def find_element_by_role(self, role: str,
                             needle: str = "") -> Optional[BrowserElement]:
        if self._state is None:
            return None
        role = str(role or "").lower()
        needle = str(needle or "").strip().lower()
        for el in self._state.elements:
            if el.role != role:
                continue
            if needle and needle not in str(el.text or "").lower():
                continue
            return el
        return None

    def element_by_id(self, element_id: str) -> Optional[BrowserElement]:
        eid = str(element_id or "")
        if self._state is None:
            return None
        for el in self._state.elements:
            if el.id == eid:
                return el
        return None

    # -- ScreenTarget-level lookups (mission contract) ----------------------------

    @staticmethod
    def _target_of(el: Optional[BrowserElement]) -> Optional[ScreenTarget]:
        return element_to_screen_target(el) if el is not None else None

    def find_by_text(self, needle: str) -> Optional[ScreenTarget]:
        return self._target_of(self.find_element_by_text(needle))

    def find_by_ordinal(self, n: int,
                        role: Optional[str] = None) -> Optional[ScreenTarget]:
        return self._target_of(self.find_element_by_ordinal(n, role))

    def find_by_role(self, role: str,
                     needle: str = "") -> Optional[ScreenTarget]:
        return self._target_of(self.find_element_by_role(role, needle))


# ═════════════════════════════════════════════════════════════════════════════
# Semantic resolver (§12)
# ═════════════════════════════════════════════════════════════════════════════

BROWSER_ACTIONS = (
    "click", "focus", "type", "navigate", "new_tab", "close_tab",
    "switch_tab", "back", "forward", "refresh", "scroll", "search",
)

_ORDINAL_WORDS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}

_RE_SCROLL_TO = re.compile(r"scroll to (?:the )?(bottom|top)"
                           r"(?: of (?:the )?page)?$")
_RE_SCROLL_DIR = re.compile(r"scroll (down|up)(?: (?:the )?page)?$")
_RE_NEW_TAB = re.compile(r"(?:open (?:a |the )?)?new ?tab$")
_RE_CLOSE_TAB = re.compile(r"close (?:this |the |current |active )?tabs?$")
_RE_SWITCH_TAB = re.compile(r"switch to (.+)$")
_RE_BACK = re.compile(r"go back$")
_RE_FORWARD = re.compile(r"go forward$")
_RE_REFRESH = re.compile(r"(?:refresh|reload)(?: (?:the )?page)?$")
_RE_SEARCH = re.compile(r"search (?:for|the web for) (.+)$")
_RE_TYPE = re.compile(r"type (.+)$")
_RE_FOCUS_BOX = re.compile(r"focus (?:on )?(?:the )?(.+?) ?(?:box|field|input)$")
_RE_FOCUS = re.compile(r"focus (?:on )?(?:the )?(.+)$")
_RE_OPEN_LINK = re.compile(r"open (?:the )?(.+?) ?links?$")
_RE_CLICK_ORDINAL = re.compile(
    r"click (?:on )?(?:the )?(first|second|third|fourth|fifth|sixth|seventh"
    r"|eighth|ninth|tenth|\d+)(?:st|nd|rd|th)? ?"
    r"(button|link|input|tab|heading|element)?$")
_RE_CLICK_TEXT = re.compile(
    r"click (?:on )?(?:the )?(.+?)(?: (?:button|link|tab|box|field|element))?$")


@dataclass
class BrowserResolution:
    """Deterministic result of resolving one utterance (§12)."""

    matched: bool = False
    action: str = ""
    element: Optional[BrowserElement] = None
    params: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    text: str = ""


def _normalize_utterance(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace (deterministic)."""
    try:
        t = str(text or "").lower()
        t = re.sub(r"[^a-z0-9\s]", " ", t)
        return re.sub(r"\s+", " ", t).strip()
    except Exception:
        return ""


class SemanticBrowserResolver:
    """Resolves utterances against the browser target map (§12).

    ONLY the fixed template grammar below can ever match — a page whose
    text says "shut down the computer" cannot trigger anything: the
    resolver would need the USER to literally say "click shut down the
    computer", and even then the outcome is the single action ``click``
    on that element, never an interpretation of the page text.
    """

    def __init__(self, mapper: BrowserTargetMapper,
                 config: Optional[Dict[str, Any]] = None) -> None:
        self._mapper = mapper
        cfg = dict(config or {})
        self._max_query = int(cfg.get("max_query_len", 200))

    # -- helpers ---------------------------------------------------------------

    def _find_tab(self, needle: str) -> Optional[Dict[str, Any]]:
        state = self._mapper.state
        if state is None:
            return None
        needle = needle.strip().lower()
        if not needle:
            return None
        for tab in state.tabs:
            if not isinstance(tab, dict):
                continue
            hay = (str(tab.get("title", "")) + " " +
                   str(tab.get("url", ""))).lower()
            if needle in hay:
                return tab
        return None

    def _res(self, matched: bool, action: str,
             element: Optional[BrowserElement], params: Dict[str, Any],
             confidence: float, text: str) -> BrowserResolution:
        params = {k: str(v)[:self._max_query] if isinstance(v, str) else v
                  for k, v in dict(params or {}).items()}
        return BrowserResolution(matched=matched, action=action,
                                 element=element, params=params,
                                 confidence=max(0.0, min(1.0, confidence)),
                                 text=str(text or ""))

    # -- resolution ---------------------------------------------------------------

    def resolve(self, text: str,
                now: Optional[float] = None) -> BrowserResolution:
        """Resolve one utterance.  Never raises; unmatched → matched=False."""
        try:
            return self._resolve(text)
        except Exception:
            return BrowserResolution(matched=False, text=str(text or ""))

    def _resolve(self, text: str) -> BrowserResolution:
        raw = str(text or "")
        t = _normalize_utterance(raw)
        if not t:
            return BrowserResolution(matched=False, text=raw)

        # -- scroll ------------------------------------------------------------
        m = _RE_SCROLL_TO.fullmatch(t)
        if m:
            return self._res(True, "scroll", None,
                             {"direction": m.group(1)}, 1.0, raw)
        m = _RE_SCROLL_DIR.fullmatch(t)
        if m:
            return self._res(True, "scroll", None,
                             {"direction": m.group(1)}, 1.0, raw)

        # -- tabs --------------------------------------------------------------
        if _RE_NEW_TAB.fullmatch(t):
            return self._res(True, "new_tab", None, {}, 1.0, raw)
        m = _RE_CLOSE_TAB.fullmatch(t)
        if m:
            return self._res(True, "close_tab", None, {}, 1.0, raw)
        m = _RE_SWITCH_TAB.fullmatch(t)
        if m:
            needle = m.group(1).strip()
            tab = self._find_tab(needle)
            if tab is None:
                return self._res(False, "switch_tab", None,
                                 {"title": needle}, 0.0, raw)
            return self._res(True, "switch_tab", None,
                             {"title": needle,
                              "tab_id": str(tab.get("id", ""))}, 0.9, raw)

        # -- navigation ----------------------------------------------------------
        if _RE_BACK.fullmatch(t):
            return self._res(True, "back", None, {}, 1.0, raw)
        if _RE_FORWARD.fullmatch(t):
            return self._res(True, "forward", None, {}, 1.0, raw)
        if _RE_REFRESH.fullmatch(t):
            return self._res(True, "refresh", None, {}, 1.0, raw)

        # -- text input ------------------------------------------------------------
        m = _RE_SEARCH.fullmatch(t)
        if m:
            query = m.group(1).strip()
            if not query:
                return self._res(False, "search", None, {}, 0.0, raw)
            return self._res(True, "search", None, {"query": query}, 1.0, raw)
        m = _RE_TYPE.fullmatch(t)
        if m:
            payload = m.group(1).strip()
            if not payload:
                return self._res(False, "type", None, {}, 0.0, raw)
            return self._res(True, "type", None, {"text": payload}, 1.0, raw)

        # -- focus -----------------------------------------------------------------
        m = _RE_FOCUS_BOX.fullmatch(t)
        if m:
            needle = m.group(1).strip()
            el = self._mapper.find_element_by_role("input", needle)
            if el is None:
                el = self._mapper.find_element_by_text(needle)
            if el is None:
                return self._res(False, "focus", None, {"needle": needle},
                                 0.0, raw)
            return self._res(True, "focus", el, {"needle": needle}, 0.9, raw)
        m = _RE_FOCUS.fullmatch(t)
        if m:
            needle = m.group(1).strip()
            el = self._mapper.find_element_by_text(needle)
            if el is None:
                el = self._mapper.find_element_by_role("input", needle)
            if el is None:
                return self._res(False, "focus", None, {"needle": needle},
                                 0.0, raw)
            return self._res(True, "focus", el, {"needle": needle}, 0.9, raw)

        # -- click family ------------------------------------------------------------
        m = _RE_OPEN_LINK.fullmatch(t)
        if m:
            needle = m.group(1).strip()
            el = self._mapper.find_element_by_role("link", needle)
            if el is None:
                el = self._mapper.find_element_by_text(needle)
            if el is None:
                return self._res(False, "click", None, {"needle": needle},
                                 0.0, raw)
            return self._res(True, "click", el, {"needle": needle}, 0.9, raw)
        m = _RE_CLICK_ORDINAL.fullmatch(t)
        if m:
            word = m.group(1)
            n = _ORDINAL_WORDS.get(word) or int(word)
            role = m.group(2)
            role = None if role in (None, "element") else role
            el = self._mapper.find_element_by_ordinal(n, role)
            if el is None:
                el = self._mapper.find_element_by_ordinal(n)
            if el is None:
                return self._res(False, "click", None,
                                 {"ordinal": n, "role": role or ""}, 0.0, raw)
            return self._res(True, "click", el,
                             {"ordinal": n, "role": role or ""}, 0.95, raw)
        m = _RE_CLICK_TEXT.fullmatch(t)
        if m:
            needle = m.group(1).strip()
            ordinal = _ORDINAL_WORDS.get(needle)
            if ordinal is None and needle.isdigit():
                ordinal = int(needle)
            if ordinal is not None:
                el = self._mapper.find_element_by_ordinal(ordinal)
            else:
                el = self._mapper.find_element_by_text(needle)
                if el is None:
                    el = self._mapper.find_element_by_role("button", needle)
            if el is None:
                return self._res(False, "click", None, {"needle": needle},
                                 0.0, raw)
            return self._res(True, "click", el, {"needle": needle}, 0.9, raw)

        # -- no template matched --------------------------------------------------
        return BrowserResolution(matched=False, text=raw)


# ═════════════════════════════════════════════════════════════════════════════
# Action verification (§13)
# ═════════════════════════════════════════════════════════════════════════════


def _passed(message: str) -> Dict[str, Any]:
    return {"status": "passed", "similarity": 1.0, "message": message}


def _failed(message: str) -> Dict[str, Any]:
    return {"status": "failed", "similarity": 0.0, "message": message}


def _unknown(message: str) -> Dict[str, Any]:
    return {"status": "unknown", "similarity": 0.0, "message": message}


class BrowserActionVerifier:
    """Before/after verification for browser actions (§13).

    Deterministic: it compares the pre-action and post-action
    :class:`BrowserState` snapshots (plus the bridge's own action history
    when available) and reports ``passed`` / ``failed`` / ``unknown``.
    """

    def verify(self, action: str,
               element: Optional[BrowserElement],
               before: Optional[BrowserState],
               after: Optional[BrowserState],
               bridge: Optional[BrowserBridge]) -> Dict[str, Any]:
        try:
            if after is None:
                return _unknown("no post-action browser state available")
            if before is None:
                return _unknown("no pre-action browser state available")
            act = str(action or "")

            if act == "click":
                return self._verify_click(element, before, after, bridge)
            if act == "navigate":
                if str(before.url or "") != str(after.url or ""):
                    return _passed("url changed after navigate")
                return _failed("url did not change after navigate")
            if act == "new_tab":
                if len(after.tabs) > len(before.tabs):
                    return _passed("tab count increased")
                return _failed("tab count did not increase")
            if act == "close_tab":
                if len(after.tabs) < len(before.tabs):
                    return _passed("tab count decreased")
                return _failed("tab count did not decrease")
            if act in ("back", "forward"):
                if str(before.url or "") != str(after.url or ""):
                    return _passed(f"url changed after {act}")
                if self._history_signal_ok(bridge, "back" if act == "back"
                                           else "forward"):
                    return _passed(f"bridge history moved ({act})")
                return _failed(f"url unchanged after {act}")
            if act == "refresh":
                if str(before.url or "") != str(after.url or "") or \
                        str(before.title or "") != str(after.title or ""):
                    return _passed("page content changed after refresh")
                if self._history_signal_ok(bridge, "refresh"):
                    return _passed("bridge recorded the refresh")
                return _failed("no observable change after refresh")
            if act == "scroll":
                if self._history_signal_ok(bridge, "scroll"):
                    return _passed("bridge recorded the scroll")
                return _failed("no scroll recorded")
            if act == "switch_tab":
                if str(before.active_tab_id or "") != \
                        str(after.active_tab_id or "") or \
                        str(before.url or "") != str(after.url or "") or \
                        str(before.title or "") != str(after.title or ""):
                    return _passed("active tab changed")
                return _failed("active tab did not change")
            if act == "focus":
                return self._verify_focus(element, before, after, bridge)
            if act == "type":
                return self._verify_type(element, before, after, bridge)
            if act == "search":
                return self._verify_type(element, before, after, bridge,
                                         label="search")
            return _unknown(f"no verification rule for action '{act}'")
        except Exception:
            return _unknown("verifier error")

    # -- helpers ---------------------------------------------------------------

    @staticmethod
    def _history_signal_ok(bridge: Optional[BrowserBridge],
                           action_type: str) -> bool:
        try:
            hist = getattr(bridge, "action_history", None)
            if isinstance(hist, list) and hist and \
                    isinstance(hist[-1], dict) and \
                    str(hist[-1].get("type", "")) == action_type and \
                    bool(hist[-1].get("ok", False)):
                return True
            last = getattr(bridge, "last_action", None)
            return (isinstance(last, dict)
                    and str(last.get("type", "")) == action_type
                    and bool(last.get("ok", False)))
        except Exception:
            return False

    @staticmethod
    def _element_snapshot(state: BrowserState,
                          element_id: str) -> Optional[BrowserElement]:
        eid = str(element_id or "")
        for el in state.elements:
            if el.id == eid:
                return el
        return None

    @classmethod
    def _verify_click(cls, element: Optional[BrowserElement],
                      before: BrowserState, after: BrowserState,
                      bridge: Optional[BrowserBridge]) -> Dict[str, Any]:
        if element is None:
            return _unknown("no element to verify the click against")
        if str(before.focused_element_id or "") != \
                str(after.focused_element_id or "") and \
                str(after.focused_element_id or "") == str(element.id):
            return _passed("focus moved to the clicked element")
        if str(before.url or "") != str(after.url or ""):
            return _passed("url changed after the click")
        hist = getattr(bridge, "action_history", None)
        if isinstance(hist, list):
            for entry in reversed(hist):
                if isinstance(entry, dict) and \
                        str(entry.get("type", "")) == "click" and \
                        str(entry.get("element_id", "")) == str(element.id) \
                        and bool(entry.get("ok", False)):
                    return _passed("bridge action history records the click")
        b_el = cls._element_snapshot(before, element.id)
        a_el = cls._element_snapshot(after, element.id)
        if b_el is not None and a_el is not None and \
                (b_el.value != a_el.value):
            return _passed("element value changed after the click")
        return _failed("no observable change after click")

    @classmethod
    def _verify_focus(cls, element: Optional[BrowserElement],
                      before: BrowserState, after: BrowserState,
                      bridge: Optional[BrowserBridge]) -> Dict[str, Any]:
        if str(after.focused_element_id or "") and \
                str(after.focused_element_id or "") != \
                str(before.focused_element_id or ""):
            if element is None or \
                    str(after.focused_element_id or "") == str(element.id):
                return _passed("focused element changed")
        if element is not None and \
                str(after.focused_element_id or "") == str(element.id) and \
                str(before.focused_element_id or "") != str(element.id):
            return _passed("target element is now focused")
        if cls._history_signal_ok(bridge, "focus"):
            return _passed("bridge recorded the focus action")
        return _failed("focus did not change")

    @classmethod
    def _verify_type(cls, element: Optional[BrowserElement],
                     before: BrowserState, after: BrowserState,
                     bridge: Optional[BrowserBridge],
                     label: str = "type") -> Dict[str, Any]:
        target_id = (str(element.id) if element is not None
                     else str(after.focused_element_id or ""))
        if target_id:
            b_el = cls._element_snapshot(before, target_id)
            a_el = cls._element_snapshot(after, target_id)
            if b_el is not None and a_el is not None and \
                    str(b_el.value or "") != str(a_el.value or ""):
                return _passed(f"input value changed ({label})")
        if cls._history_signal_ok(bridge, "type"):
            return _passed(f"bridge recorded the {label}")
        return _failed(f"no text input observed ({label})")


# ═════════════════════════════════════════════════════════════════════════════
# Controller (§12–§13 orchestration)
# ═════════════════════════════════════════════════════════════════════════════


class BrowserController:
    """Ties bridge + mapper + resolver support + verification together.

    Config keys (all optional):

    - ``enabled``        bool, default False
    - ``bridge``         'simulated' | 'cdp' | 'auto', default 'auto'
    - ``cdp_port``       int, default 9222
    - ``verify_actions`` bool, default True
    - ``poll_interval``  float seconds, default 1.0
    - ``offline``        bool, default False (offline gate: CDP disabled)

    Works with networking disabled: the offline gate only disables the
    CDP transport; the simulated bridge keeps working.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None,
                 bridge: Optional[BrowserBridge] = None,
                 context_engine: Any = None,
                 bus: Any = None) -> None:
        cfg = dict(config or {})
        self.enabled = bool(cfg.get("enabled", False))
        self.bridge_kind = str(cfg.get("bridge", "auto")).lower()
        self.cdp_port = int(cfg.get("cdp_port", 9222))
        self.verify_actions = bool(cfg.get("verify_actions", True))
        self.poll_interval = max(0.0, float(cfg.get("poll_interval", 1.0)))
        self.offline = bool(cfg.get("offline", False))
        self.context_engine = context_engine
        self.bus = bus
        self.mapper = BrowserTargetMapper()
        self._bridge = bridge
        self._explicit_bridge = bridge is not None
        self._running = False
        self._state: Optional[BrowserState] = None
        self._last_poll = -1e18
        self._verifier = BrowserActionVerifier()

    # -- lifecycle ----------------------------------------------------------------

    def start(self) -> bool:
        """Choose and activate the bridge.  Never raises."""
        try:
            if self._running:
                return True
            if self._explicit_bridge:
                self._running = True
                return True
            if not self.enabled:
                return False
            chosen: Optional[BrowserBridge] = None
            if self.bridge_kind in ("cdp", "auto") and not self.offline:
                cdp = CDPBrowserBridge(port=self.cdp_port, offline=False)
                if cdp.available():
                    chosen = cdp
            if chosen is None and self.bridge_kind == "cdp":
                # Explicit CDP that is unreachable (or offline) → stay off.
                self._running = False
                return False
            if chosen is None:
                chosen = SimulatedBrowserBridge()  # always available
            self._bridge = chosen
            self._running = True
            return True
        except Exception:
            self._running = False
            return False

    def stop(self) -> None:
        """Stop polling/execution (state stays inspectable)."""
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    @property
    def bridge(self) -> Optional[BrowserBridge]:
        return self._bridge

    @property
    def state(self) -> Optional[BrowserState]:
        return self._state

    # -- polling -----------------------------------------------------------------

    def poll(self, now: Optional[float] = None) -> Optional[BrowserState]:
        """Poll the bridge at ``poll_interval``; update mapper, bus, context.

        Returns the fresh state, or None when disabled/throttled/unavailable.
        Never raises.
        """
        try:
            if not self._running or self._bridge is None:
                return None
            now = float(now) if now is not None else now_ts()
            if (now - self._last_poll) < self.poll_interval:
                return None
            self._last_poll = now
            state = self._bridge.poll_state(now=now)
            if state is None:
                return None
            self._state = state
            self.mapper.update(state)
            targets = self.mapper.targets()
            if self.bus is not None:
                self.bus.publish(Event(
                    kind=EventKind.BROWSER_TARGET,
                    modality=Modality.BROWSER,
                    confidence=1.0,
                    source="browser_bridge",
                    payload={"url": state.url, "title": state.title,
                             "browser": state.browser,
                             "active_tab_id": state.active_tab_id,
                             "num_elements": len(state.elements)},
                    timestamp=now), now=now)
            if self.context_engine is not None:
                self.context_engine.update_browser(
                    browser=state.browser, tab_title=state.title,
                    url=state.url, now=now)
                self.context_engine.set_browser_targets(targets, now=now)
            return state
        except Exception:
            return None

    # -- execution -----------------------------------------------------------------

    def execute(self, resolution: BrowserResolution,
                now: Optional[float] = None) -> Dict[str, Any]:
        """Execute one :class:`BrowserResolution` via the bridge.

        Returns ``{'status': 'executed'|'failed'|'unavailable', 'action',
        'element_id', 'sensitive', 'verification': {...}}``.  Destructive
        actions (``close_tab``) carry ``'sensitive': True``.  Never raises.
        """
        action = str(getattr(resolution, "action", "") or "")
        element = getattr(resolution, "element", None)
        params = dict(getattr(resolution, "params", {}) or {})
        element_id = str(getattr(element, "id", "") or "") \
            if element is not None else ""
        result: Dict[str, Any] = {"status": "failed", "action": action,
                                  "element_id": element_id,
                                  "sensitive": action == "close_tab"}
        try:
            if not self._running or self._bridge is None:
                result["status"] = "unavailable"
                result["message"] = "browser controller is not running"
                return result
            before = None
            try:
                before = self._bridge.poll_state(now=now)
            except Exception:
                before = None
            ok = self._dispatch(action, element, params)
            if not ok:
                result["message"] = "bridge could not perform the action"
                return result
            result["status"] = "executed"
            if self.verify_actions:
                after = None
                try:
                    after = self._bridge.poll_state(now=now)
                except Exception:
                    after = None
                result["verification"] = self._verifier.verify(
                    action, element, before, after, self._bridge)
                result["verified"] = \
                    result["verification"].get("status") == "passed"
            return result
        except Exception:
            return {"status": "failed", "action": action,
                    "element_id": element_id,
                    "sensitive": action == "close_tab"}

    def _dispatch(self, action: str, element: Optional[BrowserElement],
                  params: Dict[str, Any]) -> bool:
        b = self._bridge
        if b is None:
            return False
        try:
            if action == "click":
                return element is not None and bool(b.click_element(element.id))
            if action == "focus":
                return element is not None and bool(b.focus_element(element.id))
            if action == "type":
                text = str(params.get("text", ""))
                return bool(text) and bool(b.type_text(text))
            if action == "navigate":
                url = str(params.get("url", ""))
                return bool(url) and bool(b.navigate(url))
            if action == "new_tab":
                out = b.new_tab()
                return True if out is None else bool(out)
            if action == "close_tab":
                tab_id = str(params.get("tab_id", "") or "")
                if not tab_id:
                    state = self._state or self.mapper.state
                    tab_id = str(getattr(state, "active_tab_id", "") or "") \
                        if state is not None else ""
                return bool(tab_id) and bool(b.close_tab(tab_id))
            if action == "switch_tab":
                tab_id = str(params.get("tab_id", "") or "")
                return bool(tab_id) and bool(b.switch_tab(tab_id))
            if action == "back":
                return bool(b.go_back())
            if action == "forward":
                return bool(b.go_forward())
            if action == "refresh":
                return bool(b.refresh())
            if action == "scroll":
                direction = str(params.get("direction", "down"))
                amounts = {"bottom": 1_000_000.0, "top": -1_000_000.0,
                           "down": 600.0, "up": -600.0}
                return bool(b.scroll(amounts.get(direction, 600.0)))
            if action == "search":
                query = str(params.get("query", "")).strip()
                if not query:
                    return False
                el = self._focused_input() or self._first_input()
                if el is None:
                    return False
                return bool(b.focus_element(el.id)) and \
                    bool(b.type_text(query))
            return False
        except Exception:
            return False

    def _focused_input(self) -> Optional[BrowserElement]:
        state = self.mapper.state
        if state is None:
            return None
        fid = str(state.focused_element_id or "")
        if not fid:
            return None
        for el in state.elements:
            if el.id == fid and el.role == "input":
                return el
        return None

    def _first_input(self) -> Optional[BrowserElement]:
        state = self.mapper.state
        if state is None:
            return None
        for el in state.elements:
            if el.role == "input" and el.actionable:
                return el
        return None

    # -- passthrough ------------------------------------------------------------

    def targets(self) -> List[ScreenTarget]:
        return self.mapper.targets()
