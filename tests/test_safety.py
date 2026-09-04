"""Tests for airmouse.safety (v8 SafetySystem) — deterministic via now=."""
from __future__ import annotations

import threading

import pytest

from airmouse.interfaces import (
    Intent,
    IntentType,
    Modality,
    SafetyDecision,
    SafetyLevel,
)
from airmouse.safety import SafetySystem


def intent(t=IntentType.CLICK, conf=0.9, sources=Modality.NONE,
           point=(10, 10), ts=100.0, params=None):
    return Intent(type=t, confidence=conf, sources=sources, point=point,
                  timestamp=ts, params=dict(params or {}))


# ── basic approval + level management ───────────────────────────────────────────

def test_approve_normal_click():
    s = SafetySystem()
    d = s.approve_intent(intent(), now=1.0)
    assert d.allowed and d.reason == "ok"
    assert d.level is SafetyLevel.NORMAL


def test_approve_accepts_level_strings():
    s = SafetySystem(config={"level": "careful"})
    assert s.level is SafetyLevel.CAREFUL
    s.set_level("normal")
    assert s.level is SafetyLevel.NORMAL


def test_low_confidence_blocked():
    s = SafetySystem()
    d = s.approve_intent(intent(conf=0.2), now=1.0)
    assert not d.allowed and d.reason == "low_confidence"
    assert s.stats["blocked_low_confidence"] == 1


def test_careful_confidence_bonus():
    s = SafetySystem(config={"level": "careful"})  # 0.35 + 0.15 = 0.5
    assert not s.approve_intent(intent(conf=0.42), now=1.0).allowed
    assert s.approve_intent(intent(conf=0.5), now=1.1).allowed


def test_gaze_uncertain_click_blocked():
    s = SafetySystem()
    d = s.approve_intent(intent(conf=0.5, sources=Modality.GAZE), now=1.0)
    assert not d.allowed and d.reason == "low_gaze_confidence"


def test_gaze_low_confidence_scroll_allowed():
    s = SafetySystem()
    d = s.approve_intent(intent(t=IntentType.SCROLL, conf=0.4,
                                sources=Modality.GAZE), now=1.0)
    assert d.allowed  # gaze gate is click-class only


def test_gaze_confident_click_allowed():
    s = SafetySystem()
    d = s.approve_intent(intent(conf=0.8, sources=Modality.GAZE), now=1.0)
    assert d.allowed


# ── rate limiter + click cooldown ────────────────────────────────────────────────

def test_rate_limiter_burst_blocks_with_cooldown():
    s = SafetySystem(config={"max_actions_per_sec": 3,
                             "min_click_interval": 0.0})
    t = 10.0
    for i in range(3):
        d = s.approve_intent(intent(t=IntentType.MOVE, point=(i, i)), now=t)
        assert d.allowed, i
    d = s.approve_intent(intent(t=IntentType.MOVE, point=(9, 9)), now=t + 0.1)
    assert not d.allowed and d.reason == "rate_limit"
    assert d.cooldown_remaining > 0.0
    # after the window slides, approvals work again
    d2 = s.approve_intent(intent(t=IntentType.MOVE, point=(8, 8)), now=t + 1.1)
    assert d2.allowed


def test_click_cooldown_blocks_rapid_clicks():
    s = SafetySystem(config={"min_click_interval": 0.15})
    assert s.approve_intent(intent(ts=1.0), now=1.0).allowed
    d = s.approve_intent(intent(ts=1.05), now=1.05)
    assert not d.allowed and d.reason == "click_cooldown"
    assert s.approve_intent(intent(ts=1.2), now=1.2).allowed


def test_click_cooldown_not_applied_to_move():
    s = SafetySystem(config={"min_click_interval": 0.15})
    assert s.approve_intent(intent(ts=1.0), now=1.0).allowed
    assert s.approve_intent(intent(t=IntentType.MOVE, ts=1.01), now=1.01).allowed


# ── sensitive types + confirmation flow ──────────────────────────────────────────

@pytest.mark.parametrize("t", [IntentType.CLOSE, IntentType.PASTE,
                               IntentType.HOTKEY, IntentType.MAXIMIZE,
                               IntentType.SWITCH_WINDOW])
def test_sensitive_types_need_confirmation(t):
    s = SafetySystem()
    d = s.approve_intent(intent(t=t), now=1.0)
    assert not d.allowed
    assert d.reason == "needs_confirmation"
    assert d.requires_confirmation is True
    assert s.pending_confirmation is not None


def test_confirmation_flow_approve_once():
    s = SafetySystem()
    i = intent(t=IntentType.PASTE)
    assert not s.approve_intent(i, now=1.0).allowed
    assert s.confirm("voice") is True
    assert s.pending_confirmation is None
    d = s.approve_intent(i, now=1.1)
    assert d.allowed
    # one-shot: a NEW sensitive intent needs a fresh confirmation
    assert not s.approve_intent(intent(t=IntentType.PASTE), now=1.2).allowed
    assert s.pending_confirmation is not None  # re-armed
    assert s.confirm() is True


def test_confirmation_expiry_with_injected_timestamp():
    s = SafetySystem(config={"confirmation_timeout": 5.0})
    s.request_confirmation(intent(t=IntentType.CLOSE))  # real clock → fresh
    assert s.pending_confirmation is not None
    assert s.confirm() is True
    # injected timestamp far in the past + tiny timeout → expired at query
    s2 = SafetySystem(config={"confirmation_timeout": 0.0})
    s2.request_confirmation(intent(t=IntentType.CLOSE), now=0.0)
    assert s2.pending_confirmation is None  # expired at query time
    assert s2.confirm() is False
    assert s2.stats["confirmations_expired"] == 1


