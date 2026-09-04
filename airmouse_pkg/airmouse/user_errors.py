"""User-grade CLI error reporting for AirMouse (v15.1.0 hardening, spec §13).

Turns internal failures into friendly, actionable terminal messages so that
ordinary users never see a raw Python traceback, and so that no secret ever
leaks into terminal output:

    * GitHub / OpenAI / Slack tokens are redacted.
    * ``Authorization: Bearer ...`` headers are redacted.
    * ``API_KEY=...`` / ``TOKEN=...`` / ``SECRET=...`` / ``PASSWORD=...``
      style assignments collapse their values.
    * The user's home directory prefix is folded to ``~`` (this also scrubs
      temp paths that embed the username).

Fail-closed design: every user-visible channel (title, reason, fixes, hint,
debug traceback) is sanitized before it reaches the screen. The generic
Exception path never includes the original exception text at all.

Public API (pinned contract for the v15.1.0 coordinator):

    AirMouseUserError(title, reason, fixes, hint=None)
    format_user_error(err, debug=False) -> str
    sanitize_message(text) -> str
    run_cli_guarded(main_fn) -> int
    microphone_not_found() / camera_not_found() / display_unavailable()
    dependency_missing(package, extra=None)
    config_corrupt(path)
    permission_denied(action)

Python 3.9 compatible. Standard library only; no network, no subprocess.
"""

from __future__ import annotations

import os
import re
import sys
import traceback
from typing import Callable, Optional, Sequence

__all__ = [
    "AirMouseUserError",
    "format_user_error",
    "sanitize_message",
    "run_cli_guarded",
    "microphone_not_found",
    "camera_not_found",
    "display_unavailable",
    "dependency_missing",
    "config_corrupt",
    "permission_denied",
]

# ── sanitize_message internals ────────────────────────────────────────────

#: Maximum characters a sanitized (user-visible, non-debug) string may have.
_SANITIZE_MAX_CHARS = 500

#: Hard cap for the debug traceback block (still fully redacted).
_TRACEBACK_MAX_CHARS = 4000

_REDACTED = "<redacted>"

# Token shapes: GitHub PATs (classic + fine-grained), OpenAI-style sk- keys,
# Slack xoxb- tokens, and Authorization: Bearer headers.
_TOKEN_PATTERNS = (
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"gho_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    # (?<![A-Za-z0-9]) keeps "task-", "risk-", "mask-" from matching "sk-".
    re.compile(r"(?<![A-Za-z0-9])sk-\S+"),
    re.compile(r"xoxb-[A-Za-z0-9-]{10,}"),
    re.compile(r"(?i)\bbearer\s+\S+"),
)

# env-style key=value assignments whose *name* is a common secret-ish key
# (suffix match, so MY_API_KEY / AUTH_TOKEN / CLIENT_SECRET also collapse).
_ENV_PAIR_RE = re.compile(
    r"(?i)\b([A-Za-z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)[A-Za-z0-9_]*)"
    r"\s*=\s*\S+"
)


def _home_prefix_pattern() -> Optional["re.Pattern[str]"]:
    """Regex matching the user's home dir as a real path prefix (not mid-word)."""
    home = os.path.expanduser("~")
    if not home or len(home) < 2 or home in ("/", "~"):
        return None
    # Followed by anything that could continue a path component -> NOT the
    # user's home ("/home/zeta" must not become "/~eta", "/home/z.txt" stays).
    return re.compile(re.escape(home) + r"(?![\w.~-])")


def _redact(text: str) -> str:
    """Replace secrets / home paths. No truncation (used for tracebacks too)."""
    text = str(text)
    for pattern in _TOKEN_PATTERNS:
        text = pattern.sub(_REDACTED, text)
    text = _ENV_PAIR_RE.sub(lambda m: m.group(1) + "=" + _REDACTED, text)
    home = os.path.expanduser("~")
    if home and len(home) >= 2 and home not in ("/", "~"):
        home_re = _home_prefix_pattern()
        if home_re is not None:
            text = home_re.sub("~", text)
        # Temp dirs *inside* the home embed the username too — scrub them.
        for var in ("TMPDIR", "TEMP", "TMP"):
            tmp = os.environ.get(var)
            if tmp and len(tmp) > 3 and tmp.startswith(home):
                text = text.replace(tmp, "<tempdir>")
    return text


