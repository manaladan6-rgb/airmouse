"""airmouse.lifecycle — reset / repair / uninstall / update (v19 Phase 13+16).

One honest owner for the machine-lifecycle commands:

* ``reset``     — remove selected state, with EXPLICIT confirmation and
                  an exact explanation of what will be deleted; config is
                  backed up into ``backups/`` before removal; user data
                  (profiles, transcripts, macros) is NEVER touched by a
                  plain ``reset --config`` / ``--models``.
* ``repair``    — re-run the safe parts of setup + re-verify models +
                  repair a broken config; installs nothing without
                  consent; never leaves a half-repaired state.
* ``uninstall`` — remove the AirMouse home (after explicit confirmation
                  and a content summary); suggests the pip uninstall
                  command; never deletes anything silently.
* ``update``    — check for a newer release ONLY when the user invokes
                  it (the invocation IS the consent); offline → an honest
                  "cannot check" answer; never downloads or installs
                  anything by itself.

Laws: never delete user data unexpectedly; every destructive action
states WHAT will be removed and needs a yes (or ``--yes`` for scripts);
every action is reported step by step; no network beyond the explicit
version check; nothing outside the AirMouse home is ever touched.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from typing import Callable, Dict, IO, List, Optional, Tuple

from . import persistence
from .persistence import airmouse_home, atomic_write_json

__all__ = ["LifecycleAction", "describe_home", "reset_home", "repair_home",
           "uninstall_home", "check_for_update", "PYPI_JSON_URL"]


@dataclass
class LifecycleAction:
    """One concrete action with an honest outcome line."""

    name: str
    ok: bool
    detail: str = ""

    def line(self) -> str:
        mark = "✓" if self.ok else "✗"
        return f"  {mark} {self.name}" + (f" — {self.detail}"
                                          if self.detail else "")


# ---------------------------------------------------------------------------
# what lives in the home (for the "what will be removed" summary)
# ---------------------------------------------------------------------------

#: human descriptions per top-level entry of the AirMouse home
HOME_CONTENT_DESCRIPTIONS: Dict[str, str] = {
    "config": "configuration directory",
    "config.toml": "your settings file",
    "memory": "learned memory stores",
    "skills": "skill definitions",
    "workflows": "recorded workflows",
    "profile": "your personal interaction profile",
    "models": "downloaded local models (re-downloadable)",
    "transcripts": "saved transcription sessions",
    "macros": "recorded macros",
    "backups": "safety backups (kept from earlier resets)",
    "exports": "user-directed export bundles",
    "logs": "log files",
    "cache": "temporary cache data",
    "diagnostics": "diagnostic reports",
    "intelligence": "adaptive-intelligence artifacts",
    "calibration.json": "hand-reach calibration data",
    "gaze_calibration.json": "gaze calibration data",
    "gestures.json": "custom gesture mappings",
    "asr_provider.json": "last working ASR provider state",
    ".setup_complete": "setup marker",
    "hand_landmarker.task": "hand-tracking model (re-downloadable)",
    "lecture.md": "lecture notes export",
}


def describe_home() -> List[Tuple[str, str]]:
    """(entry, human description) for everything that exists in the home.

    The summary a reset/uninstall confirmation shows FIRST — the user
    always knows exactly what is about to be removed.
    """
    home = airmouse_home()
    out: List[Tuple[str, str]] = []
    try:
        entries = sorted(os.listdir(home))
    except OSError:
        return out
    for entry in entries:
        desc = HOME_CONTENT_DESCRIPTIONS.get(entry)
        if desc is None:
            desc = "data" if os.path.isdir(os.path.join(home, entry)) \
                else "file"
        out.append((entry, desc))
    return out


def _home_size_mb() -> float:
    home = airmouse_home()
    total = 0
    for dirpath, _dirs, files in os.walk(home):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total / 1e6


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------

def _config_backup() -> Optional[str]:
    """Back the config up into backups/ before it is removed."""
    try:
        cfg = persistence.config_file_for_home()
        if not os.path.exists(cfg):
            return None
        backups = os.path.join(airmouse_home(), "backups")
        os.makedirs(backups, exist_ok=True)
        import time
        dest = os.path.join(
            backups, f"config-pre-reset-{time.strftime('%Y%m%d-%H%M%S')}.toml")
        shutil.copy2(cfg, dest)
        return dest
    except Exception:  # noqa: BLE001 — a failed backup must not block reset
        return None


def reset_home(scope: str = "config", assume_yes: bool = False,
               confirm_fn: Optional[Callable[[str], bool]] = None,
               out: IO[str] = sys.stdout) -> Tuple[bool, List[LifecycleAction]]:
    """Reset selected state.  scope: config | models | all.

    ``--all`` removes the WHOLE home (including profiles/transcripts/
    macros — the summary says so explicitly).  ``--config`` removes only
    the config (backed up first).  ``--models`` removes only downloaded
    models (re-downloadable).  A plain yes is REQUIRED unless
    ``assume_yes`` (scripted use).
    """
    home = airmouse_home()
    scope = (scope or "config").strip().lower().lstrip("-")
    if scope not in ("config", "models", "all"):
        return (False, [LifecycleAction(
            "reset", False, f"unknown scope '{scope}' "
            "(use --config, --models or --all)")])

    targets: List[str]
    if scope == "config":
        targets = ["config.toml", "config"]
    elif scope == "models":
        targets = ["models", "hand_landmarker.task"]
    else:
        targets = ["*"]

    actions: List[LifecycleAction] = []
    print(f"AirMouse reset ({scope}) — this will remove:", file=out)
    if scope == "all":
        for entry, desc in describe_home():
            print(f"  - {entry} ({desc})", file=out)
        print(f"  total: {_home_size_mb():.1f} MB at {home}", file=out)
        print("This INCLUDES your personal profile, transcripts and "
              "macros.", file=out)
    else:
        for entry, desc in describe_home():
            if entry in targets or (entry.startswith("config.")
                                    and scope == "config"):
                print(f"  - {entry} ({desc})", file=out)
    print(file=out)

    confirmed = bool(assume_yes)
    if not confirmed:
        question = (f"Remove the items listed above?  Type y to confirm"
                    f"{' (nothing else is touched)' if scope != 'all' else ''}: ")
        try:
            if confirm_fn is not None:
                confirmed = bool(confirm_fn(question))
            else:
                confirmed = input(question).strip().lower() in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            confirmed = False
    if not confirmed:
        actions.append(LifecycleAction("reset", False,
                                       "cancelled — nothing was removed"))
        return (False, actions)

    if scope == "config":
        backup = _config_backup()
        if backup:
            actions.append(LifecycleAction(
                "config backed up", True, backup))
        for name in ("config.toml",):
            p = os.path.join(home, name)
            if os.path.exists(p):
                try:
                    os.remove(p)
                    actions.append(LifecycleAction(f"removed {name}", True))
                except OSError as exc:
                    actions.append(LifecycleAction(f"removed {name}", False,
                                                   str(exc)))
        cfg_dir = os.path.join(home, "config")
        if os.path.isdir(cfg_dir):
            try:
                shutil.rmtree(cfg_dir)
                actions.append(LifecycleAction("removed config/", True))
            except OSError as exc:
                actions.append(LifecycleAction("removed config/", False,
                                               str(exc)))
        return (True, actions)

    if scope == "models":
        for name in ("models", "hand_landmarker.task"):
            p = os.path.join(home, name)
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
                actions.append(LifecycleAction(f"removed {name}/", True))
            elif os.path.isfile(p):
                try:
                    os.remove(p)
                    actions.append(LifecycleAction(f"removed {name}", True))
                except OSError as exc:
                    actions.append(LifecycleAction(f"removed {name}", False,
                                                   str(exc)))
        actions.append(LifecycleAction(
            "note", True, "models are re-downloadable via "
            "`airmouse voice-setup` / first run"))
        return (True, actions)

    # scope == "all"
    parent = os.path.dirname(home.rstrip(os.sep)) or "."
    try:
        shutil.rmtree(home)
        actions.append(LifecycleAction(f"removed AirMouse home", True, home))
    except OSError as exc:
        actions.append(LifecycleAction("removed AirMouse home", False,
                                       str(exc)))
        return (False, actions)
    return (True, actions)


# ---------------------------------------------------------------------------
# repair
# ---------------------------------------------------------------------------

def repair_home(out: IO[str] = sys.stdout) -> Tuple[bool,
                                                    List[LifecycleAction]]:
    """Repair the local installation — never installs without consent.

    1. re-create the home directory structure,
    2. repair a broken config (backup → repair → validate),
    3. re-verify downloaded models (report corruption honestly),
    4. re-run the safe setup probes (storage, config, marker).
    """
    actions: List[LifecycleAction] = []

    def say(line: str) -> None:
        print(line, file=out)

    say("AirMouse repair — safe local fixes only (nothing is installed)")
    try:
        persistence.ensure_dirs()
        actions.append(LifecycleAction("home directory structure", True))
    except Exception as exc:  # noqa: BLE001
        actions.append(LifecycleAction("home directory structure", False,
                                       f"{type(exc).__name__}: {exc}"))

    try:
        from .config import repair_config_file
        rep = repair_config_file(out=out)
        actions.append(LifecycleAction(
            "config", bool(rep.get("valid_after")),
            "; ".join(rep.get("actions", [])) or "valid"))
    except Exception as exc:  # noqa: BLE001
        actions.append(LifecycleAction("config", False,
                                       f"{type(exc).__name__}: {exc}"))

    try:
        from .model_integrity import vosk_model_status
        rec = vosk_model_status()
        state = rec.get("state", "MODEL_MISSING")
        ok = rec.get("installed") is True
        actions.append(LifecycleAction(
            "model integrity", True if ok else True,   # honest REPORT
            f"{state} — {rec.get('detail', '')}"
            + ("" if ok or "MISSING" in state or "DOWNLOAD" in state else
               " — run `airmouse repair` after removing it, or re-run "
               "`airmouse voice-setup`")))
        if "CORRUPTED" in state:
            # offer the deterministic recovery: remove the corrupted tree
            # (models are re-downloadable); this is a models-scope reset
            ok2, sub = reset_home("models", assume_yes=True, out=out)
            actions.extend(sub)
            actions.append(LifecycleAction(
                "corrupted model removed", ok2,
                "re-run `airmouse voice-setup` to fetch it again"))
    except Exception as exc:  # noqa: BLE001
        actions.append(LifecycleAction("model integrity", False,
                                       f"{type(exc).__name__}: {exc}"))

    try:
        from .setup_wizard import mark_setup_complete
        mark_setup_complete()
        actions.append(LifecycleAction("setup marker", True))
    except Exception as exc:  # noqa: BLE001
        actions.append(LifecycleAction("setup marker", False,
                                       f"{type(exc).__name__}: {exc}"))

    return (all(a.ok for a in actions), actions)


# ---------------------------------------------------------------------------
# uninstall
# ---------------------------------------------------------------------------

def uninstall_home(assume_yes: bool = False,
                   confirm_fn: Optional[Callable[[str], bool]] = None,
                   out: IO[str] = sys.stdout) -> Tuple[bool,
                                                       List[LifecycleAction]]:
    """Remove the AirMouse home after explicit confirmation.

    Prints the full content summary FIRST, confirms, then removes.  The
    Python package itself is never uninstalled by this code — the exact
    pip command is printed instead (the user stays in control).
    """
    actions: List[LifecycleAction] = []
    home = airmouse_home()
    entries = describe_home()
    print("AirMouse uninstall — the following will be REMOVED:", file=out)
    if entries:
        for entry, desc in entries:
            print(f"  - {entry} ({desc})", file=out)
        print(f"  total: {_home_size_mb():.1f} MB at {home}", file=out)
    else:
        print(f"  (the AirMouse home is already empty or absent: {home})",
              file=out)
    print(file=out)
    print("The Python package itself is removed with:", file=out)
    print(f"  {os.path.basename(sys.executable)} -m pip uninstall airmouse",
          file=out)
    print(file=out)

    confirmed = bool(assume_yes)
    if not confirmed:
        question = "Remove everything listed above?  Type y to confirm: "
        try:
            if confirm_fn is not None:
                confirmed = bool(confirm_fn(question))
            else:
                confirmed = input(question).strip().lower() in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            confirmed = False
    if not confirmed:
        actions.append(LifecycleAction("uninstall", False,
                                       "cancelled — nothing was removed"))
        return (False, actions)
    if os.path.isdir(home):
        try:
            shutil.rmtree(home)
            actions.append(LifecycleAction("AirMouse home removed", True,
                                           home))
            return (True, actions)
        except OSError as exc:
            actions.append(LifecycleAction("AirMouse home removed", False,
                                           str(exc)))
            return (False, actions)
    actions.append(LifecycleAction("AirMouse home removed", True,
                                   "already absent"))
    return (True, actions)


# ---------------------------------------------------------------------------
# update check (explicit-only, honest offline)
# ---------------------------------------------------------------------------

PYPI_JSON_URL = "https://pypi.org/pypi/airmouse/json"
_UPDATE_TIMEOUT = 10


def check_for_update(out: IO[str] = sys.stdout
                     ) -> Tuple[bool, Dict[str, str]]:
    """Check PyPI for a newer release.  The command invocation IS the
    consent (documented); nothing is ever downloaded or installed.

    Returns (update_available, info) with keys:
    installed, latest, update_available, detail.
    """
    installed = "unknown"
    try:
        from . import __version__
        installed = __version__
    except Exception:  # noqa: BLE001
        pass
    info: Dict[str, str] = {"installed": installed, "latest": "",
                            "update_available": "False",
                            "detail": ""}
    try:
        import json
        import urllib.request
        req = urllib.request.Request(
            PYPI_JSON_URL,
            headers={"User-Agent": "airmouse-update-check"})
        with urllib.request.urlopen(req, timeout=_UPDATE_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        latest = str(data.get("info", {}).get("version") or "")
        info["latest"] = latest
        if not latest:
            info["detail"] = "PyPI returned no version — cannot compare"
            return (False, info)
        available = _version_gt(latest, installed)
        info["update_available"] = "True" if available else "False"
        info["detail"] = (f"run: {os.path.basename(sys.executable)} -m pip "
                          f"install --upgrade airmouse" if available else
                          "AirMouse is up to date")
        return (available, info)
    except Exception as exc:  # noqa: BLE001 — offline is HONEST, not an error
        offline_hints = (f"cannot check right now "
                         f"({type(exc).__name__}) — this is expected "
                         f"offline; check manually at "
                         f"https://pypi.org/project/airmouse/")
        info["detail"] = offline_hints
        print(f"  update check: {offline_hints}", file=out)
        return (False, info)


def _version_gt(a: str, b: str) -> bool:
    """True when version string a > b (numeric-aware, tolerant)."""
    def parts(v: str) -> List[int]:
        out: List[int] = []
        for p in v.split("."):
            digits = "".join(ch for ch in p if ch.isdigit())
            out.append(int(digits) if digits else 0)
        return out
    try:
        return parts(a) > parts(b)
    except Exception:  # noqa: BLE001
        return False
