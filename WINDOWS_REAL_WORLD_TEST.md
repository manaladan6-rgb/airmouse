# WINDOWS_REAL_WORLD_TEST.md — AirMouse v16.5.0 real-world test (Windows 10/11)

**Who this is for:** any Windows 10 or Windows 11 user. No programming
knowledge needed. Every command is copy-pasteable into Command Prompt.

**Why this file exists (honesty):** AirMouse v16.5.0 was verified in a
headless Linux build sandbox — **1889 automated tests green (2 honest
skips), `airmouse verify` 12/12 automated checks PASS, simulation
suite green, but NO physical hardware was tested there** (no webcam,
no microphone, no display, no Windows). The machine cannot click a
real button or hear a real voice, cannot watch you take the v16.5
teaching tour, and has never heard a real ASR engine. **You are the
hardware test.** This document walks you through it, step by step,
and ends with how to turn your results into a bug report we can act
on.

**How to read each step:** every step has six fields:

| Field | Meaning |
|---|---|
| **COMMAND** | the exact text to type (or the settings path to open) |
| **WHAT TO DO** | your actions, in plain words |
| **WHAT SHOULD HAPPEN** | the expected behaviour |
| **PASS** | what counts as success |
| **FAIL** | what counts as a problem |
| **HOW TO FIX** | what to try before giving up |

**Golden rule:** write down the step number and one line of what you
saw for every step — you will paste that into the final report
(Step 19).

---

## Step 1 — Open Command Prompt

- **COMMAND:** Start button → type `cmd` → click **Command Prompt**
- **WHAT TO DO:** open it like any normal program. A black window with
  a `C:\Users\YourName>` prompt appears.
- **WHAT SHOULD HAPPEN:** you see a blinking cursor after the prompt.
- **PASS:** the window is open and accepts typing.
- **FAIL:** "cmd" is not found, or the window closes instantly.
- **HOW TO FIX:** try Start → type `Command Prompt`; if it still
  fails, search "enable Command Prompt Windows 11" — this is a
  Windows problem, not an AirMouse problem.

## Step 2 — Check Python

- **COMMAND:** `python --version`
- **WHAT TO DO:** type it (or copy-paste) and press Enter.
- **WHAT SHOULD HAPPEN:** something like `Python 3.12.5` (any 3.9 or
  newer is fine).
- **PASS:** version printed and starts with `3.9` or higher.
- **FAIL:** `Python was not found`, or the Microsoft Store opens, or
  version is below 3.9.
- **HOW TO FIX:** install Python from https://www.python.org/downloads/
  and tick **"Add python.exe to PATH"** in the installer's first
  screen. Close and reopen Command Prompt afterwards, then repeat this
  step.

## Step 3 — Install AirMouse

- **COMMAND:** `python -m pip install airmouse`
- **WHAT TO DO:** paste and press Enter. Wait for it to finish
  (1-3 minutes; it downloads OpenCV, MediaPipe and numpy).
- **WHAT SHOULD HAPPEN:** progress bars, then a final line like
  `Successfully installed airmouse-16.5.0 ...`.
- **PASS:** "Successfully installed" appears and the version is
  16.5.0 or newer.
- **FAIL:** red error text (no internet, permission denied, or
  "pip is not recognized").
- **HOW TO FIX:** check your internet; retry the same command once;
  if permission errors persist, run Command Prompt "as administrator"
  (right-click → Run as administrator) and repeat.

## Step 4 — Guided setup

- **COMMAND:** `airmouse setup`
- **WHAT TO DO:** run it. When it asks
  `Install missing required packages? [Y/N]`, press **Y** if you want
  it to install anything that is missing (it will show you exactly
  what). Nothing is ever installed without your consent.
- **WHAT SHOULD HAPPEN:** 11 numbered checks (environment, packages,
  storage, configuration, camera, microphone, browser, voice extras,
  keyboard/mouse, smoke test, finish), then a report ending with
  "What remains to test (needs you + hardware)".
- **PASS:** no FAIL lines; camera/microphone rows may honestly say
  ACTION_REQUIRED if the hardware is missing or blocked — that is the
  report being honest, not a crash.
- **FAIL:** a Python traceback (wall of red/white error text), or the
  tool freezes for more than a few minutes.
- **HOW TO FIX:** run it again with `python -m airmouse setup
  --debug` and keep the output for your report; make sure Step 3
  ended with "Successfully installed".

## Step 5 — Health check (doctor)

- **COMMAND:** `airmouse doctor`
- **WHAT TO DO:** run it and read the report.
- **WHAT SHOULD HAPPEN:** a component report ending with counts and
  one overall verdict line, e.g. `[READY FOR TESTING]`.
- **PASS:** verdict is `[READY FOR TESTING]` or `[PARTIAL — ...]` with
  no FAILED items. The exit code is 0 (READY) or 1 (PARTIAL).
- **FAIL:** verdict `[BLOCKED]`, any line marked FAILED, or exit code
  2. (Exit code 1 with only WARNING items is a PARTIAL, not a hard
  fail.)
- **HOW TO FIX:** follow the plain "Fix:" lines the report prints for
  each problem, then run `airmouse doctor` again. For a machine
  view: `airmouse doctor --json` (used in your final report).

## Step 6 — Connect the webcam and microphone (+ Windows privacy)

- **COMMAND:** none (physical) + Settings app
- **WHAT TO DO:**
  1. Plug in / enable your webcam and microphone.
  2. Open **Settings → Privacy & security → Camera** → turn ON
     "Camera access" and "Let desktop apps access your camera".
  3. Open **Settings → Privacy & security → Microphone** → turn ON
     "Microphone access" and "Let desktop apps access your
     microphone".
- **WHAT SHOULD HAPPEN:** the toggles stay ON; Windows may show a
  small indicator light when an app uses the camera/mic.
- **PASS:** both privacy toggles are ON for desktop apps.
- **FAIL:** the toggles are greyed out, or AirMouse later says the
  camera/microphone is "denied".
- **HOW TO FIX:** on a work/school PC these can be locked by policy —
  ask your administrator; on a personal PC, sign in as an
  administrator, and check the physical privacy shutter on the webcam
  and the mic mute key on some laptops.

