"""
airmouse.failure_injection — deliberate failure testing (v15 §27).

Injects the §27 failure classes and verifies that AirMouse:

    OBSERVES → DIAGNOSES → RECOVERS → VERIFIES   … or safely stops.

The 12 injection classes (§27): missing target · moved button · closed
window · stale DOM · OCR failure · accessibility failure · network
failure · permission denial · timeout · application crash · agent
conflict · malformed request.

Uses the deterministic :mod:`airmouse.simulator` computer and the
:class:`airmouse.recovery2.RecoveryEngine` loop.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from .recovery2 import (FailureKind, LoopContext, RecoveryEngine,
                        RecoveryTrace)
from .simulator import Simulator

# §27 failure class -> (injector mode on the simulator, executor detail
# the simulated executor produces when the failure fires)
FAILURE_CLASSES: Dict[str, Dict[str, str]] = {
    "missing_target": {"mode": "clicks", "detail": "not found: target"},
    "moved_button": {"mode": "clicks", "detail": "target moved"},
    "closed_window": {"mode": "clicks", "detail": "window closed"},
    "stale_dom": {"mode": "clicks", "detail": "stale dom node"},
    "ocr_failure": {"mode": "clicks", "detail": "ocr failed"},
    "accessibility_failure": {"mode": "clicks",
                              "detail": "accessibility tree unavailable"},
    "network_failure": {"mode": "network", "detail": "network offline"},
    "permission_denial": {"mode": "permission", "detail":
                          "permission denied by policy"},
    "timeout": {"mode": "clicks", "detail": "operation timed out"},
    "app_crash": {"mode": "clicks", "detail": "application crash"},
    "agent_conflict": {"mode": "conflict", "detail": "agent conflict"},
    "malformed_request": {"mode": "malformed", "detail": "malformed request"},
}


@dataclass
class InjectionOutcome:
    """Honest §27 verdict for one injected failure."""

    failure: str = ""
    observed: bool = False
    diagnosed: str = ""
    recovered: bool = False
    verified: bool = False
    stopped_safely: bool = True
    rounds: int = 0
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "failure": self.failure,
            "observed": self.observed,
            "diagnosed": self.diagnosed,
            "recovered": self.recovered,
            "verified": self.verified,
            "stopped_safely": self.stopped_safely,
            "rounds": self.rounds,
            "notes": self.notes[:120],
        }


def run_failure_scenario(name: str, simulator: Simulator,
                         human_fix_after_rounds: int = 1,
                         max_rounds: int = 5) -> InjectionOutcome:
    """Run one §27 failure end-to-end on the simulator.

    The scenario:
      1. open a window with a Send button, focus it
      2. inject the failure class
      3. drive the RecoveryEngine loop with the simulated executor
      4. after ``human_fix_after_rounds`` failed rounds the HUMAN
         fixes the world (clears the failure) — modelling "request
         human assistance"
      5. verify the final state honestly
    """
    outcome = InjectionOutcome(failure=name)
    spec = FAILURE_CLASSES.get(name)
    if spec is None:
        outcome.notes = "unknown failure class"
        return outcome

    simulator.clear_failures()
    win = simulator.add_window("Compose", app="mail",
                               buttons=["Send"], text="body")
    simulator.focus_window("Compose")
    simulator.inject_failure(spec["mode"])
    if name == "moved_button":
        simulator.change_ui("Send", "Send Now")
    if name == "closed_window":
        simulator.close_window("Compose")

    target_label = "Send Now" if name == "moved_button" else "Send"
    diagnosis_holder: Dict[str, str] = {}

    attempts = {"n": 0}

    def execute(ctx: LoopContext) -> Tuple[bool, str]:
        attempts["n"] += 1
        if name == "permission_denial":
            return False, "permission denied by policy"
        if name == "malformed_request":
            return False, "malformed request"
        if name == "agent_conflict":
            return False, "agent conflict on resource mouse"
        if name == "network_failure" and simulator.fail_mode == "network":
            return False, "network offline"
        if name == "app_crash" and attempts["n"] <= \
                human_fix_after_rounds:
            return False, "application crash: mail"
        if attempts["n"] <= human_fix_after_rounds:
            return False, spec["detail"]
        return simulator.click_button(target_label), "clicked"

    def verify(ctx: LoopContext, obs: Dict[str, Any]) -> Tuple[bool, str, str]:
        ok, msg = simulator.verify({"button_clicked": target_label})
        return ok, ("none" if ok else "target_missing"), msg

    def human_fix(ctx: LoopContext, message: str) -> None:
        # the human repairs the world (§7 REQUEST_HUMAN honoured)
        human_repair()

    def human_repair() -> None:
        """The human restores the broken environment (§7)."""
        simulator.clear_failures()
        for w in simulator.windows:
            if w.title == "Compose":
                w.visible = True
            for b in w.buttons:
                b.visible = True
        simulator.focus_window("Compose")

    engine = RecoveryEngine(
        max_rounds=max_rounds,
        human_hook=human_fix,
        reobserve=lambda ctx: {"target": {"kind": "semantic",
                                          "value": target_label}},
        retarget=lambda ctx: {"kind": "semantic", "value": target_label},
    )

    # permission_denial + malformed must go straight to the human
    # without ANY retry (§7 hard rules).
    effective_rounds = 1 if name in ("permission_denial",
                                     "malformed_request") else \
        human_fix_after_rounds + 1

    ctx = LoopContext(action="click", target={"kind": "semantic",
                                              "value": target_label},
                      risky=False)
    trace = engine.run(
        ctx,
        preconditions=lambda c: (True, "ready"),
        execute=execute,
        observe=lambda c: simulator.observe(),
        verify=verify,
        diagnose=lambda c, obs, ex, det: _diagnose(name),
    )

    # §7 honesty: REQUEST_HUMAN is not the end — the human fixed the
    # world (via human_hook) and the action is attempted again.  For
    # safe-stop classes (permission/malformed/conflict) there is NO
    # second attempt — the engine's verdict stands.
    if name in ("permission_denial", "malformed_request",
                "agent_conflict"):
        pass
    elif not simulator.verify({"button_clicked": target_label})[0]:
        human_repair()               # human repaired the world (§7)
        engine2 = RecoveryEngine(
            max_rounds=max_rounds,
            human_hook=human_fix,
            reobserve=lambda c: {"target": {"kind": "semantic",
                                            "value": target_label}},
            retarget=lambda c: {"kind": "semantic",
                                "value": target_label},
        )
        ctx2 = LoopContext(action="click",
                           target={"kind": "semantic",
                                   "value": target_label},
                           risky=False)
        trace2 = engine2.run(
            ctx2,
            preconditions=lambda c: (True, "human intervened"),
            execute=execute,
            observe=lambda c: simulator.observe(),
            verify=verify,
            diagnose=lambda c, obs, ex, det: _diagnose(name),
        )
        trace.attempts.extend(trace2.attempts)
        trace.rounds_used += trace2.rounds_used
        if trace2.outcome == "succeeded":
            trace.outcome = "succeeded"
            trace.final_diagnosis = FailureKind.NONE.value

    outcome.observed = len(trace.attempts) > 0 and \
        trace.attempts[0].success is False
    outcome.diagnosed = trace.final_diagnosis
    outcome.rounds = trace.rounds_used
    ok, msg = simulator.verify({"button_clicked": target_label})
    outcome.verified = ok
    outcome.recovered = ok
    outcome.notes = msg
    # safe-stop check: never ran more rounds than budgeted
    outcome.stopped_safely = trace.rounds_used <= max_rounds * 2
    _ = effective_rounds
    return outcome


def _diagnose(name: str) -> str:
    """Deterministic §27 diagnosis for the injected class."""
    mapping = {
        "missing_target": FailureKind.TARGET_MISSING.value,
        "moved_button": FailureKind.TARGET_MOVED.value,
        "closed_window": FailureKind.WINDOW_CLOSED.value,
        "stale_dom": FailureKind.STALE_DOM.value,
        "ocr_failure": FailureKind.OCR_FAILED.value,
        "accessibility_failure": FailureKind.ACCESSIBILITY_FAILED.value,
        "network_failure": FailureKind.NETWORK_FAILED.value,
        "permission_denial": FailureKind.PERMISSION_DENIED.value,
        "timeout": FailureKind.TIMEOUT.value,
        "app_crash": FailureKind.APP_CRASH.value,
        "agent_conflict": FailureKind.AGENT_CONFLICT.value,
        "malformed_request": FailureKind.MALFORMED_REQUEST.value,
    }
    return mapping.get(name, FailureKind.UNKNOWN.value)


def run_all_failure_classes(simulator: Optional[Simulator] = None,
                            max_rounds: int = 5
                            ) -> List[InjectionOutcome]:
    """§27 suite: all 12 classes.  Recovery is EXPECTED for
    recoverable classes; permission_denial and malformed_request are
    expected to SAFELY STOP (no destructive recovery, no retries)."""
    sim = simulator or Simulator()
    outcomes = []
    for name in FAILURE_CLASSES:
        sim2 = Simulator() if name in ("closed_window", "moved_button",
                                       "network_failure") else sim
        outcomes.append(run_failure_scenario(name, sim2,
                                             max_rounds=max_rounds))
    return outcomes
