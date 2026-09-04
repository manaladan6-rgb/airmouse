# AirMouse v11.5 — Student Guide

Studying hands-free: notes, study timer, source capture, bookmarks and
searchable notes.

## Activation

```bash
airmouse --student                      # student mode
airmouse --student --dictation          # + voice typing for notes
airmouse --student --transcribe         # + live transcription caption
airmouse --offline                      # hard-offline (nothing leaves the machine)
```

Phrase table (`modes.MODE_REGISTRY["student"]`) — exact deterministic
matches handled by `ModeController`:

| Say | Action |
|---|---|
| "start study session" | start the study timeline + pomodoro timer |
| "take a note" / "take a note <text>" | add a timestamped note (≤ 300 notes, ≤ 2,000 chars each) |
| "mark this important" | mark recent notes important (queried when a term follows) |
| "save this source" | capture a source (title/url/selection verbatim) |
| "next page" / "previous page" | page through reading material (generic keys) |

## Study timer

`StudyTimer` is a bounded pomodoro-style state machine (defaults:
25 min focus / 5 min break, no threads — `check()` returns
`focus_done` / `break_done` / `None` and is polled by the session
loop). It counts completed focus blocks; `stop()` reports minutes and
blocks for the session summary.

## Notes

`NotesStore` keeps ≤ 300 notes with tags (≤ 6 × 24 chars), an
important flag, and timestamps. `search(query, k)` matches note text
and tags, newest first. Notes export as markdown (⭐ marks important):

```
# Notes
- Exam covers chapters 4-6 ⭐ #exam
- Keynesian cross: plot Y=E
```

## Source capture — verbatim, never fabricated

`SourceCapture` stores **provenance verbatim**: title (≤ 160 chars),
URL (only `http://`, `https://`, `file://` schemes are recorded —
anything else is dropped to empty), and the selection **exactly as
captured** (≤ 2,000 chars). It never alters, summarizes, or invents
source content, and never fabricates a result on your behalf —
research assistance here means navigation and organization only.
Annotations (`annotate(index, text)`) are stored separately from the
selection, so original text and your commentary never mix.

The research workflow (`ResearchMode`) follows
`QUESTION → SEARCH → SOURCE → READ → CAPTURE → ANNOTATE → ORGANIZE`
and shares the same verbatim rule. Its `organize()` output is your own
sources + notes, nothing generated.

## Study session timeline

`StudentMode` runs a `TimelineSession("study-session")` alongside the
timer; entries (topic/note/important/bookmark) export as markdown or
JSON (`kind: airmouse-timeline`).

## Honest limitations

* Dictation of notes requires an installed offline ASR engine or
  transcript injection (see `docs/TRANSCRIPTION_GUIDE.md` §2);
  `airmouse voice-status` shows what is actually available.
* "save this source" captures what you give it (title/URL/selection);
  it does not crawl pages or extract content by itself.
* The study timer is cooperative (polled), not a wall-clock alarm; it
  never fires notifications by itself.

All artifacts live on your disk only — nothing syncs anywhere.
