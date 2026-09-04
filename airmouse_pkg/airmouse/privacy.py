"""
airmouse.privacy — privacy architecture + OFFLINE/ONLINE/PRIVACY state
(v11.5 §31, §32).

The dashboard exposes:

    Learning:                ON/OFF
    Memory:                  ON/OFF
    Transcription history:   ON/OFF
    Vocabulary learning:     ON/OFF
    Workflow learning:       ON/OFF
    Telemetry:               OFF by default
    Cloud:                   OFF by default (there is NO cloud path)

And provides: delete learned data, reset model personalization, clear
interaction history, export learned profile, import learned profile.

The connection state machine is honest: AirMouse is offline-first; the
CLOUD state can never become active because no cloud capability exists.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class ConnectionState(enum.Enum):
    OFFLINE = "offline"      # default + guaranteed
    ONLINE = "online"        # OS may have internet; airmouse still local-only
    PRIVACY = "privacy"      # privacy mode: learning paused, history off


@dataclass
class PrivacyFlags:
    learning: bool = True
    memory: bool = True
    transcription_history: bool = True
    vocabulary_learning: bool = True
    workflow_learning: bool = True
    telemetry: bool = False          # OFF by default (§32)
    cloud: bool = False              # OFF by default; no cloud path exists
    intelligence_enabled: bool = True

    def __post_init__(self) -> None:
        # cloud is structurally impossible — never allow it on
        self.cloud = False


class PrivacyDashboard:
    """Owns the privacy flags and delegates destructive actions to the
    plugin.  All methods are safe no-ops without a plugin."""

    def __init__(self, plugin=None,
                 transcription_engine=None) -> None:
        self.plugin = plugin
        self.transcription = transcription_engine
        self.flags = PrivacyFlags()
        self.state = ConnectionState.OFFLINE
        self.audit_log: List[Dict[str, Any]] = []

    # -- flag setters ---------------------------------------------------------

    def set(self, name: str, value: bool) -> bool:
        if not hasattr(self.flags, name):
            return False
        if name == "cloud":
            return False        # structurally impossible
        setattr(self.flags, name, bool(value))
        self._apply()
        self._log(f"set {name}={bool(value)}")
        return True

    def set_privacy_mode(self, on: bool) -> None:
        if on:
            self.state = ConnectionState.PRIVACY
            self.flags.learning = False
            self.flags.transcription_history = False
        else:
            if self.state is ConnectionState.PRIVACY:
                self.state = ConnectionState.OFFLINE
            self.flags.learning = True
        if self.plugin is not None:
            try:
                self.plugin.set_privacy_mode(bool(on))
            except Exception:
                pass
        self._apply()
        self._log(f"privacy_mode={'on' if on else 'off'}")

    def _apply(self) -> None:
        p = self.plugin
        if p is None:
            return
        try:
            p.learning_enabled = self.flags.learning
            if hasattr(p, "memory") and p.memory is not None:
                p.memory.enabled = self.flags.memory and self.flags.learning
            if hasattr(p, "vocabulary") and p.vocabulary is not None:
                p.vocabulary.enabled = (self.flags.vocabulary_learning
                                        and self.flags.learning)
            if hasattr(p, "workflow_learning"):
                p.workflow_learning = self.flags.workflow_learning
        except Exception:
            pass
        t = self.transcription
        if t is not None:
            try:
                t.history_enabled = self.flags.transcription_history
            except Exception:
                pass

    # -- destructive user actions (§32) ------------------------------------------

    def delete_learned_data(self) -> Dict[str, int]:
        out = {}
        if self.plugin is not None:
            try:
                out = self.plugin.delete_learned_data()
            except Exception:
                out = {}
        self._log("delete_learned_data")
        return out

    def reset_model_personalization(self) -> bool:
        ok = False
        if self.plugin is not None:
            try:
                self.plugin.reset_personalization()
                ok = True
            except Exception:
                ok = False
        self._log("reset_personalization")
        return ok

    def clear_interaction_history(self) -> int:
        n = 0
        if self.plugin is not None:
            try:
                n = self.plugin.clear_history()
            except Exception:
                n = 0
        if self.transcription is not None:
            try:
                self.transcription.clear_history()
            except Exception:
                pass
        self._log("clear_history")
        return n

    def export_profile(self, path: str) -> bool:
        ok = False
        if self.plugin is not None:
            try:
                ok = bool(self.plugin.export_profile(path))
            except Exception:
                ok = False
        self._log(f"export_profile -> {path}")
        return ok

    def import_profile(self, path: str) -> Dict[str, int]:
        out: Dict[str, int] = {}
        if self.plugin is not None:
            try:
                out = self.plugin.import_profile(path) or {}
            except Exception:
                out = {}
        self._log(f"import_profile <- {path}")
        return out

    # -- status ---------------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        return {
            "connection_state": self.state.value,
            "flags": {k: bool(v) for k, v in vars(self.flags).items()},
            "plugin_state": (getattr(self.plugin, "state", None).value
                             if self.plugin is not None and
                             getattr(self.plugin, "state", None) is not None
                             else "absent"),
            "audit_entries": len(self.audit_log),
        }

    # -- audit (local only, bounded) ----------------------------------------------------

    def _log(self, event: str) -> None:
        self.audit_log.append({"ts": time.time(), "event": str(event)[:120]})
        if len(self.audit_log) > 200:
            del self.audit_log[:50]


# ---------------------------------------------------------------------------
# v15.1 hardening — one-call privacy report (pure addition; no existing
# behaviour above is changed by this function).
# ---------------------------------------------------------------------------

def privacy_report() -> dict:
    """Honest, one-call privacy posture report (v15.1).

    Sections:
        telemetry_state  actual telemetry flag, read through the public
                         config API (Config.load()); OFF is the default
                         and the report shows the real value
        network_state    offline module posture — local-only, no cloud
        storage          home path + per-store health
                         (persistence.memory_status())
        learned_data     which stores exist + record counts only —
                         never any store content
        model_state      local model availability (on-device artifacts)
        controls         the user commands that operate on all of this

    Never raises: every section degrades to an ``error`` entry on
    failure, so the report is always renderable.
    """
    import os as _os

    from . import offline as _offline
    from . import persistence as _persistence

    report: Dict[str, Any] = {}

    # -- telemetry (config's public API; load() reads the real file) --------
    telemetry: Dict[str, Any] = {"enabled": None, "default": "off"}
    try:
        from . import config as _config
        with _persistence.config_path_scope() as cfg_path:
            cfg = _config.Config()
            cfg.load()
        telemetry["enabled"] = bool(getattr(cfg, "telemetry_enabled", False))
        telemetry["default_in_code"] = (_config.Config.telemetry_enabled
                                        is False)
        telemetry["config_file"] = cfg_path
    except Exception as exc:
        telemetry["error"] = f"{type(exc).__name__}: {exc}"
    report["telemetry_state"] = telemetry

    # -- network posture (offline module) ------------------------------------
    network: Dict[str, Any] = {"posture": "local-only", "cloud": False,
                               "telemetry_upload": False}
    try:
        network["offline_gate_features"] = sorted(
            _offline.OfflineGate.NETWORK_FEATURES)
    except Exception as exc:
        network["error"] = f"{type(exc).__name__}: {exc}"
    report["network_state"] = network

    # -- storage (home + per-store health, no content) -----------------------
    try:
        report["storage"] = _persistence.memory_status()
    except Exception as exc:
        report["storage"] = {"error": f"{type(exc).__name__}: {exc}"}

    # -- learned data (existence + counts only) ------------------------------
    learned: Dict[str, Any] = {"content_included": False, "stores": {}}
    try:
        stores = (report.get("storage") or {}).get("stores") or {}
        for name, status in stores.items():
            learned["stores"][name] = {
                "exists": bool(status.get("exists")),
                "records": int(status.get("records") or 0)}
        home = _persistence.airmouse_home()
        learned["intelligence_artifacts_dir"] = _os.path.isdir(
            _os.path.join(home, "intelligence"))
    except Exception as exc:
        learned["error"] = f"{type(exc).__name__}: {exc}"
    report["learned_data"] = learned

    # -- model state (local model availability) ------------------------------
    model: Dict[str, Any] = {
        "kind": "on-device PersonalInteractionModel",
        "available": False, "path": None, "paths_checked": []}
    try:
        home = _persistence.airmouse_home()
        candidates = [
            _os.path.join(home, "intelligence", "model.bin"),
            _os.path.join(_os.path.expanduser("~"), ".airmouse",
                          "intelligence", "model.bin"),
        ]
        seen: List[str] = []
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.append(candidate)
            if _os.path.isfile(candidate):
                model["available"] = True
                model["path"] = candidate
        model["paths_checked"] = seen
        model["note"] = "the local model never downloads anything"
    except Exception as exc:
        model["error"] = f"{type(exc).__name__}: {exc}"
    report["model_state"] = model

    # -- user controls (the CLI contract) ------------------------------------
    report["controls"] = [
        "airmouse memory status",
        "airmouse memory export <path>",
        "airmouse memory reset",
        "airmouse memory delete",
        "airmouse privacy",
    ]
    return report
