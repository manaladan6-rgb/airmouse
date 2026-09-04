"""
airmouse.browser_bridge — v10 Local Browser Bridge Server 🌉
=============================================================

Mission §11 transport half: a **localhost-only** HTTP endpoint that the
shipped browser extension (see ``browser_extension/``) POSTs page metadata
to, plus the extension source itself.

    ┌──────────────┐  chrome.scripting + chrome.tabs (1 s poll)  ┌─────────┐
    │ MV3 extension│ ─────────POST /state (JSON, ≤256 KB)───────►│  this   │
    │  content.js  │                                             │  server │
    └──────────────┘◄─────────GET /state / GET /health────────── └─────────┘

Design rules
------------
1.  **Localhost only.**  The server is hard-bound to ``127.0.0.1`` — it is
    NEVER exposed on 0.0.0.0.  Only the most recent state is stored.
2.  **Data only.**  The payload is parsed with ``json.loads`` and stored
    as a plain dict; nothing from it is ever executed.  Oversized
    (> 256 KB) or invalid payloads are rejected with 413 / 400.
3.  **Never raises.**  ``start`` returns False when the port cannot be
    bound; handler errors are answered with 500 and swallowed.
4.  **Deterministic tests.**  ``verify_bridge_server()`` performs a full
    POST→GET round-trip on an ephemeral port (port 0) and returns True /
    False — no fixtures, no external browser needed.

NOTE: the extension source is shipped and lint-level reviewed, but running
it inside a real Chrome/Edge is hardware-unverified in this headless
environment — see ``browser_extension/README.md``.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import copy
import http.server
import json
import threading
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

__all__ = [
    "BrowserBridgeServer",
    "verify_bridge_server",
    "DEFAULT_BRIDGE_PORT",
]

DEFAULT_BRIDGE_PORT = 17843


class _BridgeHandler(http.server.BaseHTTPRequestHandler):
    """Request handler: GET /health, GET /state, POST /state."""

    server_version = "AirMouseBridge/10.0"
    protocol_version = "HTTP/1.0"   # one request per connection: simplest + safe

    # -- helpers ----------------------------------------------------------------

    def _send_json(self, obj: Any, code: int = 200) -> None:
        try:
            body = json.dumps(obj).encode("utf-8")
            self.send_response(int(code))
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            pass

    @property
    def _owner(self) -> "BrowserBridgeServer":
        return self.server.owner  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:  # silence stderr
        return

    # -- routes -----------------------------------------------------------------

    def do_GET(self) -> None:
        try:
            path = urllib.parse.urlsplit(self.path).path
            if path == "/health":
                self._send_json({"ok": True})
                return
            if path == "/state":
                state = self._owner.latest_state()
                if state is None:
                    self._send_json({"ok": False,
                                     "error": "no state received yet"}, 404)
                else:
                    self._send_json(state)
                return
            self._send_json({"ok": False, "error": "not found"}, 404)
        except Exception:
            self._send_json({"ok": False, "error": "internal error"}, 500)

    def do_POST(self) -> None:
        try:
            path = urllib.parse.urlsplit(self.path).path
            if path != "/state":
                self._send_json({"ok": False, "error": "not found"}, 404)
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                length = 0
            if length <= 0:
                self._send_json({"ok": False, "error": "missing body"}, 400)
                return
            if length > 4 * 1024 * 1024:
                # Absurd size: reject without reading (connection will close).
                self._send_json({"ok": False, "error": "state too large"}, 413)
                return
            body = self.rfile.read(length)
            if length > self._owner.MAX_STATE_BYTES:
                self._send_json({"ok": False, "error": "state too large"}, 413)
                return
            try:
                payload = json.loads(body.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                self._send_json({"ok": False, "error": "invalid json"}, 400)
                return
            if not isinstance(payload, dict):
                self._send_json({"ok": False,
                                 "error": "state must be a JSON object"}, 400)
                return
            self._owner.store_state(payload)
            self._send_json({"ok": True})
        except Exception:
            self._send_json({"ok": False, "error": "internal error"}, 500)


class _BridgeHTTPServer(http.server.ThreadingHTTPServer):
    """ThreadingHTTPServer hard-bound to 127.0.0.1 with an owner backref."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: Any, handler: Any,
                 owner: "BrowserBridgeServer") -> None:
        super().__init__(address, handler)
        self.owner = owner


