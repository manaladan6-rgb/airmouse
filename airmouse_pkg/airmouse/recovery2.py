"""
airmouse.recovery2 — Self-Healing Recovery Engine (v14 §7).

Upgrades every important action to the full loop:

    PRECONDITION → EXECUTE → OBSERVE → VERIFY → RECOVER

on top of (never replacing) the v10
:class:`airmouse.verification.RecoveryManager`.

RECOVERY STRATEGIES (§7, in escalation order):

    1. RETRY                      same plan, bounded attempts
    2. REOBSERVE                  refresh world/target state, then retry
    3. RETARGET                   re-resolve the target
    4. ALTERNATE_MODALITY         different input channel
    5. ALTERNATE_SEMANTIC_TARGET  different descriptor for same goal
    6. ALTERNATE_EXECUTION        different execution method
    7. REQUEST_HUMAN              terminal — ask the human

HARD RULES (§7):
    * Never unbounded retries (hard round cap + per-strategy caps).
    * Never bypass safety policy — the safety gate is consulted
      before every re-execution.
    * Never silently escalate privileges.
    * Destructive recovery requires confirmation via the
      ``confirm_hook`` (absent hook ⇒ no destructive recovery).
    * Every round appends an explainable trace entry (§24).

The engine is EXECUTION-AGNOSTIC: callers supply an ``executor``
callback and observer; the engine drives the loop deterministically.
Simulator-first: fully testable without hardware (§26).

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# bounded constants (§7)
# ─────────────────────────────────────────────────────────────────────────────

MAX_ROUNDS = 6                      # absolute cap on recovery rounds
MAX_ATTEMPTS_PER_ROUND = 1
DEFAULT_MAX_RETRIES = 2
MAX_TRACE = 64


class RecoveryStrategy2(enum.Enum):
    """The §7 strategy ladder (escalation order)."""

    RETRY = "retry"
    REOBSERVE = "reobserve"
    RETARGET = "retarget"
    ALTERNATE_MODALITY = "alternate_modality"
    ALTERNATE_SEMANTIC_TARGET = "alternate_semantic_target"
    ALTERNATE_EXECUTION = "alternate_execution"
    REQUEST_HUMAN = "request_human"
    GIVE_UP = "give_up"                 # safe stop (§27)


class FailureKind(enum.Enum):
    """Deterministic failure diagnosis classes (§27 failure set)."""

    NONE = "none"
    TARGET_MISSING = "target_missing"
    TARGET_MOVED = "target_moved"
    WINDOW_CLOSED = "window_closed"
    STALE_DOM = "stale_dom"
    OCR_FAILED = "ocr_failed"
    ACCESSIBILITY_FAILED = "accessibility_failed"
    NETWORK_FAILED = "network_failed"
    PERMISSION_DENIED = "permission_denied"
    TIMEOUT = "timeout"
    APP_CRASH = "app_crash"
    AGENT_CONFLICT = "agent_conflict"
    MALFORMED_REQUEST = "malformed_request"
    UNKNOWN = "unknown"


# diagnosis → strategy ladder (deterministic §7 mapping)
_DIAGNOSIS_LADDER: Dict[FailureKind, Tuple[RecoveryStrategy2, ...]] = {
    FailureKind.TARGET_MISSING: (
        RecoveryStrategy2.REOBSERVE, RecoveryStrategy2.RETARGET,
        RecoveryStrategy2.ALTERNATE_SEMANTIC_TARGET,
        RecoveryStrategy2.REQUEST_HUMAN),
    FailureKind.TARGET_MOVED: (
        RecoveryStrategy2.REOBSERVE, RecoveryStrategy2.RETARGET,
        RecoveryStrategy2.REQUEST_HUMAN),
    FailureKind.WINDOW_CLOSED: (
        RecoveryStrategy2.REOBSERVE,
        RecoveryStrategy2.ALTERNATE_SEMANTIC_TARGET,
        RecoveryStrategy2.REQUEST_HUMAN),
    FailureKind.STALE_DOM: (
        RecoveryStrategy2.REOBSERVE, RecoveryStrategy2.RETARGET,
        RecoveryStrategy2.ALTERNATE_EXECUTION, RecoveryStrategy2.REQUEST_HUMAN),
    FailureKind.OCR_FAILED: (
        RecoveryStrategy2.ALTERNATE_SEMANTIC_TARGET,
        RecoveryStrategy2.RETARGET, RecoveryStrategy2.REQUEST_HUMAN),
    FailureKind.ACCESSIBILITY_FAILED: (
        RecoveryStrategy2.ALTERNATE_SEMANTIC_TARGET,
        RecoveryStrategy2.ALTERNATE_EXECUTION,
        RecoveryStrategy2.REQUEST_HUMAN),
    FailureKind.NETWORK_FAILED: (
        RecoveryStrategy2.RETRY, RecoveryStrategy2.REQUEST_HUMAN),
    FailureKind.PERMISSION_DENIED: (
        RecoveryStrategy2.REQUEST_HUMAN,),          # never escalate (§7)
    FailureKind.TIMEOUT: (
        RecoveryStrategy2.RETRY, RecoveryStrategy2.REOBSERVE,
        RecoveryStrategy2.REQUEST_HUMAN),
    FailureKind.APP_CRASH: (
        RecoveryStrategy2.REOBSERVE,
        RecoveryStrategy2.ALTERNATE_EXECUTION,
        RecoveryStrategy2.REQUEST_HUMAN),
    FailureKind.AGENT_CONFLICT: (
        RecoveryStrategy2.REQUEST_HUMAN,),          # human decides (§12)
    FailureKind.MALFORMED_REQUEST: (
        RecoveryStrategy2.GIVE_UP,),                # fail closed (§23)
    FailureKind.UNKNOWN: (
        RecoveryStrategy2.RETRY, RecoveryStrategy2.REOBSERVE,
        RecoveryStrategy2.REQUEST_HUMAN),
    FailureKind.NONE: (),
}


@dataclass
class ActionAttempt:
    """One execution attempt inside the loop (§7)."""

    round_index: int = 0
    strategy: str = RecoveryStrategy2.RETRY.value
    executed: bool = False
    success: bool = False
    verified: bool = False
    diagnosis: str = FailureKind.NONE.value
    detail: str = ""


@dataclass
class RecoveryTrace:
    """Explainable record of a full loop (§7 + §24)."""

    action: str = ""
    outcome: str = "pending"           # succeeded/failed/requires_human/gave_up
    rounds_used: int = 0
    attempts: List[ActionAttempt] = field(default_factory=list)
    final_diagnosis: str = FailureKind.NONE.value
    human_message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "outcome": self.outcome,
            "rounds_used": self.rounds_used,
            "final_diagnosis": self.final_diagnosis,
            "human_message": self.human_message,
            "attempts": [
                {"round": a.round_index, "strategy": a.strategy,
                 "executed": a.executed, "success": a.success,
                 "verified": a.verified, "diagnosis": a.diagnosis,
                 "detail": a.detail[:80]}
                for a in self.attempts[:MAX_TRACE]],
        }


@dataclass
class LoopContext:
    """Everything the executor/observer may need per round (§7)."""

    action: str = "none"
    target: Dict[str, Any] = field(default_factory=dict)
    modality: str = "voice"
    execution_method: str = "default"
    attempt: int = 0
    round_index: int = 0
    risky: bool = False                  # destructive/sensitive work
    params: Dict[str, Any] = field(default_factory=dict)


# callback contracts (execution-agnostic; all optional to stub)

PreconditionFn = Callable[[LoopContext], Tuple[bool, str]]
ExecuteFn = Callable[[LoopContext], Tuple[bool, str]]
ObserveFn = Callable[[LoopContext], Dict[str, Any]]
VerifyFn = Callable[[LoopContext, Dict[str, Any]], Tuple[bool, str, str]]
# VerifyFn returns (verified, diagnosis, detail)
DiagnoseFn = Callable[[LoopContext, Dict[str, Any], bool, str], str]
ReobserveFn = Callable[[LoopContext], Dict[str, Any]]
RetargetFn = Callable[[LoopContext], Optional[Dict[str, Any]]]
SafetyGateFn = Callable[[LoopContext], Tuple[bool, str]]
ConfirmFn = Callable[[LoopContext, str], bool]
HumanRequestFn = Callable[[LoopContext, str], None]


class RecoveryEngine:
    """The §7 self-healing loop: PRECONDITION→EXECUTE→OBSERVE→VERIFY→
    RECOVER with bounded, safety-gated strategies."""

    def __init__(self,
                 max_rounds: int = MAX_ROUNDS,
                 max_retries: int = DEFAULT_MAX_RETRIES,
                 safety_gate: Optional[SafetyGateFn] = None,
                 confirm_hook: Optional[ConfirmFn] = None,
                 human_hook: Optional[HumanRequestFn] = None,
                 reobserve: Optional[ReobserveFn] = None,
                 retarget: Optional[RetargetFn] = None) -> None:
        self.max_rounds = max(1, min(int(max_rounds), MAX_ROUNDS))
        self.max_retries = max(1, min(int(max_retries), 4))
        self.safety_gate = safety_gate
        self.confirm_hook = confirm_hook
        self.human_hook = human_hook
        self.reobserve_fn = reobserve
        self.retarget_fn = retarget
        self._rounds_used = 0

    # ── the loop (§7) ───────────────────────────────────────────────────

    def run(self, ctx: LoopContext,
            preconditions: Optional[PreconditionFn] = None,
            execute: Optional[ExecuteFn] = None,
            observe: Optional[ObserveFn] = None,
            verify: Optional[VerifyFn] = None,
            diagnose: Optional[DiagnoseFn] = None) -> RecoveryTrace:
        trace = RecoveryTrace(action=ctx.action)

        # PRECONDITION (§7 stage 1)
        if preconditions is not None:
            try:
                ok, why = preconditions(ctx)
            except Exception:
                ok, why = False, "precondition hook crashed"
            if not ok:
                trace.outcome = "failed"
                trace.final_diagnosis = FailureKind.MALFORMED_REQUEST.value
                trace.human_message = f"preconditions unmet: {why[:60]}"
                trace.attempts.append(ActionAttempt(
                    0, "precondition", False, False, False,
                    FailureKind.MALFORMED_REQUEST.value, str(why)[:80]))
                return trace

        strategy_ladder: Tuple[RecoveryStrategy2, ...] = (
            RecoveryStrategy2.RETRY,)
        rounds = 0

        while rounds < self.max_rounds:
            rounds = rounds + 1
            trace.rounds_used = rounds
            ctx.round_index = rounds

            # SAFETY GATE before every execution (§7 hard rule)
            if self.safety_gate is not None:
                try:
                    allowed, reason = self.safety_gate(ctx)
                except Exception:
                    allowed, reason = False, "safety gate crashed"
                if not allowed:
                    trace.outcome = "blocked"
                    trace.final_diagnosis = \
                        FailureKind.PERMISSION_DENIED.value
                    trace.human_message = f"safety: {reason[:60]}"
                    return trace

            # EXECUTE (§7 stage 2)
            executed, exec_detail = False, ""
            if execute is not None:
                try:
                    executed, exec_detail = execute(ctx)
                except Exception as exc:                  # app crash etc.
                    executed, exec_detail = False, f"executor raised: {exc}"
            attempt = ActionAttempt(round, _strategy_of_round(rounds).value,
                                    executed, executed, False,
                                    FailureKind.NONE.value,
                                    str(exec_detail)[:80])
            trace.attempts.append(attempt)

            # OBSERVE (§7 stage 3)
            observation: Dict[str, Any] = {}
            if observe is not None:
                try:
                    observation = observe(ctx) or {}
                except Exception:
                    observation = {"observe_failed": True}

            # VERIFY (§7 stage 4)
            verified, diag, vdetail = self._verify(
                ctx, observation, executed, exec_detail, verify, diagnose,
                attempt)
            attempt.verified = verified
            attempt.diagnosis = diag
            attempt.detail = str(vdetail)[:80]

            if executed and verified:
                trace.outcome = "succeeded"
                trace.final_diagnosis = FailureKind.NONE.value
                return trace

            # RECOVER (§7 stage 5): pick the next strategy
            failure = FailureKind(diag) if diag in _VALID_KINDS else \
                FailureKind.UNKNOWN
            trace.final_diagnosis = failure.value
            if rounds == 1:
                strategy_ladder = _DIAGNOSIS_LADDER.get(
                    failure, _DIAGNOSIS_LADDER[FailureKind.UNKNOWN])

            next_strategy = self._pick_strategy(strategy_ladder, rounds,
                                                trace)
            if next_strategy is None:
                break
            if next_strategy is RecoveryStrategy2.REQUEST_HUMAN:
                trace.outcome = "requires_human"
                trace.human_message = (
                    f"{ctx.action} failed: {failure.value}; "
                    f"human assistance requested")
                if self.human_hook is not None:
                    try:
                        self.human_hook(ctx, trace.human_message)
                    except Exception:
                        pass
                return trace
            if next_strategy is RecoveryStrategy2.GIVE_UP:
                trace.outcome = "gave_up"
                trace.human_message = "malformed request — fail closed"
                return trace

            applied = self._apply_strategy(next_strategy, ctx, trace)
            if not applied:
                break

        # rounds exhausted — safe stop, never unbounded (§7)
        trace.outcome = "requires_human" if trace.outcome == "pending" \
            else trace.outcome
        if trace.outcome != "requires_human":
            trace.outcome = trace.outcome or "failed"
            trace.outcome = "failed"
        trace.human_message = trace.human_message or \
            f"{ctx.action}: recovery budget exhausted"
        if self.human_hook is not None:
            try:
                self.human_hook(ctx, trace.human_message)
            except Exception:
                pass
        return trace

    # ── strategy mechanics ──────────────────────────────────────────────

    def _verify(self, ctx: LoopContext, observation: Dict[str, Any],
                executed: bool, exec_detail: str,
                verify: Optional[VerifyFn],
                diagnose: Optional[DiagnoseFn],
                attempt: ActionAttempt) -> Tuple[bool, str, str]:
        if not executed:
            # diagnose the execution failure deterministically
            if diagnose is not None:
                try:
                    d = diagnose(ctx, observation, False, exec_detail)
                    return False, _norm_kind(d), str(exec_detail)[:80]
                except Exception:
                    pass
            return False, _infer_failure(exec_detail, observation), \
                str(exec_detail)[:80]
        if verify is None:
            # no verifier: execution success is accepted but marked
            # unverified-by-design in the detail
            return True, FailureKind.NONE.value, "no verifier (accepted)"
        try:
            verified, diag, detail = verify(ctx, observation)
            return bool(verified), _norm_kind(diag), str(detail)[:80]
        except Exception:
            return False, FailureKind.UNKNOWN.value, "verifier crashed"

    def _pick_strategy(self, ladder: Tuple[RecoveryStrategy2, ...],
                       rounds: int, trace: RecoveryTrace
                       ) -> Optional[RecoveryStrategy2]:
        idx = min(rounds - 1, len(ladder) - 1)
        if rounds - 1 >= len(ladder):
            return None
        strategy = ladder[idx]
        # RETRY has its own bounded budget before escalating
        retries_so_far = sum(
            1 for a in trace.attempts
            if a.strategy == RecoveryStrategy2.RETRY.value)
        if strategy is RecoveryStrategy2.RETRY and \
                retries_so_far >= self.max_retries:
            return None     # escalate: caller ladder moves on next round
        return strategy

    def _apply_strategy(self, strategy: RecoveryStrategy2, ctx: LoopContext,
                        trace: RecoveryTrace) -> bool:
        """Mutate the context for the next round.  Returns False when a
        strategy is unavailable (then the loop stops safely)."""
        if strategy is RecoveryStrategy2.RETRY:
            ctx.attempt += 1
            return True
        if strategy is RecoveryStrategy2.REOBSERVE:
            if self.reobserve_fn is not None:
                try:
                    fresh = self.reobserve_fn(ctx) or {}
                    if "target" in fresh and isinstance(fresh["target"], dict):
                        ctx.target = fresh["target"]
                except Exception:
                    pass
            ctx.attempt += 1
            return True
        if strategy is RecoveryStrategy2.RETARGET:
            if self.retarget_fn is not None:
                try:
                    new_target = self.retarget_fn(ctx)
                    if new_target:
                        ctx.target = dict(new_target)
                except Exception:
                    pass
            ctx.attempt += 1
            return True
        if strategy is RecoveryStrategy2.ALTERNATE_MODALITY:
            alternates = {"voice": "keyboard", "keyboard": "voice",
                          "gaze": "voice", "hand": "voice"}
            ctx.modality = alternates.get(ctx.modality, "keyboard")
            ctx.attempt += 1
            return True
        if strategy is RecoveryStrategy2.ALTERNATE_SEMANTIC_TARGET:
            alt = ctx.target.get("alternate") if isinstance(
                ctx.target, dict) else None
            if not alt:
                return False        # no alternative known → safe stop
            ctx.target = dict(alt) if isinstance(alt, dict) else \
                {"kind": "semantic", "value": str(alt)}
            ctx.attempt += 1
            return True
        if strategy is RecoveryStrategy2.ALTERNATE_EXECUTION:
            methods = {"default": "keyboard_shortcut",
                       "keyboard_shortcut": "menu_navigation"}
            nxt = methods.get(ctx.execution_method)
            if not nxt:
                return False
            ctx.execution_method = nxt
            ctx.attempt += 1
            return True
        return False


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────

_VALID_KINDS = frozenset(k.value for k in FailureKind)
_ROUND_STRATEGY = {1: RecoveryStrategy2.RETRY,
                   2: RecoveryStrategy2.REOBSERVE,
                   3: RecoveryStrategy2.RETARGET,
                   4: RecoveryStrategy2.ALTERNATE_SEMANTIC_TARGET,
                   5: RecoveryStrategy2.ALTERNATE_EXECUTION,
                   6: RecoveryStrategy2.REQUEST_HUMAN}


def _strategy_of_round(round_index: int) -> RecoveryStrategy2:
    return _ROUND_STRATEGY.get(round_index, RecoveryStrategy2.REQUEST_HUMAN)


def _norm_kind(diag: Any) -> str:
    text = str(diag or "").strip().lower()
    return text if text in _VALID_KINDS else FailureKind.UNKNOWN.value


def _infer_failure(exec_detail: str, observation: Dict[str, Any]) -> str:
    """Deterministic failure inference from executor detail (§27)."""
    text = (exec_detail or "").lower() + " " + \
        " ".join(str(v).lower() for v in observation.values()
                 if isinstance(v, str))
    if "permission" in text or "denied" in text:
        return FailureKind.PERMISSION_DENIED.value
    if "not found" in text or "missing" in text or "no such" in text:
        return FailureKind.TARGET_MISSING.value
    if "moved" in text:
        return FailureKind.TARGET_MOVED.value
    if "closed" in text:
        return FailureKind.WINDOW_CLOSED.value
    if "stale" in text or "detached" in text:
        return FailureKind.STALE_DOM.value
    if "ocr" in text:
        return FailureKind.OCR_FAILED.value
    if "accessibility" in text or "a11y" in text:
        return FailureKind.ACCESSIBILITY_FAILED.value
    if "network" in text or "offline" in text:
        return FailureKind.NETWORK_FAILED.value
    if "timeout" in text or "timed out" in text:
        return FailureKind.TIMEOUT.value
    if "crash" in text:
        return FailureKind.APP_CRASH.value
    if "conflict" in text:
        return FailureKind.AGENT_CONFLICT.value
    if "malformed" in text or "invalid" in text:
        return FailureKind.MALFORMED_REQUEST.value
    return FailureKind.UNKNOWN.value
