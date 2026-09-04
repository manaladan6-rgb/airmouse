"""AirMouse v15.1 tests — capabilities / doctor / verify (task 2-a).

Deterministic, headless, no network, no sleeps.  Verifies the public
API contract of airmouse.capabilities, airmouse.doctor and
airmouse.verify, including the fail-closed behaviour (a crashing
detector becomes a FAILED component, never an exception).
"""

import json
import time

import pytest

from airmouse import capabilities
from airmouse.capabilities import (
    CATEGORIES,
    CORE_DEPENDENCIES,
    OPTIONAL_DEPENDENCIES,
    REQUIRED_CATEGORIES,
    CapabilityReport,
    Component,
    ComponentState,
    detect_all,
)
from airmouse.doctor import (
    SECTION_NAMES,
    DoctorReport,
    DoctorSection,
    format_doctor_report,
    run_doctor,
)
from airmouse.verify import (
    ACTION_REQUIRED,
    FAIL,
    PASS,
    VerifyReport,
    run_verify,
)


# ═════════════════════════════════════════════════════════════════════════════
# shared fixtures

@pytest.fixture(scope="module")
def full_report() -> CapabilityReport:
    return detect_all()


def _by_category(report: CapabilityReport) -> dict:
    out = {}
    for c in report.components:
        out.setdefault(c.category, []).append(c)
    return out


def _synthetic(states_by_category) -> CapabilityReport:
    """Build a synthetic report: {category: [ComponentState, ...]}."""
    comps = []
    n = 0
    for cat, states in states_by_category.items():
        for state in states:
            n += 1
            remediation = "" if state is ComponentState.READY else "fix it"
            comps.append(Component(
                name=f"comp-{n}", category=cat, state=state,
                detail="d", remediation=remediation))
    return CapabilityReport(components=comps)


# ═════════════════════════════════════════════════════════════════════════════
# vocabulary + exports

def test_component_state_enum_has_all_nine_members():
    expected = {"READY", "OPTIONAL", "NOT_INSTALLED", "HARDWARE",
                "ENHANCEMENT", "UNAVAILABLE", "INCOMPATIBLE", "FAILED",
                "WARNING"}
    assert {s.name for s in ComponentState} == expected


def test_component_state_values_match_names():
    for s in ComponentState:
        assert s.value == s.name


def test_categories_tuple_lists_all_twelve():
    assert len(CATEGORIES) == 12
    assert CATEGORIES == ("SYSTEM", "PYTHON", "AIRMOUSE", "CAMERA",
                          "MICROPHONE", "SPEECH", "INPUT", "BROWSER",
                          "INTELLIGENCE", "AGENT", "OFFLINE", "SAFETY")


def test_dependency_tables_shape():
    for pypi, entry in CORE_DEPENDENCIES.items():
        assert isinstance(entry, tuple) and len(entry) == 2
        import_name, pypi_name = entry
        assert pypi_name == pypi and import_name
    for pypi, entry in OPTIONAL_DEPENDENCIES.items():
        import_name, pypi_name = entry
        assert pypi_name == pypi and import_name
    assert {"numpy", "opencv-python", "mediapipe", "pynput"} == \
        set(CORE_DEPENDENCIES)
    assert {"sounddevice", "SpeechRecognition", "pyaudio", "pytesseract",
            "pyttsx3"} == set(OPTIONAL_DEPENDENCIES)


# ═════════════════════════════════════════════════════════════════════════════
# detect_all (full) — headless behaviour

def test_detect_all_runs_headless_without_raising(full_report):
    assert isinstance(full_report, CapabilityReport)
    assert len(full_report.components) > 0


def test_detect_all_covers_all_twelve_categories(full_report):
    cats = _by_category(full_report)
    missing = [c for c in CATEGORIES if c not in cats]
    assert missing == []


def test_every_component_has_valid_category_and_state(full_report):
    for c in full_report.components:
        assert c.category in CATEGORIES
        assert isinstance(c.state, ComponentState)
        assert isinstance(c.detail, str) and isinstance(c.remediation, str)


def test_failed_and_warning_components_have_remediation(full_report):
    for c in full_report.components:
        if c.state in (ComponentState.FAILED, ComponentState.WARNING):
            assert c.remediation.strip(), f"no remediation for {c.name}"


def test_required_core_is_healthy_in_this_sandbox(full_report):
    """numpy/cv2/mediapipe/airmouse itself must be READY here."""
    by_name = {c.name: c for c in full_report.components}
    for dep in ("Dependency: numpy", "Dependency: opencv-python",
                "Dependency: mediapipe", "AirMouse package"):
        assert by_name[dep].state is ComponentState.READY, dep