## Step 7 — Automated verification pass

- **COMMAND:** `airmouse verify`
- **WHAT TO DO:** run it.
- **WHAT SHOULD HAPPEN:** ~10 automated checks marked
  `[           PASS]`, then a "Physical:" list where all 5 items
  (Webcam, Microphone, Hand tracking, Gaze, Real browser) say
  `ACTION_REQUIRED`, then "Next step: Run: airmouse test --guided".
- **PASS:** every automated line PASS and the physical lines say
  ACTION_REQUIRED (they are FOR you — Steps 8-17).
- **FAIL:** any automated line says FAIL.
- **HOW TO FIX:** re-run `airmouse doctor`, fix items, re-run
  `airmouse verify`. If a check keeps failing, keep the exact output
  for the final report.

## Step 8 — Start the guided test laboratory

- **COMMAND:** `airmouse test --guided`
- **WHAT TO DO:** run it. It walks you through **12 tests**, asking
  simple `[Y/N]` questions. The next nine steps (9-17) tell you what
  each question means and what a good answer looks like. Tests 1
  (installation), 7 (intelligence), 8 (browser-sim), 9 (agent),
  10 (multi-agent), 11 (recovery) and 12 (offline) run themselves —
  you mostly watch.
- **WHAT SHOULD HAPPEN:** test [1/12] Installation prints PASS with
  your Python/OpenCV/MediaPipe versions; the lab continues to the
  camera test.
- **PASS:** the lab reaches test [2/12] without a crash.
- **FAIL:** it exits by itself before test 2, or prints a traceback.
- **HOW TO FIX:** re-run with `python -m airmouse test --guided
  --debug` and keep the output; go back to Step 5 (doctor).

## Step 9 — Hand tracking test (physical)

- **COMMAND:** in the lab, test **[2/12] Camera**; deep-dive later
  with `airmouse`
- **WHAT TO DO:** put your hand ~50 cm from the webcam, watch the
  camera preview window and the on-screen cursor. Answer the lab's
  questions: move your hand slowly left, then slowly right.
- **WHAT SHOULD HAPPEN:** your hand is outlined with landmarks in the
  preview, and the mouse cursor moves in the same direction as your
  hand.
- **PASS:** you answered Y — the cursor followed your hand both ways.
- **FAIL:** no preview window, black preview, cursor jumps wildly, or
  the cursor does not respond at all.
- **HOW TO FIX:** improve lighting (face a window/lamp, avoid
  back-light), remove the camera privacy shutter, close other apps
  using the camera, re-run `airmouse doctor` (camera row should not
  be FAILED), then re-run the lab.

## Step 10 — Mouse control test (physical)

- **COMMAND:** in the lab, test **[3/12] Mouse**; deep-dive with
  `airmouse --precision`
- **WHAT TO DO:** with your hand visible, do each action the lab
  asks: pinch-and-hold + move; quick pinch (click); double pinch;
  peace sign for half a second (right-click); two fingers up/down
  (scroll); pinch-and-drag across the screen. Answer each [Y/N].
- **WHAT SHOULD HAPPEN:** the cursor tracks; a quick pinch clicks;
  double pinch double-clicks; a peace sign opens the right-click menu
  (a FIST freezes the cursor instead — that is by design);
  two-finger movement scrolls; pinch-drag moves icons/windows.
- **PASS:** all six actions answered Y (a single N is a partial —
  note which one failed).
- **FAIL:** clicks land in the wrong place, gestures are not
  recognised, or the cursor drifts without your hand moving.
- **HOW TO FIX:** re-run `airmouse --calibrate` (8-second guided
  sweep), improve lighting, slow your gestures down, then retry. If
  clicks are offset in one direction only, say so in the report —
  that is a calibration or camera-placement issue we want to know
  about.

## Step 10a — Gesture profiles (safe, no camera needed)

- **COMMAND:**
  1. `airmouse profile list`
  2. `airmouse profile accessibility`
  3. `airmouse profile doesnotexist`
- **WHAT TO DO:** run all three. The first lists the available
  presets, the second applies the accessibility preset (bigger
  deadzone, longer confirm windows, audio feedback on), the third
  intentionally asks for a profile that does not exist.
- **WHAT SHOULD HAPPEN:** (1) prints
  `gesture profiles: accessibility, creative, default, developer, gaming, hands_free, media, presentation`;
  (2) prints `profile 'accessibility' applied: 12 settings ->
  <path to config.toml>`; (3) prints
  `unknown profile 'doesnotexist' — available profiles: ...`.
- **PASS:** all three behave exactly as described (the unknown one is
  SUPPOSED to fail with exit code 1 — that is the fail-closed design).
- **FAIL:** a profile "applies" but later settings behave identically,
  or the unknown profile is silently accepted, or any Python
  traceback.
- **HOW TO FIX:** re-run with `python -m airmouse profile list
  --debug` and keep the output. To undo a profile:
  `airmouse profile default` restores the factory settings.

## Step 10b — Gesture Academy (teaching; part simulation, part physical)

- **COMMAND:**
  1. `airmouse academy`
  2. `airmouse academy click`
- **WHAT TO DO:** run (1) with your camera plugged in — it is a live
  classroom: each lesson shows the gesture to perform, your detected
  gesture, a confidence bar and a hold-to-pass bar (hold the gesture
  steady until the bar fills). Press `[SPACE]` only if you want to
  skip WITHOUT credit, `[q]` to quit (progress is saved). Then run
  (2) to practice just the pinch-click lesson.
- **WHAT SHOULD HAPPEN:** an overlay shows `Lesson n/11`, the
  instruction, the target gesture vs what it detects, and the
  progress bar. Completed lessons are remembered (progress file in
  your AirMouse home) and skipped on the next run. Lessons 8–11
  (gaze, voice, two_hand, sequences) are TEACH-ONLY: they are printed
  in the terminal with a real next step (e.g. `airmouse
  --gaze-calibrate`) and are never auto-passed.
