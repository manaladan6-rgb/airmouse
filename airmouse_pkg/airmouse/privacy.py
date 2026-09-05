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

v15.2 adds the PRIVACY MANIFEST: a complete, honest inventory of every
on-disk artifact AirMouse creates (see :data:`PRIVACY_MANIFEST` and
:func:`privacy_manifest`).  The lifecycle in ``airmouse.persistence``
(``memory_reset`` / ``memory_delete`` / ``memory_export`` /
``deletion_verifies``) is driven by this manifest, so the user-facing
commands now cover the REAL learning artifacts (intelligence/*,
calibration, gaze calibration, gestures, macros, lecture notes) and
not just the five named stores.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass
from typing import Any, Dict, List


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
        from . import paths as _paths
        candidates = [
            _paths.intelligence_model_file(),
            # pre-manifest legacy location (kept for honest detection of
            # data written by older builds with a split AIRMOUSE_HOME)
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


# ---------------------------------------------------------------------------
# v15.2 — PRIVACY MANIFEST: the honest inventory of every artifact
# ---------------------------------------------------------------------------

def _gestures_location() -> str:
    """Where the gesture registry actually persists custom mappings.

    ``gesture_registry`` reads ``$AIRMOUSE_GESTURES`` when set and
    otherwise uses ``<home>/gestures.json``; the manifest reflects that
    real precedence so lifecycle actions hit the file users actually
    wrote.
    """
    import os as _os

    env = _os.environ.get("AIRMOUSE_GESTURES", "").strip()
    if env:
        return _os.path.abspath(_os.path.expanduser(env))
    from . import paths as _paths
    return _paths.gestures_file()


def _store_location(name: str):
    """Callable resolving the named persistence store's live path."""
    def _resolve() -> str:
        from . import persistence as _persistence
        return _persistence.get_store(name).path
    return _resolve


def _dir_json_count(path) -> int:
    import glob as _glob
    import os as _os

    if not path or not _os.path.isdir(path):
        return 0
    try:
        return len(_glob.glob(_os.path.join(path, "*.json")))
    except OSError:
        return 0


#: One row per on-disk artifact AirMouse creates.  Fields:
#:   name         stable identifier used by the lifecycle + tests
#:   purpose      why the artifact exists (plain language)
#:   location     callable or str resolving the CURRENT path (dynamic —
#:                never cached, honors AIRMOUSE_HOME on every call)
#:   kind         "file" | "dir" | "store"
#:   data_type    honest description of what the bytes contain
#:   created_by / read_by / deleted_by / exported_by   honest actors
#:   user_learning True when the artifact holds data learned from or
#:                authored by the user — the lifecycle's target set.
#:                False = settings / third-party model / backup area.
PRIVACY_MANIFEST = [
    {
        "name": "twin_store",
        "purpose": "persistent digital-twin interaction statistics",
        "location": _store_location("twin"), "kind": "store",
        "data_type": "interaction metadata: counters, timestamps and "
                     "learned parameters (content-scrubbed)",
        "created_by": "persistence.PersistentStore.save (learning modules)",
        "read_by": "persistence.PersistentStore.load; memory status/export",
        "deleted_by": "memory_reset (backup+clear); memory_delete (remove)",
        "exported_by": "memory_export (stores bundle)",
        "user_learning": True,
    },
    {
        "name": "vocabulary_store",
        "purpose": "persistent learned-vocabulary statistics store",
        "location": _store_location("vocabulary"), "kind": "store",
        "data_type": "learned vocabulary terms, counts and corrections",
        "created_by": "persistence.PersistentStore.save (learning modules)",
        "read_by": "persistence.PersistentStore.load; memory status/export",
        "deleted_by": "memory_reset (backup+clear); memory_delete (remove)",
        "exported_by": "memory_export (stores bundle)",
        "user_learning": True,
    },
    {
        "name": "skills_store",
        "purpose": "persistent skill-usage statistics store",
        "location": _store_location("skills"), "kind": "store",
        "data_type": "skill usage counters and parameters",
        "created_by": "persistence.PersistentStore.save (learning modules)",
        "read_by": "persistence.PersistentStore.load; memory status/export",
        "deleted_by": "memory_reset (backup+clear); memory_delete (remove)",
        "exported_by": "memory_export (stores bundle)",
        "user_learning": True,
    },
    {
        "name": "workflows_store",
        "purpose": "persistent learned-workflow statistics store",
        "location": _store_location("workflows"), "kind": "store",
        "data_type": "learned action-sequence workflow patterns",
        "created_by": "persistence.PersistentStore.save (learning modules)",
        "read_by": "persistence.PersistentStore.load; memory status/export",
        "deleted_by": "memory_reset (backup+clear); memory_delete (remove)",
        "exported_by": "memory_export (stores bundle)",
        "user_learning": True,
    },
    {
        "name": "preferences_store",
        "purpose": "persistent in-app preference store",
        "location": _store_location("preferences"), "kind": "store",
        "data_type": "user preference values (settings, not content)",
        "created_by": "persistence.PersistentStore.save (setup/learning)",
        "read_by": "persistence.PersistentStore.load; memory status/export",
        "deleted_by": "memory_reset (backup+clear); memory_delete (remove)",
        "exported_by": "memory_export (stores bundle)",
        "user_learning": True,
    },
    {
        "name": "intelligence_memory",
        "purpose": "interaction memory of the intelligence plugin",
        "location": lambda: _intelligence_file("memory.json"),
        "kind": "file",
        "data_type": "interaction metadata (counts/timestamps; raw text "
                     "is scrubbed before any write)",
        "created_by": "IntelligencePlugin.save (airmouse.intelligence)",
        "read_by": "IntelligencePlugin.load",
        "deleted_by": "memory_reset (backup+remove); memory_delete",
        "exported_by": "memory_export (artifacts bundle)",
        "user_learning": True,
    },
    {
        "name": "intelligence_vocabulary",
        "purpose": "personal vocabulary of the intelligence plugin",
        "location": lambda: _intelligence_file("vocabulary.json"),
        "kind": "file",
        "data_type": "learned vocabulary tokens, frequencies and "
                     "correction pairs",
        "created_by": "IntelligencePlugin.save (airmouse.intelligence)",
        "read_by": "IntelligencePlugin.load",
        "deleted_by": "memory_reset (backup+remove); memory_delete",
        "exported_by": "memory_export (artifacts bundle)",
        "user_learning": True,
    },
    {
        "name": "intelligence_workflows",
        "purpose": "learned workflow patterns of the intelligence plugin",
        "location": lambda: _intelligence_file("workflows.json"),
        "kind": "file",
        "data_type": "learned action sequences and their frequencies",
        "created_by": "IntelligencePlugin.save (airmouse.intelligence)",
        "read_by": "IntelligencePlugin.load",
        "deleted_by": "memory_reset (backup+remove); memory_delete",
        "exported_by": "memory_export (artifacts bundle)",
        "user_learning": True,
    },
    {
        "name": "intelligence_selftune",
        "purpose": "self-tuning performance counters",
        "location": lambda: _intelligence_file("selftune.json"),
        "kind": "file",
        "data_type": "runtime performance counters and tuned parameters",
        "created_by": "IntelligencePlugin.save (SelfTuner export)",
        "read_by": "IntelligencePlugin.load (SelfTuner import)",
        "deleted_by": "memory_reset (backup+remove); memory_delete",
        "exported_by": "memory_export (artifacts bundle)",
        "user_learning": True,
    },
    {
        "name": "intelligence_model",
        "purpose": "serialized on-device personalization model",
        "location": lambda: _intelligence_file("model.bin"),
        "kind": "file",
        "data_type": "binary n-gram/personalization model weights "
                     "learned from interaction statistics",
        "created_by": "IntelligencePlugin.save (PersonalInteractionModel)",
        "read_by": "IntelligencePlugin.load (PersonalInteractionModel.load)",
        "deleted_by": "memory_reset (backup+remove); memory_delete",
        "exported_by": "memory_export (artifacts bundle, base64)",
        "user_learning": True,
    },
    {
        "name": "hand_calibration",
        "purpose": "adaptive hand reach-box calibration",
        "location": lambda: _calibration_file(),
        "kind": "file",
        "data_type": "hand reach-box screen-geometry ranges + speed/"
                     "tremor motion-profile EMAs (no raw frames)",
        "created_by": "AdaptiveCalibration.save (airmouse.calibration)",
        "read_by": "AdaptiveCalibration.load",
        "deleted_by": "memory_reset (backup+remove); memory_delete",
        "exported_by": "memory_export (artifacts bundle)",
        "user_learning": True,
    },
    {
        "name": "gaze_calibration",
        "purpose": "gaze-to-screen affine calibration",
        "location": lambda: _gaze_calibration_file(),
        "kind": "file",
        "data_type": "gaze→screen affine matrix + screen geometry + "
                     "quality statistics",
        "created_by": "GazeCalibration.save (airmouse.gaze_calibration)",
        "read_by": "GazeCalibration.load",
        "deleted_by": "memory_reset (backup+remove); memory_delete",
        "exported_by": "memory_export (artifacts bundle)",
        "user_learning": True,
    },
    {
        "name": "custom_gestures",
        "purpose": "user's custom gesture→action mappings",
        "location": _gestures_location, "kind": "file",
        "data_type": "user-authored gesture/input sequences and their "
                     "configured actions",
        "created_by": "GestureRegistry.save (user edits)",
        "read_by": "GestureRegistry.load (startup)",
        "deleted_by": "memory_reset (backup+remove); memory_delete",
        "exported_by": "memory_export (artifacts bundle)",
        "user_learning": True,
    },
    {
        "name": "macros",
        "purpose": "recorded macros (replayable action sequences)",
        "location": lambda: _macros_dir(), "kind": "dir",
        "data_type": "recorded timed input action traces "
                     "(click/scroll/move/wait event sequences)",
        "created_by": "MacroRecorder.save (airmouse.macros)",
        "read_by": "MacroPlayer.load; list_macros",
        "deleted_by": "memory_reset (backup+remove each *.json); "
                      "memory_delete; MacroPlayer delete_macro",
        "exported_by": "memory_export (artifacts bundle, per file)",
        "user_learning": True,
    },
    {
        "name": "lecture_notes",
        "purpose": "default export target for TeacherMode lecture notes",
        "location": lambda: _lecture_file(), "kind": "file",
        "data_type": "verbatim user notes: transcribed lecture/meeting "
                     "timeline content",
        "created_by": "TeacherMode.export_lecture (airmouse.modes)",
        "read_by": "the user (plain markdown file)",
        "deleted_by": "memory_reset (backup+remove); memory_delete",
        "exported_by": "memory_export (artifacts bundle)",
        "user_learning": True,
    },
    {
        "name": "config",
        "purpose": "user settings file",
        "location": lambda: _config_file(), "kind": "file",
        "data_type": "user settings and thresholds incl. the telemetry "
                     "flag (no learned content)",
        "created_by": "Config.save_defaults (airmouse.config)",
        "read_by": "Config.load at startup",
        "deleted_by": "not deleted by the lifecycle (user settings — "
                      "edit or remove manually)",
        "exported_by": "not exported by memory_export",
        "user_learning": False,
    },
    {
        "name": "hand_landmarker_model",
        "purpose": "MediaPipe hand-landmarker neural network (downloaded "
                   "once, on first run)",
        "location": lambda: _model_file(), "kind": "file",
        "data_type": "third-party model weights (no user data)",
        "created_by": "tracker.ensure_model (one-time download, "
                      "no user content)",
        "read_by": "HandTracker (MediaPipe)",
        "deleted_by": "not deleted by the lifecycle (re-downloadable "
                      "third-party binary, not user data)",
        "exported_by": "not exported by memory_export",
        "user_learning": False,
    },
    {
        "name": "tutorial_done",
        "purpose": "marker that the user finished the tutorial",
        "location": lambda: _tutorial_done_file(), "kind": "file",
        "data_type": "empty marker file (no user content)",
        "created_by": "main() after tutorial completion",
        "read_by": "main() at startup (skip tutorial)",
        "deleted_by": "not deleted by the lifecycle (a marker, not "
                      "learned data)",
        "exported_by": "not exported by memory_export",
        "user_learning": False,
    },
    {
        "name": "academy_progress",
        "purpose": "per-lesson progress of the Gesture Academy",
        "location": lambda: _academy_progress_file(), "kind": "file",
        "data_type": "lesson ids, pass counts and timestamps (no media, "
                     "no content)",
        "created_by": "academy.run_academy (live lesson pass)",
        "read_by": "academy.load_progress; teach/learn resume",
        "deleted_by": "memory_reset (backup+clear); memory_delete (remove)",
        "exported_by": "memory_export",
        "user_learning": True,
    },
    {
        "name": "onboarding_state",
        "purpose": "persisted first-run teaching state (v16.5 onboarding)",
        "location": lambda: _onboarding_file(), "kind": "file",
        "data_type": "onboarding phase, per-track completion + learner "
                     "stats (no media, no content)",
        "created_by": "teacher.OnboardingStore.save (teach/learn/first run)",
        "read_by": "teacher.OnboardingStore.load; first-run detection",
        "deleted_by": "memory_reset (backup+clear); memory_delete (remove)",
        "exported_by": "memory_export",
        "user_learning": True,
    },
    {
        "name": "personal_profile",
        "purpose": "personal interaction profile learned on-device",
        "location": lambda: _profile_dir(), "kind": "dir",
        "data_type": "bounded learned parameters: dwell/confidence "
                     "preferences, voice phrase counts, gaze quality, "
                     "frequent intents (no raw audio/video, no typed "
                     "content)",
        "created_by": "profile_store.ProfileStore.save (learning loop)",
        "read_by": "profile_store.ProfileStore.load; teacher adaptivity; "
                   "privacy status",
        "deleted_by": "memory_reset (backup+clear); memory_delete (remove)",
        "exported_by": "memory_export (stores bundle)",
        "user_learning": True,
    },
    {
        "name": "transcript_sessions",
        "purpose": "user-saved transcription sessions (explicit save only)",
        "location": lambda: _transcripts_dir(), "kind": "dir",
        "data_type": "text transcripts the user explicitly chose to save "
                     "(never written automatically)",
        "created_by": "transcribe_session save command (user action)",
        "read_by": "the user; transcribe export",
        "deleted_by": "not deleted automatically (user-owned copies)",
        "exported_by": "is the save output area",
        "user_learning": False,
    },
    {
        "name": "backups",
        "purpose": "pre-reset/pre-delete backups of user data",
        "location": lambda: _backups_dir(), "kind": "dir",
        "data_type": "byte copies of the artifacts above, taken before "
                     "memory_reset/memory_delete",
        "created_by": "memory_reset / memory_delete lifecycle",
        "read_by": "the user (manual restore)",
        "deleted_by": "never deleted automatically — backups are KEPT; "
                      "remove <home>/backups/ yourself for a full wipe",
        "exported_by": "not exported (duplicates of live data)",
        "user_learning": False,
    },
    {
        "name": "exports",
        "purpose": "workspace for user-directed export bundles",
        "location": lambda: _exports_dir(), "kind": "dir",
        "data_type": "portable JSON copies of stores/artifacts the user "
                     "explicitly exported",
        "created_by": "memory_export / store.export_to (user action)",
        "read_by": "the user",
        "deleted_by": "not deleted automatically (user-owned copies)",
        "exported_by": "is the export output area",
        "user_learning": False,
    },
]


def _intelligence_file(name: str) -> str:
    import os as _os

    from . import paths as _paths
    return _os.path.join(_paths.intelligence_dir(), name)


def _calibration_file() -> str:
    from . import paths as _paths
    return _paths.calibration_file()


def _gaze_calibration_file() -> str:
    from . import paths as _paths
    return _paths.gaze_calibration_file()


def _macros_dir() -> str:
    from . import paths as _paths
    return _paths.macros_dir()


def _lecture_file() -> str:
    from . import paths as _paths
    return _paths.lecture_file()


def _config_file() -> str:
    from . import paths as _paths
    return _paths.config_file()


def _model_file() -> str:
    from . import paths as _paths
    return _paths.model_file()


def _tutorial_done_file() -> str:
    from . import paths as _paths
    return _paths.tutorial_done_file()


def _backups_dir() -> str:
    from . import paths as _paths
    return _paths.backups_dir()


def _exports_dir() -> str:
    from . import paths as _paths
    return _paths.exports_dir()


def _academy_progress_file() -> str:
    from . import paths as _paths
    import os as _os
    return _os.path.join(_paths.airmouse_home(), "academy_progress.json")


def _onboarding_file() -> str:
    from . import paths as _paths
    return _paths.onboarding_file()


def _profile_dir() -> str:
    from . import paths as _paths
    return _paths.profile_dir()


def _transcripts_dir() -> str:
    from . import paths as _paths
    return _paths.transcripts_dir()


def privacy_manifest() -> list:
    """Resolve :data:`PRIVACY_MANIFEST` with live paths + existence flags.

    Every entry is resolved FRESH on every call (paths are dynamic, so
    an ``AIRMOUSE_HOME`` change is honored) and annotated with:

        path          current absolute location (or None on resolve error)
        exists        True when the file/directory exists right now
        size_bytes    file size (0 for dirs / missing files)
        entries       dir artifacts: number of *.json files inside
        resolve_error set when the location callable itself failed

    Never raises: a failing entry degrades to ``resolve_error`` while
    the rest of the manifest still resolves.
    """
    import os as _os

    resolved: list = []
    for entry in PRIVACY_MANIFEST:
        row = {
            "name": entry.get("name"),
            "purpose": entry.get("purpose"),
            "kind": entry.get("kind", "file"),
            "data_type": entry.get("data_type"),
            "created_by": entry.get("created_by"),
            "read_by": entry.get("read_by"),
            "deleted_by": entry.get("deleted_by"),
            "exported_by": entry.get("exported_by"),
            "user_learning": bool(entry.get("user_learning")),
            "path": None, "exists": False, "size_bytes": 0,
            "entries": None,
        }
        location = entry.get("location")
        try:
            row["path"] = (location() if callable(location)
                           else str(location))
        except Exception as exc:
            row["resolve_error"] = f"{type(exc).__name__}: {exc}"
            resolved.append(row)
            continue
        path = row["path"]
        try:
            if row["kind"] == "dir":
                row["exists"] = _os.path.isdir(path)
                row["entries"] = _dir_json_count(path)
            else:
                row["exists"] = _os.path.isfile(path)
                if row["exists"]:
                    row["size_bytes"] = int(_os.path.getsize(path))
        except OSError as exc:
            row["resolve_error"] = f"{type(exc).__name__}: {exc}"
        resolved.append(row)
    return resolved