def test_pynput_unavailable_with_display_remediation(full_report):
    inp = _by_category(full_report)["INPUT"]
    pynput_row = next(c for c in inp if "pynput" in c.name.lower())
    assert pynput_row.state is ComponentState.UNAVAILABLE
    assert "display" in pynput_row.remediation.lower()
    assert "docs" in pynput_row.remediation.lower()


def test_camera_probe_is_bounded_under_five_seconds():
    t0 = time.perf_counter()
    comps = capabilities._detect_camera(False)
    elapsed = time.perf_counter() - t0
    assert elapsed < 5.0
    webcam = [c for c in comps if c.name == "Webcam"]
    assert webcam and webcam[0].category == "CAMERA"


def test_quick_mode_returns_report_fast(full_report):
    t0 = time.perf_counter()
    rep = detect_all(quick=True)
    elapsed = time.perf_counter() - t0
    assert isinstance(rep, CapabilityReport)
    assert {c.category for c in rep.components} == set(CATEGORIES)
    # quick mode never opens the camera device
    cam = [c for c in rep.components if c.category == "CAMERA"]
    assert all(c.state is ComponentState.HARDWARE for c in cam)
    assert elapsed < 5.0, "quick mode must stay well under the doctor budget"


# ═════════════════════════════════════════════════════════════════════════════
# CapabilityReport aggregation + machine output

def test_counts_contains_every_state_with_stable_keys(full_report):
    counts = full_report.counts()
    assert list(counts.keys()) == [s.name for s in ComponentState]
    assert sum(counts.values()) == len(full_report.components)


def test_to_machine_is_json_serializable(full_report):
    machine = full_report.to_machine()
    assert machine["version"]
    assert machine["overall"] in ("BLOCKED", "PARTIAL",
                                  "READY FOR TESTING")
    assert len(machine["components"]) == len(full_report.components)
    row = machine["components"][0]
    assert set(row.keys()) == {"name", "category", "state", "detail",
                               "remediation"}
    decoded = json.loads(json.dumps(machine))  # roundtrip
    assert decoded == machine


def test_to_machine_is_stable_across_calls(full_report):
    assert full_report.to_machine() == full_report.to_machine()


def test_format_has_table_counts_and_overall(full_report):
    text = full_report.format()
    assert "Capability" in text and "Status" in text
    assert text.startswith("Capability")
    assert "Counts: " in text
    for state in ComponentState:
        assert f"{state.name}=" in text
    assert "Overall: " in text


def test_format_lists_remediation_for_non_ready(full_report):
    text = full_report.format()
    for c in full_report.components:
        if c.state not in (ComponentState.READY, ComponentState.OPTIONAL):
            assert (c.remediation or c.detail) in text, c.name


# ═════════════════════════════════════════════════════════════════════════════
# verdict logic (synthetic reports — no hardware involved)

def test_overall_blocked_on_required_core_failure():
    assert "PYTHON" in REQUIRED_CATEGORIES
    rep = _synthetic({"PYTHON": [ComponentState.INCOMPATIBLE]})
    assert rep.overall() == "BLOCKED"


def test_overall_not_blocked_by_non_core_failure():
    rep = _synthetic({"CAMERA": [ComponentState.FAILED],
                      "PYTHON": [ComponentState.READY]})
    assert rep.overall() == "PARTIAL"


def test_overall_partial_on_warning():
    rep = _synthetic({"AIRMOUSE": [ComponentState.WARNING]})
    assert rep.overall() == "PARTIAL"


def test_overall_ready_for_testing():
    rep = _synthetic({
        "SYSTEM": [ComponentState.READY, ComponentState.READY],
        "PYTHON": [ComponentState.READY],
        "CAMERA": [ComponentState.HARDWARE],    # unknown-but-present hw
        "INPUT": [ComponentState.UNAVAILABLE],  # headless environment
    })
    assert rep.overall() == "READY FOR TESTING"


# ═════════════════════════════════════════════════════════════════════════════
# fail-closed detector injection

def test_detector_crash_becomes_failed_component(monkeypatch):
    def boom(quick):
        raise RuntimeError("injected detector crash")

    monkeypatch.setattr(capabilities, "_detect_camera", boom)
    rep = detect_all()
    cam = [c for c in rep.components if c.category == "CAMERA"]
    assert len(cam) == 1
    assert cam[0].state is ComponentState.FAILED
    assert "injected detector crash" in cam[0].detail
    assert cam[0].remediation.strip()


