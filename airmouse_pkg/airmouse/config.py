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

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # noqa
    except ImportError:
        tomllib = None

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".airmouse")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.toml")


class Config:
    """AirMouse configuration with v3.2 Direct Tracking defaults."""

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
    telemetry_enabled = True         # rolling perf stats (fps/latency/counters)

    def save_defaults(self):
        """Save current config as TOML (creates template for user editing)."""
        if tomllib is None:
            return
        os.makedirs(CONFIG_DIR, exist_ok=True)
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
            f"telemetry_enabled = {str(self.telemetry_enabled).lower()}      # perf stats on shutdown",
        ]
        with open(CONFIG_PATH, "w") as f:
            f.write("\n".join(lines) + "\n")

    def load(self):
        """Load config from TOML file if it exists."""
        if tomllib is None or not os.path.exists(CONFIG_PATH):
            return
        try:
            with open(CONFIG_PATH, "rb") as f:
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
                self.telemetry_enabled = v9.get("telemetry_enabled", self.telemetry_enabled)

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
            print(f"  Warning: Config load error: {e}, using defaults")
