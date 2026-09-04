"""AirMouse v10.0.0 — mission §21 comprehensive test suite.

One file covering every NEW v10 subsystem, fully headless and
deterministic:

    §6/§7   voice command grammar + registry
    §4/§5   offline voice engine (modes, VAD, wake word, dictation)
    §3      universal local event bus
    §8      context engine
    §9      gesture registry (built-ins + custom sequences)
    §16     RF abstraction
    §10     system + file action executors
    §18     safety destructive-confirmation gates
    §19     hands-free sensor combos
    §17     true offline mode (REAL socket blocking)
    §11-13  browser semantic resolution + action verification
    §14     fusion pipeline (voice / injection / RF gesture → action)
    §20/§21 bounded performance assertions

Constraints honoured: no cv2/mediapipe/pynput imports, explicit
``now=`` timestamps everywhere, no sleeps, fresh engines per test,
whole file < 30 s.
"""
from __future__ import annotations

import socket
import struct
import time

import pytest

from airmouse.interfaces import (
    ActionStatus,
    AppContext,
    Event,
    EventKind,
    GazeState,
    Intent,
    IntentType,
    Modality,
    ScreenTarget,
    ScreenTargetType,
)
from airmouse.voice_commands import match_command_grammar
from airmouse.offline_voice import (
    EnergyVAD,
    OfflineVoiceEngine,
    SimulatedSpeechProvider,
    voice_match_to_intent,
)
from airmouse.eventbus import EventBus, MultiSubscriber
from airmouse.context import ContextEngine
from airmouse.gesture_registry import (
    CustomGestureMapping,
    GestureRegistry,
)
from airmouse.rf import DummyRFProvider, RFBridge, SimulatedRFProvider
from airmouse.system_actions import (
    DESTRUCTIVE_FILE_OPS,
    DESTRUCTIVE_SYSTEM_OPS,
    FILE_OPS,
    SYSTEM_OPS,
    FileActionExecutor,
    MockSystemExecutor,
    SystemActionExecutor,
    sanitize_file_name,
    validate_url,
)
from airmouse.safety import SafetySystem
from airmouse.hands_free import (
    HANDS_FREE_COMBOS,
    SensorHealth,
    effective_combo,
)
from airmouse.offline import OfflineGate, network_isolation, run_offline_selftest
from airmouse.browser import (
    BrowserActionVerifier,
    BrowserController,
    BrowserResolution,
    SemanticBrowserResolver,
    SimulatedBrowserBridge,
)
from airmouse.agent import InteractionAgent


# ---------------------------------------------------------------------------
# shared deterministic doubles
# ---------------------------------------------------------------------------


def _button(text="Submit", bbox=(0.0, 0.0, 10.0, 10.0)):
    return ScreenTarget(id="btn-" + text.lower(), type=ScreenTargetType.BUTTON,
                        bbox=bbox, text=text, confidence=0.9, actionable=True)


def _pcm(level, samples=160):
    """One 16-bit PCM chunk of constant amplitude (deterministic VAD feed)."""
    return struct.pack("<%dh" % samples, *([level] * samples))


class StubExecutor:
    """Executor protocol double returning truthy dicts (records calls)."""

    def __init__(self):
        self.calls = []

    def click(self, x, y):
        self.calls.append(("click", (x, y)))
        return {"pointer": (x, y)}

    def move(self, x, y):
        self.calls.append(("move", (x, y)))
        return {"pointer": (x, y)}

    def double_click(self, x, y):
        return {"pointer": (x, y)}

    def scroll(self, amount):
        return {"scroll": amount}

    def type_text(self, text):
        return {"typed": text}

    def hotkey(self, *keys):
        self.calls.append(("hotkey", keys))
        return True


class FakeScreenProvider:
    """ScreenProvider double with no targets (headless screen model)."""

    name = "fake"

    def update(self, now=None):
        return []


def _agent(**overrides):
    overrides.setdefault("screen_providers", [FakeScreenProvider()])
    overrides.setdefault("executor", StubExecutor())
    overrides.setdefault("verify_actions", False)
    return InteractionAgent({"mode": "fusion"}, **overrides)


# ===========================================================================
# 1. VOICE GRAMMAR (§6/§7)
# ===========================================================================


def test_grammar_open_app():
    m = match_command_grammar("open firefox")
    assert m.is_command and m.name == "open_app"
    assert m.intent is IntentType.OPEN
    assert m.entities == {"app": "firefox"}
    assert m.confidence == 1.0


def test_grammar_close_app():
    m = match_command_grammar("close chrome")
    assert m.is_command and m.name == "close_app"
    assert m.intent is IntentType.CLOSE
    assert m.entities == {"app": "chrome"}
    assert m.params.get("what") == "app"


def test_grammar_volume_up():
    m = match_command_grammar("volume up")
    assert m.is_command and m.name == "volume_up"
    assert m.intent is IntentType.VOLUME
    assert m.params.get("direction") == "up"


def test_grammar_mute_and_unmute():
    mute = match_command_grammar("mute")
    assert mute.is_command and mute.name == "mute"
    assert mute.params.get("direction") == "mute"
    unmute = match_command_grammar("unmute")
    assert unmute.is_command and unmute.name == "unmute"
    assert unmute.params.get("direction") == "unmute"


def test_grammar_lock_sensitive():
    m = match_command_grammar("lock")
    assert m.is_command and m.name == "lock"
    assert m.intent is IntentType.LOCK
    assert m.sensitive and not m.destructive


def test_grammar_restart_destructive_and_sensitive():
    m = match_command_grammar("restart")
    assert m.is_command and m.name == "restart"
    assert m.intent is IntentType.RESTART
    assert m.destructive and m.sensitive


def test_grammar_delete_file_destructive():
    m = match_command_grammar("delete file report")
    assert m.is_command and m.name == "delete_file"
    assert m.intent is IntentType.FILE_OP
    assert m.entities == {"name": "report"}
    assert m.params.get("op") == "delete"
    assert m.destructive and m.sensitive


def test_grammar_rename_entities():
    m = match_command_grammar("rename a to b")
    assert m.is_command and m.name == "rename"
    assert m.intent is IntentType.FILE_OP
    assert m.entities.get("name") == "a"
    assert m.entities.get("new_name") == "b"
    assert m.params.get("op") == "rename"


def test_grammar_switch_tab_number():
    m = match_command_grammar("switch to tab 3")
    assert m.is_command and m.name == "switch_tab"
    assert m.intent is IntentType.SWITCH_TAB
    assert m.entities.get("number") == "3"


def test_grammar_navigate_to_url():
    m = match_command_grammar("navigate to example com")
    assert m.is_command and m.name == "open_url"
    assert m.intent is IntentType.OPEN_URL
    assert m.entities.get("url") == "example com"


