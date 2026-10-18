"""
airmouse.doctor — `airmouse doctor` (v15.1 hardening).

Reorganizes :mod:`airmouse.capabilities` detection into the twelve
doctor sections and renders the human report:

    AirMouse Doctor
    ===========================
    <sections...>

    READY:       NN
    OPTIONAL:    NN
    HARDWARE:    NN
    WARNING:     NN
    FAILED:      NN

    Overall:
    [READY FOR TESTING]

Every WARNING/FAILED (in fact every non-READY, non-OPTIONAL) item is
followed by its exact ``Fix:`` remediation line.  Fail-closed: the
report is honest about what could not be verified.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .capabilities import (
    Component,
    ComponentState,
    detect_all,
)

__all__ = [
    "DoctorSection", "DoctorReport", "run_doctor", "format_doctor_report",
    "run_doctor_json", "format_doctor_json", "SECTION_NAMES",
]


#: The EXACT section order required by the doctor contract.
SECTION_NAMES: tuple = (
    "System", "Python", "AirMouse", "Camera", "Microphone", "Speech",
    "Input", "Browser", "Intelligence", "Agent", "Offline", "Safety",
)

_CATEGORY_TO_SECTION = {
    "SYSTEM": "System",
    "PYTHON": "Python",
    "AIRMOUSE": "AirMouse",
    "CAMERA": "Camera",
    "MICROPHONE": "Microphone",
    "SPEECH": "Speech",
    "INPUT": "Input",
    "BROWSER": "Browser",
    "INTELLIGENCE": "Intelligence",
    "AGENT": "Agent",
    "OFFLINE": "Offline",
    "SAFETY": "Safety",
}

#: doctor summary buckets (exact key order of the human report)
_SUMMARY_LABELS: tuple = ("READY", "OPTIONAL", "HARDWARE", "WARNING",
                          "FAILED")

#: ComponentState -> doctor summary bucket
_STATE_TO_BUCKET: Dict[str, str] = {
    "READY": "READY",
    "OPTIONAL": "OPTIONAL",
    "NOT_INSTALLED": "OPTIONAL",
    "ENHANCEMENT": "OPTIONAL",
    "UNAVAILABLE": "OPTIONAL",
    "HARDWARE": "HARDWARE",
    "WARNING": "WARNING",
    "FAILED": "FAILED",
    "INCOMPATIBLE": "FAILED",
}


@dataclass
class DoctorSection:
    """One named doctor section with its component rows."""

    name: str
    components: List[Component] = field(default_factory=list)


@dataclass
class DoctorReport:
    """Sectioned capability report (System → Safety, exact order)."""

    sections: List[DoctorSection] = field(default_factory=list)

    #: v16.5.2 §16 — the ACTIVE ASR provider name (or the built-in
    #: grammar label).  Set by :func:`run_doctor` from THE authoritative
    #: registry — the same selection the transcribe runtime uses, so
    #: ``doctor.active_asr_provider == transcribe.selected_provider``
    #: holds by construction (the release invariant).
    active_asr_provider: str = ""

    # -- aggregation ---------------------------------------------------------

    def all_components(self) -> List[Component]:
        out: List[Component] = []
        for s in self.sections:
            out.extend(s.components)
        return out

    def summary_counts(self) -> Dict[str, int]:
        """``{"READY": n, "OPTIONAL": n, "HARDWARE": n, "WARNING": n,
        "FAILED": n}`` with the contract's bucket mapping."""
        counts: Dict[str, int] = {label: 0 for label in _SUMMARY_LABELS}
        for c in self.all_components():
            bucket = _STATE_TO_BUCKET.get(
                ComponentState(c.state).value, "WARNING")
            counts[bucket] = counts.get(bucket, 0) + 1
        return counts

    def overall(self) -> str:
        """Bracketed verdict line: READY FOR TESTING / PARTIAL / BLOCKED."""
        required = {"SYSTEM", "PYTHON", "AIRMOUSE", "SAFETY"}
        blocking = (ComponentState.FAILED, ComponentState.INCOMPATIBLE)
        comps = self.all_components()
        for c in comps:
            if ComponentState(c.state) in blocking and \
                    c.category in required:
                return "[BLOCKED]"
        has_failed = any(ComponentState(c.state) in blocking for c in comps)
        has_warning = any(ComponentState(c.state) is ComponentState.WARNING
                          for c in comps)
        if has_failed:
            return "[PARTIAL — fix FAILED items]"
        if has_warning:
            return "[PARTIAL — review WARNING items]"
        return "[READY FOR TESTING]"

    def remediations(self) -> List[str]:
        """One exact instruction per non-READY, non-OPTIONAL item."""
        out: List[str] = []
        for c in self.all_components():
            state = ComponentState(c.state)
            if state in (ComponentState.READY, ComponentState.OPTIONAL):
                continue
            fix = c.remediation or c.detail
            out.append(fix if fix else
                       f"review '{c.name}' in airmouse doctor output")
        return out

    # -- machine report (v19.0.3 R3d: honest stable ``--json``) --------------

    def to_machine(self) -> Dict[str, Any]:
        """The structured ``--json`` payload — the SAME truth as the text
        panel, never nicer.

        Shape (stable key order):

            version               package version string
            kind                  "airmouse.doctor"
            overall               "READY" | "PARTIAL" | "BLOCKED"
                                  (machine form of the bracketed verdict;
                                  PARTIAL/BLOCKED are reported as detected,
                                  never upgraded)
            verdict               the exact bracketed panel line
            counts                {READY, OPTIONAL, HARDWARE, WARNING,
                                   FAILED}
            active_asr_provider   THE registry's active ASR provider
            sections              [{name, components: [{name, category,
                                  state, detail, why, remediation,
                                  hardware_state}]}] — contract order
            blocked               required-category FAILED/INCOMPATIBLE
                                  rows with their why/fix reasons
            degraded              WARNING rows plus non-required FAILED
                                  rows (degraded-but-not-blocking)
            remediations          the copy/paste fix lines
        """
        required = {"SYSTEM", "PYTHON", "AIRMOUSE", "SAFETY"}
        blocking = (ComponentState.FAILED, ComponentState.INCOMPATIBLE)
        blocked: List[Dict[str, str]] = []
        degraded: List[Dict[str, str]] = []
        for c in self.all_components():
            state = ComponentState(c.state)
            if state not in blocking and state is not ComponentState.WARNING:
                continue
            row = {
                "name": c.name,
                "category": c.category,
                "state": state.value,
                "why": c.why or c.detail or "",
                "fix": c.remediation or c.detail or "",
            }
            if state in blocking and c.category in required:
                blocked.append(row)
            else:
                degraded.append(row)
        return {
            "version": _doctor_version(),
            "kind": "airmouse.doctor",
            "overall": _overall_machine(self),
            "verdict": self.overall(),
            "counts": self.summary_counts(),
            "active_asr_provider": self.active_asr_provider,
            "sections": [
                {
                    "name": s.name,
                    "components": [
                        {
                            "name": c.name,
                            "category": c.category,
                            "state": ComponentState(c.state).value,
                            "detail": c.detail,
                            "why": c.why,
                            "remediation": c.remediation,
                            "hardware_state": c.hardware_state,
                        }
                        for c in s.components
                    ],
                }
                for s in self.sections
            ],
            "blocked": blocked,
            "degraded": degraded,
            "remediations": self.remediations(),
        }


