"""tests/test_final_form_cli.py — R3d (OPERATION AIRLOCK rebuild).

Restores the measured v19.0.3 FINAL FORM CLI facts after the
infrastructure loss (worklog: final-form-docs / final-form-final):

- the command registry grew 75 specs / 10 namespaces → 90 specs /
  11 namespaces (+14 DICTATION TEXT_EDIT buffer edits, +1 deictic
  move_pointer);
- dictation edit literals edit the DICTATION TEXT BUFFER
  (dictation_text.VoiceTypingEngine) and NEVER the filesystem, while
  the FILES "delete <name>" wildcard stays destructive/file-targeted;
- ``airmouse doctor --json`` carries honest structured JSON
  (overall / sections / blocked / degraded — never faked to READY);
- the autostart entry launches the REAL CLI
  (``<python> -m airmouse --skip`` — not the old no-op
  ``<python> --skip``) and verifies it is usable;
- the ``voice`` extra declares ``vosk`` (TT-22).

Deterministic, headless, no network, no real filesystem writes or
deletions (destructive operations are monkeypatched to explode).
"""

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

from airmouse import voice_commands as vc
from airmouse.dictation_text import VoiceTypingEngine
from airmouse.doctor import SECTION_NAMES, run_doctor_json
from airmouse.interfaces import CommandNamespace, IntentType
from airmouse.autostart import (
    build_desktop_entry,
    build_plist,
    build_windows_value,
    launch_command,
    verify_launch_command,
)

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_PKG = _ROOT / "airmouse_pkg"
_PYPROJECT = _PKG / "pyproject.toml"


# ═════════════════════════════════════════════════════════════════════════════
# 1–2. registry shape: 90 specs / 11 namespaces, DICTATION + TEXT_EDIT

def test_registry_is_90_specs_across_11_namespaces():
    assert len(vc.REGISTRY) == 90
    namespaces = {spec.namespace for spec in vc.REGISTRY}
    assert len(namespaces) == 11
    # the ten frozen v10 namespaces are all still represented
    for ns in CommandNamespace:
        assert ns in namespaces, ns


def test_dictation_namespace_with_text_edit_intents():
    assert getattr(CommandNamespace.DICTATION, "value", None) == "dictation"
    dictation = [s for s in vc.REGISTRY if s.namespace is vc.DICTATION]
    assert len(dictation) == 14
    assert all(s.intent is vc.TEXT_EDIT for s in dictation)
    assert getattr(vc.TEXT_EDIT, "value", None) == "text_edit"
    # the measured buffer-edit literals are all present, none destructive
    names = {s.name for s in dictation}
    assert {"dict_delete_last_word", "dict_scratch_that",
            "dict_delete_that", "dict_strike_that", "dict_new_line",
            "dict_new_paragraph", "dict_replace_that", "dict_copy_that",
            "dict_cut_that", "dict_paste_that"} <= names
    assert all(not s.destructive and not s.sensitive for s in dictation)


# ═════════════════════════════════════════════════════════════════════════════
# 3–5. resolution: buffer edits vs file ops vs the deictic move

def test_delete_last_word_resolves_to_dictation_text_edit_not_file_deletion():
    # the FILES wildcard WOULD match this phrase — the literal outranks it
    files_spec = next(s for s in vc.REGISTRY if s.name == "delete_file")
    assert files_spec.match("delete last word") is not None
    for dictation_active in (True, False):
        m = vc.match_command_grammar("delete last word",
                                     dictation_active=dictation_active)
        assert m.is_command
        assert m.name == "dict_delete_last_word"
        assert m.namespace is vc.DICTATION
        assert m.intent is vc.TEXT_EDIT
        assert not m.destructive                    # a buffer edit, not data loss
        assert m.params.get("edit_op") == "delete_last_word"
        assert m.params.get("op") != "delete"       # never a file delete op


def test_delete_file_wildcard_stays_destructive_and_files_targeted():
    m = vc.match_command_grammar("delete demo.txt", dictation_active=True)
    assert m.is_command
    assert m.name == "delete_file"
    assert m.namespace is CommandNamespace.FILES
    assert m.intent is IntentType.FILE_OP
    assert m.destructive and m.sensitive
    assert m.params.get("op") == "delete"
    assert m.entities.get("name") == "demo txt"    # normalization keeps the name


def test_deictic_move_over_there_resolves_to_move_pointer():
    m = vc.match_command_grammar("move over there")
    assert m.is_command
    assert m.name == "move_pointer"
    assert m.namespace is CommandNamespace.MOUSE
    assert m.intent is IntentType.MOVE
    assert m.params.get("what") == "pointer"
    assert m.params.get("deictic") is True
    assert m.params.get("action") == "move_pointer"
    assert not m.destructive and not m.sensitive


# ═════════════════════════════════════════════════════════════════════════════
# 6–7. doctor --json: parseable, sectioned, honest

def test_doctor_json_parseable_with_overall_and_sections():
    data = json.loads(json.dumps(run_doctor_json()))   # roundtrip: parseable
    assert data["kind"] == "airmouse.doctor"
    assert data["overall"] in ("READY", "PARTIAL", "BLOCKED")
    assert [s["name"] for s in data["sections"]] == list(SECTION_NAMES)
    total = sum(len(s["components"]) for s in data["sections"])
    assert total == sum(data["counts"].values()) > 0
    for row in data["blocked"] + data["degraded"]:
        assert {"name", "category", "state", "why", "fix"} <= set(row)
    assert isinstance(data["remediations"], list)
    # end-to-end: the REAL dispatcher path emits parseable JSON with overall
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_PKG) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-m", "airmouse", "doctor", "--json"],
        cwd=str(_ROOT), env=env, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=300,
    )
    assert proc.returncode in (0, 1, 2), proc.stderr[:400]
    cli = json.loads(proc.stdout.decode("utf-8", "replace")[
        proc.stdout.decode("utf-8", "replace").find("{"):])
    assert str(cli.get("overall", "")).strip() != ""


