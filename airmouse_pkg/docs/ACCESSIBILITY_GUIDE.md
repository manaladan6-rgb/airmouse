# AirMouse v11.5 — Accessibility Guide

Interaction profiles with modality fallback chains: no single sensor
is ever a mandatory point of failure.

## The 8 built-in profiles

`modes.ACCESSIBILITY_PROFILES` — each is an ordered chain of
modalities to try; the first **alive** modality wins:

| Profile | Chain (preferred → fallback) |
|---|---|
| `hands-free` (default) | voice → gaze → gesture → keyboard |
| `voice-first` | voice → keyboard → gesture → gaze |
| `gaze-first` | gaze → voice → gesture → keyboard |
| `voice+gaze` | voice → gaze → keyboard → gesture |
| `gesture+gaze` | gesture → gaze → keyboard → voice |
| `camera-free` | voice → keyboard → rf |
| `low-vision` | voice → keyboard → gaze |
| `reduced-mobility` | voice → gaze → keyboard |

## Custom chains

```python
from airmouse.modes import AccessibilityProfiles
ap = AccessibilityProfiles()
ap.set_custom_chain("one-hand", ["voice", "gesture", "keyboard"])
ap.set_profile("one-hand")
```

* Names are lowercase; a custom chain shadows a built-in of the same
  name.
* Only known modalities are accepted (`voice, gaze, gesture, keyboard,
  rf, hand, browser`), max 6 steps per chain.
* `set_profile` accepts built-ins or registered custom chains and
  returns `False` for unknown names (never guesses).

## Fallback resolution — how it works

`AccessibilityProfiles.resolve(health)` walks the chain and keeps the
modalities reported **alive** by the v10 `SensorHealth` freshness
tracker (the same mechanism that drives the v10 hands-free combo
ladder). With no health provider, all chain entries are assumed
available. The result is the alive-subset in preference order:

```
profile "hands-free": voice → gaze → gesture → keyboard
  voice stale (no mic)      → gaze → gesture → keyboard   (falls through)
  gaze also stale           → gesture → keyboard
  camera lost entirely      → keyboard                    (still functional)
```

This composes with the v10 degradation ladder: `hands_free.effective_
combo()` computes the largest alive sensor subset, and the
accessibility chain chooses which modality *leads*. The design rule
is the same in both: **degrade, never dead-end** — a missing camera or
microphone reduces capability, it never bricks interaction.

## Configuration

`~/.airmouse/config.toml`:

```toml
[accessibility]
accessibility_profile = "hands-free"   # built-in name or custom chain id
```

## Related v11.5 accessibility surfaces

* **Universal text control** (`docs/` — text ops): 16 text operations
  (TYPE/SELECT/DELETE/REPLACE/COPY/PASTE/UNDO/REDO/CUT/MOVE/
  CAPITALIZE/LOWERCASE/UPPERCASE/FORMAT/NEW_LINE/NEW_PARAGRAPH) that
  always target the focused text field via keyboard fallback — never
  coordinate-dependent, so they work without precise pointing.
* **Contextual commands**: "click that", "close it", "save that"…
  resolved against the world model; low confidence ⇒ the assistant
  asks instead of guessing.
* **Voice aliases**: `VoiceProfile` learns your phrasing
  ("launch browser" → "open browser") so the system adapts to you
  instead of the reverse.
* **Self-tuning**: `gaze_dwell_time`, `gesture_confirm_frames`,
  `voice_command_min_confidence` etc. adapt within hard bounds
  (`docs/INTELLIGENCE_GUIDE.md` §5).

## Honest verification status

The fallback *logic* is simulation-verified (deterministic
`SensorHealth` fakes in the test suite). Real sensor behavior — actual
webcam gaze quality, microphone pickup, RF hardware — is
**hardware-unverified**; on real machines expect to tune thresholds
(the self-tuner's bounded proposals are the intended mechanism).
See `VERIFICATION_REPORT.md` for the full SIMULATION vs PHYSICAL table.
