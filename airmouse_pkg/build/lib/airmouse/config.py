"""
Configuration — loads from ~/.airmouse/config.toml or uses defaults.
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
    """AirMouse configuration with sensible Iron Man defaults."""

    # Physics
    mass = 0.8
    stiffness_min = 120.0
    stiffness_max = 400.0
    damping_ratio = 0.85        # Slightly underdamped = smooth feel
    speed_threshold = 200.0

    # Iron Man finger tracking
    exp_power = 0.6             # Exponential curve power (<1 = amplified small moves)
    exp_scale = 3.0             # Overall sensitivity scale
    deadzone = 0.008            # Ignore tiny finger movements
    home_drift_rate = 0.02      # How fast home position follows hand drift

    # Momentum throw
    throw_friction = 0.92
    throw_min_speed = 800.0

    # Edge gravity
    edge_gravity_strength = 15.0
    edge_gravity_zone = 0.08

    # Jitter filter
    jitter_alpha = 0.3

    # Gesture thresholds
    pinch_threshold = 0.06

    # Click
    pinch_cooldown = 0.25

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
            "# AirMouse v2.0 Configuration — Iron Man Edition",
            "# Edit this file to customize your airmouse feel.",
            "",
            "[physics]",
            f"mass = {self.mass}",
            f"stiffness_min = {self.stiffness_min}",
            f"stiffness_max = {self.stiffness_max}",
            f"damping_ratio = {self.damping_ratio}",
            "",
            "[ironman]",
            f"exp_power = {self.exp_power}          # <1.0 = tiny finger = big cursor (Iron Man feel)",
            f"exp_scale = {self.exp_scale}           # Overall sensitivity",
            f"deadzone = {self.deadzone}             # Ignore tiny movements",
            f"home_drift_rate = {self.home_drift_rate}",
            "",
            "[momentum]",
            f"throw_friction = {self.throw_friction}   # 0.0=infinite slide 1.0=instant stop",
            f"throw_min_speed = {self.throw_min_speed}",
            "",
            "[edge_gravity]",
            f"strength = {self.edge_gravity_strength}",
            f"zone = {self.edge_gravity_zone}         # Fraction of screen with gravity",
            "",
            "[gesture]",
            f"pinch_threshold = {self.pinch_threshold}",
            f"pinch_cooldown = {self.pinch_cooldown}",
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

            if "physics" in data:
                p = data["physics"]
                self.mass = p.get("mass", self.mass)
                self.stiffness_min = p.get("stiffness_min", self.stiffness_min)
                self.stiffness_max = p.get("stiffness_max", self.stiffness_max)
                self.damping_ratio = p.get("damping_ratio", self.damping_ratio)

            if "ironman" in data:
                im = data["ironman"]
                self.exp_power = im.get("exp_power", self.exp_power)
                self.exp_scale = im.get("exp_scale", self.exp_scale)
                self.deadzone = im.get("deadzone", self.deadzone)
                self.home_drift_rate = im.get("home_drift_rate", self.home_drift_rate)

            if "momentum" in data:
                m = data["momentum"]
                self.throw_friction = m.get("throw_friction", self.throw_friction)
                self.throw_min_speed = m.get("throw_min_speed", self.throw_min_speed)

            if "edge_gravity" in data:
                eg = data["edge_gravity"]
                self.edge_gravity_strength = eg.get("strength", self.edge_gravity_strength)
                self.edge_gravity_zone = eg.get("zone", self.edge_gravity_zone)

            if "gesture" in data:
                g = data["gesture"]
                self.pinch_threshold = g.get("pinch_threshold", self.pinch_threshold)
                self.pinch_cooldown = g.get("pinch_cooldown", self.pinch_cooldown)

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
