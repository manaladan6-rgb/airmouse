"""
airmouse_agent.client — the standalone agent client (§10/§11).

Speaks AIP JSON messages over a pluggable local transport.  Every
primitive maps 1:1 to an AIP message type; the client NEVER invents
powers beyond the protocol (permission gates live in the core).
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional

from .version import AIP_VERSION_SUPPORTED

MAX_MESSAGE_BYTES = 256 * 1024


class AipError(Exception):
    """Raised for protocol-level errors (never for application
    denials — those come back as normal error payloads)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class AirMouse:
    """The minimal agent surface::

        from airmouse_agent import AirMouse

        air = AirMouse(handler=my_local_endpoint)   # in-process
        air.connect()
        air.capabilities()
        air.execute(intent="open my research project", verify=True)

    ``transport``/``handler`` options:
      * a callable handler  (in-process endpoint; used by tests and
        embedded runtimes)
      * ``"stdio://<command>"``  spawn the AirMouse core as a child
        and speak AIP JSON-lines over stdin/stdout (lazy import of
        subprocess on first use)
    """

    def __init__(self, transport: Optional[Any] = None,
                 handler: Optional[Callable[[Dict[str, Any]], Dict[str,
                                                                  Any]]] = None,
                 agent_id: str = "agent") -> None:
        self.agent_id = str(agent_id)[:64]
        self._handler = handler
        self._transport = transport
        self._counter = 0
        self._connected = False
        self._protocol_version: Optional[str] = None
        self._child = None

    # ── lifecycle (§10) ─────────────────────────────────────────────────

    def connect(self) -> bool:
        reply = self._call("status", {})
        if reply is None:
            raise AipError("not_found", "no endpoint answered status")
        if reply.get("type") == "error":
            raise AipError(reply["payload"].get("code", "failed"),
                           reply["payload"].get("message", ""))
        self._connected = True
        self._protocol_version = reply["payload"].get("protocol_version")
        return True

    def negotiate(self, offered: str = AIP_VERSION_SUPPORTED) -> bool:
        """§9 version negotiation: same major required."""
        caps = self.capabilities()
        remote = str(caps.get("protocol_version", ""))
        ok = remote.split(".")[0] == offered.split(".")[0] if remote else \
            False
        if not ok:
            raise AipError("unsupported_version",
                           f"remote={remote!r} local={offered!r}")
        self._protocol_version = remote
        return True

    # ── primitives (§10) ────────────────────────────────────────────────

    def capabilities(self) -> Dict[str, Any]:
        return self._payload(self._call("discover", {}), "capabilities")

    def observe(self) -> Dict[str, Any]:
        return self._payload(self._call("observe", {}), "observation")

    def targets(self, kind: str = "semantic", value: str = "",
                description: str = "", **kw: Any) -> Dict[str, Any]:
        payload = {"target": {"kind": kind, "value": value,
                              "description": description, **kw}}
        return self._payload(self._call("target", payload), "targets")

    def execute(self, intent: str = "", action: str = "click",
                target: Optional[Dict[str, Any]] = None,
                verify: bool = True, **params: Any) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "action": str(action)[:40],
            "verify": bool(verify),
            "params": {str(k)[:40]: str(v)[:60]
                       for k, v in list(params.items())[:8]},
        }
        if intent:
            payload["params"]["intent"] = str(intent)[:200]
        if target:
            payload["target"] = {
                "kind": str(target.get("kind", "semantic"))[:20],
                "value": str(target.get("value", ""))[:160],
                "coordinate_fallback": bool(target.get(
                    "coordinate_fallback", False))}
        return self._payload(self._call("execute", payload), "result")

    def verify(self, action_id: str) -> Dict[str, Any]:
        return self._payload(self._call(
            "verify", {"action_id": str(action_id)[:64]}), "verification")

    def task(self, objective: str,
             steps: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        payload = {"objective": str(objective)[:200],
                   "steps": list(steps or [])[:64]}
        return self._payload(self._call("task", payload), "result")

    def stop(self) -> Dict[str, Any]:
        out = self._payload(self._call("stop", {}), "result")
        self._connected = False
        return out

    def status(self) -> Dict[str, Any]:
        return self._payload(self._call("status", {}), "status")

    # ── internals ───────────────────────────────────────────────────────

    def _payload(self, reply: Optional[Dict[str, Any]],
                 expect: str) -> Dict[str, Any]:
        if reply is None:
            raise AipError("timeout", "no reply")
        if reply.get("type") == "error":
            p = reply.get("payload", {})
            raise AipError(str(p.get("code", "failed")),
                           str(p.get("message", ""))[:200])
        if reply.get("type") != expect:
            raise AipError("bad_message",
                           f"expected {expect}, got {reply.get('type')}")
        out = dict(reply.get("payload", {}))
        out.setdefault("ok", True)
        return out

    def _call(self, mtype: str, payload: Dict[str, Any]) -> Optional[Dict[
            str, Any]]:
        self._counter += 1
        message = {
            "aip_version": AIP_VERSION_SUPPORTED,
            "type": str(mtype)[:24],
            "id": f"msg-{self._counter:08d}",
            "agent_id": self.agent_id,
            "request_id": "",
            "ts": round(time.time(), 6),
            "payload": payload if isinstance(payload, dict) else {},
        }
        wire = json.dumps(message, sort_keys=True)
        if len(wire) > MAX_MESSAGE_BYTES:
            raise AipError("bad_message", "outbound message too large")
        if self._handler is not None:
            raw = self._handler(wire)
            return self._parse(raw)
        return self._call_transport(wire)

    def _parse(self, raw: Any) -> Optional[Dict[str, Any]]:
        try:
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("utf-8")
            data = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(data, dict):
                return None
            return data
        except Exception:
            return None

    def _call_transport(self, wire: str) -> Optional[Dict[str, Any]]:
        if self._transport is None:
            raise AipError("not_found", "no transport configured")
        # in-process transport object with .send(str)->str
        if hasattr(self._transport, "send"):
            return self._parse(self._transport.send(wire))
        # stdio:// command transport (lazy subprocess import, §11)
        if isinstance(self._transport, str) and \
                self._transport.startswith("stdio://"):
            return self._parse(self._stdio_roundtrip(
                self._transport[len("stdio://"):], wire))
        raise AipError("bad_message", "unsupported transport")

    def _stdio_roundtrip(self, command: str, wire: str) -> bytes:
        import subprocess                            # lazy (§11)
        if self._child is None:
            self._child = subprocess.Popen(
                command.split(),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL)
        assert self._child.stdin is not None
        assert self._child.stdout is not None
        self._child.stdin.write((wire + "\n").encode("utf-8"))
        self._child.stdin.flush()
        line = self._child.stdout.readline()
        return line


def main(argv: Optional[List[str]] = None) -> int:
    """Tiny CLI: ``airmouse-agent --version`` (fast, §11)."""
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("--version", "-V"):
        from .version import AGENT_CORE_VERSION
        print(f"airmouse-agent-core {AGENT_CORE_VERSION} "
              f"(AIP {AIP_VERSION_SUPPORTED})")
        return 0
    print("usage: airmouse-agent --version", file=sys.stderr)
    return 2
