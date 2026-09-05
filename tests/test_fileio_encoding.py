"""v16.5.1 §1/§2/§3/§4 — the UTF-8 encoding contract, config round-trip,
honest broken-config handling, repair, and the actionable doctor check.

The Windows bug that motivated this release: config.toml was written with
``open(path, "w")`` — the Windows process default (cp1252) — so the em
dash in the template became byte 0x97 and the strict UTF-8 reader
(tomllib) failed with ``can't decode byte 0x97 in position 30``.  Every
test here runs the exact contract that prevents that class of bug.
"""

import io
import json
import os

import pytest

from airmouse import config as config_mod
from airmouse import fileio
from airmouse.config import Config, ConfigLoadError, load_config_strict, \
    repair_config_file


# ---------------------------------------------------------------------------
# fileio — the contract primitives
# ---------------------------------------------------------------------------

ROUND_TRIP_TEXTS = [
    # ASCII
    'plain_setting = "ascii42"',
    # Unicode names (Nigerian / European)
    'user_display = "Adaeze Okafor-Nwosu"',
    'user_display = "Zoë Müller-Şahin"',
    'greeting = "Héllo wörld"',
    # dashes and quotes (the exact Windows-bug characters)
    "title = \"AirMouse — Direct Tracking — Edition\"",
    "range = \"3–5 minutes\"",
    "quote = \"“smart quotes” and ‘single’\"",
    # emoji (JSON/TOML strings are fine; no surrogate-pair issues)
    'status = "✓ ready 🎯 🙌"',
]


def test_read_write_round_trip_ascii(tmp_path):
    p = str(tmp_path / "a.txt")
    fileio.write_text_atomic(p, "hello = 1\n")
    assert fileio.read_text(p) == "hello = 1\n"


@pytest.mark.parametrize("text", ROUND_TRIP_TEXTS)
def test_read_write_round_trip_unicode(tmp_path, text):
    """§3 — write → read → values identical for every content class."""
    p = str(tmp_path / "u.txt")
    fileio.write_text_atomic(p, text + "\n")
    assert fileio.read_text(p) == text + "\n"


@pytest.mark.parametrize("text", ROUND_TRIP_TEXTS)
def test_config_round_trip_toml(tmp_path, text, monkeypatch):
    """§3 — the round trip through the REAL config file."""
    home = tmp_path / "home"
    monkeypatch.setattr(config_mod, "CONFIG_DIR", str(home))
    monkeypatch.setattr(config_mod, "CONFIG_PATH", str(home / "config.toml"))
    # craft a valid TOML doc containing the text as a value
    toml_text = text + "\n" if "=" in text else \
        f'# {text}\ntracking_mode = "direct"\n'
    p = str(home / "config.toml")
    os.makedirs(str(home), exist_ok=True)
    fileio.write_text_atomic(p, toml_text)
    strict = load_config_strict(p)
    assert isinstance(strict, dict)
    raw = fileio.read_text(p)
    assert raw == toml_text


def test_write_is_utf8_no_bom(tmp_path):
    p = str(tmp_path / "b.txt")
    fileio.write_text_atomic(p, "dash — test\n")
    raw = open(p, "rb").read()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert raw.decode("utf-8") == "dash — test\n"


def test_write_leaves_no_tmp_file(tmp_path):
    p = str(tmp_path / "c.txt")
    fileio.write_text_atomic(p, "x\n")
    assert not os.path.exists(p + ".tmp")


def test_read_text_raises_text_decoding_error_with_context(tmp_path):
    p = str(tmp_path / "legacy.toml")
    payload = b'title = "mean \x97 dash"\n'      # cp1252 em dash = 0x97
    open(p, "wb").write(payload)
    with pytest.raises(fileio.TextDecodingError) as exc:
        fileio.read_text(p)
    assert "0x97" in str(exc.value)
    assert exc.value.path == p
    assert exc.value.fix == "airmouse setup --repair-config"


def test_probe_reports_the_honest_diagnosis(tmp_path):
    p = str(tmp_path / "legacy.toml")
    raw = b"# AirMouse v3.2 Configuration \x97 Direct Tracking Edition\n"
    open(p, "wb").write(raw)
    info = fileio.probe(p)
    assert info["exists"] is True
    assert info["utf8_ok"] is False
    assert info["bad_byte"] == "0x97"
    assert info["decoded_as"] in ("cp1252", "latin-1")


def test_probe_on_missing_file(tmp_path):
    info = fileio.probe(str(tmp_path / "nope.toml"))
    assert info["exists"] is False and info["error"]


def test_decode_best_effort_strips_bom(tmp_path):
    raw = "tracking = \"direct\"\n".encode("utf-8-sig")
    text, enc, bad = fileio.decode_best_effort(raw)
    assert enc == "utf-8-sig"
    assert not text.startswith("﻿")
    assert bad is None
    # clean utf-8 short-circuits
    text2, enc2, bad2 = fileio.decode_best_effort("x = 1\n".encode("utf-8"))
    assert (text2, enc2, bad2) == ("x = 1\n", "utf-8", None)