def test_detector_crash_does_not_lose_other_categories(monkeypatch):
    def boom(quick):
        raise ValueError("boom")

    monkeypatch.setattr(capabilities, "_detect_safety", boom)
    rep = detect_all()
    cats = {c.category for c in rep.components}
    assert cats == set(CATEGORIES)          # all 12 still present
    assert "crashed" in rep.format()        # and the failure is visible


def test_detector_returning_junk_is_fail_closed(monkeypatch):
    monkeypatch.setattr(capabilities, "_detect_offline",
                        lambda quick: "not a list")
    rep = detect_all()
    off = [c for c in rep.components if c.category == "OFFLINE"]
    assert len(off) == 1 and off[0].state is ComponentState.FAILED


# ═════════════════════════════════════════════════════════════════════════════
# doctor

def test_run_doctor_section_order_exact():
    report = run_doctor()
    assert [s.name for s in report.sections] == list(SECTION_NAMES)
    assert list(SECTION_NAMES) == [
        "System", "Python", "AirMouse", "Camera", "Microphone", "Speech",
        "Input", "Browser", "Intelligence", "Agent", "Offline", "Safety"]


def test_doctor_sections_mirror_detect_all(full_report):
    report = run_doctor()
    flat = report.all_components()
    assert len(flat) == len(full_report.components)
    assert {c.category for c in flat} == {c.category for c in
                                          full_report.components}
    # section placement follows the category mapping
    for section in report.sections:
        for c in section.components:
            assert c.category in CATEGORIES


def test_summary_counts_keys_exact_and_ordered():
    report = run_doctor()
    counts = report.summary_counts()
    assert list(counts.keys()) == ["READY", "OPTIONAL", "HARDWARE",
                                   "WARNING", "FAILED"]
    assert sum(counts.values()) == len(report.all_components())


def test_summary_counts_bucket_mapping():
    report = DoctorReport(sections=[DoctorSection(name="S", components=[
        Component("a", "SYSTEM", ComponentState.READY),
        Component("b", "SYSTEM", ComponentState.OPTIONAL),
        Component("c", "SYSTEM", ComponentState.NOT_INSTALLED),
        Component("d", "SYSTEM", ComponentState.ENHANCEMENT),
        Component("e", "SYSTEM", ComponentState.UNAVAILABLE),
        Component("f", "SYSTEM", ComponentState.HARDWARE),
        Component("g", "SYSTEM", ComponentState.WARNING),
        Component("h", "SYSTEM", ComponentState.FAILED),
        Component("i", "SYSTEM", ComponentState.INCOMPATIBLE),
    ])])
    counts = report.summary_counts()
    assert counts == {"READY": 1, "OPTIONAL": 4, "HARDWARE": 1,
                      "WARNING": 1, "FAILED": 2}


def test_doctor_overall_bracket_verdicts():
    blocked = DoctorReport(sections=[DoctorSection(name="S", components=[
        Component("x", "PYTHON", ComponentState.INCOMPATIBLE)])])
    assert blocked.overall() == "[BLOCKED]"

    partial = DoctorReport(sections=[DoctorSection(name="S", components=[
        Component("x", "CAMERA", ComponentState.FAILED)])])
    assert partial.overall() == "[PARTIAL — fix FAILED items]"

    ready = DoctorReport(sections=[DoctorSection(name="S", components=[
        Component("x", "PYTHON", ComponentState.READY),
        Component("y", "CAMERA", ComponentState.OPTIONAL)])])
    assert ready.overall() == "[READY FOR TESTING]"


def test_doctor_remediations_one_per_non_ready_item():
    report = DoctorReport(sections=[DoctorSection(name="S", components=[
        Component("a", "SYSTEM", ComponentState.READY, remediation="r-a"),
        Component("b", "SYSTEM", ComponentState.OPTIONAL),
        Component("c", "CAMERA", ComponentState.HARDWARE,
                  remediation="r-c"),
        Component("d", "INPUT", ComponentState.WARNING,
                  remediation="r-d"),
        Component("e", "SAFETY", ComponentState.FAILED,
                  remediation="r-e"),
    ])])
    fixes = report.remediations()
    assert fixes == ["r-c", "r-d", "r-e"]


