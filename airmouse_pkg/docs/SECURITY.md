# AirMouse v11.5 — Security

Scope: the v11.5 attack surface (intelligence subpackage, transcription
pipeline, text control, world model, fusion2, workflows) plus the
inherited v10 guarantees. Every claim below was re-verified against the
v11.5 tree with grep and by reading the code paths.

## 1. Dangerous-primitive audit (re-run for v11.5)

| Primitive | Result |
|---|---|
| `shell=True` | **absent** — the only textual match is the `system_actions.py` docstring stating "NO shell"; all subprocess calls are argv lists |
| `eval(` | **absent** in all 58 package modules |
| `exec(` | **absent** |
| `os.system` | **absent** |
| `subprocess` usage | argv-list only, with timeouts (system/file executors, media-key helpers, screen-perception probes). One legitimate `xdotool … --shell` *argument* exists (xdotool's own output-format flag) — not a shell invocation |

## 2. Inherited v10 guarantees (unchanged)

* **System/file executors:** 16 SYSTEM_OPS + 8 FILE_OPS are the only
  executable operations; argv-only subprocess; file paths must resolve
  inside allowlisted base roots (traversal and outside-root paths
  refused); `sanitize_file_name`; `validate_url` accepts
  http/https/file schemes only.
* **Browser content is untrusted data:** page text can only be
  *matched against* the fixed template grammar, never executed; the
  CDP adapter evaluates only fixed snippets built from our parameters;
  the localhost bridge server (127.0.0.1:17843) parses JSON payloads
  only, rejects oversized (> 256 KB) and invalid ones, and is
  hard-bound to loopback.
* **Safety intact:** e-stop latch, sliding-window rate limiter, click
  cooldowns, confidence gates, one-shot confirmation flow, no
  auto-retry of sensitive/blocked actions; destructive ops require
  explicit confirmation.

## 3. v11.5 input validation surfaces

| Surface | Rule | Behavior on violation |
|---|---|---|
| Workflow step names | regex `^[a-z0-9][a-z0-9_-]{0,63}$` | step rejected (`create_manual` returns `None`) |
| Workflow step params | keys ≤ 32 chars, values ≤ 120 chars, ≤ 6 params | truncated/coerced |
| Workflow runs | ≤ 24 steps; destructive needs prior preview + per-step confirmation; conditions must hold | run aborts with a reason string |
| Memory patterns | ≤ 200 chars; `is_sensitive()` screen; token-like blob redaction | credential-shaped input **refused outright** (fail-closed); blobs replaced by `[redacted-*]` placeholders |
| Memory context pairs | ≤ 8 keys × 200 chars; each `key=value` scrubbed | sensitive pairs dropped |
| Vocabulary terms/corrections | ≤ 64 chars; caps 20k/5k | rejected/evicted |
| Profile import | JSON only; per-section validation; `MAX_EXPORT_BYTES` (4 MB) payload bound | malformed/sensitive rows skipped; counts returned |
| Model artifact | magic `AIMM` + format version; bounds-checked reads; count ceilings per section | `ModelError` → plugin state `CORRUPTED`/`INCOMPATIBLE`, never a crash |
| Typed text (TextOp.TYPE) | ≤ 4,000 chars | op refused |
| Dictation buffer | ≤ 100,000 chars; undo/redo ≤ 500 ops | truncated/bounded |
| Transcript segments/history | segment ≤ 4,000 chars; ≤ 500 segments; buffer ≤ 200k chars; export ≤ 8 MB | bounded |
| Plugin facade | every public method catches all exceptions | documented no-op, `last_error` recorded |

## 4. Malicious-input test coverage (tests/test_v115.py §43)

The suite drives every v11.5 parser with a fixed hostile corpus —
SQL injection (`'; DROP TABLE users; --`), command substitution
(`$(rm -rf /)`, `` `cat /etc/passwd` ``, `$(reboot)`), path traversal
(`../../etc/passwd`), binary junk, 10,000-char strings, XSS
(`<script>alert(1)</script>`), credential shapes (`password=hunter2`,
`ghp_`-style tokens) — and asserts:

1. `VoiceTypingEngine.ingest` survives every payload; produced ops are
   inert data (never evaluated, never passed to any process).
2. `TextController` refuses oversized typed text.
3. `ContextualCommandResolver` cannot be injected into `SYSTEM_OP`
   (malicious "click that…" variants resolve to nothing or harmless
   types).
4. `WorkflowStore.create_manual` rejects shell fragments, SQL, and
   traversal strings as step names; valid identifiers pass.
5. `InteractionMemory.record` never persists credentials —
   `mem.get("password=hunter2")` is `None` and redacted placeholders
   are enforced for token-like blobs.
6. `IntelligencePlugin.import_profile` never raises on fuzzed JSON
   ("not json at all", `[]`, `null`, wrong-typed sections).
7. `LiveTranscriptionEngine` survives binary audio chunks and hostile
   transcript text — the text is stored as **data** (asserted present
   in the segment, executed by nothing).
8. Config loading ignores malformed values without raising.

## 5. PREDICTION ≠ EXECUTION as a security property

Suggestions and predictions are inert dataclasses with no executor
access. Enforcement points (architecture doc §8): data types →
ProactiveAssistant (prepare() refuses URLs/paths/commands; destructive
suggestions suppressed) → WorldModel (destructive never shown as
likely intent) → `FusedIntentCandidate.executable` (prediction-only
candidates never executable; conflicts force confirmation) → the v10
safety gate. A hostile "suggestion" therefore has no path to an
executed action without passing confirmation gates designed for
humans.

## 6. Threat-model notes (honest scope)

* **In scope:** malicious *input* through any v11.5 parser (voice
  text, injected transcripts, imported profiles/workflows, model
  artifacts, workflow definitions, typed text lengths); accidental
  destruction via automation (preview + per-step confirmation);
  secret leakage into the learned store (fail-closed scrubbing).
* **Out of scope:** a fully compromised host (AirMouse runs with your
  user privileges and cannot contain that); side channels (learned
  pattern files are on-disk plaintext JSON — disk encryption is the
  OS's job); social engineering of the human confirming destructive
  steps; denial-of-service via resource exhaustion is mitigated by
  hard bounds but not adversarially hardened.
* **Known trade-off:** the learned-artifact files under
  `~/.airmouse/intelligence/` are readable by anything running as your
  user. They are scrubbed (no credentials), bounded, and exportable
  for inspection — `delete_learned_data()` wipes them.

## 8. Network surface (audited for v11.5)

The only outbound network call in the entire package is the
**one-time MediaPipe hand-landmarker model download** in
`airmouse/tracker.py` (v1-era code: `urlretrieve(MODEL_URL, MODEL_PATH)`
to a local cache file, skipped when the file already exists).

Everything else is loopback-only:

* CDP browser bridge is host-pinned to `127.0.0.1:9222`
* the local extension bridge server binds `127.0.0.1:17843`
* voice/ASR/gaze/RF/intelligence paths perform zero networking

`airmouse --offline` (v10 `OfflineGate`) engages a real socket-level
block that makes even the tracker model download fail loudly; the
intelligence plugin, transcription engine, fusion and the full command
stack all keep working with networking disabled (verified by the 18/18
offline self-test under `network_isolation()`).

Telemetry: none exists. Cloud: no code path. The v11.5
`PrivacyDashboard` exposes `telemetry=False` / `cloud=False` as
structural facts, not settings that could be flipped on.

## 9. Residual risks (honest)

* The tracker model download fetches over HTTPS from
  `storage.googleapis.com` on first webcam use when not in offline
  mode. Users needing strict egress control should pre-seed the model
  file or always run `--offline`.
* Third-party local ASR providers (vosk/whisper/pocketsphinx) run
  their own native code; installing them is an explicit user choice
  and they are never loaded unless present.
* The `.airmouse/intelligence/` artifact directory is user-local
  plaintext/binary data; it contains scrubbed patterns only
  (credentials refused at write time), but disk encryption remains the
  user's responsibility, as with any local profile data.