def test_grammar_search_for_query():
    m = match_command_grammar("search for html css")
    assert m.is_command and m.name == "search_for"
    assert m.intent is IntentType.NAVIGATE
    assert m.entities.get("query") == "html css"
    assert m.params.get("search") is True


def test_grammar_snap_left():
    m = match_command_grammar("snap left")
    assert m.is_command and m.name == "snap_left"
    assert m.intent is IntentType.SNAP
    assert m.params.get("direction") == "left"


def test_grammar_go_to_end():
    m = match_command_grammar("go to end")
    assert m.is_command and m.name == "go_end"
    assert m.intent is IntentType.NAVIGATE
    assert m.params.get("target") == "end"


def test_grammar_go_to_end_of_line_is_cursor():
    m = match_command_grammar("go to end of line")
    assert m.is_command and m.name == "cursor"
    assert m.intent is IntentType.KEY_PRESS
    assert m.params.get("what") == "cursor"


def test_grammar_select_all():
    m = match_command_grammar("select all")
    assert m.is_command and m.name == "select_all"
    assert m.intent is IntentType.SELECT
    assert m.params.get("what") == "all"


def test_grammar_bluetooth_off_state_entity():
    m = match_command_grammar("bluetooth off")
    assert m.is_command and m.name == "bluetooth"
    assert m.intent is IntentType.BLUETOOTH
    assert m.entities.get("state") == "off"
    assert m.where_supported is False  # platform-dependent command


def test_grammar_minimize_and_next_track():
    mini = match_command_grammar("minimize")
    assert mini.is_command and mini.name == "minimize"
    assert mini.intent is IntentType.MINIMIZE
    nxt = match_command_grammar("next track")
    assert nxt.is_command and nxt.name == "media_next"
    assert nxt.params.get("action") == "next"


def test_grammar_close_tab_sensitive():
    m = match_command_grammar("close tab")
    assert m.is_command and m.name == "close_tab"
    assert m.intent is IntentType.CLOSE_TAB
    assert m.sensitive and not m.destructive


def test_grammar_close_it_resolves_close_window():
    m = match_command_grammar("close it")
    assert m.is_command and m.name == "close_window"
    assert m.intent is IntentType.CLOSE
    assert m.params.get("what") == "window"


def test_grammar_gibberish_is_not_a_command():
    m = match_command_grammar("flibber wocky zumbalo")
    assert not m.is_command
    assert m.name == ""
    assert m.intent is IntentType.NONE
    assert m.confidence == 0.0


def test_grammar_fuzzy_alias_volum_up():
    m = match_command_grammar("volum up")
    assert m.is_command and m.name == "volume_up"
    assert m.params.get("direction") == "up"


def test_grammar_fuzzy_alias_maximise():
    m = match_command_grammar("maximise")
    assert m.is_command and m.name == "maximize"
    assert m.intent is IntentType.MAXIMIZE


# ===========================================================================
# 2. VOICE INTENT MAPPING (voice_match_to_intent)
# ===========================================================================


def test_intent_open_app_maps_open_with_application():
    m = match_command_grammar("open firefox")
    it = voice_match_to_intent(m, now=1.0)
    assert it is not None and it.type is IntentType.OPEN
    assert it.params.get("application") == "firefox"
    assert it.sources is Modality.VOICE
    assert it.utterance == "open firefox"


def test_intent_volume_direction_up():
    m = match_command_grammar("volume up")
    it = voice_match_to_intent(m, now=1.0)
    assert it.type is IntentType.VOLUME
    assert it.params.get("direction") == "up"


def test_intent_switch_tab_index_three():
    m = match_command_grammar("switch to tab 3")
    it = voice_match_to_intent(m, now=1.0)
    assert it.type is IntentType.SWITCH_TAB
    assert it.params.get("index") == 3


def test_intent_search_for_is_navigate_with_query():
    m = match_command_grammar("search for html css")
    it = voice_match_to_intent(m, now=1.0)
    assert it.type is IntentType.NAVIGATE
    assert it.params.get("query") == "html css"


def test_intent_click_that_resolves_gaze_target():
    btn = _button("Submit")
    ctx = ContextEngine()
    ctx.update_gaze_target(btn, now=1.0)
    m = match_command_grammar("click that")
    it = voice_match_to_intent(m, ctx.snapshot(), now=1.1)
    assert it.type is IntentType.CLICK
    assert it.target is btn            # the exact ScreenTarget object
    assert it.point == btn.center


def test_intent_destructive_match_keeps_sensitive_info():
    m = match_command_grammar("delete file report")
    assert m.destructive and m.sensitive
    it = voice_match_to_intent(m, now=1.0)
    assert it.type is IntentType.FILE_OP
    assert it.params.get("op") == "delete"
    assert it.params.get("name") == "report"
    assert it.confidence > 0.0


def test_intent_close_app_maps_close_with_application():
    m = match_command_grammar("close chrome")
    it = voice_match_to_intent(m, now=1.0)
    assert it.type is IntentType.CLOSE
    assert it.params.get("application") == "chrome"


def test_intent_confidence_scales_by_provider_confidence():
    m = match_command_grammar("volume up")   # grammar confidence 1.0
    it = voice_match_to_intent(m, confidence_scale=0.8, now=1.0)
    assert it.confidence == pytest.approx(0.8)
    it_full = voice_match_to_intent(m, confidence_scale=1.0, now=1.0)
    assert it_full.confidence == pytest.approx(1.0)


def test_intent_non_command_returns_none():
    m = match_command_grammar("flibber wocky zumbalo")
    assert not m.is_command
    assert voice_match_to_intent(m, now=1.0) is None


# ===========================================================================
# 3. OFFLINE VOICE ENGINE / MODES (§4/§5)
# ===========================================================================


def test_engine_command_mode_open_firefox():
    eng = OfflineVoiceEngine({"mode": "command"})
    events = eng.feed_transcript("open firefox", 0.9, now=1.0)
    assert events and events[0].kind is EventKind.VOICE_COMMAND
    assert events[0].modality is Modality.VOICE
    ev = eng.poll()
    assert ev is not None and ev.kind is EventKind.VOICE_COMMAND
    assert eng.poll() is None                      # drained
    assert eng.last_intent is not None
    assert eng.last_intent.type is IntentType.OPEN
    assert eng.last_intent.params.get("application") == "firefox"


def test_engine_nl_fallback_type_text():
    eng = OfflineVoiceEngine({"mode": "command"})
    eng.feed_transcript("open firefox", 0.9, now=1.0)
    events = eng.feed_transcript("type hello world", 0.9, now=2.0)
    assert events and events[0].kind is EventKind.VOICE_COMMAND
    assert events[0].payload.get("command") == "type"
    assert eng.last_intent.type is IntentType.TYPE
    assert eng.last_intent.params.get("text") == "hello world"


