"""
airmouse.guided_test — Guided Test Laboratory (v15.1 hardening §-tests).

A 12-test guided laboratory that a real user can walk through at their
desk (`airmouse test --guided`) and that CI can run non-interactively.

HONESTY IS THE CORE FEATURE
---------------------------
* PHYSICAL (hardware) tests can NEVER auto-PASS.  They either get an
  explicit human confirmation ([Y/N] at the desk) or they end in
  ACTION_REQUIRED / FAIL — never in a manufactured PASS.
* SIMULATION results are always labelled ``[SIMULATION]``: they prove
  the SOFTWARE works (deterministic, headless) — they are NOT hardware
  evidence.
* If the user answers N, the result is FAIL and their own words are
  recorded verbatim in the detail.
* Deterministic: same inputs → same report.  All randomness/latency is
  injected or omitted; the only wall-clock value is ``generated_at`` in
  :meth:`GuidedTestReport.to_machine`.

THE 12 TESTS
------------
    installation  AUTOMATED   version, CLI entry, core deps
    camera        PHYSICAL    hand-tracking follow test (+ optional frame probe)
    mouse         PHYSICAL    move/click/double-click/right-click/scroll/drag
    gaze          PHYSICAL    targets A/B/C + blink
    voice         PHYSICAL    8 command phrases, heard/interpreted/intent/confidence
    dictation     PHYSICAL    raw → normalized → final + edit commands
    intelligence  SIMULATION  OBSERVED → PREDICTED → EXECUTED (behind the gate)
    browser       SIMULATION  simulated bridge: open → click → verify → tabs
    agent         SIMULATION  benign AIP flow approved + destructive REJECTED
    multi_agent   SIMULATION  lease → conflict → handoff → E-STOP stops all
    recovery      SIMULATION  §27 failures: OBSERVE → DIAGNOSE → RECOVER → VERIFY
    offline       SIMULATION  run_offline_selftest + no-network gate check

CONTROL HIERARCHY (never bypassed, demonstrated in the agent/multi-agent
simulations):  EMERGENCY STOP > HUMAN OVERRIDE > SAFETY POLICY >
PERMISSION > AGENT > PREDICTION.

Every subsystem use is wrapped so a missing/broken module degrades to
SKIP with a reason — the laboratory itself never crashes.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import platform
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, IO, List, Optional, Sequence, Tuple

from . import __version__

__all__ = [
    "TestStatus",
    "TestMode",
    "GuidedTestResult",
    "GuidedTestReport",
    "GuidedTestRunner",
    "TEST_ORDER",
    "TEST_IDS",
    "run_guided",
    "format_report",
]


# ---------------------------------------------------------------------------
# status + mode enums
# ---------------------------------------------------------------------------


class TestStatus(str, Enum):
    """Outcome of one guided test.  FAIL-CLOSED: only PASS means PASS."""

    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"
    ACTION_REQUIRED = "ACTION_REQUIRED"


class TestMode(str, Enum):
    """How a test is executed — and what its evidence is worth."""

    AUTOMATED = "automated"
    SIMULATION = "simulation"
    PHYSICAL = "physical"


# ---------------------------------------------------------------------------
# fixed test plan (id, title, mode) — the 12 canonical tests, in order
# ---------------------------------------------------------------------------

TEST_ORDER: Tuple[Tuple[str, str, TestMode], ...] = (
    ("installation", "Installation", TestMode.AUTOMATED),
    ("camera", "Camera", TestMode.PHYSICAL),
    ("mouse", "Mouse", TestMode.PHYSICAL),
    ("gaze", "Gaze", TestMode.PHYSICAL),
    ("voice", "Voice", TestMode.PHYSICAL),
    ("dictation", "Dictation", TestMode.PHYSICAL),
    ("intelligence", "Intelligence", TestMode.SIMULATION),
    ("browser", "Browser", TestMode.SIMULATION),
    ("agent", "Agent", TestMode.SIMULATION),
    ("multi_agent", "Multi-Agent", TestMode.SIMULATION),
    ("recovery", "Recovery", TestMode.SIMULATION),
    ("offline", "Offline", TestMode.SIMULATION),
)

#: The 12 canonical test ids, in report order.
TEST_IDS: Tuple[str, ...] = tuple(t[0] for t in TEST_ORDER)

_MODES: Dict[str, TestMode] = {tid: mode for tid, _, mode in TEST_ORDER}
_TITLES: Dict[str, str] = {tid: title for tid, title, _ in TEST_ORDER}

_STATUS_DISPLAY: Dict[TestStatus, str] = {
    TestStatus.PASS: "PASS",
    TestStatus.FAIL: "FAIL",
    TestStatus.SKIP: "SKIP",
    TestStatus.ACTION_REQUIRED: "ACTION REQUIRED",
}


# ---------------------------------------------------------------------------
# result + report
# ---------------------------------------------------------------------------


@dataclass
class GuidedTestResult:
    """One test's honest outcome."""

    id: str
    title: str
    mode: TestMode
    status: TestStatus
    detail: str = ""
    measurements: Dict[str, object] = field(default_factory=dict)


def _utc_now_iso() -> str:
    """Wall-clock timestamp for machine reports (the ONLY nondeterminism)."""
    return datetime.now(timezone.utc).isoformat()


def _jsonable(obj: Any) -> Any:
    """Best-effort JSON-safe conversion (never raises)."""
    try:
        return json.loads(json.dumps(obj, default=str, sort_keys=True))
    except Exception:
        return repr(obj)


def _major_version(version: str) -> str:
    """``"15.0.0"`` → ``"15"`` (report header version)."""
    major = str(version or "").split(".")[0]
    return major or "15"


@dataclass
class GuidedTestReport:
    """The full laboratory outcome (ordered results + honest counts)."""

    results: List[GuidedTestResult] = field(default_factory=list)
    version: str = "15"

    # -- queries -------------------------------------------------------------

    @property
    def test_ids(self) -> List[str]:
        return [r.id for r in self.results]

    def hardware_counts(self) -> Tuple[int, int]:
        """(passed, total) over PHYSICAL tests."""
        rows = [r for r in self.results if r.mode is TestMode.PHYSICAL]
        return (sum(1 for r in rows if r.status is TestStatus.PASS),
                len(rows))

    def simulation_counts(self) -> Tuple[int, int]:
        """(passed, total) over SIMULATION + AUTOMATED tests."""
        rows = [r for r in self.results
                if r.mode in (TestMode.SIMULATION, TestMode.AUTOMATED)]
        return (sum(1 for r in rows if r.status is TestStatus.PASS),
                len(rows))

    def overall(self) -> str:
        """VERIFIED only when EVERY test (hardware included) is PASS."""
        if self.results and all(r.status is TestStatus.PASS
                                for r in self.results):
            return "VERIFIED"
        return "PARTIALLY VERIFIED"

    # -- rendering -----------------------------------------------------------

    def format_report(self, version: str = "15") -> str:
        """The fixed-shape human report (exact layout, see docs)."""
        line = "=" * 40
        out: List[str] = [
            line,
            "        AIRMouse v{} TEST REPORT".format(version),
            line,
            "",
        ]
        for r in self.results:
            out.append("{:<20}{}".format(r.title,
                                         _STATUS_DISPLAY.get(r.status,
                                                             str(r.status))))
        hw_p, hw_t = self.hardware_counts()
        sim_p, sim_t = self.simulation_counts()
        out.append("")
        out.append("{:<20}{}/{}".format("Hardware tests:", hw_p, hw_t))
        out.append("{:<20}{}/{}".format("Simulation tests:", sim_p, sim_t))
        out.append("")
        out.append("OVERALL:             " + self.overall())
        out.append(line)
        return "\n".join(out) + "\n"

    def to_machine(self, version: str = "") -> Dict[str, Any]:
        """JSON-serializable report (version, generated_at, results,
        hardware counts, simulation counts, overall)."""
        v = str(version) if version else (self.version or "15")
        hw_p, hw_t = self.hardware_counts()
        sim_p, sim_t = self.simulation_counts()
        return {
            "version": v,
            "generated_at": _utc_now_iso(),
            "results": [
                {
                    "id": r.id,
                    "title": r.title,
                    "mode": r.mode.value,
                    "status": r.status.value,
                    "detail": r.detail,
                    "measurements": _jsonable(r.measurements),
                }
                for r in self.results
            ],
            "hardware": {"passed": hw_p, "total": hw_t},
            "simulation": {"passed": sim_p, "total": sim_t},
            "overall": self.overall(),
        }