# ---------------------------------------------------------------------------
# runners
# ---------------------------------------------------------------------------

def _doctor_version() -> str:
    """The package version, read honestly ("" when unavailable)."""
    try:
        from . import __version__ as v
        return str(v)
    except Exception:
        try:
            from airmouse import __version__ as v    # type: ignore
            return str(v)
        except Exception:
            return ""


def _overall_machine(report: "DoctorReport") -> str:
    """Machine form of the bracketed panel verdict — no upgrades."""
    verdict = report.overall()
    if verdict.startswith("[BLOCKED"):
        return "BLOCKED"
    if verdict.startswith("[PARTIAL"):
        return "PARTIAL"
    if verdict.startswith("[READY"):
        return "READY"
    return "UNKNOWN"      # fail-closed: an unknown verdict stays unknown


def run_doctor(verbose: bool = False) -> DoctorReport:
    """Detect everything and section it.  NEVER raises (fail-closed
    underneath — capabilities.detect_all guarantees Component rows)."""
    report = detect_all()
    buckets: Dict[str, List[Component]] = {
        name: [] for name in SECTION_NAMES}
    for comp in report.components:
        section = _CATEGORY_TO_SECTION.get(comp.category)
        if section is None:  # unknown category: park in System, visible
            buckets.setdefault("System", []).append(comp)
        else:
            buckets[section].append(comp)
    sections = [DoctorSection(name=name, components=buckets[name])
                for name in SECTION_NAMES]
    doc = DoctorReport(sections=sections)
    # v16.5.2 §16 — the invariant anchor: from THE registry, the same
    # instance the transcribe runtime selects through
    try:
        from .asr_registry import active_asr_provider
        doc.active_asr_provider = active_asr_provider()
    except Exception:
        doc.active_asr_provider = "Built-in command recognition"
    return doc