def test_engine_wake_word_gates_until_heard():
    eng = OfflineVoiceEngine({"mode": "command", "wake_word_required": True})
    assert eng.wake_word_required
    # gated: no wake word heard yet
    assert eng.feed_transcript("open firefox", 0.9, now=1.0) == []
    assert eng.last_intent is None
    # passes with the wake word prefix
    events = eng.feed_transcript("airmouse open firefox", 0.9, now=2.0)
    assert events and events[0].kind is EventKind.VOICE_COMMAND
    assert eng.last_intent.type is IntentType.OPEN
    # gate stays armed for the ARM window — bare command now passes
    events = eng.feed_transcript("volume up", 0.9, now=3.0)
    assert events and eng.last_intent.type is IntentType.VOLUME


def test_engine_dedup_same_text_within_window():
    eng = OfflineVoiceEngine({"mode": "command", "dedup_window": 1.2})
    first = eng.feed_transcript("volume up", 0.9, now=1.0)
    assert len(first) == 1
    # identical text inside the dedup window → ignored entirely
    assert eng.feed_transcript("volume up", 0.9, now=1.5) == []
    # outside the window → fires again
    again = eng.feed_transcript("volume up", 0.9, now=3.0)
    assert len(again) == 1


def test_engine_dictation_punctuation_commit():
    eng = OfflineVoiceEngine({"mode": "dictation"})
    eng.feed_transcript("hello", 0.9, now=1.0)
    eng.feed_transcript("world", 0.9, now=1.5)
    assert eng.dictation.pending == "hello world"
    assert eng.last_committed_text == ""
    events = eng.feed_transcript("today.", 0.9, now=2.0)
    assert eng.last_committed_text == "hello world today"
    assert events and events[0].kind is EventKind.VOICE_TEXT
    assert events[0].payload.get("text") == "hello world today"


def test_engine_dictation_max_chars_forces_commit():
    eng = OfflineVoiceEngine({"mode": "dictation", "dictation_max_chars": 10})
    eng.feed_transcript("aaaaa", 0.9, now=1.0)
    assert eng.last_committed_text == ""
    events = eng.feed_transcript("bbbbb", 0.9, now=1.5)
    assert eng.last_committed_text == "aaaaa bbbbb"   # 11 chars ≥ max 10
    assert events and events[0].kind is EventKind.VOICE_TEXT


def test_engine_dictation_marker_commit():
    eng = OfflineVoiceEngine({"mode": "dictation"})
    eng.feed_transcript("please send", 0.9, now=1.0)
    eng.feed_transcript("the report commit", 0.9, now=2.0)
    assert eng.last_committed_text == "please send the report"
    assert eng.dictation.pending == ""


def test_engine_hybrid_command_and_prose():
    eng = OfflineVoiceEngine({"mode": "hybrid"})
    # a real command fires immediately
    cmd_events = eng.feed_transcript("volume up", 0.9, now=1.0)
    assert cmd_events[0].kind is EventKind.VOICE_COMMAND
    assert eng.last_intent.type is IntentType.VOLUME
    # prose (no command) accumulates in the dictation buffer
    prose = "the quick brown fox jumps over the lazy dog"
    eng.feed_transcript(prose, 0.9, now=2.0)
    assert eng.dictation.pending == prose
    # the marker commits the accumulated prose
    events = eng.feed_transcript("commit", 0.9, now=2.5)
    assert eng.last_committed_text == prose
    assert events and events[0].kind is EventKind.VOICE_TEXT
    assert events[0].payload.get("text") == prose


def test_engine_set_mode_resets_dictation_buffer():
    eng = OfflineVoiceEngine({"mode": "dictation"})
    eng.feed_transcript("pending text", 0.9, now=1.0)
    assert eng.dictation.pending == "pending text"
    assert eng.set_mode("command") is not None
    assert eng.dictation.pending == ""


def test_energy_vad_single_speech_ended_edge():
    vad = EnergyVAD()
    edges = []
    state = []
    for _ in range(5):                       # silence
        vad.feed(_pcm(0), now=0.0)
        edges.append(vad.speech_ended)
        state.append(vad.active)
    for _ in range(3):                       # loud speech
        vad.feed(_pcm(1000), now=0.0)
        edges.append(vad.speech_ended)
        state.append(vad.active)
    for _ in range(30):                      # trailing silence (> end_silence)
        vad.feed(_pcm(0), now=0.0)
        edges.append(vad.speech_ended)
        state.append(vad.active)
    assert not any(state[:5])                # silent → inactive
    assert all(state[5:8])                   # loud → active
    assert not state[-1]                     # settled back to inactive
    assert sum(edges) == 1                   # exactly one falling edge


def test_simulated_speech_provider_push_transcribe_order():
    prov = SimulatedSpeechProvider()
    assert prov.available()
    prov.push("alpha")
    prov.push("beta", confidence=0.7)
    t1 = prov.transcribe(None)
    t2 = prov.transcribe(None)
    t3 = prov.transcribe(None)
    assert (t1.text, t2.text) == ("alpha", "beta")
    assert t2.confidence == pytest.approx(0.7)
    assert t3.text == "" and t3.provider == "simulated"   # silence after drain


# ===========================================================================
# 4. EVENT BUS (§3)
# ===========================================================================


def test_bus_subscribe_filters_by_kind_and_modality():
    bus = EventBus()
    everything = bus.subscribe()
    picky = bus.subscribe(kinds={EventKind.VOICE_COMMAND},
                          modalities={Modality.VOICE})
    bus.publish(Event(kind=EventKind.VOICE_COMMAND, modality=Modality.VOICE,
                      confidence=0.9, timestamp=1.0), now=1.0)
    bus.publish(Event(kind=EventKind.VOICE_TEXT, modality=Modality.VOICE,
                      confidence=0.9, timestamp=1.1), now=1.1)
    bus.publish(Event(kind=EventKind.VOICE_COMMAND, modality=Modality.HAND,
                      confidence=0.9, timestamp=1.2), now=1.2)
    assert len(everything.queue) == 3           # no filter → all events
    got = picky.drain()
    assert len(got) == 1                        # kind AND modality must match
    assert got[0].kind is EventKind.VOICE_COMMAND


def test_bus_bounded_queue_drops_oldest():
    bus = EventBus()
    sub = bus.subscribe(queue_size=2)
    for i in range(4):
        bus.publish(Event(kind=EventKind.VOICE_TEXT, payload={"i": i},
                          timestamp=float(i)), now=float(i))
    got = sub.drain()
    assert len(got) == 2                        # newest two survive
    assert [e.payload["i"] for e in got] == [2, 3]
    assert sub.dropped == 2
    assert sub.received == 4
    assert bus.stats()["dropped"] == 2


