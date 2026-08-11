
---
Task ID: 1
Agent: main
Task: Upgrade AirMouse from v3.0.1 to v3.1.0 — fix crash, improve smoothness, intentionality, usefulness

Work Log:
- Fixed keyboard.py crash: pynput Key.media attributes replaced with proper ctypes SendInput using INPUT_UNION struct for x64 compatibility
- Upgraded physics.py: added DualStageJitterFilter (micro+macro), VelocityPredictor (Kalman-like lookahead), PositionSmoother (final EMA), acceleration limiting, smooth deadzone transition (cubic easing), frame-time compensation, stability-gated HomePosition
- Upgraded gestures.py: finger confidence scoring, GestureStateMachine with action vs movement confirm frames (5 vs 4), transition cooldown (0.15s), stability gating, progress tracking for HUD
- Upgraded config.py: all new params (jitter_micro/macro_alpha, prediction_factor/max_correction, position_smooth_alpha, max_accel, stiffness_smoothing, action_confirm_frames, transition_cooldown, precision mode params)
- Upgraded audio.py: mode_enter/exit sounds (ascending/descending tones), gesture_confirm blip, precision_toggle dual-tone, recalibrate confirmation sound
- Upgraded keyboard.py: proper ctypes SendInput with INPUT_UNION struct, added copy/paste/cut/select_all/alt_tab actions
- Upgraded __main__.py: precision mode toggle [p] key, gesture confirm progress ring in HUD, confidence bar, mode enter/exit sounds, volume/brightness debounce (0.15s/0.3s), hand-absent grace period (5 frames), velocity prediction pipeline, position smoothing, all new physics wired
- Bumped version to 3.1.0, built wheel successfully

Stage Summary:
- airmouse v3.1.0 wheel built at /home/z/my-project/download/airmouse-3.1.0-py3-none-any.whl
- All new features verified working
- No crashes — all keyboard actions use ctypes SendInput for media keys
- Smoothness: dual-stage jitter + velocity prediction + position smoothing + acceleration limiting
- Intentionality: 5-frame action confirm, transition cooldown, stability gating, gesture progress feedback
- Usefulness: precision mode, mode enter/exit sounds, debounce, more keyboard actions