- **PASS:** at least one core lesson passes by holding the gesture
  (the bar fills and the lesson completes), and quitting/restarting
  resumes where you left off.
- **FAIL:** no camera window at all, the detected gesture never
  matches what you perform even with good lighting, or lessons get
  marked complete while you never touched a gesture.
- **HOW TO FIX:** lighting first (Step 9's fixes apply); run
  `airmouse doctor` and make sure the camera row is not FAILED; if a
  lesson never recognises the gesture, note the lesson id and what
  you performed — that is exactly the feedback we need.

## Step 10c — Gesture Lab (watch the safety gates work; physical readout)

- **COMMAND:**
  1. `airmouse gesture-lab 30`
  2. make an **OK** sign at the camera
- **WHAT TO DO:** run it for 30 seconds. The lab is an observatory:
  it uses the REAL recognition + execution-spine pipeline but
  dispatches into a dry-run stub — it can never move your mouse or
  press a key. Perform gestures and watch the readout (~5 updates per
  second): HAND DETECTED / GESTURE / CONFIDENCE / MODE / TWO-HAND /
  LAST ACTION / RESULT. Finish by making the OK sign — the gesture
  normally mapped to Alt+F4.
- **WHAT SHOULD HAPPEN:** the readout shows your detected gesture and
  confidence; a pinch shows `LAST ACTION: left_click (executed)` INTO
  THE STUB ONLY; the OK sign shows
  `RESULT: blocked: destructive_action_blocked_by_policy (a gesture
  must never close windows)`.
- **PASS:** your gestures appear in GESTURE with plausible confidence,
  and the OK sign is BLOCKED with the policy message.
- **FAIL:** the OK sign shows `executed`, or no hand is ever detected
  despite Step 9 passing.
- **HOW TO FIX:** if the OK sign executes, STOP and report it first —
  a destructive action allowed through the spine is a top-priority
  bug (same severity as Step 16's rejected-request check). If no hand
  is detected, apply the Step 9 lighting/privacy fixes first.

## Step 10d — Two-hand check (physical; zoom only is wired)

- **COMMAND:**
  1. `airmouse profile hands_free`
  2. `airmouse gesture-lab 30`
  3. pinch BOTH hands, then pull them apart / push them together
- **WHAT TO DO:** apply the hands_free profile (it turns on
  `two_hand`), then watch the lab's TWO-HAND and MODE fields while
  pinching with both hands and changing the distance between them.
  You can also try it live in `airmouse` itself: both hands pinched
  zooms the window under the cursor (real Ctrl+mouse-wheel).
- **WHAT SHOULD HAPPEN:** MODE flips to `two-hand`; with both pinches
  engaged the readout shows `TWO_HAND_ZOOM`-style engagement and the
  scale changing as you pull apart (zoom in) or push together (zoom
  out); single-hand gestures stop firing while both hands are
  engaged.
- **PASS:** two-hand engagement is detected and (in the live app)
  zooming works via the two-hand pinch; single-hand actions freeze
  while engaged.
- **FAIL:** MODE never shows two-hand, or the lab crashes with both
  hands visible.
- **HOW TO FIX:** check `airmouse doctor` (camera row), make sure BOTH
  hands are fully in frame and well lit; note that two-hand ROTATE
  and DRAG are detected but not yet wired to any OS action — only
  ZOOM drives a real action today. If that changes what you saw, say
  so in the report.

## Step 10e — Agent E-Stop drill (simulation; the human always wins)

- **COMMAND:**
  1. `python -m airmouse.aip_stdio` (leave this window open — it is
     the agent wire server; nothing but JSON reply lines appears)
  2. in a second CMD window, send an agent EXECUTE request, e.g.:
     `echo {"aip_version":"1.0","type":"execute","id":"x1","agent_id":"drill","request_id":"","ts":0,"payload":{"action":"click","verify":true,"params":{}}} | python -m airmouse.aip_stdio`
     (or use `agent-core` / `agent-sdk-js` pointed at the server)
  3. watch window 1, then close it with Ctrl-C
- **WHAT SHOULD HAPPEN:** the server starts silently (or with one
  banner line if you used `airmouse --aip-stdio` — both speak the
  same protocol). The EXECUTE request gets back a JSON error line:
  `"permission_denied" — "decision 'ask': no rule; default ASK fails
  closed"`. No mouse click happens anywhere. Closing the server
  window is the E-stop: any agent on the other side simply gets no
  more replies.
- **PASS:** the request is DENIED with `permission_denied` and your
  mouse never moves or clicks.
- **FAIL:** a click happens with no permission granted — treat this
  like the Step 16 destructive-request failure: top-priority bug,
  report first.
- **HOW TO FIX:** if a click happens, do not retry; keep the server
  output and report it. If you want to see the REAL execution path
  (still permission-gated), it exists via
  `airmouse --aip-real --aip-stdio` — grants are explicit, single-use
  (`ALLOW_ONCE`) or session-scoped, and there is no grant by default.

## Step 11 — Gaze test (physical)

- **COMMAND:** in the lab, test **[4/12] Gaze**; standalone:
  `airmouse --gaze-calibrate` then `airmouse --gaze`
- **WHAT TO DO:** sit at your normal distance, look straight at the
  camera, then look at the lab's target positions (screen corners and
  centre) and blink slowly twice when asked.
- **WHAT SHOULD HAPPEN:** the cursor jumps toward where you look;
  your blinks are detected.
- **PASS:** the cursor moved roughly to each looked-at target and the
  blink was detected.
- **FAIL:** the cursor goes somewhere unrelated to where you look, or
  nothing moves at all.
- **HOW TO FIX:** run `airmouse --gaze-calibrate` first (it saves a
  calibration), keep your head still during use, ensure even face
  lighting, retry. Gaze is assistive, not surgical — small offsets
  are normal; report anything larger than about a quarter of the
  screen.

## Step 12 — Voice command test (physical)

- **COMMAND:** in the lab, test **[5/12] Voice**; standalone:
  `python -m pip install "airmouse[voice]"` then `airmouse --voice`
- **WHAT TO DO:** the lab asks you to say 8 phrases out loud, one at
  a time: "click", "double click", "scroll down", "open notepad",
  "copy", "paste", "undo", "close window". Watch what AirMouse shows
  as heard and how it interpreted each phrase.
- **WHAT SHOULD HAPPEN:** each phrase is heard and matched to the
  right command (the HUD shows the recognised command).
- **PASS:** all 8 phrases recognised correctly.
- **FAIL:** "airmouse[voice]" was never installed, the mic list is
  empty, or most phrases are misheard.
- **HOW TO FIX:** install the voice extra (`python -m pip install
  "airmouse[voice]"`), re-check Step 6 microphone privacy, use
  `airmouse --mic N` to pick another microphone if you have several,
  reduce background noise, then retry. Honest note: full offline
  dictation needs an optional local ASR engine (pocketsphinx, vosk or
  whisper) — without one, only the built-in phrase grammar and the
  simulated provider are guaranteed; `airmouse voice-status` shows
  what is really available.

## Step 13 — Dictation test (physical)

- **COMMAND:** in the lab, test **[6/12] Dictation**; standalone:
  `airmouse --dictation`
- **WHAT TO DO:** dictate one short sentence (the lab lets you type
  it if no microphone is wired up), then try one spoken edit command
  such as "scratch that", "new line", or "replace that with hello".
- **WHAT SHOULD HAPPEN:** the raw → normalised → final text is shown;
  the edit command does what it says.
- **PASS:** the final text matches what you said (small punctuation
  differences are acceptable), and the edit command worked.
- **FAIL:** text is garbled, or edit commands do nothing.
- **HOW TO FIX:** same microphone fixes as Step 12; note that
  punctuation quality depends on the ASR engine available — report
  which engine `airmouse voice-status` shows.

## Step 14 — Intelligence test (simulation)

- **COMMAND:** in the lab, test **[7/12] Intelligence**; then look
  with `airmouse intelligence` and `airmouse memory`
- **WHAT TO DO:** watch the lab run a learn → predict → (simulated)
  execute round by itself. Afterwards, run `airmouse memory` to see
  that something was learned.
- **WHAT SHOULD HAPPEN:** the lab prints OBSERVED / PREDICTED /
  EXECUTED lines marked `[SIMULATION]` and ends with PASS. The
  prediction line explicitly says it is data, not an executed action.
- **PASS:** PASS with the [SIMULATION] label; `airmouse memory` shows
  at least one learned pattern.
- **FAIL:** a crash, or the executed action appears on your REAL
  screen (that would be a serious bug — report immediately).
- **HOW TO FIX:** none needed on PASS; on FAIL run
  `python -m airmouse test --guided --debug` and keep the output.

## Step 15 — Real browser test (physical + simulation)

- **COMMAND:**
  1. `"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222`
     (Edge: `"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --remote-debugging-port=9222`)
  2. in a second CMD window: `airmouse browser`
  3. in the lab, test **[8/12] Browser**
- **WHAT TO DO:** install Google Chrome or Microsoft Edge if you do
  not have it. Close ALL its windows first, then start it with the
  debug flag above (this allows the local automation bridge on port
  9222 — it is localhost only). Run `airmouse browser` — it reports
  whether the CDP bridge is available and prints the same start
  command as a reminder. Then let the lab run its browser test.
- **WHAT SHOULD HAPPEN:** `airmouse browser` prints
  `CDP bridge on :9222 -> available: True`. The lab's simulated
  browser flow (open → click → verify → new tab → back) passes,
  labelled `[SIMULATION]`.
- **PASS:** bridge available: True, and the lab browser test PASSes.
- **FAIL:** `available: False`, or real-page clicks never happen even
  though the bridge is available.
- **HOW TO FIX:** make sure Chrome was started with the debug flag
  (check the full path; try Edge instead); close other Chrome
  instances first (the flag is ignored if Chrome is already running
  without it); check your antivirus is not blocking localhost ports;
  note that real-page control is exactly the part the sandbox could
  NOT verify — a precise description of what happened is gold for the
  report.

## Step 16 — Agent safety test (simulation)

- **COMMAND:** in the lab, tests **[9/12] Agent** and **[10/12]
  Multi-Agent**
- **WHAT TO DO:** watch. The lab sends a benign agent request through
  the full pipeline (request → permission → safety → agent → action →
  verification), then a destructive request, then simulates two
  agents fighting over the mouse and an emergency stop.
- **WHAT SHOULD HAPPEN:** the benign request executes on the
  simulated window; the destructive request is REJECTED (denied by
  default); the lease conflict is refused and resolved by handoff;
  the emergency stop stops every agent.
- **PASS:** both tests PASS with the [SIMULATION] label and the word
  REJECTED for the destructive request.
- **FAIL:** the destructive request is allowed, or any crash.
- **HOW TO FIX:** do not ignore this one — a destructive request that
  is allowed is a top-priority bug: run
  `python -m airmouse test --guided --debug`, keep the output, and
  put it first in your report.

## Step 16a — First-run tour + onboarding resume (v16.5 teaching; physical)

- **COMMAND:**
  1. `set AIRMOUSE_HOME=%TEMP%\am-fresh` (fresh home; CMD) — then
     `airmouse`
  2. answer **Y** to `Would you like a 3–5 minute interactive tour?
     [Y/n]`
  3. complete the VOICE track's typed practice when offered (or press
     the skip key it shows)
  4. answer **n** the next time the tour pauses
  5. `type "%TEMP%\am-fresh\profile\onboarding.json"`
  6. `airmouse` again → answer **n** to `Continue your training?
     [Y/n]`