def test_format_doctor_report_ends_with_contract_shape():
    report = run_doctor()
    text = format_doctor_report(report)
    counts = report.summary_counts()
    expected_tail = (
        f"READY:       {counts['READY']}\n"
        f"OPTIONAL:    {counts['OPTIONAL']}\n"
        f"HARDWARE:    {counts['HARDWARE']}\n"
        f"WARNING:     {counts['WARNING']}\n"
        f"FAILED:      {counts['FAILED']}\n"
        "\n"
        "Overall:\n"
        f"{report.overall()}")
    assert text.endswith(expected_tail)
    assert text.startswith("AIRMouse Doctor\n===========================\n")
    assert report.overall() in ("[READY FOR TESTING]",
                                "[PARTIAL — fix FAILED items]",
                                "[BLOCKED]")


def test_format_doctor_report_verbose_shows_fix_lines():
    report = run_doctor()
    text = format_doctor_report(report, verbose=True)
    non_ready = [c for c in report.all_components()
                 if c.state not in (ComponentState.READY,
                                    ComponentState.OPTIONAL)]
    assert non_ready, "sandbox always has at least one non-READY row"
    assert "Fix: " in text
    for c in non_ready:
        assert c.remediation in text
    # verbose shows the section headers too
    assert "System" in text and "Safety" in text


# ═════════════════════════════════════════════════════════════════════════════
# verify

@pytest.fixture(scope="module")
def verify_report() -> VerifyReport:
    return run_verify()


def test_verify_automated_checks_all_pass_here(verify_report):
    assert len(verify_report.automated) >= 8
    failures = [i for i in verify_report.automated if i.status != PASS]
    assert failures == [], [f"{i.name}: {i.detail}" for i in failures]


def test_verify_covers_the_contracted_automated_areas(verify_report):
    names = {i.name for i in verify_report.automated}
    assert {"Core", "Voice", "Intelligence", "Safety", "Offline",
            "Browser simulator", "Packaging"} <= names


def test_verify_physical_items_all_action_required(verify_report):
    physical = verify_report.physical
    names = [i.name for i in physical]
    assert names == ["Webcam", "Microphone", "Hand tracking", "Gaze",
                     "Real browser"]
    for item in physical:
        assert item.status == ACTION_REQUIRED
        assert item.status != PASS


def test_verify_physical_never_autopasses_even_when_all_green():
    """Physical rows are static ACTION_REQUIRED rows by construction."""
    rep = run_verify()
    assert all(i.status == ACTION_REQUIRED for i in rep.physical)


def test_verify_failures_carry_reasons():
    """A crashing check must yield FAIL with the reason (honesty)."""
    from airmouse import verify as verify_mod

    def boom():
        raise RuntimeError("exploded check")

    item = verify_mod._check("Boom", boom)
    assert item.status == FAIL
    assert "exploded check" in item.detail


def test_verify_format_reproduces_spec_layout(verify_report):
    text = verify_report.format()
    assert text.startswith("AIRMouse Verification")
    assert "Automated:" in text and "Physical:" in text
    assert text.endswith("Next step:\nRun:\n\nairmouse test --guided")
    for item in verify_report.physical:
        assert f"[{ACTION_REQUIRED:>15}] {item.name}" in text


def test_verify_offline_check_is_the_real_selftest(verify_report):
    off = next(i for i in verify_report.automated if i.name == "Offline")
    assert off.status == PASS
    assert "checks passed" in off.detail


def test_verify_packaging_version_matches(verify_report):
    pkg = next(i for i in verify_report.automated
               if i.name == "Packaging")
    assert pkg.status == PASS
    import airmouse
    from airmouse.verify import _pyproject_version
    version = _pyproject_version()
    if version is not None:          # readable in a source checkout
        assert version == airmouse.__version__
        assert airmouse.__version__ in pkg.detail


# ═════════════════════════════════════════════════════════════════════════════
# hygiene

def test_no_subprocess_or_network_primitives_in_new_modules():
    import inspect
    from airmouse import doctor as doctor_mod
    from airmouse import verify as verify_mod
    for mod in (capabilities, doctor_mod, verify_mod):
        src = inspect.getsource(mod)
        for forbidden in ("subprocess", "os.system", "eval(", "exec(",
                          "pickle", "urlopen", "socket.create_connection",
                          "shell=True"):
            assert forbidden not in src, (mod.__name__, forbidden)


def test_machine_matrix_components_carry_unique_names_per_category():
    rep = detect_all(quick=True)
    seen = set()
    for c in rep.components:
        key = (c.category, c.name)
        assert key not in seen, key
        seen.add(key)
