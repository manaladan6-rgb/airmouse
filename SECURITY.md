# Security Policy

AirMouse controls your mouse, keyboard and browser. That is an
extraordinary amount of trust for a desktop application, and the
security architecture treats it that way.

## Supported versions

| Version | Supported |
|---|---|
| 19.0.x | ✅ |
| < 19.0 | best-effort only — please update |

## How to report a vulnerability

**Please do not open a public issue for a security problem.**

Use GitHub's *Report a vulnerability* (Security → Advisories → Report a
vulnerability) on this repository. Include: affected version
(`airmouse --version`), a proof of concept if you have one, and your
assessment of severity. You will get an acknowledgement within a few
days and a fix timeline for confirmed issues. We credit reporters in the
release notes unless you prefer otherwise.

## The security model, in one page

**Execution spine.** Every computer action — human-initiated or
agent-initiated — passes through one permission gate: risk classification
→ policy → rate limit → execution → observation → verification.
There is no alternate execution path; agents get **no bypass**.

**Prediction never executes.** Recognizers and predictors can *advise*
the permission layer. They can never authorize an action by themselves.
Authorization that is unavailable or invalid fails **closed**.

**Emergency stop wins.** The e-stop state overrides every other state in
the hierarchy `EMERGENCY STOP > human override > safety > permission >
agent > prediction`.

**No hidden network.** The only network operations in the codebase are:

1. Model download (`airmouse voice-setup`) — explicit user consent,
   size disclosed first, official source, SHA-256 verified **before**
   extraction, Zip-Slip-safe extraction that refuses executable archive
   members.
2. Update check (`airmouse update --check`) — explicit only, check-only,
   never downloads or installs anything.

No telemetry. No analytics. No crash reporting.

**Loopback pinning.** The browser bridge contacts DevTools endpoints on
`127.0.0.1` only; non-loopback discovery results are logged and refused.

**Subprocess discipline.** Computer-control subprocess calls use argv
lists — never `shell=True`. The model pipeline never invokes a compiler
or a package manager without an explicit, logged consent step.

**Local data.** All state lives in `~/.airmouse/`. `airmouse privacy`
prints what is stored where; `airmouse reset --all` and
`airmouse uninstall` explain exactly what they delete and ask first.
Destructive lifecycle operations require explicit confirmation.

## What we do NOT claim

- No security certification, no formal audit, no bug bounty.
- "Offline-capable" means the control paths do not need the network;
  the optional model download and the explicit update check are the only
  exceptions and both are consent-gated.
- Physical-device behaviour (your specific webcam/microphone) is
  validated by you via `airmouse test --guided` and
  [WINDOWS_REAL_WORLD_TEST.md](WINDOWS_REAL_WORLD_TEST.md).

## Known hardening backlog (transparent)

- Windows installer code-signing is scaffolded but not yet published —
  until then, verify downloads via the SHA-256/SHA-512 files attached to
  each GitHub release.
- The PyPI name `airmouse` is the official distribution; verify you are
  installing `pip install airmouse` from PyPI, not a look-alike.