def test_doctor_json_headless_is_honest_not_ready():
    data = run_doctor_json()
    # this headless sandbox is degraded — the JSON must say so, never fake READY
    assert data["overall"] in ("PARTIAL", "BLOCKED")
    assert data["verdict"].startswith(("[PARTIAL", "[BLOCKED"))
    assert data["blocked"] or data["degraded"]
    assert data["counts"]["READY"] < sum(data["counts"].values())


# ═════════════════════════════════════════════════════════════════════════════
# 8–9. autostart: a usable launch command + verification

def test_autostart_entry_launches_real_cli():
    entry = build_desktop_entry()
    exec_line = next(l for l in entry.splitlines() if l.startswith("Exec="))
    assert "-m airmouse --skip" in exec_line
    # the old defect — Exec=<python> --skip, which runs NOTHING — is gone
    assert not re.search(r"Exec=\S+\s+--skip\s*$", exec_line)
    cmd = launch_command()
    assert cmd.endswith("-m airmouse --skip")
    assert build_windows_value() == cmd
    plist = build_plist()
    assert "<string>-m</string>" in plist
    assert "<string>airmouse</string>" in plist
    assert "<string>--skip</string>" in plist


def test_autostart_verify_passes_on_generated_entry_and_rejects_old_defect():
    res = verify_launch_command()          # the generated launch command
    assert res["launch"].endswith("-m airmouse --skip")
    assert res["interpreter"] is True
    assert res["references_module"] is True
    assert res["module_importable"] is True
    assert res["ok"] is True, res["reason"]
    # the historical defect form fails verification honestly
    bad = verify_launch_command("%s --skip" % sys.executable)
    assert bad["ok"] is False
    assert bad["references_module"] is False
    assert "airmouse" in bad["reason"]


# ═════════════════════════════════════════════════════════════════════════════
# 10. packaging: the voice extra declares vosk (TT-22)

def test_voice_extra_declares_vosk():
    try:
        import tomllib
        with open(_PYPROJECT, "rb") as f:
            data = tomllib.load(f)
        voice = data["project"]["optional-dependencies"]["voice"]
    except ImportError:                     # py<3.11 fallback: honest text parse
        text = _PYPROJECT.read_text(encoding="utf-8")
        line = next(l for l in text.splitlines() if l.startswith("voice = "))
        voice = [s.strip(' "]') for s in line.split("=", 1)[1].strip()[1:-1].split(",")]
    assert any(req.split(">")[0].split("=")[0] == "vosk" for req in voice)
    assert any(req.startswith("SpeechRecognition>=3.10.0") for req in voice)
    assert any(req.startswith("pyaudio>=0.2.13") for req in voice)


# ═════════════════════════════════════════════════════════════════════════════
# 11–12. dictation-edit safety: buffer-only, precedence over FILES

@pytest.fixture()
def _forbid_file_deletion(monkeypatch):
    """Any real deletion attempt explodes the test (never actually used)."""
    def _boom(*args, **kwargs):
        raise AssertionError("filesystem deletion was attempted")

    for mod, attr in ((os, "remove"), (os, "unlink"), (os, "rmdir"),
                      (shutil, "rmtree"), (pathlib.Path, "unlink"),
                      (pathlib.Path, "rmdir")):
        monkeypatch.setattr(mod, attr, _boom)


def test_dictation_edit_applies_to_buffer_without_filesystem_deletion(
        _forbid_file_deletion):
    engine = VoiceTypingEngine()
    engine.ingest("hello world")            # dictation insert → the buffer
    assert engine.text == "Hello world"
    m = vc.match_command_grammar("delete last word", dictation_active=True)
    ops = vc.apply_dictation_text_edit(m, engine)
    assert ops and ops[0].op == "edit_command"
    assert engine.text == "Hello"           # one word removed from the BUFFER
    scratch = vc.match_command_grammar("scratch that", dictation_active=True)
    engine2 = VoiceTypingEngine()
    engine2.ingest("hello world")
    ops2 = vc.apply_dictation_text_edit(scratch, engine2)
    assert ops2 and ops2[0].op == "edit_command"
    assert engine2.text == ""               # last segment scratched away
    # a FILES match is refused by the buffer-edit applier — no file work
    assert vc.apply_dictation_text_edit(
        vc.match_command_grammar("delete demo.txt"), engine) == []


def test_text_edit_outranks_files_wildcard_when_buffer_active():
    files_spec = next(s for s in vc.REGISTRY if s.name == "delete_file")
    assert files_spec.destructive
    assert files_spec.match("delete that") is not None   # wildcard WOULD take it
    for phrase in ("delete that", "scratch that", "delete last word"):
        m = vc.match_command_grammar(phrase, dictation_active=True)
        assert m.namespace is vc.DICTATION, phrase
        assert m.intent is vc.TEXT_EDIT, phrase
        assert not m.destructive, phrase
        assert m.name != "delete_file", phrase
    # buffer-active authority also flips shared literals ("copy that") to
    # the buffer edit, while file-shaped utterances stay FILES-targeted
    assert vc.match_command_grammar("copy that",
                                    dictation_active=True).name == "dict_copy_that"
    assert vc.match_command_grammar(
        "delete demo.txt", dictation_active=True).name == "delete_file"


# ═════════════════════════════════════════════════════════════════════════════
# 13. hygiene: the registry never duplicates a command name

def test_registry_has_no_duplicate_spec_names():
    names = [s.name for s in vc.REGISTRY]
    assert len(names) == len(set(names))
