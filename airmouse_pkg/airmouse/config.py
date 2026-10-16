"""
Configuration v5.0 — Voice + Kalman hybrid + Zoom + Calibration + Macros.

New in v5.0:
  - Voice control (speech recognition + 30 commands, normal/high/turbo sensitivity)
  - Hybrid One Euro + Kalman fusion cursor filter (adaptive speed blend)
  - Pinch-to-zoom gesture (hold pinch, move hand up/down = Ctrl+wheel zoom)
  - Adaptive calibration (learns the user's reach box + tremor + speed)
  - Macro recorder (record/replay gesture-action sequences)

New in v4.0:
  - One Euro Filter (Casiez et al. 2012) for adaptive cursor filtering
    - Adapts cutoff frequency to speed (smooth when slow, responsive when fast)
    - Beats any fixed EMA cascade — no lag vs. jitter tradeoff
  - Velocity prediction for sub-frame latency compensation
  - Angle-based gesture detection with hysteresis (no flapping)
  - Better thumb detection using joint angles

New in v3.2:
  - DIRECT tracking mode (default) — cursor follows finger 1:1
  - IRONMAN mode (legacy) — exponential finger-relative tracking
  - Light jitter filter for direct mode
  - Stiffer spring defaults for direct mode

New in v3.1:
  - Dual-stage jitter filter params
  - Velocity prediction params
  - Position smoothing params
  - Acceleration limiting
  - Gesture stability / transition cooldown
  - Precision mode toggle
"""

import os
import time
from typing import Any, Dict, Optional

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # noqa
    except ImportError:
        tomllib = None

from . import fileio

from . import paths as _paths

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".airmouse")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.toml")
_DEFAULT_CONFIG_PATH = CONFIG_PATH
_DEFAULT_CONFIG_DIR = CONFIG_DIR


def _resolved_config_dir() -> str:
    """Active config dir — dynamic unless explicitly overridden.

    The v15.2 ``AIRMOUSE_HOME`` split-brain fix made every artifact
    resolver dynamic *except* this one (config.py still resolved at
    import time), so ``AIRMOUSE_HOME=... airmouse doctor`` reported the
    storage home in one directory and the config in another.  Now:
    an explicit module-global override (tests, persistence backups)
    still wins; otherwise the path re-resolves fresh on every call via
    :mod:`airmouse.paths`.
    """
    global CONFIG_DIR, CONFIG_PATH
    if CONFIG_PATH != _DEFAULT_CONFIG_PATH or \
            CONFIG_DIR != _DEFAULT_CONFIG_DIR:
        return CONFIG_DIR  # explicit override (tests / persistence)
    home = _paths.airmouse_home()
    CONFIG_DIR = home
    CONFIG_PATH = os.path.join(home, "config.toml")
    return CONFIG_DIR


def _resolved_config_path() -> str:
    """Active config path (see :func:`_resolved_config_dir`)."""
    _resolved_config_dir()
    return CONFIG_PATH


# v19.0.2 — sections the loader understands (for `airmouse config validate`).
# Unknown top-level sections are a real error (they are silently ignored at
# load time, which is exactly how typos go unnoticed); unknown keys inside a
# known section are reported as warnings (a newer AirMouse may add keys).
KNOWN_SECTIONS = frozenset({
    "direct", "one_euro", "physics", "ironman", "jitter", "prediction",
    "smoothing", "momentum", "edge_gravity", "gesture", "precision",
    "camera", "audio", "ui", "voice", "kalman", "zoom", "calibration",
    "v9", "v10", "intelligence", "learning", "memory", "transcription",
    "dictation", "emoji", "accessibility", "workflow", "privacy",
})
_KNOWN_SECTION_KEYS = {
    "direct": {"jitter_alpha", "mirror_x", "movement_threshold",
               "pixel_deadzone", "smooth_alpha", "spring_alpha"},
    "one_euro": {"beta", "dcutoff", "mincutoff", "prediction_factor"},
    "physics": {"damping_ratio", "mass", "max_accel", "stiffness_max",
                "stiffness_min", "stiffness_smoothing"},
    "ironman": {"deadzone", "exp_power", "exp_scale", "home_drift_rate",
                "home_drift_rate_moving"},
    "jitter": {"macro_alpha", "micro_alpha"},
    "prediction": {"enabled", "factor", "max_correction", "min_confidence"},
    "smoothing": {"position_alpha"},
    "momentum": {"throw_friction", "throw_max_momentum", "throw_min_speed"},
    "edge_gravity": {"strength", "zone"},
    "gesture": {"action_confirm_frames", "confirm_frames", "pinch_cooldown",
                "pinch_threshold", "transition_cooldown"},
    "precision": {"power", "scale"},
    "camera": {"detection_confidence", "index", "tracking_confidence"},
    "voice": {"enabled", "mic_index", "sensitivity", "speak"},
    "kalman": {"enabled", "fusion", "measurement_noise", "process_noise",
               "speed_ref"},
    "zoom": {"enabled", "engage_hold", "gain", "max_ticks"},
    "calibration": {"adaptive_enabled", "save_every"},
    "v9": {"action_max_retries", "action_timeout", "confirmation_timeout",
           "fusion_mode", "gaze_blink_click", "gaze_dwell_time",
           "gaze_enabled", "gaze_long_blink_estop", "gaze_min_confidence",
           "intent_min_confidence", "max_actions_per_sec",
           "min_click_interval", "safety_level", "screen_ocr_enabled",
           "screen_refresh_interval", "stream_loss_grace"},
    "v10": {"browser_bridge_port", "browser_cdp_port", "browser_enabled",
            "dictation_max_chars", "gesture_allow_destructive",
            "gesture_min_confidence_caution", "gesture_min_confidence_safe",
            "gesture_registry_enabled", "gesture_sequences",
            "macro_max_steps", "offline", "rf_enabled", "rf_min_confidence",
            "selftune_apply", "telemetry_enabled", "two_hand",
            "voice_command_min_confidence", "voice_mode10",
            "wake_word_required", "perf_report_enabled", "teach_auto",
            "ready_panel"},
    "intelligence": {"enabled", "model_capacity_mb"},
    "learning": {"enabled", "self_tuning"},
    "memory": {"enabled", "max_patterns"},
    "transcription": {"enabled", "history", "language"},
    "dictation": {"auto_punctuation", "enabled"},
    "emoji": {"enabled", "max_suggestions"},
    "accessibility": {"profile"},
    "workflow": {"learning", "max_count"},
    "privacy": {"kind", "mode", "telemetry"},
}