def test_confirm_without_pending_false():
    s = SafetySystem()
    assert s.confirm() is False


# ── e-stop ───────────────────────────────────────────────────────────────────────

def test_trip_blocks_everything_and_reset_restores():
    s = SafetySystem()
    s.trip("user e-stop")
    assert s.level is SafetyLevel.EMERGENCY
    assert s.estop_count == 1
    d = s.approve_intent(intent(t=IntentType.MOVE, conf=0.99), now=2.0)
    assert not d.allowed and d.reason == "emergency_stop"
    s.reset()
    assert s.level is SafetyLevel.NORMAL
    assert s.approve_intent(intent(), now=2.1).allowed


def test_trip_remembers_previous_level():
    s = SafetySystem(config={"level": "careful"})
    s.trip()
    s.reset()
    assert s.level is SafetyLevel.CAREFUL


def test_reset_without_trip_keeps_level():
    s = SafetySystem(config={"level": "careful"})
    s.reset()
    assert s.level is SafetyLevel.CAREFUL


def test_estop_intent_always_passes_gate():
    s = SafetySystem()
    s.trip()
    d = s.approve_intent(intent(t=IntentType.EMERGENCY_STOP, conf=1.0), now=3.0)
    assert d.allowed and d.reason == "emergency_stop_intent"


def test_trip_clears_pending_confirmation():
    s = SafetySystem()
    s.approve_intent(intent(t=IntentType.CLOSE), now=1.0)
    assert s.pending_confirmation is not None
    s.trip()
    assert s.pending_confirmation is None


# ── SAFE_MODE whitelist ──────────────────────────────────────────────────────────

def test_safe_mode_whitelist():
    s = SafetySystem(config={"level": "safe"})
    assert s.approve_intent(intent(t=IntentType.MOVE, conf=0.9), now=1.0).allowed
    assert s.approve_intent(intent(t=IntentType.SCROLL, conf=0.9,
                                   params={"amount": 2}), now=1.01).allowed
    d = s.approve_intent(intent(t=IntentType.CLICK, conf=0.9), now=1.02)
    assert not d.allowed and d.reason == "safe_mode"


# ── stream-loss watchdog ─────────────────────────────────────────────────────────

def test_stream_loss_downgrades_after_grace():
    s = SafetySystem(config={"stream_loss_grace": 2.0})
    s.report_stream_loss(Modality.GAZE, True, now=10.0)
    s.report_stream_loss(Modality.GAZE, True, now=11.5)  # within grace
    assert s.level is SafetyLevel.NORMAL
    s.report_stream_loss(Modality.GAZE, True, now=12.5)  # beyond grace
    assert s.level is SafetyLevel.SAFE_MODE
    s.report_stream_loss(Modality.GAZE, False, now=13.0)
    assert s.level is SafetyLevel.NORMAL  # restored
    assert s.stats["stream_downgrades"] == 1
    assert s.stats["stream_restores"] == 1


def test_stream_loss_voice_episode():
    s = SafetySystem(config={"stream_loss_grace": 1.0})
    s.report_stream_loss(Modality.VOICE, True, now=0.0)
    s.report_stream_loss(Modality.VOICE, True, now=1.5)
    assert s.level is SafetyLevel.SAFE_MODE
    s.report_stream_loss(Modality.VOICE, False, now=2.0)
    assert s.level is SafetyLevel.NORMAL


def test_stream_loss_hysteresis_waits_for_all_modalities():
    s = SafetySystem(config={"stream_loss_grace": 1.0})
    s.report_stream_loss(Modality.GAZE, True, now=0.0)
    s.report_stream_loss(Modality.VOICE, True, now=0.1)
    s.report_stream_loss(Modality.GAZE, True, now=1.5)   # gaze downgrades
    assert s.level is SafetyLevel.SAFE_MODE
    s.report_stream_loss(Modality.GAZE, False, now=2.0)  # gaze back...
    assert s.level is SafetyLevel.SAFE_MODE              # ...but mic still lost
    s.report_stream_loss(Modality.VOICE, False, now=2.2)
    assert s.level is SafetyLevel.NORMAL                 # all clear → restore


def test_stream_loss_ignored_for_other_modalities():
    s = SafetySystem()
    s.report_stream_loss(Modality.HAND, True, now=0.0)
    s.report_stream_loss(Modality.HAND, True, now=100.0)
    assert s.level is SafetyLevel.NORMAL


def test_stream_loss_skipped_in_emergency():
    s = SafetySystem(config={"stream_loss_grace": 0.5})
    s.trip()
    s.report_stream_loss(Modality.GAZE, True, now=0.0)
    s.report_stream_loss(Modality.GAZE, True, now=2.0)
    assert s.level is SafetyLevel.EMERGENCY  # never downgrades e-stop
    s.report_stream_loss(Modality.GAZE, False, now=2.5)
    assert s.level is SafetyLevel.EMERGENCY


# ── stats + thread safety ────────────────────────────────────────────────────────

def test_stats_counts_reasons():
    s = SafetySystem()
    s.approve_intent(intent(), now=1.0)
    s.approve_intent(intent(conf=0.1), now=1.1)
    assert s.stats["approved"] == 1
    assert s.stats["blocked_low_confidence"] == 1


def test_thread_smoke_two_threads_no_exception():
    s = SafetySystem()
    errors = []

    def worker(offset):
        try:
            for i in range(50):
                s.approve_intent(
                    intent(t=IntentType.MOVE, point=(i, offset),
                           conf=0.9),
                    now=100.0 + offset + i * 0.001,
                )
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


def test_never_raises_on_garbage_intent():
    s = SafetySystem()
    d = s.approve_intent(Intent())  # NONE-type zero-confidence intent
    assert isinstance(d, SafetyDecision)
    assert not d.allowed