- **WHAT TO DO:** on a REAL terminal (Command Prompt — it must be a
  TTY for the offer to appear), accept the tour once, then decline it
  the second time.
- **WHAT SHOULD HAPPEN:** (1) with a fresh home and an interactive
  terminal, plain `airmouse` offers the tour BEFORE the app starts;
  (2) the tour prints a plan per track and every lesson that needs a
  sensor says `PHYSICAL PRACTICE REQUIRED — never auto-passed` —
  lessons with real sensors pass only when the sensor verifies you;
  (3) `onboarding.json` exists with `"phase": "IN_PROGRESS"` (or
  `VOICE_COMPLETE` after the voice lesson) and a session count;
  (4) declining with **n** (or just pressing Enter-on-empty twice,
  or Ctrl-C) simply starts the app — the teaching prompt NEVER traps
  you, and the `AIRMouse READY` panel still appears after startup.
- **PASS:** the offer appears exactly once per fresh home, the state
  file records your progress, the second launch asks `Continue your
  training? [Y/n]`, declining proceeds straight into the app, and NO
  lesson that required a camera/microphone was ever marked complete
  without the sensor passing you.
- **FAIL:** the prompt loops forever, blocks startup, marks a
  physical lesson complete without a sensor, or `onboarding.json` is
  not created.