class Config:
    """AirMouse configuration with v3.2 Direct Tracking defaults."""

    @classmethod
    def config_path(cls) -> str:
        """Active config.toml path (AIRMOUSE_HOME-aware). Never raises."""
        try:
            return _resolved_config_path()
        except Exception:
            return CONFIG_PATH

    @classmethod
    def validate_file(cls, path: Optional[str] = None):
        """Validate a config.toml. Returns (errors, warnings); never raises.

        errors   — file missing/unreadable, invalid TOML, unknown sections
        warnings — unknown keys inside known sections (may be from a
                   newer AirMouse; silently ignored by this version's
                   loader, which is worth telling the user)
        """
        errors: List[str] = []
        warnings: List[str] = []
        p = str(path) if path else cls.config_path()
        try:
            with open(p, "rb") as f:
                raw = f.read()
        except FileNotFoundError:
            return ([f"config file not found: {p}"], warnings)
        except OSError as e:
            return ([f"config file unreadable: {e}"], warnings)
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as e:
            errors.append(f"not valid UTF-8: {e}")
            return (errors, warnings)
        if tomllib is None:
            warnings.append("tomllib unavailable on this Python; "
                            "structure not checked")
            return (errors, warnings)
        try:
            data = tomllib.loads(raw.decode("utf-8"))
        except Exception as e:
            errors.append(f"invalid TOML: {e}")
            return (errors, warnings)
        if not isinstance(data, dict):
            errors.append("top level must be a TOML table")
            return (errors, warnings)
        known_top = KNOWN_SECTIONS | {"tracking_mode"}
        for key, val in data.items():
            if key not in known_top:
                errors.append(
                    f"unknown section [{key}] (silently ignored by the "
                    "loader — typo? see `airmouse config show` docs)")
                continue
            section_keys = _KNOWN_SECTION_KEYS.get(key)
            if section_keys and isinstance(val, dict):
                for k in val:
                    if k not in section_keys:
                        warnings.append(
                            f"[{key}] {k} is not recognized by this "
                            "version (ignored by the loader)")
        return (errors, warnings)

    # v16.5.1 §2: structured problem from the most recent load().
    # ``None`` = clean (or no file).  Never raise from the lenient path.
    last_load_problem: Optional[Dict[str, Any]] = None

    # Tracking mode: "direct" (1:1 finger-to-screen) or "ironman" (exponential delta)
    tracking_mode = "direct"

    # ═══ v10.0 — Universal Offline Interaction Engine ═══
    offline = False                    # TRUE OFFLINE mode: block network features
    voice_mode10 = "command"           # command | dictation | hybrid
    voice_command_min_confidence = 0.62
    wake_word_required = False
    dictation_max_chars = 500
    browser_enabled = False            # local browser bridge control (§11)
    browser_bridge_port = 17843        # localhost-only extension endpoint
    browser_cdp_port = 9222            # Chrome/Edge DevTools protocol port
    gesture_registry_enabled = False   # custom gesture mappings (§9)
    rf_enabled = False                 # RF-sensing modality (§16, optional HW)
    rf_min_confidence = 0.4

    # ═══ v11.5 — Adaptive Human-Computer Intelligence (§40) ═══
    # [intelligence]
    intelligence_enabled = True        # optional plugin master switch
    intelligence_model_capacity = 30 * 1024 * 1024   # ~30 MB budget (§5)
    # [learning]
    learning_enabled = True
    self_tuning_enabled = True         # bounded threshold adaptation
    # [memory]
    memory_enabled = True
    memory_max_patterns = 5000
    # [transcription]
    transcription_enabled = False      # live transcription session
    transcription_history = True       # keep local transcript history
    transcription_language = "en"
    # [dictation]
    dictation_enabled = False          # live voice-typing session
    dictation_auto_punctuation = True
    # [prediction]
    prediction_enabled = True          # suggestions only — never execution
    prediction_min_confidence = 0.5
    # [emoji]
    emoji_enabled = True
    emoji_max_suggestions = 3
    # [teacher] / [student] / [office] / [meeting]
    teacher_mode = False
    student_mode = False
    office_mode = False
    meeting_mode = False
    research_mode = False
    developer_mode = False
    # [accessibility]
    accessibility_profile = "hands-free"
    # [workflow]
    workflow_learning = True
    workflow_max_count = 200
    # ═══ v12 — Personal Interaction Twin + Temporal World Model (§2/§3) ═══
    # [twin]
    twin_enabled = True                # optional; core works perfectly without it
    twin_max_facts = 2000
    twin_decay_half_life_hours = 720.0  # 30 days
    # [temporal_world]
    temporal_world_enabled = True
    temporal_world_max_history = 128

    # ═══ v13 — Goal Hierarchy + Task Engine (§4/§5) ═══
    # [goals]
    goals_parser_enabled = True
    goals_interpreter_enabled = False   # optional adapter, off by default
    # [tasks]
    tasks_max_tasks = 100
    tasks_default_step_timeout = 30.0

    # ═══ v13.5 — Interaction Compression + Skills (§6) ═══
    # [skills]
    skills_discovery_enabled = True      # propose only; approval required
    skills_min_occurrences = 3
    skills_min_confidence = 0.6
    skills_max_count = 100

    # ═══ v14 — Self-Healing Recovery + Target Resolution (§7/§8) ═══
    # [recovery]
    recovery_max_rounds = 6
    recovery_max_retries = 2
    # [target_resolution]
    target_resolution_min_confidence = 0.35
    target_resolution_coordinate_fallback = False   # explicit flag (§6/§8)

    # ═══ v14.5 — AIP Protocol + Agent SDK (§9/§10/§11) ═══
    # [aip]
    aip_enabled = True
    aip_version = "1.0"
    aip_max_message_bytes = 262144

    # ═══ v15 — Agents/Permissions/DiTM/Marketplace/Licensing ═══
    # [agents]
    agents_enabled = True
    agents_max_agents = 32
    agents_default_lease_ttl = 30.0
    # [permissions]
    permissions_fail_closed = True      # ASK without human == NO
    # [ditm]
    ditm_enabled = True                 # DO IT WITH ME flagship flow
    # [onboarding]
    onboarding_entry = "automatic"      # voice|hands|eyes|keyboard|automatic|all
    # [marketplace]
    marketplace_enabled = True          # manifests are data; no code exec
    # [licensing]
    licensing_tier = "free"             # local, transparent, no crippling

    # [privacy]
    privacy_mode = False               # pause learning + history, wipe nothing
    telemetry_enabled = False          # OFF by default; nothing phones home

    # Physics (Ironman mode)
    mass = 0.8
    stiffness_min = 120.0
    stiffness_max = 400.0
    damping_ratio = 0.85        # Slightly underdamped = smooth feel
    speed_threshold = 200.0
    max_accel = 50000.0         # Acceleration limiter (prevents jerks)
    stiffness_smoothing = 0.3   # Smooth stiffness transition

    # Direct tracking mode physics — v4.1 GOD-TIER
    # Pure One Euro Filter + dead zone. No complications.
    direct_jitter_alpha = 0.55      # Legacy compat (One Euro overrides)
    direct_spring_alpha = 0.55     # Legacy compat
    direct_smooth_alpha = 0.55     # Legacy compat
    direct_movement_threshold = 0.003  # Tight dead zone — cursor only moves on real intent
    direct_pixel_deadzone = 1.0    # 1px — pixel-perfect
    direct_mirror_x = False       # NO mirror — tracker.py already flips the camera frame

    # One Euro Filter params (v4.1) — tuned for max accuracy
    one_euro_mincutoff = 1.2      # Hz — lower = smoother at rest (was 1.5)
    one_euro_beta = 1.5           # Speed coef — higher = more responsive (was 1.0)
    one_euro_dcutoff = 1.0        # Hz — derivative filter cutoff
    direct_prediction_factor = 0.0  # OFF — prediction adds complications

    # Trackpad mode (v4.2) — natural trackpad feel
    trackpad_mode = False         # True = tap=click, hold=drag, 2-finger=scroll

    # Iron Man finger tracking
    exp_power = 0.6             # Exponential curve power (<1 = amplified small moves)
    exp_scale = 3.0             # Overall sensitivity scale
    deadzone = 0.008            # Ignore tiny finger movements
    home_drift_rate = 0.02      # How fast home position follows hand drift (when still)
    home_drift_rate_moving = 0.005  # Slower drift when hand is moving

    # Dual-stage jitter filter
    jitter_micro_alpha = 0.45   # Stage 1: fast response, kills tremor
    jitter_macro_alpha = 0.25   # Stage 2: slow smoothing, silky output

    # Velocity prediction (reduces perceived latency)
    prediction_factor = 0.15    # How far ahead to predict
    prediction_max_correction = 0.02  # Max prediction offset

    # Position smoothing (final output stage)
    position_smooth_alpha = 0.75  # High = responsive, low = smooth

    # Momentum throw
    throw_friction = 0.92
    throw_min_speed = 800.0
    throw_max_momentum = 2000.0

    # Edge gravity
    edge_gravity_strength = 15.0
    edge_gravity_zone = 0.08

    # Gesture thresholds
    pinch_threshold = 0.07       # Relaxed for far-distance pinch detection
    gesture_confirm_frames = 3       # Movement gestures (point, palm, three) — faster activation
    gesture_action_confirm_frames = 4  # Action gestures (pinch, peace, fist, etc.)
    gesture_transition_cooldown = 0.12  # Seconds between different gesture switches
    gesture_stability_frames = 2     # Frames hand must be stable for action gestures

    # Click
    pinch_cooldown = 0.25

    # Precision mode (toggle with 'p' key)
    precision_mode = False
    precision_power = 1.0           # Linear curve = precise
    precision_scale = 1.0           # Reduced sensitivity

    # Mirror X for direct mode (camera is mirrored)
    mirror_x = True

    # Camera
    camera_index = 0
    detection_confidence = 0.6   # Lower = detect hand easier from far distance
    tracking_confidence = 0.5    # Tracking confidence (keep moderate for stability)

    # Performance
    target_fps = 30

    # Audio
    audio_enabled = True

    # UI
    show_camera = True
    show_hud = True

    # ═══ v5.0 — VOICE CONTROL ═══
    voice_enabled = False            # requires: pip install SpeechRecognition pyaudio
    voice_sensitivity = "high"       # "normal" | "high" | "turbo" (turbo = MAD mode)
    voice_mic_index = -1             # -1 = system default microphone
    voice_speak = True               # spoken confirmations (pyttsx3, optional)

    # ═══ v5.0 — HYBRID ONE EURO + KALMAN FILTER ═══
    kalman_enabled = True            # False = pure One Euro (v4.1 behavior)
    kalman_fusion = "adaptive"       # "adaptive" | "kalman" | "one_euro" | "average"
    kalman_process_noise = 1.0       # higher = snappier Kalman arm
    kalman_measurement_noise = 0.05  # lower = trust camera more
    kalman_speed_ref = 0.15          # normalized units/s crossover to One Euro

    # ═══ v5.0 — PINCH-TO-ZOOM ═══
    zoom_enabled = True              # hold pinch, move hand up/down = zoom
    zoom_engage_hold = 0.30          # seconds of pinch before zoom engages
    zoom_gain = 1.0                  # zoom speed multiplier
    zoom_max_ticks = 6               # max wheel ticks per frame

    # ═══ v5.0 — ADAPTIVE CALIBRATION ═══
    adaptive_calibration = True      # learns your reach box + tremor + speed
    calibration_save_every = 300     # autosave interval (frames)

    # ═══ v9.0 — MULTIMODAL / GAZE / FUSION / SAFETY ═══
    fusion_mode = "hand"             # hand | gaze | voice | fusion | hands_free | assist
    gaze_enabled = False             # webcam eye/gaze subsystem (FaceMesh iris)
    gaze_min_confidence = 0.55       # below this, gaze never triggers actions
    gaze_dwell_time = 1.0            # seconds of fixation before dwell-click
    gaze_blink_click = False         # blink = click (OFF by default: accident-prone)
    gaze_long_blink_estop = True     # long blink (~1.2s) trips the e-stop latch
    screen_refresh_interval = 0.5    # screen-understanding model cache (seconds)
    screen_ocr_enabled = False       # PRIVACY: OCR target detection is opt-in
    intent_min_confidence = 0.35     # drop intents below this confidence
    action_timeout = 2.0             # seconds per action attempt
    action_max_retries = 1           # safe retries per action
    safety_level = "normal"          # normal | careful | safe
    max_actions_per_sec = 8          # sliding-window action rate limit
    min_click_interval = 0.15        # seconds between click-class actions
    confirmation_timeout = 5.0       # sensitive-action confirmation expiry
    stream_loss_grace = 2.0          # camera/mic loss before SAFE_MODE
    macro_max_steps = 200            # semantic macro program guard
    perf_report_enabled = True       # LOCAL perf stats report on shutdown
                                     # (§21: network telemetry stays OFF —
                                     #  telemetry_enabled below is authoritative)

    # ═══ v15.2 — TWO-HAND FOUNDATION ═══
    two_hand = False                 # enables two-hand tracking; requires a camera and mediapipe multi-hand
    # v16 execution-spine flags (gesture actions route through gesture_spine.py)
    gesture_min_confidence_safe = 0.45      # confidence floor for SAFE actions
    gesture_min_confidence_caution = 0.60   # confidence floor for CAUTION actions
    gesture_allow_destructive = False       # NEVER true by default: Alt+F4 / macro replay stay refused
    gesture_sequences = True                # feed hand gestures to the custom-sequence registry
    selftune_apply = False                  # opt-in: let SelfTuner apply bounded parameter proposals

    # ═══ v16.5 — ADAPTIVE MULTIMODAL INTELLIGENCE (teaching layer) ═══
    teach_auto = True                # offer the first-run "Teach Me" experience
    ready_panel = True               # show the zero-learning-curve READY panel

    def save_defaults(self):
        """Save current config as TOML (creates template for user editing)."""
        if tomllib is None:
            return
        os.makedirs(_resolved_config_dir(), exist_ok=True)
        lines = [
            "# AirMouse v3.2 Configuration — Direct Tracking Edition",
            "# Edit this file to customize your airmouse feel.",
            "",
            f"tracking_mode = \"{self.tracking_mode}\"   # \"direct\" (1:1) or \"ironman\" (exponential)",
            "",
            "[direct]",
            f"jitter_alpha = {self.direct_jitter_alpha}     # Legacy (One Euro overrides)",
            f"spring_alpha = {self.direct_spring_alpha}      # Legacy",
            f"smooth_alpha = {self.direct_smooth_alpha}       # Legacy",
            f"movement_threshold = {self.direct_movement_threshold}  # Noise gate — ignore tiny moves",
            f"pixel_deadzone = {self.direct_pixel_deadzone}     # Don't move if < N pixels changed",
            f"mirror_x = {str(self.direct_mirror_x).lower()}            # Mirror X (tracker already flips)",
            "",
            "[one_euro]  # v4.0 One Euro Filter — adaptive cursor filtering",
            f"mincutoff = {self.one_euro_mincutoff}        # Hz — cutoff at zero speed (lower = smoother)",
            f"beta = {self.one_euro_beta}           # Speed coef (higher = more responsive)",
            f"dcutoff = {self.one_euro_dcutoff}        # Hz — derivative filter cutoff",
            f"prediction_factor = {self.direct_prediction_factor}   # Velocity lookahead (0=off, 1=full)",
            "",
            "[physics]  # Ironman mode physics",
            f"mass = {self.mass}",
            f"stiffness_min = {self.stiffness_min}",
            f"stiffness_max = {self.stiffness_max}",
            f"damping_ratio = {self.damping_ratio}",
            f"max_accel = {self.max_accel}          # Prevents sudden jerks",
            f"stiffness_smoothing = {self.stiffness_smoothing}  # Smooth stiffness transition",
            "",
            "[ironman]",
            f"exp_power = {self.exp_power}          # <1.0 = tiny finger = big cursor (Iron Man feel)",
            f"exp_scale = {self.exp_scale}           # Overall sensitivity",
            f"deadzone = {self.deadzone}             # Ignore tiny movements",
            f"home_drift_rate = {self.home_drift_rate}",
            f"home_drift_rate_moving = {self.home_drift_rate_moving}  # Slower drift when moving",
            "",
            "[jitter]",
            f"micro_alpha = {self.jitter_micro_alpha}   # Stage 1: fast, kills tremor",
            f"macro_alpha = {self.jitter_macro_alpha}   # Stage 2: slow, silky output",
            "",
            "[prediction]",
            f"factor = {self.prediction_factor}       # Velocity prediction strength",
            f"max_correction = {self.prediction_max_correction}",
            "",
            "[smoothing]",
            f"position_alpha = {self.position_smooth_alpha}  # Final output smoothing",
            "",
            "[momentum]",
            f"throw_friction = {self.throw_friction}   # 0.0=infinite slide 1.0=instant stop",
            f"throw_min_speed = {self.throw_min_speed}",
            f"throw_max_momentum = {self.throw_max_momentum}",
            "",
            "[edge_gravity]",
            f"strength = {self.edge_gravity_strength}",
            f"zone = {self.edge_gravity_zone}         # Fraction of screen with gravity",
            "",
            "[gesture]",
            f"pinch_threshold = {self.pinch_threshold}",
            f"pinch_cooldown = {self.pinch_cooldown}",
            f"confirm_frames = {self.gesture_confirm_frames}       # Movement gestures",
            f"action_confirm_frames = {self.gesture_action_confirm_frames}  # Action gestures",
            f"transition_cooldown = {self.gesture_transition_cooldown}  # Seconds between gesture switches",
            "",
            "[precision]",
            f"power = {self.precision_power}           # Linear = precise",
            f"scale = {self.precision_scale}           # Reduced sensitivity",
            "",
            "[camera]",
            f"index = {self.camera_index}",
            f"detection_confidence = {self.detection_confidence}",
            f"tracking_confidence = {self.tracking_confidence}",
            "",
            "[audio]",
            f"enabled = {str(self.audio_enabled).lower()}",
            "",
            "[ui]",
            f"show_camera = {str(self.show_camera).lower()}",
            f"show_hud = {str(self.show_hud).lower()}",
            "",
            "# ═══ v5.0 — Voice control (pip install SpeechRecognition pyaudio) ═══",
            "[voice]",
            f"enabled = {str(self.voice_enabled).lower()}",
            f"sensitivity = \"{self.voice_sensitivity}\"   # normal | high | turbo (turbo = MAD)",
            f"mic_index = {self.voice_mic_index}             # -1 = default microphone",
            f"speak = {str(self.voice_speak).lower()}               # spoken confirmations",
            "",
            "# ═══ v5.0 — Hybrid One Euro + Kalman fusion filter ═══",
            "[kalman]",
            f"enabled = {str(self.kalman_enabled).lower()}              # False = pure One Euro (v4.1 feel)",
            f"fusion = \"{self.kalman_fusion}\"      # adaptive | kalman | one_euro | average",
            f"process_noise = {self.kalman_process_noise}        # higher = snappier Kalman arm",
            f"measurement_noise = {self.kalman_measurement_noise}   # lower = trust camera more",
            f"speed_ref = {self.kalman_speed_ref}        # units/s crossover to One Euro",
            "",
            "# ═══ v5.0 — Pinch-to-zoom (hold pinch, move up/down) ═══",
            "[zoom]",
            f"enabled = {str(self.zoom_enabled).lower()}",
            f"engage_hold = {self.zoom_engage_hold}          # seconds of pinch before zoom engages",
            f"gain = {self.zoom_gain}              # zoom speed multiplier",
            f"max_ticks = {self.zoom_max_ticks}           # max wheel ticks per frame",
            "",
            "# ═══ v5.0 — Adaptive calibration (learns your hand) ═══",
            "[calibration]",
            f"adaptive_enabled = {str(self.adaptive_calibration).lower()}",
            f"save_every = {self.calibration_save_every}          # autosave interval (frames)",
            "",
            "[v9]  # v9.0 multimodal: gaze + fusion + intent + safety",
            f"fusion_mode = \"{self.fusion_mode}\"        # hand | gaze | voice | fusion | hands_free | assist",
            f"gaze_enabled = {str(self.gaze_enabled).lower()}          # webcam eye/gaze subsystem",
            f"gaze_min_confidence = {self.gaze_min_confidence}      # gaze never acts below this",
            f"gaze_dwell_time = {self.gaze_dwell_time}        # fixation seconds before dwell-click",
            f"gaze_blink_click = {str(self.gaze_blink_click).lower()}       # blink = click (accident-prone, off)",
            f"gaze_long_blink_estop = {str(self.gaze_long_blink_estop).lower()}   # long blink trips e-stop",
            f"screen_refresh_interval = {self.screen_refresh_interval}   # screen model cache seconds",
            f"screen_ocr_enabled = {str(self.screen_ocr_enabled).lower()}      # PRIVACY: OCR is opt-in only",
            f"intent_min_confidence = {self.intent_min_confidence}    # drop low-confidence intents",
            f"action_timeout = {self.action_timeout}        # seconds per action attempt",
            f"action_max_retries = {self.action_max_retries}      # safe retries per action",
            f"safety_level = \"{self.safety_level}\"         # normal | careful | safe",
            f"max_actions_per_sec = {self.max_actions_per_sec}     # sliding-window rate limit",
            f"min_click_interval = {self.min_click_interval}     # seconds between clicks",
            f"confirmation_timeout = {self.confirmation_timeout}   # sensitive-action confirmation expiry",
            f"stream_loss_grace = {self.stream_loss_grace}       # camera/mic loss before SAFE_MODE",
            "",
            "[v10]  # v10.0 universal offline interaction engine",
            f"offline = {str(self.offline).lower()}               # TRUE OFFLINE: block network-dependent features",
            f"voice_mode10 = \"{self.voice_mode10}\"       # command | dictation | hybrid",
            f"voice_command_min_confidence = {self.voice_command_min_confidence}",
            f"wake_word_required = {str(self.wake_word_required).lower()}",
            f"dictation_max_chars = {self.dictation_max_chars}",
            f"browser_enabled = {str(self.browser_enabled).lower()}        # local browser bridge control",
            f"browser_bridge_port = {self.browser_bridge_port}      # localhost-only extension endpoint",
            f"browser_cdp_port = {self.browser_cdp_port}        # CDP port (Chrome --remote-debugging-port)",
            f"gesture_registry_enabled = {str(self.gesture_registry_enabled).lower()}",
            f"rf_enabled = {str(self.rf_enabled).lower()}              # RF modality (idles without hardware)",
            f"rf_min_confidence = {self.rf_min_confidence}",
            f"macro_max_steps = {self.macro_max_steps}        # semantic macro guard",
            f"telemetry_enabled = {str(self.telemetry_enabled).lower()}      # §21 privacy: OFF by default; nothing phones home",
            f"perf_report_enabled = {str(self.perf_report_enabled).lower()}      # local-only perf report on shutdown",
            f"two_hand = {str(self.two_hand).lower()}               # v15.2 two-hand tracking (camera + mediapipe multi-hand)",
            f"gesture_min_confidence_safe = {float(self.gesture_min_confidence_safe)}",
            f"gesture_min_confidence_caution = {float(self.gesture_min_confidence_caution)}",
            f"gesture_allow_destructive = {str(self.gesture_allow_destructive).lower()}",
            f"gesture_sequences = {str(self.gesture_sequences).lower()}",
            f"selftune_apply = {str(self.selftune_apply).lower()}",
            f"teach_auto = {str(self.teach_auto).lower()}             # v16.5 offer first-run teaching",
            f"ready_panel = {str(self.ready_panel).lower()}            # v16.5 zero-learning-curve READY panel",
        ]
        # v16.5.1 §1 encoding contract: UTF-8 EXPLICITLY, atomic write.
        # (plain open(..., "w") used the Windows process default — cp1252
        # — so the template's em dash became byte 0x97 and tomllib then
        # failed with "can't decode byte 0x97 in position 30".)
        fileio.write_text_atomic(_resolved_config_path(), "\n".join(lines) + "\n")

    def load(self):
        """Load config from TOML file if it exists (lenient; loud on §2 error)."""
        self.last_load_problem = None
        if tomllib is None or not os.path.exists(_resolved_config_path()):
            return
        try:
            with open(_resolved_config_path(), "rb") as f:
                data = tomllib.load(f)

            # Top-level tracking_mode
            self.tracking_mode = data.get("tracking_mode", self.tracking_mode)

            if "direct" in data:
                d = data["direct"]
                self.direct_jitter_alpha = d.get("jitter_alpha", self.direct_jitter_alpha)
                self.direct_spring_alpha = d.get("spring_alpha", self.direct_spring_alpha)
                self.direct_smooth_alpha = d.get("smooth_alpha", self.direct_smooth_alpha)
                self.direct_movement_threshold = d.get("movement_threshold", self.direct_movement_threshold)
                self.direct_pixel_deadzone = d.get("pixel_deadzone", self.direct_pixel_deadzone)
                self.direct_mirror_x = d.get("mirror_x", self.direct_mirror_x)

            if "one_euro" in data:
                oe = data["one_euro"]
                self.one_euro_mincutoff = oe.get("mincutoff", self.one_euro_mincutoff)
                self.one_euro_beta = oe.get("beta", self.one_euro_beta)
                self.one_euro_dcutoff = oe.get("dcutoff", self.one_euro_dcutoff)
                self.direct_prediction_factor = oe.get("prediction_factor", self.direct_prediction_factor)

            if "physics" in data:
                p = data["physics"]
                self.mass = p.get("mass", self.mass)
                self.stiffness_min = p.get("stiffness_min", self.stiffness_min)
                self.stiffness_max = p.get("stiffness_max", self.stiffness_max)
                self.damping_ratio = p.get("damping_ratio", self.damping_ratio)
                self.max_accel = p.get("max_accel", self.max_accel)
                self.stiffness_smoothing = p.get("stiffness_smoothing", self.stiffness_smoothing)

            if "ironman" in data:
                im = data["ironman"]
                self.exp_power = im.get("exp_power", self.exp_power)
                self.exp_scale = im.get("exp_scale", self.exp_scale)
                self.deadzone = im.get("deadzone", self.deadzone)
                self.home_drift_rate = im.get("home_drift_rate", self.home_drift_rate)
                self.home_drift_rate_moving = im.get("home_drift_rate_moving", self.home_drift_rate_moving)

            if "jitter" in data:
                j = data["jitter"]
                self.jitter_micro_alpha = j.get("micro_alpha", self.jitter_micro_alpha)
                self.jitter_macro_alpha = j.get("macro_alpha", self.jitter_macro_alpha)

            if "prediction" in data:
                pr = data["prediction"]
                self.prediction_factor = pr.get("factor", self.prediction_factor)
                self.prediction_max_correction = pr.get("max_correction", self.prediction_max_correction)

            if "smoothing" in data:
                s = data["smoothing"]
                self.position_smooth_alpha = s.get("position_alpha", self.position_smooth_alpha)

            if "momentum" in data:
                m = data["momentum"]
                self.throw_friction = m.get("throw_friction", self.throw_friction)
                self.throw_min_speed = m.get("throw_min_speed", self.throw_min_speed)
                self.throw_max_momentum = m.get("throw_max_momentum", self.throw_max_momentum)

            if "edge_gravity" in data:
                eg = data["edge_gravity"]
                self.edge_gravity_strength = eg.get("strength", self.edge_gravity_strength)
                self.edge_gravity_zone = eg.get("zone", self.edge_gravity_zone)

            if "gesture" in data:
                g = data["gesture"]
                self.pinch_threshold = g.get("pinch_threshold", self.pinch_threshold)
                self.pinch_cooldown = g.get("pinch_cooldown", self.pinch_cooldown)
                self.gesture_confirm_frames = g.get("confirm_frames", self.gesture_confirm_frames)
                self.gesture_action_confirm_frames = g.get("action_confirm_frames", self.gesture_action_confirm_frames)
                self.gesture_transition_cooldown = g.get("transition_cooldown", self.gesture_transition_cooldown)

            if "precision" in data:
                pr = data["precision"]
                self.precision_power = pr.get("power", self.precision_power)
                self.precision_scale = pr.get("scale", self.precision_scale)

            if "camera" in data:
                c = data["camera"]
                self.camera_index = c.get("index", self.camera_index)
                self.detection_confidence = c.get("detection_confidence", self.detection_confidence)
                self.tracking_confidence = c.get("tracking_confidence", self.tracking_confidence)

            if "audio" in data:
                self.audio_enabled = data["audio"].get("enabled", self.audio_enabled)

            if "ui" in data:
                self.show_camera = data["ui"].get("show_camera", self.show_camera)
                self.show_hud = data["ui"].get("show_hud", self.show_hud)

            # ═══ v5.0 sections ═══
            if "voice" in data:
                v = data["voice"]
                self.voice_enabled = v.get("enabled", self.voice_enabled)
                self.voice_sensitivity = v.get("sensitivity", self.voice_sensitivity)
                self.voice_mic_index = v.get("mic_index", self.voice_mic_index)
                self.voice_speak = v.get("speak", self.voice_speak)

            if "kalman" in data:
                k = data["kalman"]
                self.kalman_enabled = k.get("enabled", self.kalman_enabled)
                self.kalman_fusion = k.get("fusion", self.kalman_fusion)
                self.kalman_process_noise = k.get("process_noise", self.kalman_process_noise)
                self.kalman_measurement_noise = k.get("measurement_noise", self.kalman_measurement_noise)
                self.kalman_speed_ref = k.get("speed_ref", self.kalman_speed_ref)

            if "zoom" in data:
                z = data["zoom"]
                self.zoom_enabled = z.get("enabled", self.zoom_enabled)
                self.zoom_engage_hold = z.get("engage_hold", self.zoom_engage_hold)
                self.zoom_gain = z.get("gain", self.zoom_gain)
                self.zoom_max_ticks = z.get("max_ticks", self.zoom_max_ticks)

            if "calibration" in data:
                cb = data["calibration"]
                self.adaptive_calibration = cb.get("adaptive_enabled", self.adaptive_calibration)
                self.calibration_save_every = cb.get("save_every", self.calibration_save_every)

            # ═══ v9.0 sections ═══
            if "v9" in data:
                v9 = data["v9"]
                self.fusion_mode = v9.get("fusion_mode", self.fusion_mode)
                self.gaze_enabled = v9.get("gaze_enabled", self.gaze_enabled)
                self.gaze_min_confidence = v9.get("gaze_min_confidence", self.gaze_min_confidence)
                self.gaze_dwell_time = v9.get("gaze_dwell_time", self.gaze_dwell_time)
                self.gaze_blink_click = v9.get("gaze_blink_click", self.gaze_blink_click)
                self.gaze_long_blink_estop = v9.get("gaze_long_blink_estop", self.gaze_long_blink_estop)
                self.screen_refresh_interval = v9.get("screen_refresh_interval", self.screen_refresh_interval)
                self.screen_ocr_enabled = v9.get("screen_ocr_enabled", self.screen_ocr_enabled)
                self.intent_min_confidence = v9.get("intent_min_confidence", self.intent_min_confidence)
                self.action_timeout = v9.get("action_timeout", self.action_timeout)
                self.action_max_retries = v9.get("action_max_retries", self.action_max_retries)
                self.safety_level = v9.get("safety_level", self.safety_level)
                self.max_actions_per_sec = v9.get("max_actions_per_sec", self.max_actions_per_sec)
                self.min_click_interval = v9.get("min_click_interval", self.min_click_interval)
                self.confirmation_timeout = v9.get("confirmation_timeout", self.confirmation_timeout)
                self.stream_loss_grace = v9.get("stream_loss_grace", self.stream_loss_grace)
            if "v10" in data:
                v10 = data["v10"]
                self.offline = v10.get("offline", self.offline)
                self.voice_mode10 = v10.get("voice_mode10", self.voice_mode10)
                self.voice_command_min_confidence = v10.get("voice_command_min_confidence", self.voice_command_min_confidence)
                self.wake_word_required = v10.get("wake_word_required", self.wake_word_required)
                self.dictation_max_chars = v10.get("dictation_max_chars", self.dictation_max_chars)
                self.browser_enabled = v10.get("browser_enabled", self.browser_enabled)
                self.browser_bridge_port = v10.get("browser_bridge_port", self.browser_bridge_port)
                self.browser_cdp_port = v10.get("browser_cdp_port", self.browser_cdp_port)
                self.gesture_registry_enabled = v10.get("gesture_registry_enabled", self.gesture_registry_enabled)
                self.rf_enabled = v10.get("rf_enabled", self.rf_enabled)
                self.rf_min_confidence = v10.get("rf_min_confidence", self.rf_min_confidence)
                self.macro_max_steps = v9.get("macro_max_steps", self.macro_max_steps)
                # v9 perf-report flag moved off the privacy telemetry name
                # (§21 defect fix: the two flags collided; perf stats are
                # LOCAL and must not control the privacy telemetry flag)
                self.perf_report_enabled = v9.get(
                    "perf_report_enabled", v9.get("telemetry_enabled",
                                                  self.perf_report_enabled))
                # v15.2 two-hand foundation (same boolean pattern as the
                # neighboring v9/v10 flags)
                self.two_hand = v10.get("two_hand", self.two_hand)
                self.gesture_min_confidence_safe = float(v10.get("gesture_min_confidence_safe", self.gesture_min_confidence_safe))
                self.gesture_min_confidence_caution = float(v10.get("gesture_min_confidence_caution", self.gesture_min_confidence_caution))
                self.gesture_allow_destructive = bool(v10.get("gesture_allow_destructive", self.gesture_allow_destructive))
                self.gesture_sequences = bool(v10.get("gesture_sequences", self.gesture_sequences))
                self.selftune_apply = bool(v10.get("selftune_apply", self.selftune_apply))

            # ═══ v11.5 sections (§40) — all backward compatible ═══
            if "intelligence" in data:
                i = data["intelligence"]
                self.intelligence_enabled = i.get("enabled", self.intelligence_enabled)
                cap = i.get("model_capacity_mb", None)
                if cap is not None:
                    try:
                        self.intelligence_model_capacity = int(float(cap) * 1024 * 1024)
                    except Exception:
                        pass
            if "learning" in data:
                l = data["learning"]
                self.learning_enabled = l.get("enabled", self.learning_enabled)
                self.self_tuning_enabled = l.get("self_tuning", self.self_tuning_enabled)
            if "memory" in data:
                m = data["memory"]
                self.memory_enabled = m.get("enabled", self.memory_enabled)
                self.memory_max_patterns = m.get("max_patterns", self.memory_max_patterns)
            if "transcription" in data:
                t = data["transcription"]
                self.transcription_enabled = t.get("enabled", self.transcription_enabled)
                self.transcription_history = t.get("history", self.transcription_history)
                self.transcription_language = t.get("language", self.transcription_language)
            if "dictation" in data:
                dd = data["dictation"]
                self.dictation_enabled = dd.get("enabled", self.dictation_enabled)
                self.dictation_auto_punctuation = dd.get("auto_punctuation", self.dictation_auto_punctuation)
            if "prediction" in data:
                pr = data["prediction"]
                self.prediction_enabled = pr.get("enabled", self.prediction_enabled)
                self.prediction_min_confidence = pr.get("min_confidence", self.prediction_min_confidence)
            if "emoji" in data:
                em = data["emoji"]
                self.emoji_enabled = em.get("enabled", self.emoji_enabled)
                self.emoji_max_suggestions = em.get("max_suggestions", self.emoji_max_suggestions)
            for _sec, _attr in (("teacher", "teacher_mode"), ("student", "student_mode"),
                                ("office", "office_mode"), ("meeting", "meeting_mode"),
                                ("research", "research_mode"), ("developer", "developer_mode")):
                if _sec in data:
                    setattr(self, _attr, bool(data[_sec].get("enabled", getattr(self, _attr))))
            if "accessibility" in data:
                a = data["accessibility"]
                self.accessibility_profile = a.get("profile", self.accessibility_profile)
            if "workflow" in data:
                wf = data["workflow"]
                self.workflow_learning = wf.get("learning", self.workflow_learning)
                self.workflow_max_count = wf.get("max_count", self.workflow_max_count)
            if "privacy" in data:
                pv = data["privacy"]
                self.privacy_mode = pv.get("mode", self.privacy_mode)
                self.telemetry_enabled = pv.get("telemetry", self.telemetry_enabled)

        except Exception as e:
            # v16.5.1 §2 — NEVER silently fall back from a broken user
            # config.  The problem is recorded on the instance (for
            # doctor/verify) and surfaced LOUDLY with the exact fix.
            self.last_load_problem = _describe_load_problem(e)
            _print_config_problem(self.last_load_problem)

    # -- honesty API (v16.5.1 §2/§4) ---------------------------------

    def load_problem(self) -> Optional[Dict[str, Any]]:
        """The structured problem from the most recent :meth:`load`.

        ``None`` when the configuration loaded clean (or no file
        existed).  Shape: ``{"kind", "message", "path", "fix"}``.
        """
        return self.last_load_problem


