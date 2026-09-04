# AirMouse v11.5 — Teacher Guide

Lecturing hands-free: slide control, classroom timeline, notes, and a
clean lecture artifact.

## Activation

```bash
airmouse --teacher                     # teacher mode
airmouse --teacher --voice-mode hybrid # + voice (commands + dictation)
airmouse --teacher --transcribe        # + live transcription caption
airmouse --teacher --offline           # hard-offline
```

The mode's phrase table (`modes.MODE_REGISTRY["teacher"]`) is routed by
`ModeController`; every phrase is a deterministic exact match (a
parameterized form is also accepted, e.g. "add note <text>").

## Phrase table

| Say | Action |
|---|---|
| "start lecture" | start the lecture timeline |
| "pause lecture" / "resume lecture" | pause/resume (pauses excluded from elapsed time) |
| "mark important" | timestamped `important` marker |
| "add note" | timestamped `note` marker |
| "export transcript" | export the lecture timeline |
| "start presentation" | send F5 (presentation start) |
| "next slide" / "previous slide" | Right / Left arrow keys |
| "black screen" / "white screen" | `b` / `w` presenter keys |

## Presentation control — generic hotkeys, honest scope

`PresentationController` sends **generic keyboard shortcuts**
(`modes.PRESENTATION_KEYS`): Right/Left for slide navigation, F5 to
start, Esc to exit, `b`/`w` for black/white screen, Home/End for
first/last slide, Ctrl+L as an attempt at "highlight".

That is exactly why it works with **any** presentation software —
PowerPoint, Keynote, LibreOffice Impress, Google Slides in a browser,
PDF viewers — without proprietary integrations. The honest limits:

* a few actions are app-dependent (e.g. "pointer" is listed as
  app-dependent in the table; some viewers don't support `b`/`w`);
* slide numbering is tracked locally (best effort), so
  "jump to slide" is relative navigation, not an app-API jump;
* nothing here controls the presentation *content* — only the keys
  above.

## Lecture timeline + artifacts

`TimelineSession` records timestamped entries
(`topic | important | note | action_item | decision | question |
bookmark`; ≤ 500 entries, pause-aware elapsed time).

* **Markdown lecture** — `export transcript` writes
  `~/.airmouse/lecture.md` via `TeacherMode.export_lecture`:

  ```
  # lecture
  Duration: 312s
  - **[00:41]** note: remember to re-derive the cross
  - **[02:05]** important: exam covers chapters 4-6
  ```

* **JSON** — `export_lecture(path, fmt="json")` writes
  `kind: airmouse-timeline` with per-entry timestamps and kinds.

## Transcription in class

Pair with `--transcribe` for a live caption. **Honest limitation:** a
transcription *session* needs a real ASR provider (vosk/whisper/
pocketsphinx) or a deterministic injected transcript (how tests and
demos drive it). Without an installed engine the mode's timeline,
notes and slide control still work — only the speech-to-text part is
absent, and `airmouse voice-status` tells you honestly what is
installed.

## Classroom-relevant limits

* No speaker identification anywhere in AirMouse — the timeline marks
  what *you* chose to mark.
* No audio recording is performed; only finalized transcript segments
  (if transcription is on and history is enabled) and your markers are
  kept.
* Everything is local: the lecture artifact is written to your disk
  only when you export it.