def test_bus_history_kinds_and_limit():
    bus = EventBus()
    bus.publish(Event(kind=EventKind.VOICE_TEXT, timestamp=1.0), now=1.0)
    bus.publish(Event(kind=EventKind.HAND_GESTURE, timestamp=2.0), now=2.0)
    bus.publish(Event(kind=EventKind.VOICE_TEXT, timestamp=3.0), now=3.0)
    assert len(bus.history()) == 3
    assert all(e.kind is EventKind.VOICE_TEXT
               for e in bus.history(kinds={EventKind.VOICE_TEXT}))
    tail = bus.history(limit=2)
    assert [e.timestamp for e in tail] == [2.0, 3.0]


def test_bus_stats_counters():
    bus = EventBus()
    bus.subscribe()
    bus.publish(Event(kind=EventKind.VOICE_TEXT, timestamp=1.0), now=1.0)
    bus.publish(Event(kind=EventKind.HAND_GESTURE, timestamp=2.0), now=2.0)
    stats = bus.stats()
    assert stats["published"] == 2
    assert stats["rejected"] == 0
    assert stats["subscribers"] == 1
    assert stats["by_kind"] == {"voice_text": 1, "hand_gesture": 1}


def test_bus_rejects_none_kind_and_non_events():
    bus = EventBus()
    assert bus.publish(Event(kind=EventKind.NONE)) is False
    assert bus.publish("not-an-event") is False
    assert bus.publish(None) is False
    stats = bus.stats()
    assert stats["rejected"] == 3 and stats["published"] == 0


def test_bus_clamps_confidence_into_unit_range():
    bus = EventBus()
    hi = Event(kind=EventKind.VOICE_TEXT, confidence=7.0)
    lo = Event(kind=EventKind.VOICE_TEXT, confidence=-3.0)
    assert bus.publish(hi, now=1.0) and bus.publish(lo, now=1.1)
    assert hi.confidence == 1.0 and lo.confidence == 0.0


def test_multisubscriber_polls_in_timestamp_order():
    # Each poll round pops one event per subscriber and returns the
    # globally-earliest one, so sequential rounds drain in timestamp order.
    bus = EventBus()
    voice_sub = bus.subscribe(kinds={EventKind.VOICE_TEXT})
    hand_sub = bus.subscribe(kinds={EventKind.HAND_GESTURE})
    merged = MultiSubscriber([voice_sub, hand_sub])
    bus.publish(Event(kind=EventKind.VOICE_TEXT, timestamp=1.0), now=1.0)
    assert merged.poll().timestamp == 1.0
    bus.publish(Event(kind=EventKind.HAND_GESTURE, timestamp=2.0), now=2.0)
    assert merged.poll().timestamp == 2.0
    bus.publish(Event(kind=EventKind.VOICE_TEXT, timestamp=3.0), now=3.0)
    assert merged.poll().timestamp == 3.0
    # two fresh events in the same round → the earliest timestamp wins
    bus.publish(Event(kind=EventKind.VOICE_TEXT, timestamp=9.0), now=9.0)
    bus.publish(Event(kind=EventKind.HAND_GESTURE, timestamp=4.0), now=4.0)
    winner = merged.poll()
    assert winner.kind is EventKind.HAND_GESTURE and winner.timestamp == 4.0
    assert merged.poll() is None                  # everything drained


def test_bus_unsubscribe_stops_delivery():
    bus = EventBus()
    sub = bus.subscribe()
    assert bus.unsubscribe(sub) is True
    bus.publish(Event(kind=EventKind.VOICE_TEXT, timestamp=1.0), now=1.0)
    assert len(sub.queue) == 0
    assert bus.stats()["subscribers"] == 0
    assert bus.unsubscribe(sub) is False         # already gone


def test_bus_publish_voice_and_text_helpers():
    bus = EventBus()
    assert bus.publish_voice("click", "click", 0.9, now=1.0)
    assert bus.publish_text("hello there", 0.8, now=1.1)
    hist = bus.history()
    assert hist[0].kind is EventKind.VOICE_COMMAND
    assert hist[0].payload == {"command": "click", "text": "click"}
    assert hist[1].kind is EventKind.VOICE_TEXT
    assert hist[1].payload == {"text": "hello there"}


# ===========================================================================
# 5. CONTEXT ENGINE (§8)
# ===========================================================================


def test_context_update_window_detects_browser():
    ce = ContextEngine()
    ce.update_window("Google Chrome — YouTube", application="chrome", now=1.0)
    snap = ce.snapshot()
    assert snap.app_context is AppContext.BROWSER
    assert snap.focused_application == "chrome"


def test_context_resolve_that_returns_gaze_target():
    ce = ContextEngine()
    btn = _button("Submit")
    ce.update_gaze_target(btn, now=1.0)
    assert ce.resolve_reference("that", now=1.1) is btn
    assert ce.resolve_reference("it", now=1.1) is btn


def test_context_gaze_ttl_expiry():
    ce = ContextEngine({"gaze_ttl": 2.0})
    btn = _button("Submit")
    ce.update_gaze_target(btn, now=1.0)
    assert ce.resolve_reference("that", now=2.5) is btn    # inside TTL
    assert ce.resolve_reference("that", now=4.0) is None   # expired
    assert ce.snapshot().current_gaze_target is None


def test_context_window_reference_builds_synthetic_target():
    ce = ContextEngine()
    ce.update_window("My Editor — notes.txt", application="notepad", now=1.0)
    target = ce.resolve_reference("window", now=1.1)
    assert target is not None
    assert target.id == "context:window"
    assert target.type is ScreenTargetType.WINDOW
    assert target.text == "My Editor — notes.txt"
    assert target.application == "notepad"


def test_context_browser_targets_fallback_substring():
    ce = ContextEngine()
    link = ScreenTarget(id="l1", type=ScreenTargetType.LINK,
                        bbox=(0, 0, 10, 10), text="Downloads page",
                        confidence=0.9, actionable=True)
    ce.set_browser_targets([link], now=1.0)
    assert ce.resolve_reference("downloads", now=1.1) is link
    assert ce.resolve_reference("zzz-no-match", now=1.1) is None


def test_context_record_action_and_recent_target():
    ce = ContextEngine()
    tgt = _button("Cancel")
    ce.record_action("click", tgt, now=2.0)
    snap = ce.snapshot()
    assert snap.recent_action == "click"
    assert snap.recent_target is tgt


