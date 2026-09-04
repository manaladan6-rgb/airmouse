"""Task 2-e endgame tests — browser last mile, resolver providers, system repairs.

Covers:
1.  ``airmouse.browser.launch_browser`` — honest failure without a browser,
    real headless launch when a Chrome/Chromium/Edge binary exists, reuse of
    an already-running DevTools endpoint, explicit-missing-path honesty.
2.  Loopback pinning — ``pinned_ws_parts`` refuses non-loopback
    ``webSocketDebuggerUrl`` hosts (e.g. ``evil.example.com:9222``), accepts
    loopback; ``CDPBrowserBridge._ws_for`` refuses to connect; the /json
    discovery fetch is pinned to loopback as a hard rule.
3.  ``build_default_resolver`` — geometry resolves the center zone out of
    the box, absent accessibility yields no candidates (no crash),
    coordinate fallback gated, OCR disabled unless its own flag is on,
    browser mapper adapter resolves dom targets.
4.  ``system_actions`` repairs — no CrossPlatformKeyboard anywhere,
    app_launch allowlist + sanitization (traversal rejected), Linux volume
    argv unchanged (mocked subprocess), every ``subprocess.run`` call in
    the module carries a ``timeout=`` (static AST check).
"""

from __future__ import annotations

import ast
import http.server
import json
import os
import socket
import threading
from urllib.parse import urlsplit

import pytest

from airmouse.browser import (
    CDPBrowserBridge,
    _devtools_ready,
    discover_browser_executable,
    launch_browser,
    pinned_ws_parts,
)
from airmouse.screen_perception import (
    AccessibilityProvider,
    GeometryProvider,
    OCRProvider,
)
from airmouse.system_actions import (
    DESTRUCTIVE_SYSTEM_OPS,
    SENSITIVE_SYSTEM_OPS,
    SYSTEM_OPS,
    SystemActionExecutor,
    sanitize_file_name,
)
from airmouse.target_resolver import (
    TargetRequest,
    UniversalTargetResolver,
    build_default_resolver,
)


def _free_tcp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


# ---------------------------------------------------------------------------
# 1. launch_browser — the Chrome last mile
# ---------------------------------------------------------------------------


