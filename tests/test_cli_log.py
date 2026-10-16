"""Tests for v19.0.2 ``airmouse logs`` backend: airmouse.cli_log.

Deterministic, offline, stdlib-only.  Every test isolates storage by
pointing AIRMOUSE_HOME at a fresh tmp_path (the authoritative
airmouse.paths pattern — paths re-read the env on every call, so the
module's dynamic path resolution is exercised for real).  No test may
touch the real ~/.airmouse.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta

import pytest

from airmouse import cli_log

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """Isolated AIRMOUSE_HOME pointing at a fresh temp dir."""
    monkeypatch.setenv("AIRMOUSE_HOME", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# append + read roundtrip
# ---------------------------------------------------------------------------


def test_append_read_recent_roundtrip_order():
    cli_log.log_command("status", 0, msg="first")
    cli_log.log_command("click", 0, msg="second")
    cli_log.log_command("doctor", 1, level="error", msg="third")
    recs = cli_log.read_recent(10)
    assert [r["cmd"] for r in recs] == ["status", "click", "doctor"]
    assert [r["msg"] for r in recs] == ["first", "second", "third"]
    assert cli_log.read_recent(2) == recs[1:]          # tail, oldest→newest
    assert set(recs[0].keys()) == {"t", "mono", "cmd", "rc", "level", "msg"}


def test_utc_timestamp_format_sanity():
    cli_log.log_command("status", 0)
    t = cli_log.read_recent(1)[0]["t"]
    assert t.endswith("Z") or t.endswith("+00:00")     # UTC marker present
    iso = t[:-1] + "+00:00" if t.endswith("Z") else t
    dt = datetime.fromisoformat(iso)                   # parseable
    assert dt.tzinfo is not None
    assert dt.utcoffset() == timedelta(0)              # truly zero-offset UTC
    assert dt.year >= 2024                             # sane wall clock
    # monotonic sidecar is a plausible float
    mono = cli_log.read_recent(1)[0]["mono"]
    assert isinstance(mono, float) and mono >= 0.0


# ---------------------------------------------------------------------------
# injection safety
# ---------------------------------------------------------------------------


def test_ansi_csi_escape_stripped():
    cli_log.log_command("run", 1, msg="\x1b[31mERR\x1b[0m fake")
    msg = cli_log.read_recent(1)[0]["msg"]
    assert msg == "ERR fake"
    assert "\x1b" not in msg


def test_ansi_osc_escape_stripped():
    cli_log.log_command("run", 0, msg="\x1b]0;window title\x07tail")
    msg = cli_log.read_recent(1)[0]["msg"]
    assert msg == "tail"
    assert "\x1b" not in msg and "\x07" not in msg


def test_control_chars_stripped_newline_kept():
    cli_log.log_command("run", 0, msg="a\x07b\rc\nd\x00e\tf\x7fg")
    msg = cli_log.read_recent(1)[0]["msg"]
    assert msg == "abc\ndefg"                          # \n is the only survivor
    assert "\r" not in msg and "\x07" not in msg and "\x00" not in msg


def test_msg_capped_at_2000_chars():
    cli_log.log_command("big", 0, msg="x" * 5000)
    msg = cli_log.read_recent(1)[0]["msg"]
    assert len(msg) == 2000
    assert set(msg) == {"x"}


# ---------------------------------------------------------------------------
# rotation (bounded disk)
# ---------------------------------------------------------------------------


def test_rotation_to_generation_one_and_read_recent():
    big = "y" * 2000                                   # capped at 2000 chars
    for i in range(600):                               # ~1.25 MiB of records
        cli_log.log_command("fill%04d" % i, 0, msg=big)
    cur = cli_log.path()
    rot = cli_log.path() + ".1"
    assert os.path.exists(rot)                         # .1 generation exists
    assert os.path.getsize(cur) <= cli_log.MAX_BYTES + 8192  # live file bounded
    total = cli_log.read_recent(1000)
    assert len(total) == 600                           # history across rotation
    idx = [int(r["cmd"][4:]) for r in total]
    assert idx == sorted(idx)                          # oldest → newest intact
    tail = cli_log.read_recent(50)
    assert tail[-1]["cmd"] == "fill0599"               # newest record readable


def test_rotation_overwrites_old_generation(monkeypatch):
    monkeypatch.setattr(cli_log, "MAX_BYTES", 4096)    # small bound → fast test
    for i in range(30):
        cli_log.log_command("gen1", 0, msg=("g1-%02d-" % i) + "x" * 150)
    cli_log.log_command("gen2", 0, msg="g2-marker")
    rot = cli_log.path() + ".1"
    with open(rot, encoding="utf-8") as f:
        gen1_view = f.read()
    assert "g1-05-" in gen1_view                       # rotation #1 happened
    # grow again → later rotations must OVERWRITE .1 (one generation only)
    for i in range(30):
        cli_log.log_command("gen3", 0, msg=("g3-%02d-" % i) + "z" * 150)
    cli_log.log_command("gen4", 0, msg="g4-last")
    with open(rot, encoding="utf-8") as f:
        gen2_view = f.read()
    with open(cli_log.path(), encoding="utf-8") as f:
        cur_view = f.read()
    assert "g1-05-" not in gen2_view                   # old .1 discarded
    assert "g1-05-" not in cur_view
    assert len(gen2_view.splitlines()) >= 1            # still exactly one gen
    recs = cli_log.read_recent(1000)
    assert recs[0]["cmd"].startswith("gen3")           # only recent gens remain
    assert recs[-1]["cmd"] == "gen4"                   # newest record readable
    assert all(r["cmd"] in ("gen3", "gen4") for r in recs)
    assert os.path.getsize(cli_log.path()) <= 4096 + 8192  # bounded


# ---------------------------------------------------------------------------
# read_recent clamping + robustness
# ---------------------------------------------------------------------------


def test_n_clamped_up_to_at_least_one():
    for i in range(3):
        cli_log.log_command("c%d" % i, 0)
    recs = cli_log.read_recent(0)
    assert len(recs) == 1                              # n=0 → at least 1
    assert recs[0]["cmd"] == "c2"                      # and it's the newest
    assert len(cli_log.read_recent(-5)) == 1


def test_n_clamped_down_to_1000():
    for i in range(1005):
        cli_log.log_command("c", 0, msg="m%04d" % i)
    recs = cli_log.read_recent(99999)
    assert len(recs) == 1000                           # hard READ_MAX cap
    assert recs[-1]["msg"] == "m1004"                  # newest kept


def test_corrupt_lines_skipped_no_crash():
    cli_log.log_command("good1", 0)
    with open(cli_log.path(), "a", encoding="utf-8") as f:
        f.write("{not json at all\n")                  # broken JSON
        f.write('["a","list"]\n')                      # valid JSON, not a dict
        f.write("\n")                                  # empty line
        f.write('{"t": "x", "mono": "NaN?", "cmd": 5}\n')  # partial record
    cli_log.log_command("good2", 0)
    recs = cli_log.read_recent(10)
    assert [r["cmd"] for r in recs] == ["good1", "good2"]


def test_missing_file_returns_empty_no_crash():
    assert not os.path.exists(cli_log.path())
    assert cli_log.read_recent(5) == []
    assert cli_log.read_recent_json(5) == "[]"


def test_clear_removes_both_files():
    cli_log.log_command("a", 0)
    cli_log.log_command("b", 0)
    with open(cli_log.path() + ".1", "w", encoding="utf-8") as f:
        f.write('{"t":"t","mono":0.0,"cmd":"old","rc":0,"level":"info","msg":""}\n')
    cli_log.clear()
    assert not os.path.exists(cli_log.path())
    assert not os.path.exists(cli_log.path() + ".1")
    assert cli_log.read_recent(5) == []
    cli_log.log_command("after", 0)                    # still usable after clear
    assert [r["cmd"] for r in cli_log.read_recent(5)] == ["after"]


# ---------------------------------------------------------------------------
# concurrency + types + paths + convenience API
# ---------------------------------------------------------------------------


def test_thread_safety_smoke_8_threads_x_50():
    def worker(tid):
        for i in range(50):
            cli_log.log_command("cmd%d" % tid, 0, msg="t%d-i%02d" % (tid, i))

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    recs = cli_log.read_recent(1000)
    assert len(recs) == 400                            # nothing lost, no dupes
    with open(cli_log.path(), encoding="utf-8") as f:  # every line intact JSON
        lines = [ln for ln in f.read().splitlines() if ln.strip()]
    assert len(lines) == 400
    assert all(isinstance(json.loads(ln), dict) for ln in lines)
    cmds = sorted({r["cmd"] for r in recs})
    assert cmds == ["cmd%d" % t for t in range(8)]


def test_rc_and_level_roundtrip_types():
    cli_log.log_command("neg", -3, level="error")
    cli_log.log_command("warned", 2, level="warn")
    cli_log.log_command("odd", 2.9, level="PANIC")     # unknown level, float rc
    recs = cli_log.read_recent(10)
    assert recs[0]["rc"] == -3 and isinstance(recs[0]["rc"], int)
    assert recs[0]["level"] == "error"
    assert recs[1]["rc"] == 2 and recs[1]["level"] == "warn"
    assert recs[2]["rc"] == 2 and isinstance(recs[2]["rc"], int)   # 2.9 → int
    assert recs[2]["level"] == "info"                  # whitelisted fallback


def test_paths_honor_airmouse_home_dynamically(home, monkeypatch):
    assert cli_log.path().startswith(str(home))
    cli_log.log_command("there", 0)
    assert (home / "cli_log.jsonl").exists()
    sub = home / "elsewhere"
    sub.mkdir()
    monkeypatch.setenv("AIRMOUSE_HOME", str(sub))      # dynamic re-resolution
    assert cli_log.path().startswith(str(sub))
    cli_log.log_command("here", 0)
    assert (sub / "cli_log.jsonl").exists()
    assert [r["cmd"] for r in cli_log.read_recent(5)] == ["here"]


def test_log_command_returns_bool_and_stores_cmd(home, monkeypatch):
    assert cli_log.log_command("status", 0, msg="ok") is True
    assert cli_log.read_recent(1)[0]["cmd"] == "status"
    # I/O failure must be best-effort: home resolves to a FILE → False, no raise
    bad = home / "not_a_dir"
    bad.write_text("x", encoding="utf-8")
    monkeypatch.setenv("AIRMOUSE_HOME", str(bad))
    assert cli_log.log_command("nope", 1) is False
    assert cli_log.read_recent(3) == []                # never raises


def test_class_and_module_singleton_share_store():
    log = cli_log.CliLog()
    assert log.log_command("cls", 0) is True           # class API works
    assert log.append("cls2", 1) is True               # readable alias
    assert cli_log.log_command("mod", 0) is True       # module singleton
    assert [r["cmd"] for r in cli_log.read_recent(10)] == ["cls", "cls2", "mod"]
    assert log.path() == cli_log.path()                # same dynamic resolution
    assert json.loads(log.read_recent_json(1))[0]["cmd"] == "mod"


def test_read_recent_json_compact_and_valid():
    cli_log.log_command("js", 0, msg="hi")
    s = cli_log.read_recent_json(5)
    data = json.loads(s)
    assert isinstance(data, list) and len(data) == 1
    assert data[0]["cmd"] == "js" and data[0]["msg"] == "hi"
    assert '"cmd":"js"' in s                           # compact separators
    assert ", " not in s and '": ' not in s
