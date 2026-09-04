"""
airmouse.intelligence.plugin — the OPTIONAL adaptive-intelligence facade.

v11.5 §4 contract: the AirMouse core must function normally when the
plugin is

    not installed / disabled / unavailable / corrupted / incompatible /
    out of memory / disabled for privacy / disabled for performance

This facade is the ONLY object the core touches.  Guarantees:

* the constructor never raises
* ``load()`` never raises — failures become documented states
* every method catches ALL exceptions internally and degrades to a
  safe no-op / empty answer
* learning can be paused or wiped by the user at any time
* everything is local, offline and bounded

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional, Sequence

from . import IntelligenceState

# artifact names (all inside ~/.airmouse/intelligence/)
_ART_MODEL = "model.bin"
_ART_MEMORY = "memory.json"
_ART_VOCAB = "vocabulary.json"
_ART_WORKFLOWS = "workflows.json"
_ART_SELFTUNE = "selftune.json"


class IntelligencePlugin:
    """Optional adaptive-intelligence facade (never raises)."""

    def __init__(self, config: Optional[Dict[str, Any]] = None,
                 base_dir: Optional[str] = None) -> None:
        cfg = dict(config or {})
        self.base_dir = base_dir or os.path.join(
            os.path.expanduser("~"), ".airmouse", "intelligence")
        self.enabled = bool(cfg.get("enabled", True))
        self.learning_enabled = bool(cfg.get("learning", True))
        self.memory_enabled = bool(cfg.get("memory_enabled", True))
        self.vocabulary_enabled = bool(cfg.get("vocabulary_enabled", True))
        self.workflow_learning = bool(cfg.get("workflow_learning", True))
        self.privacy_mode = bool(cfg.get("privacy_mode", False))
        self.capacity_bytes = int(cfg.get("model_capacity_bytes", 0) or 0)
        self.state = IntelligenceState.DISABLED
        self.last_error = ""
        self.load_seconds = 0.0
        self._loaded = False

        # components (filled by load(); all Optional by design)
        self.model = None
        self.memory = None
        self.vocabulary = None
        self.workflows = None
        self.discovery = None
        self.tuner = None
        self.personalization = None
        self.predictor = None
        self.assistant = None

        if self.enabled:
            self.load()

    # ── paths ────────────────────────────────────────────────────────────────

    def _path(self, name: str) -> str:
        return os.path.join(self.base_dir, name)

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def load(self) -> IntelligenceState:
        """Load all artifacts.  NEVER raises."""
        if not self.enabled:
            self.state = IntelligenceState.DISABLED
            return self.state
        t0 = time.perf_counter()
        try:
            from .model import PersonalInteractionModel
            from .memory import InteractionMemory
            from .vocabulary import PersonalVocabulary
            from .workflows import WorkflowStore, WorkflowDiscovery
            from .selftune import SelfTuner
            from .personalization import PersonalizationEngine
            from .prediction import Predictor
            from .workflows import ProactiveAssistant
        except ImportError as exc:
            self.state = IntelligenceState.UNAVAILABLE
            self.last_error = f"import_failed:{exc}"
            return self.state

        try:
            kwargs: Dict[str, Any] = {}
            if self.capacity_bytes > 0:
                kwargs["capacity_bytes"] = self.capacity_bytes
            try:
                self.model = PersonalInteractionModel.load(
                    self._path(_ART_MODEL), **kwargs)
            except FileNotFoundError:
                self.model = PersonalInteractionModel(**kwargs)
            self.memory = InteractionMemory.load(self._path(_ART_MEMORY))
            self.vocabulary = PersonalVocabulary.load(self._path(_ART_VOCAB))
            self.workflows = WorkflowStore.load(self._path(_ART_WORKFLOWS))
            self.discovery = WorkflowDiscovery()
            self.tuner = SelfTuner()
            try:
                with open(self._path(_ART_SELFTUNE), "r",
                          encoding="utf-8") as f:
                    import json
                    self.tuner.import_data(json.load(f))
            except FileNotFoundError:
                pass
            self.personalization = PersonalizationEngine()
            self.predictor = Predictor(self.model, self.vocabulary)
            self.assistant = ProactiveAssistant(self.predictor, self.workflows)
        except MemoryError:
            self.state = IntelligenceState.OUT_OF_MEMORY
            self.last_error = "load_out_of_memory"
            return self.state
        except Exception as exc:
            # version mismatch vs corruption
            from .model import ModelError
            if isinstance(exc, ModelError) and "incompatible" in str(exc):
                self.state = IntelligenceState.INCOMPATIBLE
            else:
                self.state = IntelligenceState.CORRUPTED
            self.last_error = f"load_failed:{exc}"
            return self.state

        self._apply_privacy_flags()
        self._loaded = True
        self.state = IntelligenceState.AVAILABLE
        self.load_seconds = time.perf_counter() - t0
        return self.state

    def _apply_privacy_flags(self) -> None:
        if self.model is not None and not self.learning_enabled:
            self.model.capacity_bytes = self.model.size_bytes()  # freeze growth
        if self.memory is not None:
            self.memory.enabled = self.memory_enabled and self.learning_enabled
            self.memory.set_privacy_mode(self.privacy_mode)
        if self.vocabulary is not None:
            self.vocabulary.enabled = (self.vocabulary_enabled
                                       and self.learning_enabled)
            if self.privacy_mode:
                self.vocabulary.pause_learning()
        if self.predictor is not None:
            self.predictor.enabled = self.learning_enabled

    # ── availability ───────────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        return (self.enabled and self.state == IntelligenceState.AVAILABLE
                and self._loaded)

    @property
    def learning_active(self) -> bool:
        return (self.available and self.learning_enabled
                and not self.privacy_mode)

    # ── learning events (from verified actions — §46 loop) ────────────────────

    def record_action(self, action: str, history: Sequence[str] = (),
                      success: bool = True) -> None:
        """Record a verified action (LearningEvent → Personal Memory)."""
        if not self.learning_active:
            return
        try:
            a = str(action or "")[:64]
            if a and self.model is not None:
                self.model.learn_action_step(list(history)[-2:], a)
            if self.memory is not None:
                self.memory.record(f"action:{a}", success=success)
            if self.discovery is not None and self.workflow_learning:
                self.discovery.observe_step(a)
        except MemoryError:
            self.state = IntelligenceState.OUT_OF_MEMORY
        except Exception as exc:
            self.last_error = f"record_action:{exc}"

    def record_command(self, command: str, hour: Optional[int] = None) -> None:
        if not self.learning_active:
            return
        try:
            c = str(command or "")[:64]
            if c and self.model is not None:
                self.model.learn_command(c, hour=hour)
            if self.memory is not None:
                self.memory.record(f"command:{c}", success=True)
        except Exception as exc:
            self.last_error = f"record_command:{exc}"

    def record_text(self, text: str) -> None:
        """Learn language statistics from committed text (privacy-scrubbed
        upstream by the transcription layer)."""
        if not self.learning_active:
            return
        try:
            if self.model is not None:
                self.model.learn_text(text)
        except Exception as exc:
            self.last_error = f"record_text:{exc}"

    def record_correction(self, raw: str, preferred: str) -> None:
        if not self.learning_active or self.vocabulary is None:
            return
        try:
            self.vocabulary.learn_correction(raw, preferred)
            self.vocabulary.learn_term(preferred)
        except Exception as exc:
            self.last_error = f"record_correction:{exc}"

    def observe_gesture(self, gesture: str, amplitude: float = 0.0,
                        speed: float = 0.0, duration: float = 0.0,
                        false_positive: bool = False) -> None:
        if not self.learning_active or self.personalization is None:
            return
        try:
            from .personalization import GestureSample
            self.personalization.gesture.observe(GestureSample(
                gesture=gesture, amplitude=amplitude, speed=speed,
                duration=duration, false_positive=false_positive))
        except Exception as exc:
            self.last_error = f"observe_gesture:{exc}"

    def observe_gaze(self, offset_x: float = 0.0, offset_y: float = 0.0,
                     dwell_seconds: float = 0.0, region: str = "",
                     false_positive: bool = False) -> None:
        if not self.learning_active or self.personalization is None:
            return
        try:
            from .personalization import GazeSample
            self.personalization.gaze.observe(GazeSample(
                offset_x=offset_x, offset_y=offset_y,
                dwell_seconds=dwell_seconds, region=region,
                false_positive=false_positive))
        except Exception as exc:
            self.last_error = f"observe_gaze:{exc}"

    def observe_voice_command(self, phrase: str, canonical: str = "") -> None:
        if not self.learning_active or self.personalization is None:
            return
        try:
            self.personalization.voice.observe_command(phrase, canonical)
        except Exception as exc:
            self.last_error = f"observe_voice:{exc}"

    # ── predictions (data only — never executed) ───────────────────────────────

    def predict_next_action(self, history: Sequence[str] = ()):
        if not self.available or self.predictor is None:
            return None
        try:
            return self.predictor.predict_next_action(history)
        except Exception as exc:
            self.last_error = f"predict_action:{exc}"
            return None

    def predict_command(self, hour: Optional[int] = None):
        if not self.available or self.predictor is None:
            return None
        try:
            return self.predictor.predict_command(hour=hour)
        except Exception as exc:
            self.last_error = f"predict_command:{exc}"
            return None

    def suggest_emoji(self, text: str, k: int = 3) -> List[Any]:
        if not self.available or self.predictor is None:
            return []
        try:
            return self.predictor.suggest_emoji(text, k=k)
        except Exception as exc:
            self.last_error = f"suggest_emoji:{exc}"
            return []

    def complete_text(self, prefix: str, k: int = 3) -> List[Any]:
        if not self.available or self.predictor is None:
            return []
        try:
            return self.predictor.complete_text(prefix, k=k)
        except Exception as exc:
            self.last_error = f"complete_text:{exc}"
            return []

    def suggestions(self, action_history: Sequence[str] = (),
                    hour: Optional[int] = None) -> List[Any]:
        if not self.available or self.assistant is None:
            return []
        try:
            return self.assistant.suggest(action_history, hour=hour)
        except Exception as exc:
            self.last_error = f"suggestions:{exc}"
            return []

    def apply_emoji_preference(self, text: str, emoji: str) -> None:
        """Learn that the user picked ``emoji`` in this context."""
        if not self.learning_active or self.model is None:
            return
        try:
            words = [w for w in str(text or "").lower().split() if w][:3]
            tag = " ".join(words)[:32] or "general"
            self.model.learn_emoji(tag, emoji)
        except Exception as exc:
            self.last_error = f"emoji_pref:{exc}"

    # ── privacy / lifecycle controls (§32) ─────────────────────────────────────

    def pause_learning(self) -> None:
        self.learning_enabled = False
        self._apply_privacy_flags()
        if self.state == IntelligenceState.AVAILABLE:
            self.state = IntelligenceState.LEARNING_PAUSED

    def resume_learning(self) -> None:
        self.learning_enabled = True
        self.privacy_mode = False
        if self.state in (IntelligenceState.LEARNING_PAUSED,
                          IntelligenceState.PRIVACY_PAUSED):
            self.state = IntelligenceState.AVAILABLE
        self._apply_privacy_flags()

    def set_privacy_mode(self, on: bool) -> None:
        self.privacy_mode = bool(on)
        self._apply_privacy_flags()
        if self.privacy_mode and self.state == IntelligenceState.AVAILABLE:
            self.state = IntelligenceState.PRIVACY_PAUSED
        elif not self.privacy_mode and self.state == IntelligenceState.PRIVACY_PAUSED:
            self.state = IntelligenceState.AVAILABLE

    def delete_learned_data(self) -> Dict[str, int]:
        """Wipe all learned artifacts (§32).  Returns per-store counts."""
        counts = {"model": 0, "memory": 0, "vocabulary": 0, "workflows": 0}
        try:
            if self.model is not None:
                counts["model"] = self.model.ngram.total_words
                kwargs = {}
                if self.capacity_bytes > 0:
                    kwargs["capacity_bytes"] = self.capacity_bytes
                from .model import PersonalInteractionModel
                self.model = PersonalInteractionModel(**kwargs)
            if self.memory is not None:
                counts["memory"] = self.memory.reset()
            if self.vocabulary is not None:
                counts["vocabulary"] = self.vocabulary.size
                self.vocabulary.reset()
            if self.workflows is not None:
                counts["workflows"] = len(self.workflows)
                self.workflows = type(self.workflows)()
            if self.discovery is not None:
                self.discovery.forget()
        except Exception as exc:
            self.last_error = f"delete_learned:{exc}"
        return counts

    def reset_personalization(self) -> None:
        if self.personalization is not None:
            try:
                self.personalization.reset_all()
            except Exception as exc:
                self.last_error = f"reset_personalization:{exc}"
        if self.tuner is not None:
            try:
                self.tuner.reset()
            except Exception as exc:
                self.last_error = f"reset_tuner:{exc}"

    def clear_history(self) -> int:
        if self.memory is None:
            return 0
        try:
            return self.memory.reset()
        except Exception:
            return 0

    def export_profile(self, path: str) -> bool:
        """Export the learned profile as one validated JSON bundle."""
        try:
            import json
            bundle = {
                "version": 1,
                "kind": "airmouse-intelligence-profile",
                "exported_at": time.time(),
                "memory": self.memory.export_data() if self.memory else {},
                "vocabulary": (self.vocabulary.export_data()
                               if self.vocabulary else {}),
                "workflows": (self.workflows.export_data()
                              if self.workflows else {}),
                "selftune": self.tuner.export_data() if self.tuner else {},
                "personalization": (self.personalization.export_data()
                                    if self.personalization else {}),
            }
            d = os.path.dirname(path)
            if d:
                os.makedirs(d, exist_ok=True)
            tmp = f"{path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(bundle, f, ensure_ascii=False, sort_keys=True)
            os.replace(tmp, path)
            return True
        except Exception as exc:
            self.last_error = f"export_profile:{exc}"
            return False

    def import_profile(self, path: str) -> Dict[str, int]:
        """Import a learned profile.  EVERY section is validated +
        scrubbed on import; malicious files are rejected per-section."""
        counts = {"memory": 0, "vocabulary": 0, "workflows": 0, "selftune": 0}
        try:
            import json
            with open(path, "r", encoding="utf-8") as f:
                bundle = json.load(f)
            if not isinstance(bundle, dict) or bundle.get("kind") not in (
                    "airmouse-intelligence-profile", None):
                return counts
            if self.memory is not None and isinstance(
                    bundle.get("memory"), dict):
                counts["memory"] = self.memory.import_data(bundle["memory"])
            if self.vocabulary is not None and isinstance(
                    bundle.get("vocabulary"), dict):
                counts["vocabulary"] = self.vocabulary.import_data(
                    bundle["vocabulary"])
            if self.workflows is not None and isinstance(
                    bundle.get("workflows"), dict):
                counts["workflows"] = self.workflows.import_data(
                    bundle["workflows"])
            if self.tuner is not None and isinstance(
                    bundle.get("selftune"), dict):
                counts["selftune"] = self.tuner.import_data(
                    bundle["selftune"])
        except Exception as exc:
            self.last_error = f"import_profile:{exc}"
        return counts

    # ── persistence ─────────────────────────────────────────────────────────────

    def save(self) -> Dict[str, int]:
        saved = {}
        if not self._loaded:
            return saved
        try:
            if self.model is not None:
                saved["model"] = self.model.save(self._path(_ART_MODEL))
            if self.memory is not None:
                saved["memory"] = self.memory.save(self._path(_ART_MEMORY))
            if self.vocabulary is not None:
                saved["vocabulary"] = self.vocabulary.save(
                    self._path(_ART_VOCAB))
            if self.workflows is not None:
                saved["workflows"] = self.workflows.save(
                    self._path(_ART_WORKFLOWS))
            if self.tuner is not None:
                import json
                payload = json.dumps(self.tuner.export_data(), sort_keys=True)
                tmp = f"{self._path(_ART_SELFTUNE)}.tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(payload)
                os.replace(tmp, self._path(_ART_SELFTUNE))
                saved["selftune"] = len(payload)
        except Exception as exc:
            self.last_error = f"save:{exc}"
        return saved

    # ── introspection ─────────────────────────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        st: Dict[str, Any] = {
            "state": self.state.value,
            "enabled": self.enabled,
            "learning_enabled": self.learning_enabled,
            "privacy_mode": self.privacy_mode,
            "load_seconds": round(self.load_seconds, 4),
            "last_error": self.last_error,
        }
        if self.model is not None:
            try:
                st["model"] = self.model.stats()
            except Exception:
                pass
        if self.memory is not None:
            st["memory_patterns"] = self.memory.size()
        if self.vocabulary is not None:
            st["vocabulary_terms"] = self.vocabulary.size
            st["vocabulary_corrections"] = self.vocabulary.correction_count
        if self.workflows is not None:
            st["workflows"] = len(self.workflows)
        if self.tuner is not None:
            st["tuned_parameters"] = {
                k: v for k, v in self.tuner.current.items()
                if abs(v - self.tuner.defaults.get(k, v)) > 1e-9}
        return st