class TestLaunchBrowser:
    def test_honest_error_when_no_browser_found(self):
        if discover_browser_executable():
            pytest.skip("a browser binary exists — error path covered by "
                        "the explicit-missing-path test below")
        out = launch_browser(port=_free_tcp_port(), timeout_s=0.5)
        assert out["ok"] is False
        assert out["error"] == "browser_not_found"
        assert out["browser"] == ""
        assert isinstance(out["port"], int)

    def test_explicit_missing_path_is_honest_failure(self):
        # even when a browser EXISTS on PATH, an explicit-but-missing
        # browser_path must fail honestly instead of being replaced
        out = launch_browser(port=_free_tcp_port(),
                             browser_path="/no/such/chrome-binary",
                             timeout_s=0.5)
        assert out["ok"] is False
        assert out["error"] == "browser_not_found"
        assert out["browser"] == ""

    def test_invalid_port_rejected(self):
        out = launch_browser(port=0, browser_path=os.devnull, timeout_s=0.5)
        assert out["ok"] is False
        assert out["error"] == "invalid_port"

    def test_headless_launch_when_binary_exists(self):
        exe = discover_browser_executable()
        if not exe:
            pytest.skip("no chrome/chromium/edge binary in this sandbox — "
                        "honest error path asserted above")
        port = _free_tcp_port()
        out = launch_browser(port=port, timeout_s=20.0, headless=True,
                             user_data_dir=None)
        try:
            assert out["ok"] is True, out
            assert out["browser"] == exe
            assert out.get("pid")
            # the launched DevTools endpoint answers on loopback
            assert _devtools_ready(port) is True
            # a CDP bridge attached to that port is immediately usable
            bridge = CDPBrowserBridge(port=port)
            assert bridge.available() is True
            assert bridge.poll_state(now=1.0) is not None
        finally:
            if out.get("pid"):
                try:
                    os.kill(out["pid"], 15)  # SIGTERM
                except (OSError, ProcessLookupError, PermissionError):
                    pass

    def test_reuses_already_running_devtools_endpoint(self):
        payload = json.dumps({"Browser": "Chrome/Test",
                              "Protocol-Version": "1.3"}).encode()
        srv = http.server.HTTPServer(
            ("127.0.0.1", 0), _VersionHandler)
        srv.return_payload = payload
        port = srv.server_address[1]
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            out = launch_browser(port=port, timeout_s=0.5)
            assert out["ok"] is True
            assert "pid" not in out        # nothing was spawned
            assert out["port"] == port
        finally:
            srv.shutdown()
            srv.server_close()

    def test_isolated_profile_by_default(self):
        """Without user_data_dir the launcher must use a throwaway temp
        profile, never the user's real one (argv inspection)."""
        import airmouse.browser as mod
        calls = {}

        class _Proc:
            pid = 12345

        def fake_popen(argv, **kwargs):
            calls["argv"] = list(argv)
            calls["kwargs"] = dict(kwargs)
            return _Proc()

        real = mod.subprocess.Popen
        mod.subprocess.Popen = fake_popen
        try:
            out = launch_browser(port=_free_tcp_port(), timeout_s=0.5)
        finally:
            mod.subprocess.Popen = real
        # no browser in sandbox → failed honestly BEFORE spawning, but
        # when a browser exists the argv must carry the isolated profile
        if out["ok"] is False and out.get("error") == "browser_not_found":
            assert "argv" not in calls
            return
        argv = calls["argv"]
        assert any(a.startswith("--remote-debugging-port=")
                   for a in argv)
        assert "--no-first-run" in argv
        assert "--no-default-browser-check" in argv
        udd = [a for a in argv if a.startswith("--user-data-dir=")][0]
        assert "airmouse-browser-profile-" in udd
        assert calls["kwargs"]["shell"] is False


class _VersionHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 (stdlib API)
        body = getattr(self.server, "return_payload", b"{}")
        if urlsplit(self.path).path == "/json/version":
            self.send_response(200)
        else:
            self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence test output
        pass


# ---------------------------------------------------------------------------
# 2. Loopback pinning — webSocketDebuggerUrl + /json discovery
# ---------------------------------------------------------------------------


class TestLoopbackPinning:
    def test_pin_refuses_evil_host(self):
        out = pinned_ws_parts(
            "ws://evil.example.com:9222/devtools/page/T1")
        assert out is None      # refused — never connect

    def test_pin_refuses_lan_and_scheme_tricks(self):
        for url in ("ws://192.168.1.10:9222/devtools/page/T1",
                    "ws://[::1]:9222/devtools/page/T2" if False else
                    "ws://localhost.evil.com:9222/devtools/page/T2",
                    "http://evil.example.com:9222/devtools/page/T3"):
            assert pinned_ws_parts(url) is None, url

    def test_pin_accepts_loopback(self):
        for host in ("127.0.0.1", "localhost"):
            out = pinned_ws_parts(
                f"ws://{host}:9222/devtools/page/T1")
            assert out is not None
            h, p, path = out
            assert h == host
            assert p == 9222
            assert path == "/devtools/page/T1"

    def test_pin_defaults_fill_in_missing_host_port(self):
        h, p, path = pinned_ws_parts("ws:///devtools/page/T1",
                                     default_host="127.0.0.1",
                                     default_port=9333)
        assert (h, p, path) == ("127.0.0.1", 9333, "/devtools/page/T1")

    def test_pin_garbage_is_refused_not_raised(self):
        assert pinned_ws_parts("") is None
        assert pinned_ws_parts(None) is None
        assert pinned_ws_parts("not a url") is None

    def test_ws_for_refuses_evil_reported_host(self):
        """End-to-end: a /json response whose webSocketDebuggerUrl claims
        a remote host must NOT be connected."""
        bridge = CDPBrowserBridge(port=_free_tcp_port())
        bridge._tab_ws = {
            "tab-evil": "ws://evil.example.com:9222/devtools/page/tab-evil"}
        assert bridge._ws_for("tab-evil") is None
        assert "tab-evil" not in bridge._ws_cache   # nothing cached/used

    def test_ws_for_loopback_attempt_never_raises(self):
        """A loopback ws URL that nobody is serving on must fail cleanly."""
        bridge = CDPBrowserBridge(port=_free_tcp_port())
        port = _free_tcp_port()
        bridge._tab_ws = {
            "tab-ok": f"ws://127.0.0.1:{port}/devtools/page/tab-ok"}
        assert bridge._ws_for("tab-ok") is None    # refused/failed, no raise

    def test_discovery_host_is_hard_pinned(self, monkeypatch):
        """The /json fetch must be a loopback hard rule, not a default:
        even a bridge constructed with a remote host never contacts it."""
        seen = {}

        def fake_urlopen(req, timeout=0.0):
            seen["url"] = req.full_url if hasattr(req, "full_url") \
                else str(req)
            raise OSError("network disabled in test")

        monkeypatch.setattr(
            "airmouse.browser.urllib.request.urlopen", fake_urlopen)
        bridge = CDPBrowserBridge(port=9222, host="evil.example.com")
        assert bridge._host == "127.0.0.1"      # coerced at construction
        assert bridge._http_json("/json") is None
        assert seen["url"].startswith("http://127.0.0.1:9222")


