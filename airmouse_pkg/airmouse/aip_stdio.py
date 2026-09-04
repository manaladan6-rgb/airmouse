"""airmouse.aip_stdio — the AIP wire server (v14.5 §9/§11, v15.1.1).

The missing transport half of the Agent SDK: a deterministic,
sequential, stdlib-only JSON-LINES server that connects
``agent-core``'s ``stdio://<command>`` transport (and
``agent-sdk-js``' StdioTransport) to a real :class:`AipEndpoint`.
The coordinator exposes it as ``airmouse --aip-stdio``.

WIRE PROTOCOL (matches agent-core/airmouse_agent/client.py exactly)
--------------------------------------------------------------------
* One AIP envelope per line, both directions::

      {"aip_version": "1.0", "type": "execute", "id": "msg-00000001",
       "agent_id": "agent", "request_id": "", "ts": 1690000000.0,
       "payload": {...}}\n

* Inbound lines are parsed by :func:`airmouse.aip.parse_message`
  (strict §23 fail-closed) and routed through
  :meth:`AipEndpoint.handle`; the reply envelope is serialized with
  ``AipMessage.to_json()`` and flushed as ONE line.
* Malformed / unknown / oversized lines get ONE ``type:"error"``
  response line (code ``bad_message``) and the loop CONTINUES —
  the server never crashes and never dies on bad input.
* Size cap: 262144 bytes per line (mirrors
  :data:`airmouse.aip.MAX_MESSAGE_BYTES`).
* NOTHING that is not a response line is ever written to the output
  stream — no banners, no logs.  Logs go to stderr only.
* ``request_timeout_s`` (default 10 s) guards handler hangs
  best-effort via ``SIGALRM`` on POSIX main threads; elsewhere the
  guard degrades to plain dispatch (still exception-proof).

MODES (env contract for ``main()``)
-----------------------------------
* ``AIRMOUSE_AIP_SIMULATOR=1`` → simulated endpoint; every EXECUTE
  response is honestly labelled ``"simulated": true``.
* ``AIRMOUSE_AIP_REAL=1``      → real mode; REQUIRES an executor
  wired in-process (via :func:`provide_action_engine`, the
  ``AIRMOUSE_AIP_ENGINE="module:attr"`` env spec, or an injected
  ``endpoint=`` argument).  Without one the process exits 2 with
  stderr ``real mode requires --executor wiring``.
* ``AIRMOUSE_HOME`` is honored through
  :func:`airmouse.persistence.airmouse_home`.
* Default (no env) → simulated mode (safe, honest labelling).

Sequential loop, no threads, explicit flush — works with pipes.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import importlib
import os
import signal
import sys
import threading
import time
from typing import Any, Optional, Tuple

from .aip import AIP_VERSION, MAX_MESSAGE_BYTES, AipMessage, MsgType, \
    parse_message
from .agent_sdk import AipEndpoint

#: per-line size cap (mirrors aip.MAX_MESSAGE_BYTES)
MAX_LINE_BYTES = MAX_MESSAGE_BYTES

SIMULATOR_ENV = "AIRMOUSE_AIP_SIMULATOR"
REAL_ENV = "AIRMOUSE_AIP_REAL"
ENGINE_ENV = "AIRMOUSE_AIP_ENGINE"

#: real engine injected by the coordinator (provide_action_engine)
_injected_engine: Optional[Any] = None


class _HandlerTimeout(Exception):
    """Raised inside the SIGALRM guard when a handler overruns."""


# ─────────────────────────────────────────────────────────────────────────────
# endpoint construction (env contract)
# ─────────────────────────────────────────────────────────────────────────────


def provide_action_engine(engine: Any) -> None:
    """Coordinator wiring point: inject the real executor engine.

    Call BEFORE ``main()``/``serve()`` so ``AIRMOUSE_AIP_REAL=1`` has
    an in-process executor.  (``__main__`` will call this when wiring
    ``--aip-stdio`` to the live action stack.)
    """
    global _injected_engine
    _injected_engine = engine


def _truthy(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in (
        "1", "true", "yes", "on")


def default_endpoint(action_engine: Optional[Any] = None,
                     label: str = "simulated") -> AipEndpoint:
    """Build the stdio endpoint: permission engine attached (fail-
    closed: no grants until wired/granted), AIRMOUSE_HOME honored
    through persistence."""
    from .permissions import AgentPermissionEngine
    from .persistence import airmouse_home     # honors $AIRMOUSE_HOME
    home = airmouse_home()                     # resolve env contract
    del home                                   # (logged by main(), not stdout)
    return AipEndpoint(permission_engine=AgentPermissionEngine(),
                       action_engine=action_engine, label=label)


def _resolve_real_engine() -> Optional[Any]:
    """Real executor available in-process?  (injection > env spec)."""
    if _injected_engine is not None:
        return _injected_engine
    spec = str(os.environ.get(ENGINE_ENV, "")).strip()
    if not spec:
        return None
    try:
        module_name, _, attr = spec.partition(":")
        obj = importlib.import_module(module_name)
        if attr:
            obj = getattr(obj, attr)
        return obj() if callable(obj) else obj
    except Exception as exc:
        print(f"aip-stdio: cannot load engine {spec!r}: {exc}",
              file=sys.stderr)
        return None


def endpoint_from_env() -> Tuple[Optional[AipEndpoint], int, str]:
    """(endpoint, exit_code, stderr_message) from the env contract."""
    if _truthy(REAL_ENV):
        engine = _resolve_real_engine()
        if engine is None:
            return None, 2, "real mode requires --executor wiring"
        return default_endpoint(action_engine=engine, label="real"), 0, ""
    return default_endpoint(label="simulated"), 0, ""


# ─────────────────────────────────────────────────────────────────────────────
# the sequential serve loop
# ─────────────────────────────────────────────────────────────────────────────


def serve(endpoint: Optional[AipEndpoint] = None,
          in_stream: Optional[Any] = None,
          out_stream: Optional[Any] = None,
          max_requests: int = 0,
          request_timeout_s: float = 10.0) -> int:
    """Serve AIP JSON-lines on ``in_stream``/``out_stream``.

    Reads one request line → routes it through ``endpoint.handle`` →
    writes ONE reply line (flushed).  Malformed/oversized input gets
    one ``error`` reply line and the loop continues.  ``max_requests``
    bounds the loop (0 = until EOF).  Returns a process exit code
    (0 normal, 2 = real mode requested without an executor).
    """
    if endpoint is None:
        endpoint, code, err = endpoint_from_env()
        if endpoint is None:
            if err:
                print(f"aip-stdio: {err}", file=sys.stderr)
            return code
    inp = sys.stdin if in_stream is None else in_stream
    out = sys.stdout if out_stream is None else out_stream
    served = 0
    seq = 0
    while max_requests <= 0 or served < int(max_requests):
        line, oversized = _read_line_capped(inp, MAX_LINE_BYTES)
        if line is None:                       # EOF → clean shutdown
            break
        if not oversized and not line.strip():
            continue                           # tolerate blank separators
        seq += 1
        if oversized:
            reply = _wire_error(
                seq, line, f"line too large (> {MAX_LINE_BYTES} bytes)")
        else:
            reply = _dispatch_line(endpoint, seq, line, request_timeout_s)
        if not _write_line(out, reply):
            break                              # peer closed the pipe
        served += 1
    return 0


def main(argv: Optional[list] = None, endpoint: Optional[AipEndpoint] = None,
         in_stream: Optional[Any] = None,
         out_stream: Optional[Any] = None) -> int:
    """Entry point for ``airmouse --aip-stdio`` and
    ``python -m airmouse.aip_stdio``: builds the endpoint from the env
    contract (or accepts an injected one for tests/coordinator wiring)
    and serves on sys.stdin/sys.stdout."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("--version", "-V"):
        print(f"airmouse aip-stdio (AIP {AIP_VERSION})")
        return 0
    if endpoint is None:
        endpoint, code, err = endpoint_from_env()
        if endpoint is None:
            if err:
                print(f"aip-stdio: {err}", file=sys.stderr)
            return code
    print(f"aip-stdio: serving AIP {AIP_VERSION} JSON-lines on stdio "
          f"(mode={getattr(endpoint, 'label', 'simulated')}); "
          f"logs go to stderr only", file=sys.stderr)
    return serve(endpoint=endpoint, in_stream=in_stream,
                 out_stream=out_stream)