# ---------------------------------------------------------------------------
# the runner
# ---------------------------------------------------------------------------


#: hard cap on recorded free-text answers (hostile-input bound: a
#: oversized/abusive answer can never bloat the report or its evidence)
_ANSWER_MAX_CHARS = 2000


class GuidedTestRunner:
    """Runs the 12-test laboratory; plain-spoken; never crashes.

    ``out`` receives the narration.  ``input_fn`` is the [Y/N] answer
    source (default ``input``).  In NON-interactive mode (``interactive=
    False`` or when ``input_fn`` raises EOFError/OSError) PHYSICAL tests
    are never executed: they become ACTION_REQUIRED with the full
    instructions — and no prompt is ever attempted.
    """

    def __init__(self, out: IO = sys.stdout,
                 input_fn: Callable[[str], str] = input) -> None:
        self.out = out
        self.input_fn = input_fn

    # -- io helpers ------------------------------------------------------------

    def _print(self, text: str = "") -> None:
        try:
            self.out.write(str(text) + "\n")
            try:
                self.out.flush()
            except Exception:
                pass
        except Exception:
            pass  # output must never break the lab

    def _ask_yes_no(self, question: str) -> Optional[bool]:
        """Ask one [Y/N] question.  None = input ended (EOF/OSError).

        Fail-closed: only an explicit ``y``/``yes`` counts as YES;
        anything else (n, empty, garbage) counts as NO.
        """
        self._print("  " + question)
        try:
            raw = str(self.input_fn("  Y/N: ") or "")
        except (EOFError, OSError):
            return None
        except Exception:
            return None
        return raw.strip().strip("'\"").lower() in ("y", "yes")

    def _ask_line(self, prompt: str) -> Optional[str]:
        """Ask for one free-text line.  None = input ended.

        The recorded answer is hard-capped at ``_ANSWER_MAX_CHARS`` so
        an oversized or hostile input can never bloat the report.
        """
        try:
            return str(self.input_fn(prompt) or "")[:_ANSWER_MAX_CHARS]
        except (EOFError, OSError):
            return None
        except Exception:
            return None

    # -- orchestration -----------------------------------------------------------

    def run(self, interactive: bool = True, auto_simulate: bool = False,
            only: Optional[Sequence[str]] = None) -> GuidedTestReport:
        """Run the laboratory and return the report (also printed to out)."""
        self.interactive = bool(interactive)
        self.auto_simulate = bool(auto_simulate)
        narrate = self.interactive and not self.auto_simulate
        wanted: Optional[List[str]] = None
        if only is not None:
            wanted = [str(o).strip().lower() for o in only if str(o).strip()]

        self._print("")
        self._print("AIRMouse Guided Test Laboratory — v" + __version__)
        self._print("Physical (hardware) tests can NEVER auto-pass: they need")
        self._print("a human at the desk.  Simulation results are labelled")
        self._print("[SIMULATION] — they prove the software, not the hardware.")
        if wanted is not None:
            self._print("Running only: " + ", ".join(wanted))

        results: List[GuidedTestResult] = []
        total = len(TEST_ORDER)
        for i, (tid, title, mode) in enumerate(TEST_ORDER, 1):
            if wanted is not None and tid not in wanted:
                continue
            self._print("")
            self._print("[{}/{}] {} ({})".format(i, total, title, mode.value))
            if narrate:
                why = _NARRATION.get(tid)
                if why:
                    self._print("  " + why)
            try:
                result = self._dispatch(tid, title, mode)
            except Exception as exc:          # the lab must never crash
                result = GuidedTestResult(
                    tid, title, mode, TestStatus.SKIP,
                    detail="SKIP: runner error: {!r}".format(exc),
                    measurements={"reason": "runner error"})
            if mode is TestMode.SIMULATION and \
                    not result.detail.startswith("[SIMULATION]"):
                result.detail = "[SIMULATION] " + result.detail
            results.append(result)
            self._print("  -> " + _STATUS_DISPLAY.get(result.status,
                                                      str(result.status)))
            # full evidence into the log — CI gets the whole honest story
            for ln in result.detail.splitlines():
                self._print("    " + ln)

        report = GuidedTestReport(results=results,
                                  version=_major_version(__version__))
        self._print("")
        self._print(report.format_report(version=_major_version(__version__)))
        return report

    def _dispatch(self, tid: str, title: str,
                  mode: TestMode) -> GuidedTestResult:
        table = {
            "installation": self._test_installation,
            "camera": self._test_camera,
            "mouse": self._test_mouse,
            "gaze": self._test_gaze,
            "voice": self._test_voice,
            "dictation": self._test_dictation,
            "intelligence": self._test_intelligence,
            "browser": self._test_browser,
            "agent": self._test_agent,
            "multi_agent": self._test_multi_agent,
            "recovery": self._test_recovery,
            "offline": self._test_offline,
        }
        fn = table.get(tid)
        if fn is None:
            return GuidedTestResult(tid, title, mode, TestStatus.SKIP,
                                    detail="SKIP: unknown test id")
        return fn(tid, title)

    def _skip(self, tid: str, title: str, why: str) -> GuidedTestResult:
        return GuidedTestResult(tid, title, _MODES.get(tid, TestMode.SIMULATION),
                                TestStatus.SKIP,
                                detail="SKIP: " + str(why),
                                measurements={"reason": str(why)})

    # ════════════════════════════════════════════════════════════════════
    # AUTOMATED: installation
    # ════════════════════════════════════════════════════════════════════

    def _test_installation(self, tid: str,
                           title: str) -> GuidedTestResult:
        measurements: Dict[str, object] = {}
        detail_lines: List[str] = []
        problems: List[str] = []

        measurements["version"] = __version__
        detail_lines.append("package version: " + __version__)
        if not __version__:
            problems.append("package version missing")

        cli: Dict[str, object] = {"importable": False}
        try:
            importlib.import_module("airmouse.__main__")
            cli["importable"] = True
            detail_lines.append("CLI entry (airmouse.__main__): importable")
        except Exception as exc:
            cli["error"] = type(exc).__name__
            problems.append("CLI module not importable")
            detail_lines.append("CLI entry (airmouse.__main__): NOT importable")
        measurements["cli"] = cli

        deps: Dict[str, Dict[str, object]] = {}
        for modname, dist in (("numpy", "numpy"), ("cv2", "opencv-python"),
                              ("mediapipe", "mediapipe"),
                              ("pynput", "pynput")):
            entry: Dict[str, object] = {"present": False, "version": ""}
            try:
                spec = importlib.util.find_spec(modname)
                entry["present"] = spec is not None
                if entry["present"]:
                    try:
                        entry["version"] = importlib.metadata.version(dist)
                    except Exception:
                        entry["version"] = "present (version unreadable)"
            except Exception:
                entry["present"] = False
            deps[modname] = entry
            if not entry["present"]:
                problems.append("missing dependency: " + modname)
            detail_lines.append(
                "{}: {}".format(modname, entry["version"] or "MISSING"))
        measurements["dependencies"] = deps

        pyver = platform.python_version()
        measurements["python"] = pyver
        detail_lines.append("python: " + pyver)

        detail = "\n".join(detail_lines)
        if problems:
            return GuidedTestResult(tid, title, TestMode.AUTOMATED,
                                    TestStatus.FAIL,
                                    detail=detail + "\nproblems: " +
                                    "; ".join(problems),
                                    measurements=measurements)
        return GuidedTestResult(tid, title, TestMode.AUTOMATED,
                                TestStatus.PASS,
                                detail=detail, measurements=measurements)

    # ════════════════════════════════════════════════════════════════════
    # PHYSICAL tests (never auto-pass)
    # ════════════════════════════════════════════════════════════════════

    def _instructions_text(self, steps: Sequence[str],
                           questions: Sequence[str],
                           intro: str = "") -> str:
        lines: List[str] = []
        if intro:
            lines.append(intro)
        for i, s in enumerate(steps, 1):
            lines.append("{}. {}".format(i, s))
        lines.extend(questions)
        return "\n".join(lines)

    def _physical_yn_test(self, tid: str, title: str,
                          steps: Sequence[str], questions: Sequence[str],
                          intro: str = "",
                          extra: Optional[Dict[str, object]] = None,
                          ) -> GuidedTestResult:
        """Shared PHYSICAL flow: show steps, ask [Y/N]s, record honestly."""
        instructions = self._instructions_text(steps, questions, intro)

        if not self.interactive:
            measurements: Dict[str, object] = {
                "reason": "non-interactive run: no human to confirm",
                "steps": list(steps),
                "questions": list(questions),
            }
            if extra:
                measurements.update(extra)
            detail = ("ACTION REQUIRED — a human must do this test at the "
                      "desk; hardware tests can NEVER auto-pass.\n"
                      + instructions
                      + "\nRun `airmouse test --guided` to confirm "
                        "interactively.")
            return GuidedTestResult(tid, title, TestMode.PHYSICAL,
                                    TestStatus.ACTION_REQUIRED,
                                    detail=detail, measurements=measurements)

        measurements = {"input": "interactive [Y/N] answers",
                        "steps": list(steps)}
        if extra:
            measurements.update(extra)
        if intro:
            self._print("  " + intro)
        for s in steps:
            self._print("  " + s)

        answers: List[bool] = []
        for q in questions:
            ans = self._ask_yes_no(q)
            if ans is None:
                measurements["reason"] = \
                    "input ended (EOF/OSError) before confirmation"
                detail = ("Input ended before this test could be confirmed — "
                          "recorded as ACTION REQUIRED, never a PASS.\n"
                          + instructions)
                return GuidedTestResult(tid, title, TestMode.PHYSICAL,
                                        TestStatus.ACTION_REQUIRED,
                                        detail=detail,
                                        measurements=measurements)
            answers.append(ans)
        measurements["answers"] = ["Y" if a else "N" for a in answers]

        if all(answers):
            return GuidedTestResult(tid, title, TestMode.PHYSICAL,
                                    TestStatus.PASS,
                                    detail="User confirmed every step with Y "
                                           "(interactive [Y/N]).",
                                    measurements=measurements)

        note = self._ask_line("What went wrong? (your own words — press "
                              "Enter to skip): ")
        failed = [q for q, a in zip(questions, answers) if not a]
        detail = ("User answered N — recorded as FAIL with their words.\n"
                  "Failed checks:\n  - " + "\n  - ".join(failed))
        if note:
            detail += '\nUser note: "' + note + '"'
        measurements["user_note"] = note or ""
        return GuidedTestResult(tid, title, TestMode.PHYSICAL,
                                TestStatus.FAIL, detail=detail,
                                measurements=measurements)

    def _camera_probe(self) -> Dict[str, object]:
        """OPTIONAL real frame grab (guarded).  Evidence only — it can
        never turn the test into a PASS."""
        probe: Dict[str, object] = {"attempted": False, "opened": False,
                                    "frame_ok": False}
        try:
            import cv2  # noqa: F401
        except Exception as exc:
            probe["error"] = "cv2 unavailable: " + type(exc).__name__
            return probe
        try:
            probe["attempted"] = True
            cap = cv2.VideoCapture(0)
            try:
                probe["opened"] = bool(cap.isOpened())
                if probe["opened"]:
                    ok, _frame = cap.read()
                    probe["frame_ok"] = bool(ok)
            finally:
                cap.release()
        except Exception as exc:
            probe["error"] = type(exc).__name__
        return probe

    def _test_camera(self, tid: str, title: str) -> GuidedTestResult:
        probe = self._camera_probe()
        extra: Dict[str, object] = {"camera_probe": probe}
        steps = [
            "Put your hand in front of the camera.",
            "Move your hand slowly to the left.",
            "Move your hand slowly to the right.",
        ]
        questions = ["Did the cursor follow your hand? [Y/N]"]
        return self._physical_yn_test(
            tid, title, steps, questions,
            intro="Watch the on-screen cursor while you move your hand.",
            extra=extra)

    def _test_mouse(self, tid: str, title: str) -> GuidedTestResult:
        steps = [
            "Pinch and hold, then move your hand around.",
            "Pinch and release quickly (single click).",
            "Pinch twice quickly (double-click).",
            "Make a fist for half a second (right-click).",
            "Raise two fingers and move your hand up and down (scroll).",
            "Pinch, hold, and drag across the screen.",
        ]
        questions = [
            "Did the cursor follow your hand? [Y/N]",
            "Did the click work? [Y/N]",
            "Did the double-click work? [Y/N]",
            "Did the right-click menu appear? [Y/N]",
            "Did the page scroll? [Y/N]",
            "Did the drag work? [Y/N]",
        ]
        return self._physical_yn_test(tid, title, steps, questions)

    def _test_gaze(self, tid: str, title: str) -> GuidedTestResult:
        steps = [
            "Sit comfortably and look straight at the camera.",
            "Look at target A (top-left corner of the screen).",
            "Look at target B (center of the screen).",
            "Look at target C (bottom-right corner of the screen).",
            "Blink slowly, twice.",
        ]
        questions = [
            "Did the cursor jump to each target as you looked at it? [Y/N]",
            "Did AirMouse detect your blink? [Y/N]",
        ]
        return self._physical_yn_test(tid, title, steps, questions)

    # -- voice (PHYSICAL, typed-heard path) -----------------------------------

    _VOICE_PHRASES: Tuple[Tuple[str, str], ...] = (
        ("click", "click"),
        ("double click", "double_click"),
        ("scroll down", "scroll_down"),
        ("open notepad", "open_app"),
        ("copy", "copy_text"),
        ("paste", "paste_text"),
        ("undo", "undo"),
        ("close window", "close_window"),
    )
    _VOICE_QUESTION = ("Did AirMouse hear and interpret all 8 commands "
                       "correctly? [Y/N]")

    def _voice_instructions(self) -> str:
        phrase_list = ", ".join('"{}"'.format(p) for p, _ in
                                self._VOICE_PHRASES)
        return ("\n".join([
            "ACTION REQUIRED — a human must speak these; hardware tests can "
            "NEVER auto-pass.",
            "1. Say each of these 8 phrases out loud, one at a time:",
            "   " + phrase_list,
            "2. After each phrase, check what AirMouse shows as heard and how "
            "it interpreted it.",
            "3. " + self._VOICE_QUESTION,
            "Run `airmouse test --guided` to do this interactively.",
        ]))

    def _test_voice(self, tid: str, title: str) -> GuidedTestResult:
        if not self.interactive:
            return GuidedTestResult(
                tid, title, TestMode.PHYSICAL, TestStatus.ACTION_REQUIRED,
                detail=self._voice_instructions(),
                measurements={
                    "reason": "non-interactive run: no human to confirm",
                    "phrases": [p for p, _ in self._VOICE_PHRASES],
                })
        try:
            from .voice_commands import match_command_grammar
        except ImportError as exc:
            return self._skip(tid, title,
                              "voice grammar subsystem unavailable: "
                              + repr(exc))
        except Exception as exc:
            return self._skip(tid, title,
                              "voice grammar failed to import: " + repr(exc))

        measurements: Dict[str, object] = {
            "input": "user typed what was heard",
            "phrases": [],
        }
        rows: List[Dict[str, object]] = []
        for phrase, expected in self._VOICE_PHRASES:
            self._print('  Say: "{}"'.format(phrase))
            heard = self._ask_line(
                '  What did AirMouse hear? (Enter = exactly "{}"): '
                .format(phrase))
            if heard is None:
                measurements["reason"] = \
                    "input ended (EOF/OSError) before confirmation"
                detail = ("Input ended before the voice test could be "
                          "confirmed — recorded as ACTION REQUIRED, never a "
                          "PASS.\n" + self._voice_instructions())
                return GuidedTestResult(tid, title, TestMode.PHYSICAL,
                                        TestStatus.ACTION_REQUIRED,
                                        detail=detail,
                                        measurements=measurements)
            typed = heard.strip()
            if not typed:
                typed = phrase
            match = match_command_grammar(typed)
            matched = bool(match.is_command and match.name == expected)
            self._print("    heard: " + typed)
            self._print("    interpreted: " + (match.name or "(no command)"))
            self._print("    intent: " + str(match.intent.value))
            self._print("    confidence: {:.2f}".format(match.confidence))
            rows.append({
                "say": phrase,
                "heard": typed,
                "interpreted": match.name,
                "intent": str(match.intent.value),
                "confidence": round(float(match.confidence), 4),
                "matched": matched,
            })
        measurements["phrases"] = rows
        matched_all = all(r["matched"] for r in rows)

        answer = self._ask_yes_no(self._VOICE_QUESTION)
        if answer is None:
            measurements["reason"] = \
                "input ended (EOF/OSError) before confirmation"
            detail = ("Input ended before the voice test could be confirmed — "
                      "recorded as ACTION REQUIRED, never a PASS.\n"
                      + self._voice_instructions())
            return GuidedTestResult(tid, title, TestMode.PHYSICAL,
                                    TestStatus.ACTION_REQUIRED,
                                    detail=detail, measurements=measurements)
        measurements["answer"] = "Y" if answer else "N"

        if answer and matched_all:
            return GuidedTestResult(
                tid, title, TestMode.PHYSICAL, TestStatus.PASS,
                detail=("User confirmed with Y; all 8 heard phrases resolved "
                        "to the expected commands (deterministic grammar on "
                        "the user-typed heard text — mic path not verified "
                        "here)."),
                measurements=measurements)

        note = self._ask_line("What went wrong? (your own words — press "
                              "Enter to skip): ")
        bad = [str(r["say"]) for r in rows if not r["matched"]]
        detail = "User answered N — recorded as FAIL with their words."
        if bad:
            detail += "\nNot interpreted as expected: " + ", ".join(bad)
        if note:
            detail += '\nUser note: "' + note + '"'
        measurements["user_note"] = note or ""
        return GuidedTestResult(tid, title, TestMode.PHYSICAL,
                                TestStatus.FAIL, detail=detail,
                                measurements=measurements)

    # -- dictation (PHYSICAL, typed input allowed) -----------------------------

    _DICTATION_QUESTION = "Did the dictated text come out right? [Y/N]"

    def _dictation_instructions(self) -> str:
        return "\n".join([
            "ACTION REQUIRED — a human must dictate; hardware tests can "
            "NEVER auto-pass.",
            "1. Dictate one short sentence (type it here if no microphone is "
            "wired up) and press Enter.",
            "2. Try one voice edit command: scratch that / undo / redo / "
            "replace that with <words> / new line / capitalize that.",
            "3. Check raw → normalized → final text, then answer:",
            "   " + self._DICTATION_QUESTION,
            "Run `airmouse test --guided` to do this interactively.",
        ])

    def _test_dictation(self, tid: str, title: str) -> GuidedTestResult:
        if not self.interactive:
            return GuidedTestResult(
                tid, title, TestMode.PHYSICAL, TestStatus.ACTION_REQUIRED,
                detail=self._dictation_instructions(),
                measurements={
                    "reason": "non-interactive run: no human to confirm",
                })
        try:
            from .dictation_text import VoiceTypingEngine
        except ImportError as exc:
            return self._skip(tid, title,
                              "dictation subsystem unavailable: " + repr(exc))
        except Exception as exc:
            return self._skip(tid, title,
                              "dictation failed to import: " + repr(exc))

        measurements: Dict[str, object] = {}
        self._print("  Dictation test — we will turn one sentence into text.")
        raw = self._ask_line("  Dictate one short sentence (or press Enter "
                             "for a sample): ")
        if raw is None:
            measurements["reason"] = \
                "input ended (EOF/OSError) before confirmation"
            detail = ("Input ended before the dictation test could be "
                      "confirmed — recorded as ACTION REQUIRED, never a "
                      "PASS.\n" + self._dictation_instructions())
            return GuidedTestResult(tid, title, TestMode.PHYSICAL,
                                    TestStatus.ACTION_REQUIRED,
                                    detail=detail, measurements=measurements)
        input_note = "user typed"
        if not raw.strip():
            raw = "hello world this is airmouse"
            input_note = "sample sentence (nothing typed)"
        measurements["input"] = input_note
        measurements["raw"] = raw

        try:
            engine = VoiceTypingEngine()
            ops = list(engine.ingest(raw))
            normalized = engine.text
            edit = self._ask_line(
                "  Edit command (scratch that / undo / redo / replace that "
                "with X / new line / capitalize that; Enter to skip): ")
            if edit is None:
                measurements["reason"] = \
                    "input ended (EOF/OSError) before confirmation"
                detail = ("Input ended before the dictation test could be "
                          "confirmed — recorded as ACTION REQUIRED, never a "
                          "PASS.\n" + self._dictation_instructions())
                return GuidedTestResult(tid, title, TestMode.PHYSICAL,
                                        TestStatus.ACTION_REQUIRED,
                                        detail=detail,
                                        measurements=measurements)
            edit_cmd = edit.strip()
            if edit_cmd:
                ops += list(engine.ingest(edit_cmd))
            final = engine.text
        except Exception as exc:
            return GuidedTestResult(tid, title, TestMode.PHYSICAL,
                                    TestStatus.FAIL,
                                    detail="Dictation engine raised: "
                                           + repr(exc),
                                    measurements=measurements)

        measurements["normalized"] = normalized
        measurements["edit_command"] = edit_cmd
        measurements["final"] = final
        measurements["ops"] = [str(o.op) for o in ops]

        text_calls: Optional[int] = None
        try:
            from .text_control import TextAction, TextController, TextOp
            from .text_control import TextExecutor
            executor = TextExecutor()
            controller = TextController(executor=executor)
            controller.execute(TextAction(op=TextOp.TYPE, text=final))
            text_calls = len(executor.calls)
        except Exception:
            text_calls = None
        measurements["text_control_typed_calls"] = \
            text_calls if text_calls is not None else "unavailable"

        self._print("  raw:        " + raw)
        self._print("  normalized: " + normalized)
        self._print("  final:      " + final)

        answer = self._ask_yes_no(self._DICTATION_QUESTION)
        if answer is None:
            measurements["reason"] = \
                "input ended (EOF/OSError) before confirmation"
            detail = ("Input ended before the dictation test could be "
                      "confirmed — recorded as ACTION REQUIRED, never a "
                      "PASS.\n" + self._dictation_instructions())
            return GuidedTestResult(tid, title, TestMode.PHYSICAL,
                                    TestStatus.ACTION_REQUIRED,
                                    detail=detail, measurements=measurements)
        measurements["answer"] = "Y" if answer else "N"
        if answer:
            return GuidedTestResult(tid, title, TestMode.PHYSICAL,
                                    TestStatus.PASS,
                                    detail="User confirmed the dictated text "
                                           "and edits with Y (interactive).",
                                    measurements=measurements)
        note = self._ask_line("What went wrong? (your own words — press "
                              "Enter to skip): ")
        detail = 'User answered N — recorded as FAIL with their words.'
        if note:
            detail += '\nUser note: "' + note + '"'
        measurements["user_note"] = note or ""
        return GuidedTestResult(tid, title, TestMode.PHYSICAL,
                                TestStatus.FAIL, detail=detail,
                                measurements=measurements)

    # ════════════════════════════════════════════════════════════════════
    # SIMULATION tests (deterministic, headless, labelled [SIMULATION])
    # ════════════════════════════════════════════════════════════════════

    def _test_intelligence(self, tid: str, title: str) -> GuidedTestResult:
        try:
            from .intelligence.model import PersonalInteractionModel
            from .intelligence.prediction import Predictor
            from .intelligence.twin.twin import PersonalInteractionTwin
            from .interfaces import Intent, IntentType, Modality
            from .safety import SafetySystem
        except ImportError as exc:
            return self._skip(tid, title,
                              "intelligence subsystem unavailable: "
                              + repr(exc))
        except Exception as exc:
            return self._skip(tid, title,
                              "intelligence failed to import: " + repr(exc))

        lines: List[str] = []
        measurements: Dict[str, object] = {}
        try:
            # OBSERVED — learn a repeated interaction via the Twin.
            twin = PersonalInteractionTwin()
            fact = None
            for _ in range(3):
                fact = twin.learn("modality_preference", "click", "hand",
                                  source="system", confidence=0.8)
            if fact is None:
                return self._skip(tid, title,
                                  "twin rejected the learning input "
                                  "(fail-closed)")
            lines.append("OBSERVED: modality_preference click -> {} "
                         "(frequency {}, confidence {:.2f})"
                         .format(fact.value, fact.frequency, fact.confidence))
            measurements["observed"] = {
                "category": fact.category, "key": "click",
                "value": fact.value, "frequency": fact.frequency,
                "confidence": fact.confidence,
            }

            # PREDICTED — data only; never executed without approval (§14).
            model = PersonalInteractionModel()
            for _ in range(6):
                model.learn_action_step(["open_app"], "click")
            pred = Predictor(model=model).predict_next_action(["open_app"])
            if pred is None or not getattr(pred, "value", ""):
                return self._skip(tid, title,
                                  "prediction API returned no prediction")
            lines.append("PREDICTED: next action after open_app -> {} "
                         "(confidence {:.2f}) — prediction is DATA, NOT "
                         "executed unless approved"
                         .format(pred.value, pred.confidence))
            measurements["predicted"] = {"value": pred.value,
                                         "confidence": pred.confidence}

            # EXECUTED — only AFTER the safety gate approves (§14: SAFETY
            # sits ABOVE PREDICTION; a prediction alone can never act).
            safety = SafetySystem()
            intent = Intent(type=IntentType.CLICK, point=(100.0, 100.0),
                            confidence=0.9, sources=Modality.NONE,
                            timestamp=1.0)
            decision = safety.approve_intent(intent, now=1.0)
            measurements["safety_gate"] = {"allowed": bool(decision.allowed),
                                           "reason": decision.reason}
            if not decision.allowed:
                lines.append("EXECUTED: NOT executed — the safety gate "
                             "blocked the predicted action (reason: {})"
                             .format(decision.reason))
                measurements["executed"] = False
                return GuidedTestResult(tid, title, TestMode.SIMULATION,
                                        TestStatus.FAIL,
                                        detail="\n".join(lines),
                                        measurements=measurements)

            calls: List[Tuple[float, float]] = []

            class _SimExecutor:
                def click(self, x, y):
                    calls.append((float(x), float(y)))
                    return {"pointer": (x, y)}

                def move(self, x, y):
                    return {"pointer": (x, y)}

            point = intent.target_point or (0.0, 0.0)
            _SimExecutor().click(point[0], point[1])
            lines.append("EXECUTED: click at ({:.0f}, {:.0f}) on the "
                         "SIMULATED executor (safety gate: {}; level: {})"
                         .format(point[0], point[1], decision.reason,
                                 decision.level.value))
            measurements["executed"] = bool(calls)
            measurements["labels"] = ["OBSERVED", "PREDICTED", "EXECUTED"]
            return GuidedTestResult(tid, title, TestMode.SIMULATION,
                                    TestStatus.PASS, detail="\n".join(lines),
                                    measurements=measurements)
        except Exception as exc:
            return GuidedTestResult(tid, title, TestMode.SIMULATION,
                                    TestStatus.FAIL,
                                    detail="\n".join(lines) +
                                    "\nintelligence flow raised: " + repr(exc),
                                    measurements=measurements)

    def _test_browser(self, tid: str, title: str) -> GuidedTestResult:
        try:
            from .browser import (BrowserController, BrowserResolution,
                                  SemanticBrowserResolver,
                                  SimulatedBrowserBridge)
        except ImportError as exc:
            return self._skip(tid, title,
                              "browser subsystem unavailable: " + repr(exc))
        except Exception as exc:
            return self._skip(tid, title,
                              "browser failed to import: " + repr(exc))

        steps: List[Dict[str, object]] = []

        def step(name: str, ok: bool, why: str = "") -> bool:
            steps.append({"step": name, "ok": bool(ok),
                          "detail": str(why)[:120]})
            self._print("  {}: {}{}".format(
                name, "ok" if ok else "FAILED", (" — " + why) if why else ""))
            return bool(ok)

        measurements: Dict[str, object] = {"steps": steps}
        try:
            sim = SimulatedBrowserBridge()
            ctrl = BrowserController(
                config={"enabled": True, "verify_actions": True,
                        "poll_interval": 0.0},
                bridge=sim)
            resolver = SemanticBrowserResolver(ctrl.mapper)
            now = 100.0

            ctrl.start()
            state = ctrl.poll(now=now)
            ok_open = step("open_local_page",
                           state is not None and len(state.elements) > 0,
                           "{} elements on the built-in demo page"
                           .format(len(state.elements) if state else 0))
            now += 1.0

            res = resolver.resolve("click the login button", now=now)
            out = ctrl.execute(res, now=now + 0.1)
            ok_click = step(
                "find_and_click_button",
                out.get("status") == "executed" and
                out.get("verification", {}).get("status") == "passed",
                "login button clicked, verified: {}".format(
                    out.get("verification", {}).get("status")))
            now += 2.0

            out = ctrl.execute(resolver.resolve("open a new tab", now=now),
                               now=now + 0.1)
            state = ctrl.poll(now=now + 0.2)
            ok_tab = step("new_tab",
                          out.get("status") == "executed" and
                          state is not None and len(state.tabs) == 3,
                          "{} tabs open".format(
                              len(state.tabs) if state else 0))
            now += 2.0

            out = ctrl.execute(resolver.resolve("switch to demo portal",
                                                now=now), now=now + 0.1)
            state = ctrl.poll(now=now + 0.2)
            ok_switch = step("switch_back_to_test_page",
                             out.get("status") == "executed" and
                             state is not None and
                             state.url.endswith("demo.airmouse.local/home"),
                             str(state.url) if state else "no state")
            now += 2.0

            nav = BrowserResolution(matched=True, action="navigate",
                                    params={"url": "about:blank"},
                                    confidence=1.0)
            out = ctrl.execute(nav, now=now + 0.1)
            state = ctrl.poll(now=now + 0.2)
            ok_nav = step("navigate_about_blank",
                          out.get("status") == "executed" and
                          state is not None and state.url == "about:blank",
                          str(state.url) if state else "no state")
            now += 2.0

            out = ctrl.execute(resolver.resolve("go back", now=now),
                               now=now + 0.1)
            state = ctrl.poll(now=now + 0.2)
            ok_back = step("go_back_to_test_page",
                           out.get("status") == "executed" and
                           state is not None and
                           state.url.endswith("demo.airmouse.local/home"),
                           str(state.url) if state else "no state")

            ok_evidence = step("click_evidence",
                               "btn-login" in list(sim.clicked_elements),
                               str(sim.clicked_elements))

            all_ok = all([ok_open, ok_click, ok_tab, ok_switch, ok_nav,
                          ok_back, ok_evidence])
            measurements["all_steps_ok"] = all_ok
            return GuidedTestResult(tid, title, TestMode.SIMULATION,
                                    TestStatus.PASS if all_ok
                                    else TestStatus.FAIL,
                                    detail=("Simulated browser flow: open → "
                                            "click → verify → new tab → "
                                            "navigate → back ({} steps, {} "
                                            "ok)".format(
                                                len(steps),
                                                sum(1 for s in steps
                                                    if s["ok"]))
                                            ) if all_ok else
                                            ("Browser flow step failed: " +
                                             "; ".join(
                                                 str(s["step"]) for s in steps
                                                 if not s["ok"])),
                                    measurements=measurements)
        except Exception as exc:
            return GuidedTestResult(tid, title, TestMode.SIMULATION,
                                    TestStatus.FAIL,
                                    detail="browser flow raised: " + repr(exc),
                                    measurements=measurements)

    def _test_agent(self, tid: str, title: str) -> GuidedTestResult:
        try:
            from .agents import AgentRegistry
            from .aip import parse_message
            from .interfaces import Intent, IntentType
            from .permissions import (AgentPermissionEngine, Decision)
            from .safety import SafetySystem
            from .simulator import Simulator
        except ImportError as exc:
            return self._skip(tid, title,
                              "agent subsystem unavailable: " + repr(exc))
        except Exception as exc:
            return self._skip(tid, title,
                              "agent failed to import: " + repr(exc))

        lines: List[str] = []
        measurements: Dict[str, object] = {}
        try:
            engine = AgentPermissionEngine()
            engine.grant("helper", "mouse.click", Decision.ALLOW,
                         granted_by="human", reason="guided test grant")
            registry = AgentRegistry(permission_engine=engine)
            registry.register("helper", "Helper", capabilities=("click",))
            registry.register("wrecker", "Wrecker")

            # 1. REQUEST (AIP envelope, strictly parsed, fail-closed)
            request = {"aip_version": "1.0", "type": "request",
                       "id": "guided-req-0001", "agent_id": "helper",
                       "ts": 1.0, "payload": {"action": "click"}}
            msg, errs = parse_message(json.dumps(request))
            ok_request = msg is not None and not errs
            lines.append("REQUEST: helper asks to click (AIP request "
                         "parsed: {})".format(bool(ok_request)))

            # 2. PERMISSION (§15 granular rule)
            decision = engine.check("helper", "mouse.click")
            ok_perm = bool(decision.allowed)
            lines.append("PERMISSION: mouse.click -> {} (level {})"
                         .format(decision.decision.value,
                                 decision.level.name.lower()))

            # 3. SAFETY (§8 gate above the agent)
            safety = SafetySystem()
            intent = Intent(type=IntentType.CLICK, point=(60.0, 60.0),
                            confidence=0.9, timestamp=1.0)
            sd = safety.approve_intent(intent, now=1.0)
            ok_safety = bool(sd.allowed)
            lines.append("SAFETY: click intent {} (reason: {}, level: {})"
                         .format("approved" if sd.allowed else "blocked",
                                 sd.reason, sd.level.value))

            # 4. AGENT (lease + §12 authorize gate)
            lease = registry.acquire("helper", "mouse",
                                     task_id="guided-req-0001")
            auth, why = registry.authorize_action("helper", "mouse",
                                                  permission_key="mouse.click")
            ok_agent = lease is not None and bool(auth)
            lines.append("AGENT: lease mouse -> helper ({}); "
                         "authorize -> {}".format(
                             lease.lease_id if lease else "denied", why))

            # 5. ACTION on the simulated desktop
            sim = Simulator()
            sim.add_window("Compose", app="mail", buttons=["Send"],
                           text="body")
            sim.focus_window("Compose")
            clicked = bool(sim.click_button("Send"))
            lines.append("ACTION: click Send on the simulated Compose "
                         "window -> {}".format(clicked))

            # 6. VERIFICATION (evidence, not optimism)
            verified, vmsg = sim.verify({"button_clicked": "Send"})
            lines.append("VERIFICATION: {} ({})".format(bool(verified), vmsg))

            # 7. RESULT envelope
            ok_result = clicked and bool(verified)
            lines.append("RESULT: ok={}".format(ok_result))

            # second request: DESTRUCTIVE — must be REJECTED (fail-closed)
            d_dec = engine.check("wrecker", "destructive.action")
            d_auth, d_why = registry.authorize_action(
                "wrecker", "files", permission_key="destructive.action")
            rejected = (not d_dec.allowed) and (not d_auth)
            lines.append("REJECTED: wrecker's destructive request denied — "
                         "permission {} ({}); agent gate: {}"
                         .format(d_dec.decision.value, d_dec.reason, d_why))

            # hierarchy proof: SAFETY outranks PERMISSION (§14)
            engine.safety_block("file.write")
            s_dec = engine.check("wrecker", "file.write")
            hierarchy_ok = (not s_dec.allowed) and \
                s_dec.level.name.lower() == "safety_policy"
            lines.append("HIERARCHY: safety_block(file.write) -> deny at "
                         "level {} (SAFETY above PERMISSION)"
                         .format(s_dec.level.name.lower()))

            all_ok = all([ok_request, ok_perm, ok_safety, ok_agent,
                          ok_result, rejected, hierarchy_ok])
            measurements.update({
                "benign_ok": all([ok_request, ok_perm, ok_safety, ok_agent,
                                  ok_result]),
                "destructive_rejected": bool(rejected),
                "destructive_decision": d_dec.decision.value,
                "destructive_reason": d_dec.reason,
                "agent_gate_reason": d_why,
                "hierarchy_demonstrated": ["safety_policy", "permission",
                                           "agent"],
                "safety_level_after_block": s_dec.level.name.lower(),
                "conflicts": registry.conflicts(),
            })
            detail = "\n".join(lines)
            if all_ok:
                detail = ("Benign agent request flowed REQUEST → PERMISSION "
                          "→ SAFETY → AGENT → ACTION → VERIFICATION → "
                          "RESULT; the destructive request was REJECTED.\n"
                          + detail)
            else:
                detail = "Agent flow gate failed.\n" + detail
            return GuidedTestResult(tid, title, TestMode.SIMULATION,
                                    TestStatus.PASS if all_ok
                                    else TestStatus.FAIL,
                                    detail=detail, measurements=measurements)
        except Exception as exc:
            return GuidedTestResult(tid, title, TestMode.SIMULATION,
                                    TestStatus.FAIL,
                                    detail="\n".join(lines) +
                                    "\nagent flow raised: " + repr(exc),
                                    measurements=measurements)

    def _test_multi_agent(self, tid: str, title: str) -> GuidedTestResult:
        try:
            from .agents import AgentRegistry
            from .interfaces import Intent, IntentType
            from .permissions import AgentPermissionEngine, ControlLevel
            from .safety import SafetySystem
        except ImportError as exc:
            return self._skip(tid, title,
                              "multi-agent subsystem unavailable: "
                              + repr(exc))
        except Exception as exc:
            return self._skip(tid, title,
                              "multi-agent failed to import: " + repr(exc))

        lines: List[str] = []
        measurements: Dict[str, object] = {}
        try:
            engine = AgentPermissionEngine()
            registry = AgentRegistry(permission_engine=engine)
            registry.register("agentA", "Agent A", priority=3)
            registry.register("agentB", "Agent B", priority=5)

            lease = registry.acquire("agentA", "mouse", task_id="guided-ma-1")
            ok_lease = lease is not None
            lines.append("LEASE: agentA holds mouse ({})"
                         .format(lease.lease_id if lease else "denied"))

            lease_b = registry.acquire("agentB", "mouse",
                                       task_id="guided-ma-2")
            conflicts = registry.conflicts()
            ok_conflict = lease_b is None and len(conflicts) >= 1
            resolution = conflicts[-1]["resolution"] if conflicts else "none"
            lines.append("CONFLICT: agentB challenged mouse -> denied "
                         "({} recorded; resolution: {})"
                         .format(len(conflicts), resolution))

            handed = registry.handoff("agentA", "agentB", "mouse",
                                      task_id="guided-ma-2")
            holder = registry.holder("mouse")
            ok_resolution = bool(handed) and holder == "agentB"
            lines.append("RESOLUTION: handoff mouse agentA -> agentB "
                         "(holder now: {})".format(holder))

            stopped = registry.emergency_stop_all()
            post_ok, post_why = registry.authorize_action("agentB", "mouse")
            level = engine.active_level()
            ok_estop = (stopped == 2 and not post_ok and
                        level is ControlLevel.EMERGENCY_STOP)
            lines.append("E-STOP: emergency_stop_all -> {} agents stopped; "
                         "permission level {}; post-stop authorize -> {}"
                         .format(stopped, level.name.lower(), post_why))

            safety = SafetySystem()
            safety.trip("guided multi-agent test")
            sd = safety.approve_intent(
                Intent(type=IntentType.CLICK, point=(1.0, 1.0),
                       confidence=0.9, timestamp=1.0), now=1.0)
            ok_safety_estop = (not sd.allowed) and \
                sd.reason == "emergency_stop"
            lines.append("E-STOP (SAFETY): the safety gate blocks clicks "
                         "after trip (reason: {}) — EMERGENCY STOP outranks "
                         "everything".format(sd.reason))

            all_ok = all([ok_lease, ok_conflict, ok_resolution, ok_estop,
                          ok_safety_estop])
            measurements.update({
                "lease_granted": bool(ok_lease),
                "lease_id": lease.lease_id if lease else "",
                "conflict_detected": bool(ok_conflict),
                "conflict_records": conflicts[:2],
                "resolution": "handoff",
                "holder_after_handoff": holder or "",
                "estop_agents_stopped": stopped,
                "estop_level": level.name.lower(),
                "post_estop_authorized": bool(post_ok),
                "safety_gate_blocked": bool(ok_safety_estop),
            })
            detail = ("Two agents, one mouse: lease granted, conflict "
                      "detected and resolved by handoff, then the emergency "
                      "stop stopped every agent and every gate.\n"
                      + "\n".join(lines)) if all_ok else \
                ("Multi-agent flow assertion failed.\n" + "\n".join(lines))
            return GuidedTestResult(tid, title, TestMode.SIMULATION,
                                    TestStatus.PASS if all_ok
                                    else TestStatus.FAIL,
                                    detail=detail, measurements=measurements)
        except Exception as exc:
            return GuidedTestResult(tid, title, TestMode.SIMULATION,
                                    TestStatus.FAIL,
                                    detail="\n".join(lines) +
                                    "\nmulti-agent flow raised: " + repr(exc),
                                    measurements=measurements)

    def _test_recovery(self, tid: str, title: str) -> GuidedTestResult:
        try:
            from .failure_injection import run_failure_scenario
            from .simulator import Simulator
        except ImportError as exc:
            return self._skip(tid, title,
                              "failure-injection subsystem unavailable: "
                              + repr(exc))
        except Exception as exc:
            return self._skip(tid, title,
                              "failure-injection failed to import: "
                              + repr(exc))

        lines: List[str] = []
        measurements: Dict[str, object] = {}
        plan = (
            # (class, initial diagnosis, expect full recovery?)
            ("missing_target", "target_missing", True),
            ("timeout", "timeout", True),
            ("app_crash", "app_crash", True),
            ("permission_denial", "permission_denied", False),
        )
        all_ok = True
        try:
            for name, diagnosis, expect_recovered in plan:
                outcome = run_failure_scenario(name, Simulator(),
                                               human_fix_after_rounds=1,
                                               max_rounds=5)
                lines.append("OBSERVE: {} — first attempt failed: {}"
                             .format(name, outcome.observed))
                lines.append("DIAGNOSE: initial={}, final={}"
                             .format(diagnosis, outcome.diagnosed))
                if expect_recovered:
                    lines.append("RECOVER: {} (rounds {})"
                                 .format("yes" if outcome.recovered else "no",
                                         outcome.rounds))
                    lines.append("VERIFY: {} ({})"
                                 .format("yes" if outcome.verified else "no",
                                         outcome.notes))
                    ok = (outcome.observed and outcome.recovered and
                          outcome.verified and outcome.stopped_safely)
                else:
                    lines.append("RECOVER: safely stopped WITHOUT retry "
                                 "(rounds {}) — permission denial must never "
                                 "be retried (§7)".format(outcome.rounds))
                    lines.append("VERIFY: {} ({})"
                                 .format("yes" if outcome.verified else "no",
                                         outcome.notes))
                    ok = (outcome.observed and not outcome.recovered and
                          not outcome.verified and outcome.stopped_safely and
                          outcome.rounds == 1)
                all_ok = all_ok and ok
                measurements[name] = outcome.to_dict()
            measurements["all_scenarios_ok"] = all_ok
            detail = "§27 failure drill: OBSERVE → DIAGNOSE → RECOVER → " \
                     "VERIFY for every injected class.\n" + "\n".join(lines)
            return GuidedTestResult(tid, title, TestMode.SIMULATION,
                                    TestStatus.PASS if all_ok
                                    else TestStatus.FAIL,
                                    detail=detail, measurements=measurements)
        except Exception as exc:
            return GuidedTestResult(tid, title, TestMode.SIMULATION,
                                    TestStatus.FAIL,
                                    detail="\n".join(lines) +
                                    "\nrecovery flow raised: " + repr(exc),
                                    measurements=measurements)

    def _test_offline(self, tid: str, title: str) -> GuidedTestResult:
        try:
            from .offline import OfflineGate, run_offline_selftest
        except ImportError as exc:
            return self._skip(tid, title,
                              "offline subsystem unavailable: " + repr(exc))
        except Exception as exc:
            return self._skip(tid, title,
                              "offline failed to import: " + repr(exc))

        try:
            report = run_offline_selftest()
            passed = sum(1 for c in report.checks if c["passed"])
            failed_names = [c["name"] for c in report.checks
                            if not c["passed"]]
            lines = ["offline selftest: {} ({}/{} checks passed)"
                     .format("OK" if report.ok else "FAILED", passed,
                             len(report.checks))]
            for c in report.checks:
                lines.append("  {}  {} — {}".format(
                    "PASS" if c["passed"] else "FAIL",
                    c["name"], str(c["detail"])[:80]))

            # tiny no-network assertion beside the full selftest
            gate = OfflineGate(engaged=True)
            gate_ok = (not gate.check("cloud_asr")) and \
                gate.check("local_grammar")
            lines.append("NO-NETWORK: OfflineGate(engaged) blocks cloud_asr="
                         "True while the local grammar stays allowed={}"
                         .format(gate.check("local_grammar")))

            all_ok = bool(report.ok) and gate_ok
            measurements = {
                "offline_report_ok": bool(report.ok),
                "checks_passed": passed,
                "checks_total": len(report.checks),
                "failed_checks": failed_names,
                "offline_gate_ok": bool(gate_ok),
            }
            detail = "\n".join(lines)
            return GuidedTestResult(tid, title, TestMode.SIMULATION,
                                    TestStatus.PASS if all_ok
                                    else TestStatus.FAIL,
                                    detail=detail, measurements=measurements)
        except Exception as exc:
            return GuidedTestResult(tid, title, TestMode.SIMULATION,
                                    TestStatus.FAIL,
                                    detail="offline flow raised: " + repr(exc),
                                    measurements={})


