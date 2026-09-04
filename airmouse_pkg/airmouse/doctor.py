"""
airmouse.doctor — `airmouse doctor` (v15.1 hardening).

Reorganizes :mod:`airmouse.capabilities` detection into the twelve
doctor sections and renders the human report:

    AIRMouse Doctor
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

from dataclasses import dataclass, field
from typing import Dict, List

from .capabilities import (
    Component,
    ComponentState,
    detect_all,
)

__all__ = [
    "DoctorSection", "DoctorReport", "run_doctor", "format_doctor_report",
    "SECTION_NAMES",
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
        for c in comps:
            if ComponentState(c.state) in blocking or \
                    ComponentState(c.state) is ComponentState.WARNING:
                return "[PARTIAL — fix FAILED items]"
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


# ---------------------------------------------------------------------------
# runners
# ---------------------------------------------------------------------------

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
    return DoctorReport(sections=sections)


def format_doctor_report(report: DoctorReport, verbose: bool = False) -> str:
    """Human output.

    ``verbose=False`` renders EXACTLY the contractual shape (title,
    counts, Overall verdict).  ``verbose=True`` inserts the per-section
    component rows (with ``Fix:`` lines for every non-READY,
    non-OPTIONAL item) between the title and the counts.
    """
    lines: List[str] = ["AIRMouse Doctor", "===========================", ""]

    if verbose:
        for section in report.sections:
            lines.append(section.name)
            if not section.components:
                lines.append("  (no components detected)")
            for c in section.components:
                state = ComponentState(c.state).value
                detail = f" — {c.detail}" if c.detail else ""
                lines.append(f"  {c.name:<34} {state:<13}{detail}")
                state_enum = ComponentState(c.state)
                if state_enum not in (ComponentState.READY,
                                      ComponentState.OPTIONAL):
                    fix = c.remediation or c.detail or \
                        "review this item in airmouse doctor output"
                    lines.append(f"      Fix: {fix}")
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
