"""Tests for airmouse.browser + airmouse.browser_bridge (v10 §11–§13).

Covers: the simulated bridge state/click/verify cycle, ScreenTarget
mapping, target-map finders, the deterministic semantic resolver for the
§12 utterance set, the action verifier (§13), the BrowserController
execute+verify pipeline (including the bus/context wiring and the
offline gate), the CDP bridge's never-raise/unavailable behaviour, and
the localhost bridge-server round-trip.
"""

import json
import socket
import urllib.error
import urllib.request

import pytest

from airmouse.browser import (
    BROWSER_ACTIONS,
    BrowserActionVerifier,
    BrowserBridge,
    BrowserController,
    BrowserElement,
    BrowserResolution,
    BrowserState,
    BrowserTargetMapper,
    CDPBrowserBridge,
    SemanticBrowserResolver,
    SimulatedBrowserBridge,
    element_to_screen_target,
)
from airmouse.browser_bridge import (
    BrowserBridgeServer,
    verify_bridge_server,
)
from airmouse.context import ContextEngine
from airmouse.eventbus import EventBus
from airmouse.interfaces import EventKind, Modality, ScreenTargetType

DEMO_URL = "https://demo.airmouse.local/home"
YOUTUBE_URL = "https://www.youtube.com/"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _free_tcp_port() -> int:
    """Find a free localhost port (bind 0 → read → close)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def _mapper_with_demo(bridge=None):
    bridge = bridge if bridge is not None else SimulatedBrowserBridge()
    mapper = BrowserTargetMapper()
    mapper.update(bridge.poll_state(now=1.0))
    return mapper, bridge


# ---------------------------------------------------------------------------
# BrowserBridge protocol + simulated bridge
# ---------------------------------------------------------------------------


def test_browser_bridge_protocol_raises():
    b = BrowserBridge()
    with pytest.raises(NotImplementedError):
        b.available()
    with pytest.raises(NotImplementedError):
        b.poll_state()


def test_simulated_bridge_default_state_and_click_cycle():
    bridge = SimulatedBrowserBridge()
    state = bridge.poll_state(now=1.0)
    assert state.browser == "chrome"
    assert state.timestamp == 1.0
    assert len(state.tabs) == 2
    assert state.active_tab()["id"] == "tab-1"
    assert state.url == DEMO_URL
    ids = [el.id for el in state.elements]
    assert ids == ["input-search", "btn-login", "btn-signup",
                   "link-downloads", "link-home", "link-youtube"]
    # every element collected from a page is untrusted
    assert all(el.untrusted for el in state.elements)

    before = bridge.poll_state(now=1.0)
    assert bridge.available() is True
    assert bridge.click_element("btn-login") is True
    assert "btn-login" in bridge.clicked_elements
    assert bridge.last_action == {"type": "click", "element_id": "btn-login",
                                  "ok": True}
    after = bridge.poll_state(now=1.1)
    assert after.focused_element_id == "btn-login"

    verdict = BrowserActionVerifier().verify(
        "click", before.elements[1], before, after, bridge)
    assert verdict["status"] == "passed"
    assert verdict["similarity"] == 1.0

    assert bridge.click_element("no-such-element") is False
    assert bridge.last_action["ok"] is False


def test_simulated_bridge_poll_state_returns_copy():
    bridge = SimulatedBrowserBridge()
    first = bridge.poll_state(now=2.0)
    first.url = "tampered://mutated"
    first.elements[0].text = "MUTATED"
    second = bridge.poll_state(now=3.0)
    assert second.url == DEMO_URL
    assert second.elements[0].text == "Search"
    assert second.timestamp == 3.0


def test_simulated_bridge_navigation_and_tabs():
    bridge = SimulatedBrowserBridge()
    home = bridge.poll_state(now=1.0).url

    # link click navigates (url becomes observable evidence of the click)
    assert bridge.click_element("link-downloads") is True
    s = bridge.poll_state(now=1.1)
    assert s.url == "https://demo.airmouse.local/downloads"

    assert bridge.navigate("https://docs.example.com/guide") is True
    assert bridge.poll_state(now=1.2).url == "https://docs.example.com/guide"
    assert bridge.go_back() is True
    assert bridge.poll_state(now=1.3).url == \
        "https://demo.airmouse.local/downloads"
    assert bridge.go_forward() is True
    assert bridge.poll_state(now=1.4).url == "https://docs.example.com/guide"
    assert bridge.go_forward() is False          # nothing further forward
    assert bridge.go_back() is True
    assert bridge.go_back() is True
    assert bridge.poll_state(now=1.5).url == home
    assert bridge.go_back() is False             # nothing earlier

    assert bridge.new_tab() is True
    s = bridge.poll_state(now=1.6)
    assert len(s.tabs) == 3
    assert s.active_tab_id == "tab-3"

    assert bridge.switch_tab("tab-2") is True
    s = bridge.poll_state(now=1.7)
    assert s.active_tab_id == "tab-2"
    assert s.url == YOUTUBE_URL

    assert bridge.close_tab("tab-2") is True
    s = bridge.poll_state(now=1.8)
    assert len(s.tabs) == 2
    assert s.active_tab_id == "tab-3"            # fallback to remaining tab

    assert bridge.close_tab("tab-does-not-exist") is False
    assert bridge.switch_tab("tab-does-not-exist") is False


# ---------------------------------------------------------------------------
# ScreenTarget mapping + target-map finders
# ---------------------------------------------------------------------------


def test_element_to_screen_target_mapping():
    el = BrowserElement(id="btn-x", role="button", text="Buy",
                        tag="button", bbox=(0.1, 0.2, 0.3, 0.4))
    t = element_to_screen_target(el)
    assert t.id == "browser:btn-x"
    assert t.type is ScreenTargetType.BUTTON
    assert t.bbox == (192.0, 216.0, 576.0, 432.0)     # 1920x1080 scaled
    assert t.center == (480.0, 432.0)
    assert t.source == "dom"
    assert t.text == "Buy"
    assert t.actionable is True
    assert t.application == "browser"

    # role → type table
    assert element_to_screen_target(BrowserElement(
        id="l", role="link")).type is ScreenTargetType.LINK
    assert element_to_screen_target(BrowserElement(
        id="i", role="input")).type is ScreenTargetType.TEXT_FIELD
    assert element_to_screen_target(BrowserElement(
        id="t", role="tab")).type is ScreenTargetType.BROWSER_CONTROL
    assert element_to_screen_target(BrowserElement(
        id="h", role="heading")).type is ScreenTargetType.UNKNOWN

    # absolute-pixel elements pass through unscaled
    px = BrowserElement(id="px", role="button", bbox=(10, 20, 30, 40),
                        is_px=True, browser="edge")
    tp = element_to_screen_target(px)
    assert tp.bbox == (10.0, 20.0, 30.0, 40.0)
    assert tp.application == "edge"

    # defaults: untrusted, never raises on garbage bboxes
    assert BrowserElement(id="d").untrusted is True
    assert element_to_screen_target(
        BrowserElement(id="g", bbox=None)).id == "browser:unknown"


def test_browser_target_mapper_finders():
    mapper, _bridge = _mapper_with_demo()

    # substring, case-insensitive
    t = mapper.find_by_text("you")
    assert t is not None and t.id == "browser:link-youtube"
    assert mapper.find_by_text("YOUTUBE").id == "browser:link-youtube"
    assert mapper.find_by_text("does-not-exist") is None
    assert mapper.find_by_text("") is None

    # 1-based ordinal over actionable elements
    assert mapper.find_by_ordinal(1).id == "browser:input-search"
    assert mapper.find_by_ordinal(2).id == "browser:btn-login"
    assert mapper.find_by_ordinal(4).id == "browser:link-downloads"
    assert mapper.find_by_ordinal(99) is None
    assert mapper.find_by_ordinal(0) is None

    # role + optional needle
    assert mapper.find_by_role("link", "down").id == "browser:link-downloads"
    assert mapper.find_by_role("link").id == "browser:link-downloads"
    assert mapper.find_by_role("button", "login").id == "browser:btn-login"
    assert mapper.find_by_role("button", "nope") is None
    assert mapper.find_by_role("heading") is None

    # element-level variants mirror the target lookups
    assert mapper.find_element_by_ordinal(1, role="link").id == \
        "link-downloads"
    assert mapper.element_by_id("btn-signup").role == "button"

    empty = BrowserTargetMapper()
    assert empty.targets() == []
    assert empty.find_by_text("login") is None


# ---------------------------------------------------------------------------
# Semantic resolver (§12 utterances)
# ---------------------------------------------------------------------------


def test_resolver_click_patterns():
    mapper, _bridge = _mapper_with_demo()
    r = SemanticBrowserResolver(mapper)

    res = r.resolve("click the login button", now=1.0)
    assert res.matched and res.action == "click"
    assert res.element.id == "btn-login"
    assert res.confidence > 0.5

    res = r.resolve("click login")
    assert res.matched and res.element.id == "btn-login"

    # normalization: case + punctuation
    res = r.resolve("Click The Login Button!")
    assert res.matched and res.element.id == "btn-login"

    res = r.resolve("click the first link")
    assert res.matched and res.element.id == "link-downloads"
    assert res.params.get("ordinal") == 1

    res = r.resolve("click the second link")
    assert res.matched and res.element.id == "link-home"

    res = r.resolve("click the 2nd link")
    assert res.matched and res.element.id == "link-home"

    res = r.resolve("open the download link")
    assert res.matched and res.action == "click"
    assert res.element.id == "link-downloads"

    res = r.resolve("click on the sign up button")
    assert res.matched and res.element.id == "btn-signup"

    res = r.resolve("click totally-unknown-widget")
    assert res.matched is False
    assert res.confidence == 0.0
    assert res.element is None


def test_resolver_focus_and_type():
    mapper, _bridge = _mapper_with_demo()
    r = SemanticBrowserResolver(mapper)

    res = r.resolve("focus the search box")
    assert res.matched and res.action == "focus"
    assert res.element.id == "input-search"

    res = r.resolve("focus search")
    assert res.matched and res.element.id == "input-search"

    res = r.resolve("focus the nothere field")
    assert res.matched is False

    res = r.resolve("type hello world")
    assert res.matched and res.action == "type"
    assert res.params == {"text": "hello world"}

    res = r.resolve("type")
    assert res.matched is False


def test_resolver_tabs_and_navigation():
    mapper, _bridge = _mapper_with_demo()
    r = SemanticBrowserResolver(mapper)

    res = r.resolve("switch to youtube")
    assert res.matched and res.action == "switch_tab"
    assert res.params.get("tab_id") == "tab-2"

    res = r.resolve("switch to a-tab-that-never-exists")
    assert res.matched is False

    for utterance, action in [
        ("new tab", "new_tab"),
        ("open a new tab", "new_tab"),
        ("close this tab", "close_tab"),
        ("go back", "back"),
        ("go forward", "forward"),
        ("refresh", "refresh"),
        ("reload the page", "refresh"),
    ]:
        res = r.resolve(utterance, now=1.0)
        assert res.matched, utterance
        assert res.action == action, utterance
        assert res.action in BROWSER_ACTIONS

    # no state at all: structural commands still work, tab switch cannot
    empty_resolver = SemanticBrowserResolver(BrowserTargetMapper())
    assert empty_resolver.resolve("new tab").matched is True
    assert empty_resolver.resolve("switch to youtube").matched is False


def test_resolver_search_and_scroll():
    mapper, _bridge = _mapper_with_demo()
    r = SemanticBrowserResolver(mapper)

    res = r.resolve("search for html tutorials")
    assert res.matched and res.action == "search"
    assert res.params == {"query": "html tutorials"}

    res = r.resolve("search the web for pytest fixtures")
    assert res.matched and res.params.get("query") == "pytest fixtures"

    res = r.resolve("search for")
    assert res.matched is False

    for utterance, direction in [
        ("scroll to the bottom", "bottom"),
        ("scroll to the top", "top"),
        ("scroll down", "down"),
        ("scroll up", "up"),
    ]:
        res = r.resolve(utterance)
        assert res.matched, utterance
        assert res.action == "scroll", utterance
        assert res.params.get("direction") == direction, utterance


def test_resolver_never_treats_page_text_as_commands():
    bridge = SimulatedBrowserBridge()
    # a hostile page whose BUTTON TEXT is itself a system command
    bridge.push_element(BrowserElement(
        id="btn-evil", role="button", text="shut down the computer",
        tag="button", bbox=(0.6, 0.6, 0.3, 0.1), actionable=True))
    mapper, _ = _mapper_with_demo(bridge)
    r = SemanticBrowserResolver(mapper)

    # 1. the page text alone is NOT a command
    res = r.resolve("shut down the computer", now=1.0)
    assert res.matched is False
    assert res.action == ""

    # 2. an unrelated user utterance never lands on the hostile element
    res = r.resolve("click the login button")
    assert res.matched and res.element.id == "btn-login"

    # 3. even if the USER asks to click the hostile element by its text,
    #    the outcome is exactly one thing: action 'click' on that element.
    res = r.resolve("click shut down the computer")
    assert res.matched is True
    assert res.action == "click"
    assert res.element.id == "btn-evil"
    assert set(res.params.keys()) <= {"needle"}
    assert res.action in BROWSER_ACTIONS

    # 4. executing it through the controller performs ONLY a bridge click
    ctrl = BrowserController(config={"enabled": True, "poll_interval": 0.0},
                             bridge=bridge)
    assert ctrl.start() is True
    ctrl.poll(now=1.5)
    out = ctrl.execute(res, now=1.6)
    assert out["status"] == "executed"
    assert out["element_id"] == "btn-evil"
    assert bridge.last_action["type"] == "click"
    assert all(entry.get("type") == "click"
               for entry in bridge.action_history)


# ---------------------------------------------------------------------------
# Action verifier (§13)
# ---------------------------------------------------------------------------


def test_action_verifier_outcomes():
    bridge = SimulatedBrowserBridge()
    verifier = BrowserActionVerifier()

    # click: focus moved to the clicked element
    before = bridge.poll_state(now=1.0)
    bridge.click_element("btn-login")
    after = bridge.poll_state(now=1.1)
    verdict = verifier.verify("click", before.elements[1], before, after,
                              bridge)
    assert verdict["status"] == "passed"

    # click via link navigation (url changed)
    bridge2 = SimulatedBrowserBridge()
    b0 = bridge2.poll_state(now=1.0)
    bridge2.click_element("link-downloads")
    b1 = bridge2.poll_state(now=1.1)
    el = b0.elements[3]
    assert verifier.verify("click", el, b0, b1, bridge2)["status"] == \
        "passed"

    # navigate: url changed
    s0 = bridge2.poll_state(now=1.2)
    bridge2.navigate("https://example.org/docs")
    s1 = bridge2.poll_state(now=1.3)
    assert verifier.verify("navigate", None, s0, s1, bridge2)["status"] == \
        "passed"
    assert verifier.verify("navigate", None, s0, s0, bridge2)["status"] == \
        "failed"

    # new_tab / close_tab: tab count
    t0 = bridge2.poll_state(now=1.4)
    bridge2.new_tab()
    t1 = bridge2.poll_state(now=1.5)
    assert verifier.verify("new_tab", None, t0, t1, bridge2)["status"] == \
        "passed"
    bridge2.close_tab("tab-3")
    t2 = bridge2.poll_state(now=1.6)
    assert verifier.verify("close_tab", None, t1, t2, bridge2)["status"] == \
        "passed"

    # back: url changed
    u0 = bridge2.poll_state(now=1.7)
    bridge2.go_back()
    u1 = bridge2.poll_state(now=1.8)
    assert verifier.verify("back", None, u0, u1, bridge2)["status"] == \
        "passed"

    # refresh: url is expected to stay, the bridge's action record decides
    r0 = bridge2.poll_state(now=1.9)
    bridge2.refresh()
    r1 = bridge2.poll_state(now=2.0)
    assert verifier.verify("refresh", None, r0, r1, bridge2)["status"] == \
        "passed"

    # scroll: bridge action record
    c0 = bridge2.poll_state(now=2.1)
    bridge2.scroll(600)
    c1 = bridge2.poll_state(now=2.2)
    assert verifier.verify("scroll", None, c0, c1, bridge2)["status"] == \
        "passed"

    # unknown states
    assert verifier.verify("click", before.elements[1], before, None,
                           bridge)["status"] == "unknown"
    assert verifier.verify("click", before.elements[1], None, after,
                           bridge)["status"] == "unknown"
    assert verifier.verify("teleport", None, before, after,
                           bridge)["status"] == "unknown"

    # failed click: nothing changed
    f0 = bridge2.poll_state(now=2.3)
    f1 = bridge2.poll_state(now=2.4)
    assert verifier.verify("click", f0.elements[1], f0, f1,
                           bridge2)["status"] == "failed"


# ---------------------------------------------------------------------------
# BrowserController (§12–§13 orchestration)
# ---------------------------------------------------------------------------


def test_controller_execute_and_verify():
    bridge = SimulatedBrowserBridge()
    ctrl = BrowserController(config={"enabled": True, "poll_interval": 0.0},
                             bridge=bridge)
    assert ctrl.start() is True
    assert ctrl.poll(now=1.0) is not None

    resolver = SemanticBrowserResolver(ctrl.mapper)

    out = ctrl.execute(resolver.resolve("click the login button"), now=1.5)
    assert out["status"] == "executed"
    assert out["action"] == "click"
    assert out["element_id"] == "btn-login"
    assert out["sensitive"] is False
    assert out["verification"]["status"] == "passed"
    assert out["verified"] is True

    out = ctrl.execute(resolver.resolve("close this tab"), now=2.0)
    assert out["status"] == "executed"
    assert out["sensitive"] is True            # destructive → marked
    assert out["verification"]["status"] == "passed"

    out = ctrl.execute(resolver.resolve("search for html tutorials"),
                       now=2.5)
    assert out["status"] == "executed"
    assert out["verification"]["status"] == "passed"

    # unmatched click → failed, never raises
    out = ctrl.execute(resolver.resolve("click no-such-thing"), now=3.0)
    assert out["status"] == "failed"

    # poll_interval throttling is honoured
    ctrl.poll_interval = 10.0
    assert ctrl.poll(now=3.1) is None
    assert ctrl.poll(now=20.0) is not None


def test_controller_poll_publishes_event_and_context():
    bridge = SimulatedBrowserBridge()
    bus = EventBus()
    engine = ContextEngine()
    ctrl = BrowserController(config={"enabled": True, "poll_interval": 0.0},
                             bridge=bridge, context_engine=engine, bus=bus)
    sub = bus.subscribe(kinds={EventKind.BROWSER_TARGET})
    assert ctrl.start() is True

    state = ctrl.poll(now=1.0)
    assert state is not None
    ev = sub.poll()
    assert ev is not None
    assert ev.kind is EventKind.BROWSER_TARGET
    assert ev.modality is Modality.BROWSER
    assert ev.source == "browser_bridge"
    assert ev.payload["url"] == DEMO_URL
    assert ev.payload["title"] == "Demo Portal - AirMouse Test Page"
    assert ev.timestamp == 1.0

    snap = engine.snapshot()
    assert snap.current_url == DEMO_URL
    assert snap.active_browser == "chrome"
    assert snap.active_tab_title == "Demo Portal - AirMouse Test Page"
    assert len(snap.browser_targets) == 6
    assert all(t.source == "dom" for t in snap.browser_targets)
    assert ctrl.targets()[0].application == "chrome"

    # throttled poll → no new event
    ctrl.poll_interval = 5.0
    assert ctrl.poll(now=1.5) is None
    assert sub.poll() is None
    # fresh poll → next event
    assert ctrl.poll(now=10.0) is not None
    ev2 = sub.poll()
    assert ev2 is not None and ev2.timestamp == 10.0


def test_controller_bridge_selection_and_unavailable():
    # default config: disabled → start() refuses
    ctrl = BrowserController()
    assert ctrl.start() is False
    res = SemanticBrowserResolver(ctrl.mapper).resolve("new tab")
    assert res.matched is True            # structural command still resolves
    out = ctrl.execute(res)
    assert out["status"] == "unavailable"

    # explicit CDP + offline gate → stays off, never reaches the network
    ctrl_cdp = BrowserController(config={
        "enabled": True, "bridge": "cdp",
        "cdp_port": _free_tcp_port(), "offline": True})
    assert ctrl_cdp.start() is False
    assert ctrl_cdp.execute(res)["status"] == "unavailable"

    # auto + offline → falls back to the simulated bridge (always works)
    ctrl_auto = BrowserController(config={
        "enabled": True, "bridge": "auto", "offline": True})
    assert ctrl_auto.start() is True
    assert isinstance(ctrl_auto.bridge, SimulatedBrowserBridge)


# ---------------------------------------------------------------------------
# CDP bridge — unreachable / offline behaviour (never raises)
# ---------------------------------------------------------------------------


def test_cdp_bridge_unavailable_on_dead_port():
    cdp = CDPBrowserBridge(port=_free_tcp_port())
    assert cdp.available() is False
    assert cdp.poll_state(now=1.0) is None
    # every mutation degrades to False without raising
    assert cdp.click_element("x") is False
    assert cdp.focus_element("x") is False
    assert cdp.type_text("hello") is False
    assert cdp.navigate("https://example.invalid/") is False
    assert cdp.new_tab() is False
    assert cdp.close_tab("abc") is False
    assert cdp.switch_tab("abc") is False
    assert cdp.scroll(120) is False
    assert cdp.go_back() is False
    assert cdp.go_forward() is False
    assert cdp.refresh() is False


def test_cdp_bridge_offline_gate():
    cdp = CDPBrowserBridge(port=_free_tcp_port(), offline=True)
    assert cdp.available() is False       # immediate: no network attempt
    assert cdp.poll_state(now=1.0) is None


# ---------------------------------------------------------------------------
# browser_bridge server (§11)
# ---------------------------------------------------------------------------


def test_verify_bridge_server_roundtrip():
    assert verify_bridge_server() is True


def test_bridge_server_validation_and_caps():
    srv = BrowserBridgeServer(port=0)
    try:
        assert srv.start() is True
        assert srv.running is True
        assert srv.port > 0
        assert srv.url.startswith("http://127.0.0.1:")
        base = f"http://127.0.0.1:{srv.port}"

        # health
        with urllib.request.urlopen(base + "/health", timeout=1.0) as resp:
            assert json.loads(resp.read().decode("utf-8")) == {"ok": True}

        # GET /state before anything was posted → 404
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(srv.url, timeout=1.0)
        assert err.value.code == 404

        # invalid JSON → 400
        bad = urllib.request.Request(srv.url, data=b"{definitely-not-json",
                                     method="POST")
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(bad, timeout=1.0)
        assert err.value.code == 400

        # oversized payload (>256 KB) → 413
        big = b'{"x": "' + b"a" * (256 * 1024 + 64) + b'"}'
        big_req = urllib.request.Request(srv.url, data=big, method="POST")
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(big_req, timeout=2.0)
        assert err.value.code == 413

        # unknown path → 404
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(base + "/nope", timeout=1.0)
        assert err.value.code == 404

        # valid round-trip; only the LATEST state is kept
        state1 = {"browser": "chrome", "url": "https://a.example/",
                  "title": "A", "elements": []}
        state2 = {"browser": "chrome", "url": "https://b.example/",
                  "title": "B", "elements": [
                      {"id": "e1", "role": "button", "text": "Ok"}]}
        for payload in (state1, state2):
            req = urllib.request.Request(
                srv.url, data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST")
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                assert json.loads(resp.read().decode("utf-8")) == {"ok": True}
        with urllib.request.urlopen(srv.url, timeout=1.0) as resp:
            assert json.loads(resp.read().decode("utf-8")) == state2
        assert srv.latest_state() == state2
    finally:
        srv.stop()
        srv.stop()          # idempotent
    assert srv.running is False
