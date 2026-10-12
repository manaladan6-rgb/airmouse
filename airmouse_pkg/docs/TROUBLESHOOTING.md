# AirMouse v19 — Troubleshooting

Format: **Symptom → Diagnosis → Fix**. Every fix below is a command that
exists in v19. AirMouse never fakes success: if a probe could not
observe something, it says so.

## Symptom table

| Symptom | Diagnosis | Fix |
|---|---|---|
| `no camera detected` (doctor/capabilities/wizard) | OS has no usable camera at the probed index, or privacy settings block desktop apps | Windows: Settings → Privacy & security → Camera → allow desktop apps; connect/enable the webcam; `--cam N` selects another index; re-run `airmouse doctor` |
| `no microphone detected` | No usable input device (or privacy block) | Windows: Settings → Privacy & security → Microphone → allow desktop apps; connect a mic; `airmouse voice-status` shows devices + backend; voice stays OPTIONAL either way |
| Voice model download fails — **consent declined** | The pipeline treats a declined consent as `MODEL_DOWNLOAD_REQUIRED` — honest, nothing downloaded | Run `airmouse voice-setup` again and accept, or download the archive manually (URL is printed) into `~/.airmouse/models/vosk/` |
| Voice model download fails — **offline** | `offline=True` short-circuits BEFORE any network attempt with `MODEL_DOWNLOAD_REQUIRED` + manual instructions | Re-run when connected, or follow the printed manual path (the same 12-stage verification applies to a manually provided archive) |
| Voice model download fails — **SHA256/SHA512 mismatch** | Digest verification failed → the archive is DISCARDED, nothing extracted (never a half-trusted model) | Retry `airmouse voice-setup` (transient corruption); if it repeats, `airmouse repair` then `airmouse voice-setup`; compare the digests in `docs/V19_BASELINE.md §3` |
| `MODEL_CORRUPTED` (doctor/capabilities show it) | The installed model tree failed re-verification | `airmouse repair` — it auto-removes the corrupted copy (models are re-downloadable) and says so; then `airmouse voice-setup` |
| `Could not parse config` / weird behaviour after manual edits | Broken or legacy-encoded config (pre-v16.5.1 files could be cp1252) | `airmouse setup --repair-config` (backup → repair → validate → report) or `airmouse repair`; `reset --config` removes it (backed up into `backups/` first) and defaults are re-created on the next run |
| `input automation unavailable` / pynput raises on Linux | pynput needs a display server (X11/Wayland session); headless SSH/CI has none — this is honest reporting, not an error | Run on a machine with a display; on macOS grant Accessibility permission; on Windows it works out of the box |
| `pip install airmouse` tries to compile / fails building | A dependency resolved to an sdist — AirMouse's own installs can never do this (`--only-binary :all:`), but a hand-run pip can | Upgrade pip (`python -m pip install --upgrade pip`), retry with `pip install --only-binary :all: airmouse`; do NOT install a compiler — that is the wheels-only law |
| `disk full` during voice-setup | The pipeline hit ENOSPC | Free space (model: 41.2 MB zip / ~70.9 MB unpacked) and retry — the pipeline ROLLS BACK: nothing is left behind, a previous model is restored |
| Model state shows `MODEL_UNKNOWN` | The status record itself could not be read (never raised silently) | `airmouse repair`, then `airmouse doctor` |

## The 8 MODEL_* states (what they mean)

Emitted by `airmouse.model_integrity.ModelState` and shown by doctor /
capabilities / setup:

| State | Meaning | What to do |
|---|---|---|
| `MODEL_MISSING` | No model installed at `~/.airmouse/models/vosk` | Optional: `airmouse voice-setup` |
| `MODEL_DOWNLOAD_REQUIRED` | Consent declined or offline — nothing was fetched | Accept the consent prompt / reconnect; manual path is printed |
| `MODEL_DOWNLOADING` | Download in progress (observer state) | Wait |
| `MODEL_VERIFYING` | Size + SHA256 + SHA512 + CRC + structure checks running | Wait — a failure here discards the archive, never installs it |
| `MODEL_INSTALLING` | Staged extraction + atomic switch in progress | Wait — rollback restores the previous model on any failure |
| `MODEL_READY` | Installed AND verified against the pinned digests | Nothing — voice can use it |
| `MODEL_CORRUPTED` | Existing tree failed re-verification | `airmouse repair` (auto-removes) → `airmouse voice-setup` |
| `MODEL_INCOMPATIBLE` | Model present but not usable by this engine | `airmouse reset --models` → `airmouse voice-setup` |

(The status record itself failing yields `MODEL_UNKNOWN` — reported,
never guessed.)

## Where logs and diagnostics live

- `~/.airmouse/logs/` — log files.
- `~/.airmouse/diagnostics/` — diagnostic reports (v19 layout).
- `airmouse doctor --advanced` — the full technical report (same as
  `--verbose`); every WARNING/FAILED row explains WHAT / WHY / FIX.
- `airmouse doctor --json` — machine-readable report, including the
  v19 `interaction_readiness` block (registry + model record + ladder).
- `airmouse privacy` — the storage manifest: every on-disk artifact
  with purpose + location + exists flag.

## The deterministic recovery ladder (in order; each asks first)

1. **`airmouse repair`** — safe local fixes only: re-create the home
   structure, repair the config (backed up first), re-verify models and
   auto-remove a corrupted one, re-write the setup marker. Installs
   NOTHING without consent; pip is never run.
2. **`airmouse reset --config`** — remove configuration only. The
   config is backed up into `backups/` first; user data (profiles,
   transcripts, macros) is untouched.
3. **`airmouse reset --models`** — remove downloaded models only
   (re-downloadable via `airmouse voice-setup` / first run).
4. **`airmouse reset --all`** — remove the WHOLE home. The confirmation
   lists every entry with a human description + total MB, and says
   explicitly that profiles, transcripts and macros are included.
5. **`airmouse uninstall`** — remove the AirMouse home after the same
   explicit confirmation and content summary; the package itself is
   then removed with `python -m pip uninstall airmouse` (the exact
   command is printed — nothing is deleted silently).

Every rung states WHAT will be removed and requires a yes (`--yes`
exists for scripted use). Cancelling is always safe and reported as
such.

Every claim in this file is checkable against the code and the test
suite.