def sanitize_message(text: str) -> str:
    """Make ``text`` safe for terminal display.

    * folds the home directory prefix to ``~``
    * redacts ghp_/gho_/github_pat_/sk-/xoxb-/Bearer tokens -> ``<redacted>``
    * collapses secret-ish ``KEY=value`` pairs -> ``KEY=<redacted>``
    * caps output at 500 chars (ellipsis-terminated)
    """
    out = _redact(text)
    if len(out) > _SANITIZE_MAX_CHARS:
        out = out[: _SANITIZE_MAX_CHARS - 3] + "..."
    return out


# ── the exception type ────────────────────────────────────────────────────


class AirMouseUserError(Exception):
    """A user-facing AirMouse failure: what happened + how to fix it.

    Attributes:
        title:  short verb phrase completing "AirMouse could not <title>."
        reason: one-line plain-language explanation.
        fixes:  ordered, numbered remediation steps (stored as a tuple).
        hint:   optional extra tip rendered as ``Hint: ...``.
    ``str(exc)`` returns the title.
    """

    def __init__(self, title: str, reason: str, fixes: Sequence[str],
                 hint: Optional[str] = None) -> None:
        super().__init__(title)
        self.title = str(title)
        self.reason = str(reason)
        self.fixes: tuple = tuple(str(fix) for fix in fixes)
        self.hint = None if hint is None else str(hint)

    def __str__(self) -> str:
        return self.title


# ── formatting (spec §13 shape) ───────────────────────────────────────────

_GENERIC_FIXES: tuple = (
    "Re-run the command.",
    "If it persists, run: airmouse doctor",
    "Report the issue with the debug output: airmouse --debug ...",
)


def _render(title: str, reason: str, fixes: Sequence[str],
            hint: Optional[str]) -> str:
    lines = ["AirMouse could not " + title + ".", "", "Reason:", reason, "",
             "Fix:"]
    for i, fix in enumerate(fixes, 1):
        lines.append(str(i) + ". " + fix)
    if hint:
        lines.append("")
        lines.append("Hint: " + hint)
    lines.extend(["", "Run:", "", "airmouse doctor"])
    return "\n".join(lines)


def format_user_error(err: "AirMouseUserError | Exception", debug: bool = False) -> str:
    """Render ``err`` as the user-facing §13 message (sanitized, no secrets).

    ``debug=True`` appends the active traceback (redacted) for bug reports;
    normal users must never see it — the generic path never echoes the
    original exception text at all.
    """
    if isinstance(err, AirMouseUserError):
        text = _render(sanitize_message(err.title),
                       sanitize_message(err.reason),
                       [sanitize_message(f) for f in err.fixes],
                       sanitize_message(err.hint) if err.hint else None)
    else:
        text = _render("complete this action",
                       "An unexpected internal error occurred.",
                       list(_GENERIC_FIXES), None)
    if debug:
        tb = None
        if sys.exc_info()[0] is not None:
            tb = traceback.format_exc()
        if not tb or tb.strip() == "NoneType: None":
            tb = "".join(traceback.format_exception(type(err), err,
                                                    err.__traceback__))
            if "Traceback (most recent call last)" not in tb:
                # py3.12 omits the header when the exception was never raised
                tb = "Traceback (most recent call last):\n" + tb
        tb = _redact(tb)
        if len(tb) > _TRACEBACK_MAX_CHARS:
            tb = tb[: _TRACEBACK_MAX_CHARS - 3] + "..."
        text = text + "\n\nDebug traceback:\n" + tb.strip("\n") + "\n"
    return text