class BrowserBridgeServer:
    """Localhost-only HTTP sink for browser-extension state (§11).

    - ``start()``      bind 127.0.0.1:<port> and serve in a daemon thread
                       (True on success, False when the port is taken).
    - ``stop()``       shut down and close the socket.
    - ``url``          the state endpoint, e.g. ``http://127.0.0.1:17843/state``.
    - ``latest_state()`` the most recent posted payload (deep copy) or None.

    At most ONE state (the latest) is retained.  Payloads are validated
    with ``json.loads`` and capped at 256 KB.  The socket is bound to
    ``127.0.0.1`` unconditionally — never ``0.0.0.0``.
    """

    MAX_STATE_BYTES = 256 * 1024

    def __init__(self, port: int = DEFAULT_BRIDGE_PORT,
                 config: Optional[Dict[str, Any]] = None) -> None:
        cfg = dict(config or {})
        self._requested_port = int(cfg.get("port", port))
        self._server: Optional[_BridgeHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._state: Optional[Dict[str, Any]] = None
        self._lock = threading.Lock()

    # -- properties ----------------------------------------------------------------

    @property
    def port(self) -> int:
        """Actual bound port (useful when constructed with port 0)."""
        if self._server is not None:
            try:
                return int(self._server.server_address[1])
            except Exception:
                pass
        return self._requested_port

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def url(self) -> str:
        """The /state endpoint the extension POSTs to."""
        return f"{self.base_url}/state"

    @property
    def running(self) -> bool:
        return self._server is not None

    def latest_state(self) -> Optional[Dict[str, Any]]:
        """Thread-safe deep copy of the most recent state (None if none)."""
        with self._lock:
            return copy.deepcopy(self._state) if self._state is not None \
                else None

    # -- storage ----------------------------------------------------------------

    def store_state(self, payload: Dict[str, Any]) -> None:
        """Store the latest payload (replaces any previous one)."""
        try:
            if not isinstance(payload, dict):
                return
            with self._lock:
                self._state = copy.deepcopy(payload)
        except Exception:
            pass

    # -- lifecycle ----------------------------------------------------------------

    def start(self) -> bool:
        """Bind + serve in a daemon thread.  Never raises."""
        try:
            if self._server is not None:
                return True
            srv = _BridgeHTTPServer(("127.0.0.1", self._requested_port),
                                    _BridgeHandler, self)
            self._server = srv
            self._thread = threading.Thread(
                target=srv.serve_forever,
                kwargs={"poll_interval": 0.05},
                name="airmouse-browser-bridge",
                daemon=True)
            self._thread.start()
            return True
        except Exception:
            self._server = None
            self._thread = None
            return False

    def stop(self) -> None:
        """Shut the server down (idempotent).  Never raises."""
        srv = self._server
        self._server = None
        if srv is not None:
            try:
                srv.shutdown()
            except Exception:
                pass
            try:
                srv.server_close()
            except Exception:
                pass
        thread = self._thread
        self._thread = None
        if thread is not None:
            try:
                thread.join(timeout=1.0)
            except Exception:
                pass


def verify_bridge_server() -> bool:
    """Self-test: ephemeral server + POST /state + GET /state round-trip.

    Returns True only when the health check, the POST and the GET all
    succeed and the fetched state equals the posted one.  Never raises.
    """
    server = BrowserBridgeServer(port=0)
    try:
        if not server.start():
            return False
        base = f"http://127.0.0.1:{server.port}"

        sample: Dict[str, Any] = {
            "browser": "chrome",
            "tabs": [{"id": "tab-1", "title": "Example",
                      "url": "https://example.com/"}],
            "active_tab_id": "tab-1",
            "url": "https://example.com/",
            "title": "Example",
            "focused_element_id": "",
            "elements": [
                {"id": "el-1", "role": "button", "text": "Ok",
                 "tag": "button", "bbox": [0.1, 0.1, 0.2, 0.1],
                 "actionable": True, "value": "", "href": "",
                 "confidence": 1.0, "untrusted": True},
            ],
            "timestamp": 123.456,
        }

        # 1. health (bounded retry for the daemon thread startup race)
        deadline = time.monotonic() + 3.0
        healthy = False
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(base + "/health",
                                            timeout=0.5) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                if isinstance(body, dict) and body.get("ok") is True:
                    healthy = True
                    break
            except Exception:
                time.sleep(0.05)
        if not healthy:
            return False

        # 2. POST the sample state
        req = urllib.request.Request(
            server.url, data=json.dumps(sample).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                ack = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return False
        if not (isinstance(ack, dict) and ack.get("ok") is True):
            return False

        # 3. GET it back and compare
        try:
            with urllib.request.urlopen(server.url, timeout=1.0) as resp:
                fetched = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return False
        return fetched == sample
    except Exception:
        return False
    finally:
        try:
            server.stop()
        except Exception:
            pass