def test_context_snapshot_is_an_independent_copy():
    ce = ContextEngine()
    ce.record_action("click", _button("Cancel"), now=1.0)
    snap = ce.snapshot()
    assert snap is not ce.state
    snap.recent_action = "mutated"
    assert ce.snapshot().recent_action != "mutated"


# ===========================================================================
# 6. GESTURE REGISTRY (§9)
# ===========================================================================


def test_registry_builtin_pinch_maps_click():
    reg = GestureRegistry()
    event, intent = reg.feed("pinch", point=(10, 20), confidence=0.9, now=1.0)
    assert event.kind is EventKind.HAND_GESTURE
    assert event.modality is Modality.HAND
    assert event.payload["gesture"] == "pinch"
    assert intent is not None and intent.type is IntentType.CLICK
    assert intent.point == (10, 20)


def test_registry_swipe_left_maps_switch_window():
    reg = GestureRegistry()
    _event, intent = reg.feed("swipe_left", confidence=0.9, now=1.0)
    assert intent.type is IntentType.SWITCH_WINDOW
    assert intent.params.get("direction") == "left"


def test_registry_double_pinch_synthesis():
    reg = GestureRegistry()                       # double_pinch_window 0.6
    _ev1, first = reg.feed("pinch", confidence=0.9, now=1.0)
    assert first.type is IntentType.CLICK         # single pinch
    _ev2, second = reg.feed("pinch", confidence=0.9, now=1.2)
    assert second.type is IntentType.DOUBLE_CLICK
    assert second.utterance == "gesture:double_pinch"


def test_registry_custom_air_delete_sequence():
    reg = GestureRegistry()
    assert reg.define(CustomGestureMapping(
        name="air_delete",
        pattern=["fist", "swipe_left", "pinch_release"],
        intent=IntentType.HOTKEY,
        params={"keys": ["ctrl", "backspace"]}))
    # partial steps keep the built-in per-label mapping...
    _e1, i1 = reg.feed("fist", confidence=0.9, now=1.0)
    assert i1.type is IntentType.CANCEL           # built-in fist mapping
    assert i1.utterance == "gesture:fist"
    _e2, i2 = reg.feed("swipe_left", confidence=0.9, now=1.2)
    assert i2.type is IntentType.SWITCH_WINDOW    # built-in swipe mapping
    # ...until the pattern completes and the custom intent fires
    _e3, i3 = reg.feed("pinch_release", confidence=0.9, now=1.4)
    assert i3 is not None and i3.type is IntentType.HOTKEY
    assert i3.params == {"keys": ["ctrl", "backspace"]}
    assert i3.utterance == "gesture_seq:air_delete"


def test_registry_sequence_step_gap_timeout_resets():
    reg = GestureRegistry()
    reg.define(CustomGestureMapping(
        name="air_delete",
        pattern=["fist", "swipe_left", "pinch_release"],
        intent=IntentType.HOTKEY,
        params={"keys": ["ctrl", "backspace"]},
        step_gap=1.5))
    reg.feed("fist", confidence=0.9, now=1.0)
    reg.feed("swipe_left", confidence=0.9, now=1.2)
    # third step arrives after step_gap → pattern restarts, no completion
    _ev, intent = reg.feed("pinch_release", confidence=0.9, now=1.2 + 2.0)
    assert intent is not None
    assert intent.utterance == "gesture:pinch_release"   # built-in fallback
    assert intent.type is not IntentType.HOTKEY


def test_registry_any_wildcard_step():
    reg = GestureRegistry()
    reg.define(CustomGestureMapping(
        name="doit", pattern=["any", "pinch"],
        intent=IntentType.CLICK))
    # step 1 ("any") matches the fist; the built-in fist mapping still fires
    _e1, i1 = reg.feed("fist", confidence=0.9, now=1.0)
    assert i1.type is IntentType.CANCEL and i1.utterance == "gesture:fist"
    # step 2 completes the wildcard pattern → custom intent
    _e2, i2 = reg.feed("pinch", confidence=0.9, now=1.1)
    assert i2 is not None and i2.type is IntentType.CLICK
    assert i2.utterance == "gesture_seq:doit"


def test_registry_define_remove_get():
    reg = GestureRegistry()
    mapping = CustomGestureMapping(name="zap", pattern=["pinch", "fist"],
                                   intent=IntentType.CANCEL)
    assert reg.define(mapping) is True
    assert reg.get("zap") is mapping
    assert reg.define(CustomGestureMapping(name="", pattern=["pinch"])) is False
    assert reg.remove("zap") is True
    assert reg.get("zap") is None
    assert reg.remove("zap") is False


def test_registry_save_load_roundtrip(tmp_path):
    path = str(tmp_path / "gestures.json")
    reg = GestureRegistry()
    reg.define(CustomGestureMapping(
        name="air_delete",
        pattern=["fist", "swipe_left", "pinch_release"],
        intent=IntentType.HOTKEY,
        params={"keys": ["ctrl", "backspace"]},
        description="delete word"))
    assert reg.save(path) is True
    reg2 = GestureRegistry()
    assert reg2.load(path) == 1
    loaded = reg2.get("air_delete")
    assert loaded is not None
    assert loaded.pattern == ["fist", "swipe_left", "pinch_release"]
    assert loaded.params == {"keys": ["ctrl", "backspace"]}
    # the loaded mapping is live: the sequence completes on the new registry
    reg2.feed("fist", confidence=0.9, now=1.0)
    reg2.feed("swipe_left", confidence=0.9, now=1.2)
    _ev, done = reg2.feed("pinch_release", confidence=0.9, now=1.4)
    assert done is not None and done.type is IntentType.HOTKEY


def test_registry_override_mapping_and_restore():
    reg = GestureRegistry()
    reg.override_mapping("pinch", IntentType.SCROLL, {"amount": 2})
    _e1, overridden = reg.feed("pinch", confidence=0.9, now=1.0)
    assert overridden.type is IntentType.SCROLL
    assert overridden.params.get("amount") == 2
    reg.override_mapping("pinch", None)           # restore built-in
    # well past the double-pinch window so no double_pinch synthesis kicks in
    _e2, restored = reg.feed("pinch", confidence=0.9, now=5.0)
    assert restored.type is IntentType.CLICK


# ===========================================================================
# 7. RF ABSTRACTION (§16)
# ===========================================================================


def test_simulated_rf_provider_push_poll_drain():
    prov = SimulatedRFProvider()
    assert prov.available()
    prov.push("gesture", "swipe_left", 0.9, now=1.0)
    prov.push("motion", "push", 0.5, now=1.1)
    events = prov.poll(now=2.0)
    assert [(e.kind, e.label) for e in events] == \
        [("gesture", "swipe_left"), ("motion", "push")]
    assert prov.poll(now=2.1) == []               # drained