# ─────────────────────────────────────────────────────────────────────────────
# framing + dispatch internals
# ─────────────────────────────────────────────────────────────────────────────


def _read_line_capped(stream: Any, cap: int) -> Tuple[Optional[str], bool]:
    """Read ONE line; return (content_without_newline, oversized).

    ``(None, False)`` signals EOF.  Oversized lines are drained (in
    capped chunks) so the request/response framing stays in sync.
    """
    chunk = stream.readline(cap + 2)
    if not chunk:
        return None, False
    body, terminated = chunk, False
    if body.endswith("\n"):
        body, terminated = body[:-1], True
        if body.endswith("\r"):
            body = body[:-1]
    if len(body) > cap:
        if not terminated:                     # drain the rest of the line
            while True:
                more = stream.readline(cap + 2)
                if not more or more.endswith("\n"):
                    break
        return body, True
    return body, False


def _dispatch_line(endpoint: AipEndpoint, seq: int, line: str,
                   timeout_s: float) -> AipMessage:
    msg, errs = parse_message(line)
    if msg is None:
        return _wire_error(seq, line,
                           errs[0] if errs else "invalid message")
    try:
        return _handle_with_timeout(endpoint, msg, timeout_s)
    except _HandlerTimeout:
        return _wire_error(seq, line, "handler timeout", code="timeout",
                           request_id=msg.id)
    except Exception as exc:                   # never crash the loop
        return _wire_error(seq, line, f"handler error: {exc}",
                           code="failed", request_id=msg.id)