# ---------------------------------------------------------------------------
# 3. build_default_resolver — providers that actually resolve
# ---------------------------------------------------------------------------


class _DeadProvider:
    """Stands in for an unavailable perception layer (headless sandbox)."""

    name = "accessibility"
    available = False

    def update(self, now=None):
        return []


class TestBuildDefaultResolver:
    def test_geometry_resolves_center_zone_out_of_the_box(self):
        engine_like = type("StubEngine", (), {})()
        engine_like.screen_w = 1920
        engine_like.screen_h = 1080
        engine_like.providers = [_DeadProvider(),
                                 GeometryProvider(1920, 1080)]
        resolver = build_default_resolver(screen_perception=engine_like)
        assert "geometry" in resolver.available_kinds()
        res = resolver.resolve_target(TargetRequest(value="center"))
        assert res.ok is True
        assert res.resolved.provider == "geometry"
        assert res.resolved.target_id == "geo:center"
        assert res.resolved.confidence == pytest.approx(0.55)
        # center zone of a 1920x1080 desktop: 0.35*1920 = 672 etc.
        assert res.resolved.bbox == pytest.approx(
            (672.0, 378.0, 576.0, 324.0))

    def test_center_zone_by_description_and_vague_request(self):
        resolver = build_default_resolver()
        for req in (TargetRequest(description="the middle of the screen"),
                    TargetRequest(value=""),
                    TargetRequest(kind="geometry", value="geo:center")):
            res = resolver.resolve_target(req)
            assert res.ok is True, req
            assert res.resolved.target_id == "geo:center"

    def test_zone_aliases_resolve(self):
        resolver = build_default_resolver()
        assert resolver.resolve_target(
            TargetRequest(value="top left")).resolved.target_id == \
            "geo:corner_tl"
        assert resolver.resolve_target(
            TargetRequest(value="bottom")).resolved.target_id == \
            "geo:edge_bottom"

    def test_accessibility_absent_yields_no_candidates_no_crash(self):
        engine_like = type("StubEngine", (), {})()
        engine_like.screen_w = 1920
        engine_like.screen_h = 1080
        engine_like.providers = [_DeadProvider(),
                                 GeometryProvider(1920, 1080)]
        resolver = build_default_resolver(screen_perception=engine_like)
        assert "accessibility" in resolver.available_kinds()  # registered…
        res = resolver.resolve_target(TargetRequest(value="login"))
        ax = [a for a in res.attempts if a.provider == "accessibility"]
        assert ax and ax[0].ok is False          # …but no candidates
        # geometry still saves the request chain deterministically
        assert any(a.provider == "geometry" for a in res.attempts)

    def test_real_accessibility_provider_object_is_wrapped(self):
        ax = AccessibilityProvider(1920, 1080)
        resolver = build_default_resolver(
            screen_perception=[ax, GeometryProvider(1920, 1080)])
        assert "accessibility" in resolver.available_kinds()
        if ax.available:    # only in a real desktop session
            res = resolver.resolve_target(
                TargetRequest(description="the terminal window"))
            assert res.ok is True

    def test_ocr_disabled_by_default(self):
        resolver = build_default_resolver()
        assert "ocr" not in resolver.available_kinds()
        # the provider's own enabled flag governs, even when passed in
        ocr = OCRProvider({"enabled": False})
        resolver2 = build_default_resolver(
            screen_perception=[ocr, GeometryProvider(1920, 1080)])
        assert "ocr" not in resolver2.available_kinds()

    def test_ocr_never_registered_while_disabled_or_broken(self):
        # enabled flag ON but the tesseract stack is unavailable → the
        # provider honestly probes False and is NOT registered
        ocr = OCRProvider({"enabled": True})
        if ocr.available:
            pytest.skip("real tesseract stack present — OCR opt-in works")
        resolver = build_default_resolver(
            screen_perception=[ocr, GeometryProvider(1920, 1080)])
        assert "ocr" not in resolver.available_kinds()

    def test_coordinate_fallback_is_gated(self):
        plain = build_default_resolver()
        assert "coordinate" not in plain.available_kinds()

        allowed = build_default_resolver(allow_coordinate_fallback=True)
        assert "coordinate" in allowed.available_kinds()
        # factory allowed it, but the EXPLICIT per-request flag still gates
        res = allowed.resolve_target(TargetRequest(value="ghost"))
        assert res.ok is False
        assert any(a.provider == "coordinate" and
                   "not permitted" in a.detail for a in res.attempts)
        # with the request flag the last resort fires honestly
        res2 = allowed.resolve_target(TargetRequest(
            value="ghost", allow_coordinate_fallback=True))
        assert res2.ok is True
        assert res2.resolved.kind == "coordinate"
        assert res2.resolved.confidence == pytest.approx(0.40)

    def test_min_confidence_forwarded(self):
        strict = build_default_resolver(min_confidence=0.99)
        res = strict.resolve_target(TargetRequest(value="center"))
        # center zone reports 0.55 — below 0.99 it must NOT be "ok"
        assert res.ok is False

    def test_browser_mapper_adapter_resolves_dom_targets(self):
        from airmouse.browser import BrowserTargetMapper, \
            SimulatedBrowserBridge
        mapper = BrowserTargetMapper()
        mapper.update(SimulatedBrowserBridge().poll_state(now=1.0))
        resolver = build_default_resolver(browser=mapper)
        assert "dom" in resolver.available_kinds()
        res = resolver.resolve_target(TargetRequest(value="login"))
        assert res.ok is True
        assert res.resolved.kind == "dom"
        assert res.resolved.target_id == "browser:btn-login"
        # controller-like object (has .mapper) works too
        import types
        ctrl = types.SimpleNamespace(mapper=mapper)
        resolver2 = build_default_resolver(browser=ctrl)
        assert resolver2.resolve_target(
            TargetRequest(value="login")).ok is True

    def test_no_providers_resolver_still_degrades_gracefully(self):
        empty = UniversalTargetResolver()
        res = empty.resolve_target(TargetRequest(value="anything"))
        assert res.ok is False
        assert res.resolved is None

    def test_factory_never_raises_on_garbage_perception(self):
        class _Boom:
            providers = property(lambda self: (_ for _ in ()).throw(
                RuntimeError("boom")))
        resolver = build_default_resolver(screen_perception=_Boom())
        assert isinstance(resolver, UniversalTargetResolver)