class ConfigLoadError(Exception):
    """Raised by :func:`load_config_strict` when the config is broken.

    ``.problem`` is the structured ``{"kind", "message", "path", "fix"}``
    dict; ``str(exc)`` is the full human-readable WHAT/WHY/FIX report.
    """

    def __init__(self, problem: Dict[str, Any]) -> None:
        super().__init__(_format_problem(problem))
        self.problem = problem


def load_config_strict(path: Optional[str] = None) -> dict:
    """Parse the config file STRICTLY; raise :class:`ConfigLoadError`.

    Used by doctor / verify / repair — the code paths that must surface
    a broken configuration instead of absorbing it into defaults.
    Returns the parsed document (``{}`` when no file exists).
    """
    p = path or _resolved_config_path()
    if tomllib is None or not os.path.exists(p):
        return {}
    try:
        with open(p, "rb") as f:
            return tomllib.load(f)
    except Exception as e:                                  # noqa: BLE001
        raise ConfigLoadError(_describe_load_problem(e)) from None


def _describe_load_problem(e: Exception) -> Dict[str, Any]:
    """Classify a config failure into the honest problem record."""
    kind = "unknown"
    if isinstance(e, UnicodeDecodeError):
        kind = "encoding"
    elif tomllib is not None and isinstance(e, tomllib.TOMLDecodeError):
        kind = "syntax"
    elif isinstance(e, OSError):
        kind = "io"
    return {
        "kind": kind,
        "message": str(e),
        "path": _resolved_config_path(),
        "fix": fileio.FIX_COMMAND,
    }