# ---------------------------------------------------------------------------
# config writer — the encoding contract on the real template
# ---------------------------------------------------------------------------

def test_config_template_writes_utf8_with_em_dash(tmp_path, monkeypatch):
    """§1 — the v16.5.0 bug: em dash at byte 30 as 0x97.  Now impossible."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(config_mod, "CONFIG_DIR", str(home))
    monkeypatch.setattr(config_mod, "CONFIG_PATH", str(home / "config.toml"))
    cfg = Config()
    cfg.save_defaults()
    raw = open(str(home / "config.toml"), "rb").read()
    # strict UTF-8 decode succeeds — the exact operation tomllib performs
    text = raw.decode("utf-8")
    assert "— Direct Tracking Edition" in text       # em dash preserved
    assert b"\x97" not in raw                        # no cp1252 artifact
    assert not raw.startswith(b"\xef\xbb\xbf")       # no BOM


def test_config_round_trip_save_then_load(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(config_mod, "CONFIG_DIR", str(home))
    monkeypatch.setattr(config_mod, "CONFIG_PATH", str(home / "config.toml"))
    cfg = Config()
    cfg.save_defaults()
    cfg2 = Config()
    cfg2.load()
    assert cfg2.load_problem() is None
    # a representative value survived the write→read round trip
    assert cfg2.tracking_mode == cfg.tracking_mode


# ---------------------------------------------------------------------------
# §2 — never silently fall back from a broken user config
# ---------------------------------------------------------------------------

def _write_bytes(home, raw):
    home.mkdir(parents=True, exist_ok=True)
    p = home / "config.toml"
    p.write_bytes(raw)
    return p


def test_broken_encoding_surfaces_loudly(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    monkeypatch.setattr(config_mod, "CONFIG_DIR", str(home))
    monkeypatch.setattr(config_mod, "CONFIG_PATH", str(home / "config.toml"))
    _write_bytes(home, b'tracking_mode = "direct"  # \x97 em dash\n')
    cfg = Config()
    cfg.load()                       # lenient: defaults, but LOUD and honest
    problem = cfg.load_problem()
    assert problem is not None and problem["kind"] == "encoding"
    out = capsys.readouterr().out
    # the OLD silent one-liner is gone; the actionable block replaced it
    assert "using defaults" not in out
    assert "could NOT be loaded" in out
    assert "airmouse setup --repair-config" in out
    # the user's file was NOT modified by the load path
    assert b"\x97" in (home / "config.toml").read_bytes()


def test_broken_syntax_surfaces_with_kind(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(config_mod, "CONFIG_DIR", str(home))
    monkeypatch.setattr(config_mod, "CONFIG_PATH", str(home / "config.toml"))
    _write_bytes(home, b"tracking_mode = [unclosed\n")
    cfg = Config()
    cfg.load()
    assert cfg.load_problem()["kind"] == "syntax"


def test_strict_load_raises_config_load_error(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(config_mod, "CONFIG_DIR", str(home))
    monkeypatch.setattr(config_mod, "CONFIG_PATH", str(home / "config.toml"))
    _write_bytes(home, b'tracking_mode = "direct"  # \x97\n')
    with pytest.raises(ConfigLoadError) as exc:
        load_config_strict()
    assert exc.value.problem["kind"] == "encoding"
    assert "--repair-config" in str(exc.value)


def test_strict_load_missing_file_is_empty_dict(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(config_mod, "CONFIG_DIR", str(home))
    monkeypatch.setattr(config_mod, "CONFIG_PATH", str(home / "config.toml"))
    assert load_config_strict() == {}


# ---------------------------------------------------------------------------
# §2 — the explicit repair flow (backup → repair → validate → report)
# ---------------------------------------------------------------------------

def test_repairs_legacy_windows_encoding_preserving_content(
        tmp_path, monkeypatch, capsys):
    """The EXACT Windows scenario: cp1252 content with a 0x97 em dash."""
    home = tmp_path / "home"
    monkeypatch.setattr(config_mod, "CONFIG_DIR", str(home))
    monkeypatch.setattr(config_mod, "CONFIG_PATH", str(home / "config.toml"))
    legacy = ('tracking_mode = "direct"\n'
              '# AirMouse v3.2 Configuration — Direct Tracking Edition\n'
              ).encode("cp1252")
    _write_bytes(home, legacy)

    buf = io.StringIO()
    rep = repair_config_file(out=buf)
    out = buf.getvalue()

    assert rep["existed"] is True
    assert rep["repaired"] is True
    assert rep["valid_after"] is True
    assert rep["recovered_encoding"] == "cp1252"
    # content preserved byte-for-byte (as text)
    assert fileio.read_text(str(home / "config.toml")).encode("cp1252") == \
        legacy
    # backup preserved FOREVER, byte-for-byte
    backup = open(rep["backup_path"], "rb").read()
    assert backup == legacy
    assert os.path.dirname(rep["backup_path"]).endswith("backups")
    # the report says exactly what happened
    assert "recovered from legacy encoding cp1252" in out or \
        "cp1252" in out
    assert any("backup" in a for a in rep["actions"])


def test_repairs_utf8_bom(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(config_mod, "CONFIG_DIR", str(home))
    monkeypatch.setattr(config_mod, "CONFIG_PATH", str(home / "config.toml"))
    _write_bytes(home, 'tracking_mode = "direct"\n'.encode("utf-8-sig"))
    rep = repair_config_file()
    assert rep["repaired"] is True
    assert rep["recovered_encoding"] == "utf-8-sig"
    raw = (home / "config.toml").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")


def test_repair_of_invalid_toml_backs_up_then_defaults(
        tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(config_mod, "CONFIG_DIR", str(home))
    monkeypatch.setattr(config_mod, "CONFIG_PATH", str(home / "config.toml"))
    garbage = b"name = 'x'\nnot valid toml ===\n"
    _write_bytes(home, garbage)
    rep = repair_config_file()
    assert rep["rewrote_defaults"] is True
    assert rep["valid_after"] is True
    # the broken original is still recoverable from the backup
    assert open(rep["backup_path"], "rb").read() == garbage
    # and the fresh file parses
    load_config_strict()


def test_repair_when_no_file_exists_writes_fresh_template(
        tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(config_mod, "CONFIG_DIR", str(home))
    monkeypatch.setattr(config_mod, "CONFIG_PATH", str(home / "config.toml"))
    rep = repair_config_file()
    assert rep["valid_after"] is True
    assert (home / "config.toml").exists()


def test_repair_is_idempotent(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(config_mod, "CONFIG_DIR", str(home))
    monkeypatch.setattr(config_mod, "CONFIG_PATH", str(home / "config.toml"))
    Config().save_defaults()
    r1 = repair_config_file()
    r2 = repair_config_file()
    assert r1["valid_after"] and r2["valid_after"]
    assert r2["recovered_encoding"] == "utf-8"


# ---------------------------------------------------------------------------
# §4 — doctor's actionable CONFIG check
# ---------------------------------------------------------------------------

def test_doctor_config_ready_when_valid(tmp_path, monkeypatch):
    from airmouse.capabilities import detect_all, ComponentState
    home = tmp_path / "home"
    monkeypatch.setattr(config_mod, "CONFIG_DIR", str(home))
    monkeypatch.setattr(config_mod, "CONFIG_PATH", str(home / "config.toml"))
    home.mkdir(parents=True, exist_ok=True)
    Config().save_defaults()
    comps = [c for c in detect_all(quick=True).components
             if c.name == "Configuration"]
    assert comps and comps[0].state is ComponentState.READY
    assert "UTF-8 configuration valid" in comps[0].detail


def test_doctor_config_warning_names_the_exact_fix(tmp_path, monkeypatch):
    from airmouse.capabilities import detect_all, ComponentState
    home = tmp_path / "home"
    monkeypatch.setattr(config_mod, "CONFIG_DIR", str(home))
    monkeypatch.setattr(config_mod, "CONFIG_PATH", str(home / "config.toml"))
    _write_bytes(home, b'tracking_mode = "direct"  # \x97\n')
    comps = [c for c in detect_all(quick=True).components
             if c.name == "Configuration"]
    assert comps and comps[0].state is ComponentState.WARNING
    assert "--repair-config" in comps[0].remediation
    assert comps[0].why      # §4: the WHY is present, not just the fix


def test_doctor_config_defaults_when_missing(tmp_path, monkeypatch):
    from airmouse.capabilities import detect_all, ComponentState
    home = tmp_path / "home"
    monkeypatch.setattr(config_mod, "CONFIG_DIR", str(home))
    monkeypatch.setattr(config_mod, "CONFIG_PATH", str(home / "config.toml"))
    comps = [c for c in detect_all(quick=True).components
             if c.name == "Configuration"]
    assert comps and comps[0].state is ComponentState.READY
    assert "no config file yet" in comps[0].detail


# ---------------------------------------------------------------------------
# CLI wiring — `airmouse setup --repair-config`
# ---------------------------------------------------------------------------

def test_cli_repair_config_roundtrip(tmp_path, monkeypatch, capsys):
    import sys
    home = tmp_path / "home"
    monkeypatch.setattr(config_mod, "CONFIG_DIR", str(home))
    monkeypatch.setattr(config_mod, "CONFIG_PATH", str(home / "config.toml"))
    _write_bytes(home, b'tracking_mode = "direct"  # \x97\n')
    from airmouse import __main__ as m
    monkeypatch.setattr(sys, "argv", ["airmouse", "setup", "--repair-config"])
    rc = m.main()
    assert rc == 0
    out = capsys.readouterr().out
    assert "valid UTF-8" in out and "backup" in out


def test_fileio_json_contract_unicode_round_trip(tmp_path):
    """JSON files (profiles etc.) keep human-readable Unicode on disk."""
    p = str(tmp_path / "profile.json")
    obj = {"display": "Adaeze Okafor-Nwosu", "note": "café — résumé ✓"}
    fileio.write_json_atomic(p, obj)
    assert json.loads(fileio.read_text(p)) == obj
    assert "Adaeze" in open(p, encoding="utf-8").read()