# ── the CLI guard (coordinator wraps main() with this) ────────────────────


def run_cli_guarded(main_fn: Callable[[], int]) -> int:
    """Run ``main_fn``; convert any failure into a friendly exit code.

    * AirMouseUserError  -> §13 message on stderr, exit 1
    * KeyboardInterrupt  -> "Interrupted." on stderr, exit 130
    * any other Exception-> generic §13 message (never a traceback), exit 1
    * otherwise          -> main_fn's return value (``None`` normalizes to 0)

    ``SystemExit`` (argparse errors, explicit sys.exit) passes through
    untouched so existing CLI exit codes keep working.
    """
    try:
        result = main_fn()
    except AirMouseUserError as err:
        print(format_user_error(err), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except Exception as err:  # noqa: BLE001 — deliberate last-resort guard
        print(format_user_error(err, debug=False), file=sys.stderr)
        return 1
    return 0 if result is None else result


# ── ready-made factories (exact wording pinned for the coordinator) ───────


def microphone_not_found() -> AirMouseUserError:
    return AirMouseUserError(
        title="use your microphone",
        reason="No microphone was detected on this system.",
        fixes=[
            "Plug in or enable a microphone, then try again.",
            "Check your system sound settings: the input must not be muted or disabled.",
            "Run: airmouse doctor",
        ],
        hint="Voice features are optional — AirMouse still runs without a microphone.",
    )


def camera_not_found() -> AirMouseUserError:
    return AirMouseUserError(
        title="start the camera",
        reason="No camera was detected on this system.",
        fixes=[
            "Connect a webcam and close any other app that is using it.",
            "If you have more than one camera, try: airmouse --cam 1",
            "Run: airmouse doctor",
        ],
        hint="airmouse doctor lists every camera index this machine exposes.",
    )


def display_unavailable() -> AirMouseUserError:
    return AirMouseUserError(
        title="access the display",
        reason="No usable graphical display is available in this session.",
        fixes=[
            "Run AirMouse from inside your desktop session (not from a plain SSH console).",
            "On Linux, make sure your session's DISPLAY or WAYLAND_DISPLAY variable is set.",
            "Run: airmouse doctor",
        ],
    )


def dependency_missing(package: str, extra: Optional[str] = None) -> AirMouseUserError:
    pkg = str(package).strip() or "the-package"
    if extra:
        name = str(extra).strip()
        why = (f"The optional '{pkg}' feature needs the "
               f"'airmouse[{name}]' extra, which is not installed.")
        cmd = f'python -m pip install "airmouse[{name}]"'
    else:
        why = (f"The optional package '{pkg}' is required for this "
               "feature but is not installed.")
        cmd = f"python -m pip install {pkg}"
    return AirMouseUserError(
        title="install a required dependency",
        reason=why,
        fixes=[
            f"Install it with: {cmd}",
            "Then re-run the same command again.",
            "Run: airmouse doctor",
        ],
        hint="Install into the same Python environment you run airmouse from.",
    )


def config_corrupt(path: str) -> AirMouseUserError:
    return AirMouseUserError(
        title="load your settings",
        reason=(f"The settings file {path} could not be read "
                "(it may be corrupted or incomplete)."),
        fixes=[
            f"Back up the file (copy it somewhere safe), then delete it: {path}",
            "Re-run: airmouse setup",
            "Run: airmouse doctor",
        ],
        hint="AirMouse recreates the settings file with defaults on the next start.",
    )


def permission_denied(action: str) -> AirMouseUserError:
    act = str(action).strip() or "perform this action"
    return AirMouseUserError(
        title=act,
        reason=f"The operating system denied permission to {act}.",
        fixes=[
            "Windows: open Settings > Privacy & security and allow this permission for the app.",
            "macOS/Linux: grant the permission under System Settings (Privacy & Security).",
            "Run: airmouse doctor",
        ],
        hint="If your OS shows a permission prompt, approve it and retry.",
    )