def test_rf_bridge_converts_and_publishes_to_bus():
    bus = EventBus()
    sub = bus.subscribe(kinds={EventKind.RF_GESTURE})
    prov = SimulatedRFProvider()
    prov.push("gesture", "swipe_left", 0.9, now=1.0)
    bridge = RFBridge(provider=prov, bus=bus)
    pairs = bridge.poll(now=2.0)
    assert len(pairs) == 1
    event, raw = pairs[0]
    assert event.kind is EventKind.RF_GESTURE
    assert event.modality is Modality.RF
    assert event.payload["label"] == "swipe_left"
    assert raw.label == "swipe_left"
    delivered = sub.drain()
    assert len(delivered) == 1 and delivered[0] is event


def test_rf_bridge_min_confidence_filter():
    prov = SimulatedRFProvider()
    prov.push("gesture", "weak", 0.2, now=1.0)
    prov.push("gesture", "strong", 0.9, now=1.1)
    bridge = RFBridge(provider=prov, config={"min_confidence": 0.4})
    pairs = bridge.poll(now=2.0)
    assert [raw.label for _ev, raw in pairs] == ["strong"]
    assert bridge.event_count == 1


def test_dummy_rf_provider_unavailable():
    prov = DummyRFProvider()
    assert prov.available() is False
    assert prov.poll(now=1.0) == []
    bridge = RFBridge(provider=prov)
    assert bridge.available() is False
    assert bridge.poll(now=1.0) == []


def test_rf_bridge_without_provider_degrades():
    bridge = RFBridge(provider=None)
    assert bridge.available() is False
    assert bridge.poll(now=1.0) == []
    assert bridge.sensor_name == ""


# ===========================================================================
# 8. SYSTEM + FILE ACTION EXECUTORS (§10/§26)
# ===========================================================================


def test_system_ops_allowlist_rejects_unknown_op():
    assert "format_c" not in SYSTEM_OPS
    ex = SystemActionExecutor()
    result = ex.execute("format_c")
    assert result.ok is False and result.message == "op_not_allowed"
    mock = MockSystemExecutor()
    result = mock.execute("format_c")
    assert result.ok is False and result.message == "op_not_allowed"
    assert mock.calls == [("format_c", {})]


def test_mock_system_executor_records_and_fail_for():
    mock = MockSystemExecutor(fail_for={"volume_up"})
    bad = mock.execute("volume_up")
    good = mock.execute("mute")
    assert bad.ok is False and bad.message == "mock_failure"
    assert good.ok is True
    assert [op for op, _params in mock.calls] == ["volume_up", "mute"]
    assert mock.available() is True
    assert MockSystemExecutor(available=False).available() is False


def test_sanitize_file_name_strips_traversal_and_control_chars():
    assert sanitize_file_name("../../etc/passwd") == "passwd"
    assert sanitize_file_name("a\\b\\c.txt") == "c.txt"
    assert sanitize_file_name("na\x00me") == "name"
    assert sanitize_file_name("..hidden.") == "hidden"
    assert sanitize_file_name("  spaced  ") == "spaced"
    assert sanitize_file_name("") == ""
    assert "/" not in sanitize_file_name("x/../../y")


def test_validate_url_accepts_https_bare_domain_and_file():
    for url, expected in [
        ("https://example.com", "https://example.com"),
        ("example.com", "https://example.com"),
        ("file:///tmp/x", "file:///tmp/x"),
    ]:
        ok, cleaned = validate_url(url)
        assert ok and cleaned == expected


def test_validate_url_rejects_schemes_spaces_and_empty():
    for bad in ["javascript:alert(1)", "data:text/html,x", "not a url",
                "", None, "ftp://x", "https://x com"]:
        ok, cleaned = validate_url(bad)
        assert ok is False and cleaned == ""


def _file_executor(tmp_path, dry_run=False):
    root = tmp_path / "root"
    root.mkdir()
    return FileActionExecutor({"roots": [str(root)], "base_dir": str(root),
                               "dry_run": dry_run}), root


def test_file_executor_create_folder(tmp_path):
    ex, root = _file_executor(tmp_path)
    assert ex.available()
    result = ex.execute("create_folder", {"name": "docs"})
    assert result.ok is True
    assert (root / "docs").is_dir()


def test_file_executor_rename(tmp_path):
    ex, root = _file_executor(tmp_path)
    (root / "a.txt").write_text("hello")
    result = ex.execute("rename", {"name": "a.txt", "new_name": "b.txt"})
    assert result.ok is True
    assert (root / "b.txt").exists() and not (root / "a.txt").exists()


def test_file_executor_copy_paste(tmp_path):
    ex, root = _file_executor(tmp_path)
    (root / "orig.txt").write_text("data")
    assert ex.execute("copy", {"name": "orig.txt"}).ok
    assert ex.execute("paste", {}).ok
    assert (root / "orig (1).txt").read_text() == "data"


def test_file_executor_delete_refuses_root(tmp_path):
    root = tmp_path / "root"
    docs = root / "docs"
    docs.mkdir(parents=True)
    # a nested allowlisted root must never be deletable
    ex = FileActionExecutor({"roots": [str(root), str(docs)],
                             "base_dir": str(root)})
    result = ex.execute("delete", {"name": "docs"})
    assert result.ok is False and result.message == "refused_root"
    assert docs.is_dir()


def test_file_executor_delete_removes_file(tmp_path):
    ex, root = _file_executor(tmp_path)
    (root / "gone.txt").write_text("bye")
    result = ex.execute("delete", {"name": "gone.txt"})
    assert result.ok is True
    assert not (root / "gone.txt").exists()