_NARRATION: Dict[str, str] = {
    "installation": "Checking the installation: version, CLI entry point "
                    "and core dependencies.",
    "intelligence": "Teaching AirMouse a repeated habit, watching it predict "
                    "your next action, and executing the prediction behind "
                    "the safety gate — in simulation.",
    "browser": "Driving the built-in simulated browser: open the test page, "
               "find a button, click, verify, new tab, navigate, back.",
    "agent": "A simulated agent asks permission to click — every gate "
             "reviews it.  A second agent asks for something destructive "
             "and must be REJECTED.",
    "multi_agent": "Two agents, one mouse: lease, conflict, handoff — then "
                   "the emergency stop ends everything.",
    "recovery": "Injecting failures (missing target, timeout, crash, "
                "permission denial) and watching OBSERVE → DIAGNOSE → "
                "RECOVER → VERIFY.",
    "offline": "Running the full offline self-test with networking truly "
               "blocked.",
}


# ---------------------------------------------------------------------------
# module-level convenience API (pinned for CLI wiring)
# ---------------------------------------------------------------------------


def run_guided(interactive: bool = True, auto_simulate: bool = False,
               only: Optional[List[str]] = None, out: IO = sys.stdout,
               input_fn: Callable[[str], str] = input) -> GuidedTestReport:
    """Run the guided test laboratory (``airmouse test --guided``)."""
    return GuidedTestRunner(out=out, input_fn=input_fn).run(
        interactive=interactive, auto_simulate=auto_simulate, only=only)


def format_report(report: GuidedTestReport, version: str = "15") -> str:
    """Render a :class:`GuidedTestReport` in the fixed report shape."""
    return report.format_report(version=version)