def _format_problem(problem: Dict[str, Any]) -> str:
    """The WHAT / WHY / FIX report block (doctor-style, §4)."""
    kind = problem.get("kind", "unknown")
    why = {
        "encoding": "The configuration file is not valid UTF-8 — it was "
                    "probably saved by a legacy Windows encoding (or a "
                    "non-UTF-8 editor).",
        "syntax": "The configuration file is not valid TOML.",
        "io": "The configuration file could not be read.",
    }.get(kind, "The configuration file could not be parsed.")
    return ("\n".join([
        "Configuration file could not be loaded.",
        f"  File: {problem.get('path')}",
        f"  Cause: {problem.get('message')}",
        f"  Why: {why}",
        f"  Action: run  {problem.get('fix')}",
    ]))


def _print_config_problem(problem: Dict[str, Any]) -> None:
    """LOUD, actionable warning — never a bare 'using defaults' (§2)."""
    print("=" * 64)
    print("  WARNING: your AirMouse configuration could NOT be loaded;")
    print("  defaults are being used for THIS session.  Your file was")
    print("  NOT modified and will be retried on the next start.")
    print(_format_problem(problem))
    print("=" * 64)


# ---------------------------------------------------------------------------
# v16.5.1 §2 — explicit, safe config repair (`airmouse setup --repair-config`)
# ---------------------------------------------------------------------------

