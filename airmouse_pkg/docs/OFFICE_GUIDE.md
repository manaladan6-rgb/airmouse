# AirMouse v11.5 — Office & Meeting Guide

Meeting capture with structured output, task capture, dictation, and
window management — plus the honest limits (no speaker ID; transcript
quality depends on your local ASR).

## Activation

```bash
airmouse --office                  # office mode (meeting control + tasks)
airmouse --meeting                 # meeting mode (transcription + summary)
airmouse --office --dictation      # + voice typing
airmouse --meeting --transcribe    # + live transcription session
airmouse --offline                 # hard-offline; everything stays local
```

Office and meeting share one meeting session ("start meeting" in
office mode controls the same transcript as meeting mode), so you can
switch without losing the timeline.

## Phrase tables

**office** (`MODE_REGISTRY["office"]`):

| Say | Action |
|---|---|
| "start meeting" | start the (shared) meeting session |
| "stop meeting" | pause the meeting session |
| "capture task <text>" | add to the office task list (≤ 200 items) |

**meeting** (`MODE_REGISTRY["meeting"]`):

| Say | Action |
|---|---|
| "start transcription" | start the meeting timeline |
| "pause transcription" / "resume transcription" | pause/resume |
| "mark important" | timestamped important marker |
| "add action item <text>" | add an action item (≤ 200) |
| "add note" | timestamped note |
| "add decision" | record a decision |
| "add question" | record an open question |
| "bookmark moment" | bookmark the current moment |
| "search transcript" | search timeline entries for a term |
| "export transcript" | export the structured summary |

## The structured summary — exactly what it contains

`MeetingMode.summary()` returns a **structured, user-curated** record:

```json
{
  "title": "meeting",
  "elapsed": 1840.2,
  "action_items": ["email the team", "send the budget sheet"],
  "decisions": ["ship Friday"],
  "questions": ["who owns the rollout?"],
  "important": ["budget approved"],
  "timeline": [{"timestamp": 12.0, "kind": "important", "text": "budget approved", "slide": 0}]
}
```

* **No speaker-ID claims.** AirMouse does not identify, separate, or
  attribute speakers. Markers are user-driven; the transcript is
  whatever the local ASR heard. Any "who said what" would have to come
  from you.
* The summary contains **what you marked** — action items, decisions
  and questions appear because you (or your macros) marked them, not
  because the system inferred them. Nothing is auto-extracted from
  the transcript.
* `export_summary(path, fmt)` writes markdown (the timeline) or JSON
  (the structure above).

## Transcription in meetings

Pair `--meeting --transcribe` for a live caption + searchable history
(≤ 500 segments, export txt/json/md, substring search). **Honest
limitation:** transcription requires a real local ASR engine
(vosk/whisper/pocketsphinx) or deterministic transcript injection —
without one, markers and structure still work but speech-to-text is
absent (`airmouse voice-status` reports honestly what is installed).
Pause/resume transcription around sensitive conversations; privacy
mode disables history retention entirely.

## Task capture & privacy

* `capture task` items live in the office task list for the session;
  meeting artifacts exist only on your disk after you export them.
* Recording other people raises real consent issues — transcription
  history is OFF-switchable at any time from the privacy dashboard
  (see `docs/PRIVACY.md`), and `clear_interaction_history()` wipes
  transcript history immediately.
* Everything is local: no cloud transcription exists in AirMouse at
  all (the cloud flag is structurally impossible to enable).