def run_doctor_json() -> Dict[str, Any]:
    """Detect everything and return the stable machine report (dict).

    The doctor-owned ``--json`` structure: parseable, deterministic,
    honest (PARTIAL/BLOCKED stay PARTIAL/BLOCKED — a headless sandbox
    reports its real degradation), and carrying the same per-section
    truth as :func:`format_doctor_report`.  Exit codes belong to the
    CLI dispatcher, not to this module.
    """
    return run_doctor().to_machine()


def format_doctor_json(report: Optional[DoctorReport] = None,
                       indent: int = 2) -> str:
    """The ``--json`` document as a JSON string (stable key order)."""
    doc = report.to_machine() if report is not None else run_doctor_json()
    return json.dumps(doc, indent=indent)


def format_doctor_report(report: DoctorReport, verbose: bool = False) -> str:
    """Human output.

    ``verbose=False`` renders EXACTLY the contractual shape (title,
    counts, Overall verdict).  ``verbose=True`` inserts the per-section
    component rows (with ``Fix:`` lines for every non-READY,
    non-OPTIONAL item) between the title and the counts.
    """
    lines: List[str] = ["AirMouse Doctor", "===========================", ""]

    if verbose:
        for section in report.sections:
            lines.append(section.name)
            if not section.components:
                lines.append("  (no components detected)")
            for c in section.components:
                state = ComponentState(c.state).value
                detail = f" — {c.detail}" if c.detail else ""
                # v16.5.1 §15: explicit real-world capability label
                hw = f"  [{c.hardware_state}]" if c.hardware_state else ""
                lines.append(f"  {c.name:<34} {state:<13}{detail}{hw}")
                state_enum = ComponentState(c.state)
                if state_enum not in (ComponentState.READY,
                                      ComponentState.OPTIONAL):
                    # v16.5.1 §4: every WARNING/FAILED explains
                    # WHAT (the row) / WHY / FIX — never a bare
                    # "using defaults".
                    why = c.why or c.detail or \
                        "the check could not complete cleanly"
                    fix = c.remediation or c.detail or \
                        "review this item in airmouse doctor output"
                    lines.append(f"      WHY: {why}")
                    lines.append(f"      FIX: {fix}")
            lines.append("")

    counts = report.summary_counts()
    for label in _SUMMARY_LABELS:
        lines.append(f"{label + ':':<13}{counts.get(label, 0)}")
    lines.append("")
    lines.append("Overall:")
    lines.append(report.overall())
    if verbose:
        fixes = report.remediations()
        if fixes:
            lines.append("")
            lines.append("All fixes (copy/paste):")
            lines.extend(f"  - {f}" for f in fixes)
    return "\n".join(lines)
