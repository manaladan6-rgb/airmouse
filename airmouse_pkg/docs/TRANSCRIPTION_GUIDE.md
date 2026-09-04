# AirMouse v11.5 — Transcription & Voice Typing Guide

Live transcription (`airmouse --transcribe`), voice typing
(`airmouse --dictation`), the post-processing chain, history/privacy,
and the WER evaluator.

Everything here is **local and offline**. The engine never pretends a
provider is installed — provider availability is always surfaced
honestly in `status()` and `airmouse voice-status`.

---

## 1. The streaming pipeline

```
 MICROPHONE / deterministic transcript injection
   → AUDIO PREPROCESSING
   → EnergyVAD (hysteresis; speech end auto-finalizes)
   → STREAMING ASR  (provider)
   → PARTIAL TRANSCRIPT   ──► on_partial callbacks (HUD caption)
   → STABILIZATION
   → PUNCTUATION          (spoken punctuation → symbols)
   → CAPITALIZATION       (discourse commas → numbers → sentence case)
   → PERSONAL VOCABULARY  (proper nouns + learned corrections)
   → FINAL TRANSCRIPT     ──► on_final callbacks (TranscriptSegment)
```

`LiveTranscriptionEngine` requires no threads (an optional audio
thread can push chunks); it is deterministic when driven with explicit
`now` values and a scripted provider. Lifecycle: `start()` →
`pause()/resume()` → `stop()` (a pending partial is finalized on
stop).

## 2. Providers — what is real, what is not

| Provider | Status | Notes |
|---|---|---|
| `SimulatedStreamingProvider` | **always available** | deterministic scripted streaming for tests, CI and demos |
| `StreamingProviderAdapter` | wrapper | turns any v10 batch `OfflineSpeechProvider` (simulated / pocketsphinx / vosk / whisper) into a streaming interface with a deterministic word-by-word partial stream; native streaming providers pass their own partials through |
| PocketSphinx / Vosk / Whisper | **adapters exist in `offline_voice.py`; require optional deps** | guarded imports + `detect_providers()` auto-detection. If the package is not installed, the provider reports `available() → False`. **AirMouse never claims an engine is installed when it is not.** |

In the build sandbox none of the three engines is installed, so the
verified path is the simulated provider and the **transcript
injection** path (`feed_transcript`). Install one of the engines
(e.g. `pip install vosk`) and the same pipeline runs on real audio,
on-device.

## 3. Spoken punctuation table

`apply_spoken_punctuation` converts these spoken phrases
deterministically (phrase table from `transcription.SPOKEN_PUNCTUATION`):

