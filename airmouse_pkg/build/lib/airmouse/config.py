"""
Configuration v3.2 — loads from ~/.airmouse/config.toml or uses defaults.

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

    # Physics (Ironman mode)
    mass = 0.8
    stiffness_min = 120.0
    stiffness_max = 400.0
    damping_ratio = 0.85        # Slightly underdamped = smooth feel
    speed_threshold = 200.0
    max_accel = 50000.0         # Acceleration limiter (prevents jerks)
    stiffness_smoothing = 0.3   # Smooth stiffness transition

    # Direct tracking mode physics
    direct_jitter_alpha = 0.75      # Light jitter filter (high = responsive)
    direct_spring_alpha = 0.55     # Stiff EMA spring (high = fast tracking, low = smooth)
    direct_smooth_alpha = 0.85     # Light final smoothing
    direct_mirror_x = True         # Mirror X so cursor follows hand naturally

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
    pinch_threshold = 0.06
    gesture_confirm_frames = 4       # Movement gestures (point, palm, three)
    gesture_action_confirm_frames = 5  # Action gestures (pinch, peace, fist, etc.)
    gesture_transition_cooldown = 0.15  # Seconds between different gesture switches
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
    detection_confidence = 0.7
    tracking_confidence = 0.5

    # Performance
    target_fps = 30

    # Audio
    audio_enabled = True

    # UI
    show_camera = True
    show_hud = True

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
            f"jitter_alpha = {self.direct_jitter_alpha}     # Light jitter filter (high = responsive)",
            f"spring_alpha = {self.direct_spring_alpha}      # Stiff EMA spring (high = fast, low = smooth)",
            f"smooth_alpha = {self.direct_smooth_alpha}       # Light final smoothing",
            f"mirror_x = {str(self.direct_mirror_x).lower()}            # Mirror X for natural cursor direction",
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
                self.direct_mirror_x = d.get("mirror_x", self.direct_mirror_x)

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

        except Exception as e:
            print(f"  Warning: Config load error: {e}, using defaults")