- **HOW TO FIX:** delete the folder `%TEMP%\am-fresh` and retry fresh;
  if the prompt never appears, check you used a real Command Prompt
  window (not a pipe); re-run with `python -m airmouse --debug` and
  keep the output. To disable the offer entirely: set `teach_auto =
  false` in `%TEMP%\am-fresh\config.toml` (or your real home's
  config.toml).

## Step 16b — `airmouse transcribe` with a real ASR engine (physical + optional engine)

- **COMMAND:**
  1. `python -m pip install vosk` (+ download a small vosk model and
     note its folder) — or `python -m pip install pocketsphinx`
  2. `airmouse voice-status`
  3. `airmouse transcribe`
  4. say a few sentences, then type `status`, `save`, `quit`
- **WHAT TO DO:** install the engine yourself FIRST (AirMouse never
  installs anything), then run the session and speak.
- **WHAT SHOULD HAPPEN:** (2) `voice-status` flips honestly —
  `✓ Local ASR available` (and `✓ Vosk available` if you installed
  vosk), `Active:` names the engine, and the microphone row shows ✓
  once Windows mic permission is granted; (3) the session banner no
  longer says `⚠ SIMULATED provider` — it names the real engine;
  segments appear as you speak with `[HH:MM:SS] confidence%
  "text"`; `status` shows `history: n/500 segments` and the
  transcripts folder; `save` writes a text file under
  `%USERPROFILE%\.airmouse\transcripts\` (or your `AIRMOUSE_HOME`).
- **PASS:** the provider panel and the session banner AGREE with
  each other and with what you actually installed, segments carry
  timestamps + confidence, and `save` produces a text-only file (no
  audio file anywhere).
- **FAIL:** the banner claims a real provider while `voice-status`
  says none is installed (or vice versa) — that inconsistency is a
  top-priority honesty bug; also report no segments appearing despite
  a working mic in Step 13.
- **HOW TO FIX:** without an engine the session is SUPPOSED to say
  `⚠ SIMULATED provider` — that is the honest design, not a bug;
  re-check the mic privacy toggles from Step 6; keep the transcript
  file private (it is your content).

## Step 16c — Temporal observer + HUD badges during gestures (physical readout)

- **COMMAND:**
  1. `airmouse` (live app, camera on)
  2. pinch, HOLD ~2 seconds, move, release — several times
  3. cover the camera briefly
- **WHAT TO DO:** watch the HUD badge row while performing
  pinch-hold-move-release cycles.
- **WHAT SHOULD HAPPEN:** a `TMP:` badge appears and changes with
  your pinch lifecycle (START/HOLD/MOVE/RELEASE and any proposal
  text); when the camera view degrades (covered lens, bad lighting)
  a `SENSOR:` badge appears, and it disappears when health recovers.
  IMPORTANT: the temporal system is an OBSERVER — a proposal must
  NEVER click, type, or move anything by itself; your normal gesture
  actions keep going through the same spine as before.
- **PASS:** `TMP:` tracks your pinch lifecycle, `SENSOR:` appears
  only when the camera view degrades, and NO OS action fires from a
  proposal alone.
- **FAIL:** the badges never appear at all with a working camera
  (report it), or an OS action (click/key) fires with no matching
  gesture behind it (STOP, press E-STOP, and report FIRST — that
  would be a spine-integrity bug, our highest severity).
- **HOW TO FIX:** lighting first (Step 9's fixes); the badges are
  status-only by design — if you want to see the underlying data
  without a camera, `airmouse gesture-lab 30` shows the dry-run
  observatory.

## Step 16d — `help-me` sanity check (safe, no hardware needed)

- **COMMAND:**
  1. `airmouse help-me`
  2. `airmouse help-me "how do I scroll?"`
  3. `airmouse help-me "why didn't that work?"`
- **WHAT TO DO:** run all three and read the answers.
- **WHAT SHOULD HAPPEN:** (1) the capability panel — hand gestures
  (including `ok … close window (Alt+F4 class) [refused by default]`
  and the honest two-hand note that rotation/drag are DETECTED but
  their OS action is NOT MAPPED yet), the offline voice grammar with
  the "NOT full speech recognition" note, gaze, teacher and health
  commands; (2) the three real scroll paths (pinch-hold + move,
  swipe up/down, "scroll up/down" by voice); (3) a gate-by-gate
  debug list (e-stop → confidence floors → destructive policy → rate
  limit → gesture mapping) ending with `airmouse doctor`.
- **PASS:** all three exit 0 with readable answers, and every answer
  matches what `airmouse gestures` / `airmouse commands` print (the
  answers are generated from the real capability data, not canned
  marketing).
- **FAIL:** an empty answer, a Python traceback, or an answer that
  contradicts `airmouse gestures`.
- **HOW TO FIX:** re-run with `python -m airmouse help-me --debug`,
  keep the output, and report it (the help database is a single
  module — such a bug is easy for us to fix).

## Step 16e — v16.5.1 config encoding + repair drill (Windows reality)

This is the drill for the P0 defect v16.5.1 fixes (`'utf-8' codec
can't decode byte 0x97 in position 30`).