def test_file_executor_traversal_is_baselined_into_root(tmp_path):
    ex, root = _file_executor(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    result = ex.execute("delete", {"name": "../outside.txt"})
    assert result.ok is False                     # resolved INSIDE the root
    assert outside.exists()                       # nothing escaped the root
    assert result.message == "not_found"


def test_file_executor_dry_run_makes_no_changes(tmp_path):
    ex, root = _file_executor(tmp_path, dry_run=True)
    (root / "keep.txt").write_text("x")
    result = ex.execute("delete", {"name": "keep.txt"})
    assert result.ok is True and result.message == "dry_run"
    assert (root / "keep.txt").exists()           # untouched
    assert ex.execute("create_folder", {"name": "nope"}).message == "dry_run"
    assert not (root / "nope").exists()


def test_destructive_op_lists_contents():
    assert {"delete", "move"}.issubset(set(DESTRUCTIVE_FILE_OPS))
    assert set(DESTRUCTIVE_SYSTEM_OPS) == {"shutdown", "restart", "sleep",
                                           "lock"}
    assert {"open", "create_folder", "rename", "copy", "paste", "move",
            "delete", "select"} == set(FILE_OPS)
    ex = FileActionExecutor()
    assert ex.is_destructive("delete") and ex.is_destructive("move")
    assert not ex.is_destructive("open")
    sysx = MockSystemExecutor()
    assert sysx.is_destructive("shutdown") and not sysx.is_destructive("mute")


# ===========================================================================
# 9. SAFETY (§18)
# ===========================================================================


def test_safety_file_op_delete_blocked_open_allowed():
    s = SafetySystem()
    delete = Intent(type=IntentType.FILE_OP,
                    params={"op": "delete", "name": "x"},
                    confidence=0.9, sources=Modality.VOICE, timestamp=10.0)
    decision = s.approve_intent(delete, now=10.0)
    assert decision.allowed is False
    assert decision.reason == "needs_confirmation"
    assert decision.requires_confirmation is True
    opener = Intent(type=IntentType.FILE_OP, params={"op": "open", "name": "x"},
                    confidence=0.9, sources=Modality.VOICE, timestamp=10.1)
    assert s.approve_intent(opener, now=10.1).allowed is True


def test_safety_shutdown_blocked_first():
    s = SafetySystem()
    intent = Intent(type=IntentType.SHUTDOWN, confidence=0.9,
                    sources=Modality.VOICE, timestamp=1.0)
    decision = s.approve_intent(intent, now=1.0)
    assert decision.allowed is False
    assert decision.reason == "needs_confirmation"
    assert s.pending_confirmation is not None


def test_safety_close_tab_blocked_first():
    s = SafetySystem()
    intent = Intent(type=IntentType.CLOSE_TAB, confidence=0.9,
                    sources=Modality.VOICE, timestamp=1.0)
    decision = s.approve_intent(intent, now=1.0)
    assert decision.allowed is False
    assert decision.reason == "needs_confirmation"


def test_safety_confirmation_flow_arm_confirm_allow():
    s = SafetySystem()
    intent = Intent(type=IntentType.RESTART, confidence=0.9,
                    sources=Modality.VOICE, timestamp=10.0)
    assert s.approve_intent(intent, now=10.0).reason == "needs_confirmation"
    assert s.confirm() is True                    # user says "confirm"
    assert s.approve_intent(intent, now=10.2).allowed is True
    # one-shot: the SAME intent a third time re-arms the flow
    assert s.approve_intent(intent, now=10.4).reason == "needs_confirmation"


def test_safety_system_op_destructive_vs_benign():
    s = SafetySystem()
    reboot = Intent(type=IntentType.SYSTEM_OP, params={"op": "restart"},
                    confidence=0.9, sources=Modality.VOICE, timestamp=1.0)
    decision = s.approve_intent(reboot, now=1.0)
    assert decision.allowed is False
    assert decision.reason == "needs_confirmation"
    volume = Intent(type=IntentType.SYSTEM_OP, params={"op": "volume_up"},
                    confidence=0.9, sources=Modality.VOICE, timestamp=1.1)
    assert s.approve_intent(volume, now=1.1).allowed is True


# ===========================================================================
# 10. HANDS-FREE COMBOS (§19)
# ===========================================================================


def test_hands_free_combos_catalogue():
    assert len(HANDS_FREE_COMBOS) == 8
    assert "full_fusion" in HANDS_FREE_COMBOS
    assert HANDS_FREE_COMBOS["full_fusion"] == frozenset(
        {"voice", "gaze", "hand", "rf"})


def test_effective_combo_full_when_all_alive():
    health = SensorHealth()
    for modality in ("gaze", "voice", "hand", "rf"):
        health.mark(modality, now=1.0)
    assert effective_combo("full_fusion", health, now=1.5) == "full_fusion"


def test_effective_combo_downgrades_when_gaze_stale():
    health = SensorHealth()
    health.mark("voice", now=1.0)                 # gaze never marked → stale
    assert effective_combo("full_fusion", health, now=1.5) == "voice_only"


def test_effective_combo_rf_dead_from_full_fusion():
    health = SensorHealth()
    for modality in ("gaze", "voice", "hand"):
        health.mark(modality, now=1.0)            # rf never marked
    assert effective_combo("full_fusion", health, now=1.5) == "voice_gaze_hand"


def test_effective_combo_unknown_wanted_falls_back():
    health = SensorHealth()
    health.mark("voice", now=1.0)
    assert effective_combo("bogus-combo", health, now=1.5) == "voice_only"
    assert effective_combo("bogus-combo", SensorHealth(), now=1.5) == ""


def test_sensor_health_snapshot_keys():
    health = SensorHealth()
    health.mark("gaze", now=1.0)
    snap = health.snapshot(now=1.5)
    assert set(snap.keys()) == {"gaze", "voice", "hand", "rf"}
    assert snap["gaze"] is True and snap["voice"] is False
    assert snap["hand"] is False and snap["rf"] is False


# ===========================================================================
# 11. OFFLINE MODE (§17)
# ===========================================================================


def test_offline_selftest_full_stack_ok():
    report = run_offline_selftest()
    assert report.ok is True
    assert len(report.checks) >= 9
    assert all(c["passed"] for c in report.checks)


def test_network_isolation_blocks_external_connect():
    with network_isolation():
        with pytest.raises(OSError):
            socket.create_connection(("example.com", 80), timeout=0.2)



def test_network_isolation_allows_loopback_by_default():
    with network_isolation():
        server = socket.socket()
        try:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            port = server.getsockname()[1]
            client = socket.socket()
            client.settimeout(1.0)
            client.connect(("127.0.0.1", port))     # must succeed
            assert client.getpeername()[0] == "127.0.0.1"
        finally:
            server.close()


def test_offline_gate_blocks_cloud_allows_local():
    gate = OfflineGate(engaged=True)
    assert gate.engaged and gate.blocked
    assert gate.check("cloud_asr") is False
    assert gate.check("local_grammar") is True
    assert gate.blocked_calls == 1
    assert gate.blocked_features == ["cloud_asr"]


def test_offline_guard_decorator_short_circuits():
    gate = OfflineGate(engaged=True)

    @gate.guard("cloud_asr")
    def network_call():
        return "network-data"

    assert network_call() is None                 # short-circuited offline

    gate.relax()
    assert network_call() == "network-data"       # passes when disengaged


def test_offline_gate_engage_relax_roundtrip():
    gate = OfflineGate()
    assert gate.engaged is False
    assert gate.check("cloud_asr") is True        # default: nothing blocked
    gate.engage()
    assert gate.blocked is True
    assert gate.check("cloud_asr") is False
    gate.relax()
    assert gate.blocked is False
    assert gate.check("cloud_asr") is True


# ===========================================================================
# 12. BROWSER VERIFICATION EXTRA (§11-§13)
# ===========================================================================


def _sim_controller():
    bridge = SimulatedBrowserBridge()
    ctrl = BrowserController(config={"enabled": True}, bridge=bridge)
    assert ctrl.start() is True
    return ctrl, bridge


def test_browser_verifier_navigate_url_unchanged_fails():
    ctrl, _bridge = _sim_controller()
    state = ctrl.poll(now=1.0)
    assert state is not None
    same_url = BrowserResolution(matched=True, action="navigate",
                                 params={"url": state.url})
    out = ctrl.execute(same_url, now=2.0)
    assert out["status"] == "executed"
    assert out["verification"]["status"] == "failed"
    assert "did not change" in out["verification"]["message"]


def test_browser_verifier_unknown_states_and_actions():
    verifier = BrowserActionVerifier()
    assert verifier.verify("navigate", None, None, None, None)["status"] == \
        "unknown"
    missing = verifier.verify("nonexistent-action", None, None, None, None)
    assert missing["status"] == "unknown"


def test_browser_resolver_click_ordinal_link():
    ctrl, _bridge = _sim_controller()
    ctrl.poll(now=1.0)
    resolver = SemanticBrowserResolver(ctrl.mapper)
    resolution = resolver.resolve("click the first link", now=1.1)
    assert resolution.matched and resolution.action == "click"
    assert resolution.element.id == "link-downloads"
    assert resolution.params.get("ordinal") == 1


def test_browser_resolver_switch_to_youtube_tab():
    ctrl, _bridge = _sim_controller()
    ctrl.poll(now=1.0)
    resolver = SemanticBrowserResolver(ctrl.mapper)
    resolution = resolver.resolve("switch to youtube", now=1.1)
    assert resolution.matched and resolution.action == "switch_tab"
    assert resolution.params.get("tab_id") == "tab-2"
    out = ctrl.execute(resolution, now=1.2)
    assert out["status"] == "executed"
    assert out["verification"]["status"] == "passed"


# ===========================================================================
# 13. FUSION PIPELINE (§14)
# ===========================================================================


def test_fusion_voice_volume_up_executes_system_op():
    voice = OfflineVoiceEngine({"mode": "command"})
    voice.feed_transcript("volume up", 0.9, now=1.0)
    assert voice.last_intent.type is IntentType.VOLUME
    system = MockSystemExecutor()
    agent = _agent(voice_engine=voice, system_executor=system)
    out = agent.process_frame(now=1.1)
    assert out["reports"] and out["reports"][0].ok
    assert out["reports"][0].plan.action.value == "system_operation"
    assert ("volume_up", {"direction": "up", "op": "volume_up"}) in system.calls


def test_fusion_injected_click_with_confident_gaze_executes():
    stub = StubExecutor()
    agent = _agent(executor=stub)
    agent.inject_intent(Intent(type=IntentType.CLICK, point=(100, 100),
                               confidence=0.9, sources=Modality.VOICE,
                               timestamp=1.0))
    out = agent.process_frame(now=1.1, gaze_state=GazeState(confidence=0.9))
    assert out["reports"] and out["reports"][0].ok
    assert out["reports"][0].plan.action.value == "click"
    assert ("click", (100, 100)) in stub.calls


def test_fusion_low_gaze_confidence_blocks_click():
    agent = _agent()
    agent.inject_intent(Intent(type=IntentType.CLICK, point=(100, 100),
                               confidence=0.45, sources=Modality.GAZE,
                               timestamp=1.0))
    out = agent.process_frame(now=1.1, gaze_state=GazeState(confidence=0.3))
    assert len(out["reports"]) == 1
    report = out["reports"][0]
    assert report.status is ActionStatus.BLOCKED
    assert report.message == "low_gaze_confidence"


def test_fusion_rf_gesture_switch_window_via_confirmation():
    prov = SimulatedRFProvider()
    prov.push("gesture", "swipe_left", 0.9, now=1.0)
    bridge = RFBridge(provider=prov)
    registry = GestureRegistry()
    stub = StubExecutor()
    agent = _agent(executor=stub, rf=bridge, gesture_registry=registry)
    # frame 1: RF swipe → registry maps it → SWITCH_WINDOW is sensitive →
    # the safety layer arms the confirmation flow
    assert agent.poll_events(now=1.0) == 1
    out = agent.process_frame(now=1.05)
    assert len(out["reports"]) == 1
    assert out["reports"][0].status is ActionStatus.BLOCKED
    assert out["reports"][0].message == "needs_confirmation"
    intent = out["intents"][0]
    assert intent.type is IntentType.SWITCH_WINDOW
    # confirm, then re-run the SAME intent: it now executes the alt+tab hotkey
    assert agent.safety.confirm() is True
    agent.inject_intent(intent)
    out = agent.process_frame(now=2.0)
    assert out["reports"] and out["reports"][0].ok
    assert ("hotkey", ("alt", "tab")) in stub.calls


# ===========================================================================
# 14. PERFORMANCE (§20/§21 — bounded, deterministic)
# ===========================================================================


_PERF_UTTERANCES = [
    "open firefox", "volume up", "close tab", "search for python tutorials",
    "scroll down", "switch to tab 3", "minimize", "select all",
    "go to end", "mute", "zoom in", "go back",
]


def test_perf_grammar_50_utterances_under_half_second():
    t0 = time.perf_counter()
    for i in range(50):
        match_command_grammar(_PERF_UTTERANCES[i % len(_PERF_UTTERANCES)])
    assert time.perf_counter() - t0 < 0.5


def test_perf_voice_engine_30_transcripts_under_half_second():
    eng = OfflineVoiceEngine({"mode": "command"})
    t0 = time.perf_counter()
    for i in range(30):
        eng.feed_transcript(_PERF_UTTERANCES[i % len(_PERF_UTTERANCES)],
                            0.9, now=float(i))
    assert time.perf_counter() - t0 < 0.5


def test_perf_eventbus_publish_1000_events_under_half_second():
    bus = EventBus()
    t0 = time.perf_counter()
    for i in range(1000):
        bus.publish(Event(kind=EventKind.VOICE_TEXT, modality=Modality.VOICE,
                          confidence=0.5, timestamp=float(i)), now=float(i))
    assert time.perf_counter() - t0 < 0.5
    assert bus.stats()["published"] == 1000


def test_perf_context_resolve_1000_calls_under_200ms():
    ce = ContextEngine()
    ce.update_gaze_target(_button("Perf"), now=1.0)
    t0 = time.perf_counter()
    for _ in range(1000):
        ce.resolve_reference("that", now=1.5)
    assert time.perf_counter() - t0 < 0.2