def _handle_with_timeout(endpoint: AipEndpoint, msg: AipMessage,
                         timeout_s: float) -> AipMessage:
    """Best-effort hang guard: SIGALRM when available (POSIX + main
    thread), plain exception-proof dispatch otherwise."""
    armable = (timeout_s is not None and float(timeout_s) > 0 and
               hasattr(signal, "SIGALRM") and
               threading.current_thread() is threading.main_thread())
    if not armable:
        return endpoint.handle(msg)

    def _bang(signum: int, frame: Any) -> None:
        raise _HandlerTimeout()

    old = signal.signal(signal.SIGALRM, _bang)
    signal.setitimer(signal.ITIMER_REAL, max(0.05, float(timeout_s)))
    try:
        return endpoint.handle(msg)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, old)


def _wire_error(seq: int, raw_line: str, message: str,
                code: str = "bad_message", request_id: str = "") -> AipMessage:
    """One deterministic error envelope for an unparseable request."""
    if not request_id:
        request_id = _best_effort_id(raw_line)
    return AipMessage(type=MsgType.ERROR.value, id=f"err-{seq:08d}",
                      agent_id="airmouse", version=AIP_VERSION,
                      payload={"code": code,
                               "message": str(message)[:200],
                               "request_id": str(request_id)[:64]},
                      ts=time.time())


def _best_effort_id(raw_line: str) -> str:
    """Salvage the request id from a partially-valid line (never raises,
    never execs — data only)."""
    try:
        import json
        data = json.loads(raw_line)
    except Exception:
        return ""
    if isinstance(data, dict):
        for key in ("id", "request_id"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return value[:64]
    return ""


def _write_line(out: Any, reply: AipMessage) -> bool:
    """Serialize ONE reply line + explicit flush.  False = peer gone."""
    try:
        out.write(reply.to_json())
        out.write("\n")
        out.flush()
        return True
    except Exception:
        return False


if __name__ == "__main__":                     # python -m airmouse.aip_stdio
    sys.exit(main())