- **COMMAND/WHAT TO DO:**
  1. `airmouse doctor` — read the CONFIG row.
  2. Open `%USERPROFILE%\.airmouse\config.toml` in **Notepad**, add a
     line containing an em dash, e.g. `note = "test — dash"`, then
     **Save As → Encoding: ANSI** (this recreates the legacy cp1252
     encoding that broke v16.5.0 on Windows).
  3. `airmouse doctor` again — read the CONFIG row now.
  4. `airmouse setup --repair-config`
  5. `airmouse doctor` once more.
- **WHAT SHOULD HAPPEN:** (1) either `defaults (no config file yet)`
  or `UTF-8 configuration valid (…)`, never a bare "using defaults";
  (3) a WARNING row for Configuration with `WHY:` (legacy Windows
  encoding) and `FIX:` naming the repair command — and the app still
  starts with defaults while saying so loudly; (4) the repair prints
  the backup path under `<home>\backups\` (your original file,
  byte-for-byte), then reports the re-encode; (5) CONFIG is READY
  again: `UTF-8 configuration valid`, and your `note = "test — dash"`
  line still contains the em dash (content preserved).
- **PASS:** no `0x97`-style decode error anywhere after repair; the
  backup exists; doctor CONFIG = READY; content preserved.
- **FAIL:** any silent fallback, any data loss, or a repair that
  reports success while `airmouse doctor` still shows WARNING.

## Step 16f — v16.5.1 Voice Academy: provider detection + REAL vs TEXT

- **COMMAND:** `airmouse teach voice` (interactive TTY)
- **WHAT TO DO:** read the VOICE HARDWARE panel, then try Lesson 1.
  If you have a microphone AND installed a local ASR engine
  (`pip install faster-whisper`, or vosk, or pocketsphinx), also
  actually SPEAK one "click" when it says `Listening…`.
- **WHAT SHOULD HAPPEN:** the panel shows
  `Microphone: ✓/○ detected`, per-engine marks (Whisper / Vosk /
  PocketSphinx), `Active provider: …`, `Command grammar: ✓
  available`; with no engine installed it also says
  `Full speech recognition: NOT INSTALLED` and offers
  `Install local voice engine? [Y] Yes [N] Not now` (N never
  installs anything).  WITH mic+ASR the lesson says
  `Say: "click"` + `Listening…` and a pass appears only after the
  real pipeline recognized it; WITHOUT them every prompt is labelled
  `TEXT PRACTICE` under `Real microphone practice: PHYSICAL TEST
  REQUIRED / Text practice: AVAILABLE`.
- **PASS:** panel matches your actual hardware; a spoken pass is only
  granted after you actually spoke; text practice is never called
  microphone validation; typing nothing prints
  `Skipped — not completed.` and three wrong tries print
  `Answer revealed. Lesson not completed.`
- **FAIL:** a "pass" you did not speak, a missing honesty label, or
  the academy consuming your answers without the panel.

## Step 16g — v16.5.1 live transcription with the real microphone

- **COMMAND:** `airmouse transcribe` (mic + ASR installed)
- **WHAT TO DO:** press Enter and speak; try `pause`, `resume`,
  `status`, `save`, `quit`.
- **WHAT SHOULD HAPPEN:** the banner shows your provider and
  `mode: MIC LIVE`; each spoken utterance prints a final line with a
  timestamp and confidence; `save` writes text only
  (`transcript-*.txt|json` under `<home>\transcripts\`); the summary
  ends `no audio was ever stored`.
- **PASS:** your speech appears as final text with confidence +
  timestamp; no `.wav`/`.pcm` file appears anywhere under
  `~/.airmouse`.
- **FAIL:** a claimed transcript you never spoke, or any audio file
  on disk.

## Step 16h — v16.5.1 doctor capability states

- **COMMAND:** `airmouse doctor --verbose`
- **WHAT SHOULD HAPPEN:** hardware rows carry explicit state labels —
  `Webcam … [ACTIVE]`, `Microphone … [MICROPHONE DETECTED]`,
  `Transcription providers … [ACTIVE]` or `[NOT INSTALLED]`, and the
  new `Gaze subsystem` row (`UNAVAILABLE` / `CALIBRATED` /
  `REQUIRES CALIBRATION` with the exact `airmouse --gaze-calibrate`
  fix).  Every WARNING/FAILED row has `WHY:` and `FIX:` lines.
- **PASS:** every non-READY row answers what/why/fix without you
  having to guess; the gaze fix command works when run.
- **FAIL:** any row that only says "using defaults"-style text.

## Step 16i — v16.5.2 lightweight local voice runtime (THE hotfix drill)

This is the regression drill for the v16.5.1 defect where
`doctor` reported `pocketsphinx [ACTIVE]` while `airmouse transcribe`
still launched `provider: simulated_stream`.

- **COMMAND:** `airmouse voice-status --verbose`
- **WHAT SHOULD HAPPEN:** the v16.5.2 §8 view, all from ONE registry:
  `MICROPHONE` shows `✓ detected`, `devices: 6`, the selected device
  name, `backend: sounddevice/PortAudio`, sample rate + channels.
  `ASR PROVIDERS` shows PocketSphinx with `installed: YES`,
  `importable: YES`, `model: available (bundled en-US…)`,
  `initialization: PASS`, `runtime: READY`, `role: ACTIVE` (plus the
  probe time under `--verbose`; vosk/whisper rows honestly say
  `installed: NO … role: OPTIONAL` unless you installed them).
  `TRANSCRIPTION` shows `real microphone: AVAILABLE`,
  `provider: pocketsphinx`, `mode: MIC LIVE`, `offline: YES`,
  `audio storage: NONE`, `network: NOT REQUIRED`.
- **PASS:** `initialization: PASS` + `role: ACTIVE` on PocketSphinx
  (a REAL decoder build, not an import flag) and
  `real microphone: AVAILABLE`.
- **FAIL / honest alternate:** if PocketSphinx shows
  `initialization: FAIL` the row carries the exact reason + the
  `pip install pocketsphinx SpeechRecognition` fix — that is an
  HONEST report (record it), not a software bug.

- **COMMAND:** `airmouse transcribe`
- **WHAT SHOULD HAPPEN:**
  `══ LIVE TRANSCRIPTION ══` →
  `provider: pocketsphinx (local ASR engine)` →
  `mode: MIC LIVE — speak one utterance per Enter; typed commands
  still work` → `Listening...`.  Press Enter and speak; the utterance
  goes Microphone → sounddevice → VAD → PocketSphinx → transcript +
  timestamp (confidence shows `conf: unavailable` — PocketSphinx
  supplies none, and AirMouse never invents one).
- **PASS:** the provider line names a real engine and a spoken
  "hello bro" produces a final transcript segment.  **THE INVARIANT:**
  the engine named here is exactly the one `voice-status` and
  `doctor` report as ACTIVE.
- **FAIL:** `provider: simulated_stream` while `voice-status` shows
  PocketSphinx `role: ACTIVE` — that is the v16.5.1 bug; any
  recurrence is a release blocker.

- **COMMAND:** inside the same session, speak/verify the three
  mission utterances:
  1. `hello bro this is a real AirMouse voice test` — must appear as
     a final segment (real ASR, real transcript).
  2. `open browser` — must appear as a final segment and (voice
     control live) resolve through the grammar to the OPEN intent.
  3. `hello bro comma how are you question mark` — dictation chain:
     spoken punctuation → capitalization →
     `Hello bro, how are you?`
- **PASS:** all three produce finals; the summary line reports the
  session honestly (`provider: pocketsphinx`, `no audio was ever
  stored`).
- **FAIL:** finals that appear without audio reaching the engine
  (they cannot — text fallback is only for typed lines) or a crash.

- **COMMAND:** `airmouse transcribe --text`
- **WHAT SHOULD HAPPEN:** `mode: TEXT PRACTICE` +
  `TEXT PRACTICE — typed input through the real pipeline; this is
  NOT microphone validation`.  Typed lines behave exactly like the
  text-input mode above.
- **PASS:** the label is unambiguous.
- **FAIL:** any wording that calls typed input microphone-validated.

- **COMMAND:** `airmouse doctor --verbose`
- **WHAT SHOULD HAPPEN (cross-check):**
  `Transcription providers … READY — local ASR: pocketsphinx
  [ACTIVE — initialization verified (≈0.4s)]`, and the Microphone
  row names the backend + device count.  The engine name matches
  `voice-status` and `transcribe` — one registry, one truth.
- **PASS:** all three commands name the SAME engine.

## Step 16j — v16.5.3 ZERO-COMPILER voice setup (THE bootstrap drill)

**Why this step exists:** on Windows/CPython 3.12, `pip install
pocketsphinx` downloads the SOURCE tarball and fails (no CMake, no
NMake, no MSVC).  That is a packaging fact — PocketSphinx 5.1.1 has
no CPython 3.12 wheel on ANY platform — not a microphone bug and not
an AirMouse bug.  v16.5.3 makes real local voice a ONE-COMMAND setup
using engines whose prebuilt wheels actually fit your Python.  **You
must NOT install any compiler.**

- **COMMAND:** `airmouse voice-setup`
- **WHAT TO DO:** run it, read the plan, press **Y** when asked to
  install the lightweight engine and (once) to fetch the official
  model.
- **WHAT SHOULD HAPPEN (expected on Windows 11 + Python 3.12):**
  microphone READY, `PocketSphinx wheel compatibility… NOT FOUND`
  (honest — no cp312 wheel exists), `Vosk wheel compatibility… FOUND`,
  then "Installing lightweight local voice engine (vosk)…" (13.7 MB
  prebuilt wheel — NO compiler), then "Fetching the official
  lightweight model…" (41.2 MB, one-time, SHA256-verified), then:

  ```text
  VOICE READY
  Provider: Vosk
  Mode: MIC LIVE
  Network required: NO
  Audio uploaded: NO
  ```

- **PASS:** the panel above (or, if you already had a working engine,
  `VOICE READY` with that engine's name).  No compiler was installed.
- **FAIL:** any CMake/NMake/MSVC/compiler output (AirMouse must never
  start a native build), a pip traceback instead of a panel, or
  `VOICE SETUP COULD NOT COMPLETE` WITHOUT a clear Reason + next step.
  If you see `VOICE SETUP COULD NOT COMPLETE`, copy its Reason line —
  that is the bug report we need.
- **Also valid:** `airmouse voice-setup --check-only` (plan only,
  installs nothing) and `airmouse voice-setup --repair` (re-verify +
  fix an existing install).

- **COMMAND:** `airmouse voice-status --verbose`
- **WHAT SHOULD HAPPEN:** ASR PROVIDERS shows your engine with
  `installed: YES`, `importable: YES`, real `initialization` result,
  `role: ACTIVE`; TRANSCRIPTION shows `real microphone: AVAILABLE`,
  `provider: <engine>`, `mode: MIC LIVE`, `offline: YES`,
  `audio storage: NONE`, `network: NOT REQUIRED`.
- **PASS:** those exact facts, for the engine voice-setup installed.

- **COMMAND:** `airmouse transcribe`
- **WHAT SHOULD HAPPEN:**

  ```text
  ══ LIVE TRANSCRIPTION ══
  provider: vosk (local ASR engine)      ← or pocketsphinx on 3.11/3.13
  mode: MIC LIVE
  ```

- **WHAT TO DO:** press bare Enter and speak each of these, one at a
  time:
  1. `hello bro this is a real AirMouse voice test`
  2. `open browser`
  3. `hello bro comma how are you question mark`
- **PASS (REAL, MIC LIVE):** each utterance produces a transcript
  line with a timestamp (confidence may show `unavailable` — vosk
  small supplies none; that is honesty, not a bug).  Phrase 2 should
  look like "open browser"; phrase 3 should be punctuated by the
  dictation layer ("Hello bro, how are you?").  Small-model hearing
  quirks ("air mouth" for "AirMouse") are EXPECTED and still count —
  the pipeline is real; only accuracy tuning differs.
- **FAIL:** `text-input mode` without asking, `simulated_stream`,
  any cloud claim, or a crash.

- **COMMAND:** `airmouse doctor --verbose` (cross-check)
- **WHAT SHOULD HAPPEN:** `Transcription providers … READY — local
  ASR: <engine> [ACTIVE]` — the SAME engine voice-status and
  transcribe report.  One registry, one truth (v16.5.2 law, still
  enforced; the invariant is regression-tested).

- **OPTIONAL — measure it:** `airmouse voice-benchmark` (timing +
  RAM + disclosed sizes) and `airmouse voice-benchmark --wav
  your-file.wav` (real-speech latency + words/minute).

## Step 17 — Recovery and offline tests (simulation)

- **COMMAND:** in the lab, tests **[11/12] Recovery** and **[12/12]
  Offline**; standalone: `airmouse --offline` then
  `airmouse offline-test`
- **WHAT TO DO:** watch the recovery drill (injected failures —
  missing target, timeout, app crash, permission denial — each
  diagnosed and recovered), then the offline drill. Optionally run
  `airmouse offline-test` with your Wi-Fi/Ethernet still connected:
  it proves local features work with network features blocked.
- **WHAT SHOULD HAPPEN:** recovery recovers each failure (and
  correctly does NOT retry the permission denial); offline selftest
  reports 18/18 checks passed.
- **PASS:** `18/18 checks passed, overall=OK`; recovery test PASS.
- **FAIL:** any failed check or a crash.
- **HOW TO FIX:** re-run once; if it persists, keep the output for
  the report.

## Step 18 — Export your results and your data

- **COMMAND:**
  1. `airmouse doctor --json > "%USERPROFILE%\Desktop\airmouse-doctor.json"`
  2. `airmouse verify > "%USERPROFILE%\Desktop\airmouse-verify.txt"`
  3. `airmouse test --guided > "%USERPROFILE%\Desktop\airmouse-test.txt"`
     (answer the prompts; the transcript is saved)
  4. `airmouse memory export --to "%USERPROFILE%\Desktop\airmouse-memory.json"`
  5. `airmouse privacy > "%USERPROFILE%\Desktop\airmouse-privacy.txt"`
- **WHAT TO DO:** run all five. This saves the machine-readable health
  report, the verification transcript, the guided-lab transcript, a
  copy of your learned local memory, and the privacy report to your
  Desktop. (`airmouse memory export` writes a local file only —
  nothing is sent anywhere.)
- **WHAT SHOULD HAPPEN:** five files appear on your Desktop; the
  export prints `exported local memory -> <path>`.
- **PASS:** all files exist and are non-empty.
- **FAIL:** "Access is denied" or the export says it could not write.
- **HOW TO FIX:** use a writable folder:
  `airmouse memory export --to "%TEMP%\airmouse-memory.json"`; never
  paste learned-memory contents into a public issue — the file is
  for YOU (Step 19 needs only the store summary from
  `airmouse memory status`).

## Step 19 — Assemble the final report

If everything passed: congratulations — you have done what the build
machine physically could not. If something failed, help us fix it:

**Where:** open a GitHub issue on the AirMouse repository
(https://github.com/manaladan6-rgb/airmouse/issues), title:
`Windows real-world test v16.5.0 — step N failed`.

**Paste these, in this order:**

1. One line per step: `Step 9: FAIL — no preview window` (or PASS).
2. The output of `airmouse doctor` (the text report).
3. The failing step's exact output (from the Desktop files of
   Step 18, e.g. `airmouse-test.txt` — copy only the relevant test's
   section).
4. Your environment, one line: Windows version (10/11), laptop or
   desktop, webcam built-in or external, microphone type, Python
   version from Step 2.
5. What you already tried from the HOW TO FIX fields.

**Do NOT paste:** the contents of `airmouse-memory.json` (your
learned patterns — local by design), or any file from outside the
AirMouse folders. The doctor/verify/test/privacy outputs contain no
personal content by design.

**Pass criteria for the whole document:** Steps 1-8 PASS and at least
Steps 9, 10, 12, 13, 15 answered Y (hand, mouse, voice, dictation,
browser); Steps 10a–10e recorded (10c's OK-sign BLOCK and 10e's
permission_denied are mandatory PASSes — a FAIL there is a software
bug); Steps 16a–16d recorded (16d's `help-me` answers and 16a's
never-trap/never-auto-pass behaviour are mandatory PASSes — a FAIL
there is a software bug; 16a/16b/16c pass only with a real TTY /
ASR engine / camera respectively and stay recorded-as-attempted if
the hardware is missing); v16.5.1 Steps 16e–16h recorded (16e's
repair-preserves-content and 16h's WHY/FIX rows are mandatory
PASSes; 16f/16g's real-microphone passes require a real mic + local
ASR engine and stay recorded-as-attempted if the hardware is
missing); **v16.5.2 Step 16i recorded — 16i's invariant (doctor,
voice-status and transcribe all name the SAME engine) and the
TEXT-PRACTICE labelling are mandatory PASSes; a recurrence of
`simulated_stream` while PocketSphinx is ACTIVE is a release
blocker.  16i's spoken-utterance passes require a real mic + local
ASR engine and stay recorded-as-attempted if the hardware is
missing.**  Steps 14, 16, 17 must PASS (they are
simulation tests — a FAIL here is a software bug, not a hardware
quirk).

---

*Honesty footer: AirMouse v16.5.2 — 1992 automated tests green (2
honest headless skips), simulation suite green, `airmouse verify`
12/12 automated PASS in the build sandbox.  The v16.5.1
provider-divergence defect (doctor `pocketsphinx [ACTIVE]` via a
hasattr flag vs transcribe `simulated_stream`) is reproduced in a
regression test and fixed by the single authoritative ASR registry;
the v16.5.0 Windows encoding defect (`0x97` at byte 30) remains
reproduced byte-for-byte and repaired in automated tests. Physical
hardware (webcam, microphone, hand tracking, two-hand geometry,
gaze, real browser, real OS input automation, the v16.5 teaching
tour with real sensors, live ASR engines, live mic transcription,
the temporal HUD under a real camera, Windows bundles) was NOT
tested in the sandbox and CANNOT be — that is what this document is
for; the real-mic parts of Step 16i are exactly where the lightweight
local voice runtime must prove itself. Windows runtime was not
tested in the sandbox.*