def repair_config_file(path: Optional[str] = None, *, out: Any = None) -> Dict[str, Any]:
    """Repair a broken/badly-encoded config — explicitly, and safely.

    Steps (§2 contract): preserve → backup → repair → validate → write
    canonical UTF-8 → report exactly what happened.  NEVER destructive
    without a timestamped backup, never silent.

    Returns a report dict::

        {"path", "existed", "problem_kind", "recovered_encoding",
         "backup_path", "repaired", "rewrote_defaults", "valid_after",
         "actions": [str, ...], "detail": str}
    """
    import shutil

    w = out if out is not None else None

    def _emit(text: str = "") -> None:
        if w is not None:
            try:
                w.write(text + "\n")
                w.flush()
            except Exception:
                pass

    target = path or _resolved_config_path()
    actions: list = []
    report: Dict[str, Any] = {
        "path": target, "existed": False, "problem_kind": None,
        "recovered_encoding": None, "backup_path": None,
        "repaired": False, "rewrote_defaults": False,
        "valid_after": False, "actions": actions, "detail": "",
    }

    if not os.path.exists(target):
        # Nothing to repair; (re)write the canonical template so the
        # user has a fresh, valid, UTF-8 config.
        _emit(f"  no configuration file at {target} — writing a fresh "
              "UTF-8 template")
        Config().save_defaults()
        actions.append("wrote fresh UTF-8 template (no file existed)")
        report["valid_after"] = True
        report["detail"] = "fresh template written"
        return report

    report["existed"] = True
    raw = open(target, "rb").read()

    # 1) diagnose honestly
    dec_text, enc, bad_pos = fileio.decode_best_effort(raw)
    report["recovered_encoding"] = enc
    if enc == fileio.TEXT_ENCODING and bad_pos is None:
        actions.append("file already decodes as UTF-8; no re-encoding "
                       "needed")
    else:
        why = ("a UTF-8 BOM was present" if enc == "utf-8-sig"
               else f"content was recovered from legacy encoding {enc}")
        _emit(f"  encoding issue detected: {why}")
        actions.append(f"recovered text via {enc} "
                       f"(utf-8 failed at byte {bad_pos})"
                       if bad_pos is not None else f"recovered text via {enc}")

    # 2) backup — the original is preserved FOREVER, byte-for-byte
    backup_dir = os.path.join(os.path.dirname(target) or ".",
                              "backups")
    os.makedirs(backup_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_path = os.path.join(
        backup_dir, f"config-backup-{stamp}{os.extsep}toml")
    n = 0
    while os.path.exists(backup_path):
        n += 1
        backup_path = os.path.join(
            backup_dir, f"config-backup-{stamp}-{n}{os.extsep}toml")
    shutil.copy2(target, backup_path)
    report["backup_path"] = backup_path
    actions.append(f"original preserved at {backup_path}")
    _emit(f"  backup: {backup_path}")

    # 3) validate the recovered text as TOML
    parsed: Any = None
    parse_err: Optional[str] = None
    if tomllib is not None:
        try:
            parsed = tomllib.loads(dec_text)
        except Exception as exc:                            # noqa: BLE001
            parse_err = str(exc)
    if parsed is not None:
        # 4) rewrite the SAME content as canonical UTF-8 (no BOM)
        fileio.write_text_atomic(target, dec_text)
        actions.append("rewrote configuration as canonical UTF-8 "
                       "(content preserved)")
        report["repaired"] = True
        report["valid_after"] = True
        report["detail"] = f"re-encoded from {enc}; TOML valid; content unchanged"
        _emit("  repaired: same configuration, now canonical UTF-8 ✓")
        return report

    # 5) content itself is broken (or tomllib missing) — restoring
    #    defaults is only done AFTER the backup above, and reported.
    reason = parse_err or "TOML parser unavailable"
    _emit(f"  configuration content is not valid TOML: {reason}")
    _emit("  writing a fresh default template (your original is in the "
          "backup above; nothing was destroyed)")
    Config().save_defaults()
    actions.append("wrote fresh UTF-8 default template "
                   f"(previous file invalid TOML: {reason})")
    report["rewrote_defaults"] = True
    report["valid_after"] = True
    report["detail"] = f"defaults restored after backup ({reason})"
    return report