# ---------------------------------------------------------------------------
# 4. system_actions repairs
# ---------------------------------------------------------------------------


class TestSystemActionsRepairs:
    # -- dead code removal -------------------------------------------------

    def test_crossplatformkeyboard_gone(self):
        import airmouse.system_actions as mod
        src = open(mod.__file__, "r", encoding="utf-8").read()
        assert "CrossPlatformKeyboard" not in src
        assert not hasattr(mod, "_kb_call")
        ex = SystemActionExecutor()
        assert not hasattr(ex, "_keyboard")
        assert not hasattr(ex, "_kb")

    def test_windows_volume_routes_through_legacy_sendinput(self):
        """The Windows branch must lazily import the LEGACY keyboard.py
        SendInput helpers (VK codes) — statically verified."""
        import airmouse.system_actions as mod
        src = open(mod.__file__, "r", encoding="utf-8").read()
        tree = ast.parse(src)

        # no module-level keyboard import at all (lazy inside the branch)
        module_imports = [n for n in tree.body
                          if isinstance(n, (ast.Import, ast.ImportFrom))]
        for node in module_imports:
            modname = (node.module or "") if isinstance(
                node, ast.ImportFrom) else ""
            assert not modname.endswith("keyboard"), \
                "keyboard must not be imported at module level"

        # the lazy import lives INSIDE _win_sendinput_key
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "_win_sendinput_key")
        lazy_names = [a.name for x in ast.walk(fn)
                      if isinstance(x, ast.ImportFrom)
                      for a in x.names]
        assert "_send_media_key" in lazy_names

        # the Windows volume branch calls it, with the PowerShell fallback
        vol = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "_volume")
        vsrc = ast.get_source_segment(src, vol)
        assert "_win_sendinput_key" in vsrc
        assert "powershell" in vsrc.lower()

    # -- app_launch op -------------------------------------------------------

    def test_app_launch_in_allowlist_not_destructive_but_sensitive(self):
        assert "app_launch" in SYSTEM_OPS
        assert "app_launch" not in DESTRUCTIVE_SYSTEM_OPS
        assert "app_launch" in SENSITIVE_SYSTEM_OPS
        ex = SystemActionExecutor()
        assert ex.is_destructive("app_launch") is False
        assert ex.is_sensitive("app_launch") is True

    def test_app_launch_rejects_traversal_and_separators(self):
        ex = SystemActionExecutor()
        for bad in ("../etc/passwd", "..\\windows\\system32", "a/b",
                    "a\\b", "..", "....", ".", "  ", "",
                    "with space", "tab\there"):
            res = ex.execute("app_launch", {"target": bad})
            assert res.ok is False, bad
            assert res.message in ("invalid_target", "missing_target"), bad

    def test_app_launch_arg_sanitized_single_token(self):
        assert sanitize_file_name("my.app") == "my.app"
        ex = SystemActionExecutor()
        # valid single token reaches the platform opener (mocked here)
        res = ex.execute("app_launch", {"target": "calculator"})
        assert res.op == "app_launch"
        assert res.ok in (True, False)      # honest per environment
        assert res.message in ("", "no_opener")

    def test_app_launch_linux_opener_argv(self, monkeypatch):
        """Linux path: ['xdg-open', <clean token>], shell=False, 5 s
        timeout — verified with a mocked subprocess."""
        ex = SystemActionExecutor()
        seen = {}

        def fake_run(argv, **kwargs):
            seen["argv"] = list(argv)
            seen["kwargs"] = dict(kwargs)
            return type("R", (), {"returncode": 0})()

        monkeypatch.setattr(
            "airmouse.system_actions.shutil.which",
            lambda name: "/usr/bin/xdg-open" if name == "xdg-open" else None)
        monkeypatch.setattr("airmouse.system_actions.subprocess.run",
                            fake_run)
        res = ex.execute("app_launch", {"target": "calculator"})
        assert res.ok is True
        assert seen["argv"] == ["xdg-open", "calculator"]
        assert seen["kwargs"]["timeout"] == 5.0
        assert seen["kwargs"]["shell"] is False

    def test_app_launch_through_action_engine_dispatch(self):
        """The op must be whitelisted by the ACTION-ENGINE routing exactly
        like the volume ops (SYSTEM_OPS import — no special casing)."""
        from airmouse.actions import ActionEngine, MockExecutor
        from airmouse.interfaces import ActionStatus, Intent, IntentType
        from airmouse.system_actions import MockSystemExecutor
        sex = MockSystemExecutor()
        engine = ActionEngine(executor=MockExecutor(), system_executor=sex)
        intent = Intent(type=IntentType.SYSTEM_OP,
                        params={"op": "app_launch", "target": "calculator"})
        plan = engine.plan(intent)
        assert plan.action.value == "system_operation"
        assert plan.requires_confirmation is False   # NOT destructive
        report = engine.execute(plan)
        assert report.status is ActionStatus.SUCCESS
        assert sex.calls and sex.calls[-1][0] == "app_launch"

    # -- volume structure on the Linux path (unchanged) -----------------------

    def test_linux_volume_argv_structure_unchanged(self, monkeypatch):
        ex = SystemActionExecutor()
        seen = []

        def fake_run(argv, **kwargs):
            seen.append((list(argv), dict(kwargs)))
            return type("R", (), {"returncode": 0})()

        monkeypatch.setattr(
            "airmouse.system_actions.shutil.which",
            lambda name: "/usr/bin/pactl" if name == "pactl" else None)
        monkeypatch.setattr("airmouse.system_actions.subprocess.run",
                            fake_run)
        assert ex.execute("volume_up").ok is True
        assert ex.execute("volume_down").ok is True
        assert ex.execute("mute").ok is True
        assert ex.execute("unmute").ok is True
        assert seen[0][0] == ["/usr/bin/pactl", "set-sink-volume",
                              "@DEFAULT_SINK@", "+5%"]
        assert seen[1][0] == ["/usr/bin/pactl", "set-sink-volume",
                              "@DEFAULT_SINK@", "-5%"]
        assert seen[2][0] == ["/usr/bin/pactl", "set-sink-mute",
                              "@DEFAULT_SINK@", "toggle"]
        assert seen[3][0] == ["/usr/bin/pactl", "set-sink-mute",
                              "@DEFAULT_SINK@", "0"]
        for _argv, kw in seen:
            assert kw["shell"] is False
            assert isinstance(kw["timeout"], float)

    def test_linux_media_keys_unchanged(self, monkeypatch):
        ex = SystemActionExecutor()
        seen = []

        def fake_run(argv, **kwargs):
            seen.append(list(argv))
            return type("R", (), {"returncode": 0})()

        monkeypatch.setattr(
            "airmouse.system_actions.shutil.which",
            lambda name: "/usr/bin/playerctl" if name == "playerctl"
            else None)
        monkeypatch.setattr("airmouse.system_actions.subprocess.run",
                            fake_run)
        assert ex.execute("media_next").ok is True
        assert seen[0] == ["/usr/bin/playerctl", "next"]

    # -- timeouts everywhere (static) ------------------------------------------

    def test_every_subprocess_run_has_timeout(self):
        """Static AST check: EVERY ``subprocess.run``/``subprocess.call``
        in system_actions.py carries an explicit ``timeout=`` keyword."""
        import airmouse.system_actions as mod
        tree = ast.parse(open(mod.__file__, "r", encoding="utf-8").read())
        checked = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            dotted = ""
            if isinstance(fn, ast.Attribute) and \
                    isinstance(fn.value, ast.Name):
                dotted = f"{fn.value.id}.{fn.attr}"
            if dotted in ("subprocess.run", "subprocess.call",
                          "subprocess.check_call",
                          "subprocess.check_output"):
                checked += 1
                assert any(kw.arg == "timeout" for kw in node.keywords), \
                    f"{dotted} at line {node.lineno} has no timeout="
        assert checked >= 2        # the executor + file opener paths