| Say | You get | | Say | You get |
|---|---|---|---|---|
| "full stop" / "period" | `.` | | "open paren" / "close paren" | `(` `)` |
| "question mark" | `?` | | "open/close bracket" | `[` `]` |
| "exclamation mark/point" | `!` | | "open/close brace" | `{` `}` |
| "comma" | `,` | | "ampersand" | `&` |
| "semicolon" | `;` | | "at sign" | `@` |
| "colon" | `:` | | "hash symbol" | `#` |
| "new paragraph" | paragraph break | | "percent sign" | `%` |
| "new line" | line break | | "plus sign" / "equals sign" | `+` `=` |
| "dot dot dot" / "ellipsis" | `...` | | "slash" / "backslash" / "asterisk" | `/` `\` `*` |
| "smiley face" / "frowny face" | `:)` `:(` | | "open quote"/"close quote"/"apostrophe" | quotes |

Spacing is tidied afterwards (no space before `,.;:!?`, none after
opening brackets, collapsed blank lines).

## 4. Punctuation & capitalization heuristics — honest description

**These are deterministic text heuristics, not an AI punctuation
model.** Documented so you know exactly what will and will not happen:

* **Discourse commas** (`insert_discourse_commas`): a comma is added
  after a *vocative greeting* ("Hello Anna" → "Hello, Anna" — markers:
  hello/hi/hey) and after a *discourse marker* in sentence-initial
  position ("yes/yeah/ok/okay/well/now/thanks/welcome/however/
  meanwhile/unfortunately/honestly/basically"). Anything else is left
  as the ASR produced it — the heuristic never rewrites sentence
  structure.
* **Capitalization** (`capitalize_text`): sentence-start
  capitalization (after `.`, `!`, `?` and newlines), standalone `i` →
  `I`, and known proper nouns from your personal vocabulary. No
  title-casing heuristics beyond that.
* **Numbers** (`spell_numbers`): standalone number words become digits
  (zero…ninety, hundred, thousand). The word "one" is deliberately
  left alone outside isolated use.

For rich punctuation/paraphrasing you need a real ASR engine with its
own capabilities — AirMouse adds only the deterministic layer above.

## 5. Voice typing — modes and edit commands

`VoiceTypingEngine` mirrors the v10 `VoiceMode`:
`COMMAND` / `DICTATION` / `HYBRID`. Dictation applies the §3–4
formatting chain and appends to the committed buffer
(≤ 100,000 chars). Edit commands are exact deterministic phrase
matches, checked **before** dictation:

| Say | Effect |
|---|---|
| "delete last word" | remove the trailing word |
| "delete last sentence" | remove back to the last terminator |
| "scratch that" / "strike that" / "delete that" | delete the last finalized segment |
| "replace that with X" | replace the last segment with X |
| "replace X with Y" | replace the first occurrence of X |
| "capitalize that" / "uppercase that" / "lowercase that" | recase the last segment |
| "new line" / "new paragraph" | insert `\n` / `\n\n` |
| "undo" / "redo" | bounded undo/redo stacks (≤ 500 ops) |
| "select all" | emitted as a `select_all` op |
| "copy that" / "cut that" / "paste that" | emitted as copy/cut/paste ops |

Results are returned as inert `DictationOp` records (insert /
edit_command / replace) — data the text layer applies; nothing is
evaluated.

## 6. Text prediction & emoji suggestions

* **TextPredictor** — word completions + one phrase completion from
  the personal model, sorted by confidence, capped (`max_candidates`,
  5 by default). Context inputs: application name, document type,
  current text, mode, personal vocabulary. Off when no predictor is
  wired (e.g. `--no-intelligence`).
* **EmojiSuggester** — suggestions from the personal emoji model (or
  the deterministic `EMOJI_KEYWORDS` baseline map: "amazing" → 🔥 😂
  🎉 ❤️, "birthday" → 🎂 🥳, "deadline" → ⏰ 😰, …). **Rate-limited:
  at most one batch per 30 s** (`EMOJI_COOLDOWN_S`) and ≤ 3
  suggestions per batch, so it never spams. Choosing a suggestion is
  learned as a personal preference (`record_choice`).

## 7. History, privacy, export, search

* History: ≤ 500 segments; rolling buffer ≤ 200,000 chars.
* `history_enabled=False` (or the privacy dashboard's
  *transcription history* flag OFF) → segments are delivered to
  callbacks but not retained.
* Privacy mode turns history off (learning paused); clearing
  interaction history wipes transcript history too.
* Export: `export(path, fmt)` with `txt` (plain lines), `json`
  (`kind: airmouse-transcript`, full provenance per segment) or `md`
  (`HH:MM:SS (provider, conf%) text`); exports are bounded at 8 MB and
  written atomically.
* Search: `search(query, k)` — newest-first substring match over
  segments, ≤ 100 results.

## 8. Metrics & WER

`engine.status()` reports: state, provider + availability, installed
providers, segment/buffer counts, current partial, average confidence,
average finalization delay, false activations, language/model/mic.

`evaluate_wer(reference)` computes Word Error Rate (Levenshtein
distance on words — substitutions + deletions + insertions over the
reference length) between a reference text and the accumulated
transcript buffer. The `wer()` function itself is public for
evaluating provider quality on recorded material. It is an
*evaluation* tool — it never alters the transcript.

## 9. Quick start

```bash
airmouse --transcribe            # live transcription session (HUD caption)
airmouse --dictation             # voice typing with formatting + edit commands
airmouse voice-status            # which offline ASR engines are actually installed
airmouse --offline --dictation   # voice typing, hard-offline
```

Dictation/transcription work without a microphone in tests and demos
via transcript injection; with a mic + an installed offline ASR engine
they run fully on-device.
