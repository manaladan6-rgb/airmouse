"""AirMouse first-run launcher (v19 Phase 4 — Instant Deployment).

A brand-new user who just runs ``airmouse`` gets a friendly launcher —
not a camera window, not developer jargon.  The launcher shows the
interaction status as honest dots (probed, never guessed) and offers
the shortest paths: start, test hardware, setup, learn, settings.

Coordinator contract (unchanged): ``should_show_menu`` at the top of
``main()``; when True, ``run_menu`` → the returned argv is dispatched
through the normal CLI path.  An empty argv (choice 1, "Start
AirMouse") is a VALID result meaning "fall through and start the app".

The first-run marker lives at ``<airmouse_home>/.setup_complete`` where
``airmouse_home`` is ``$AIRMOUSE_HOME`` if set, else ``~/.airmouse``.

Fail-closed laws: the menu must NEVER block the app — every probe is
guarded, every failure degrades into an honest dot, and EOF/abort
simply proceeds normally.  No network.  No subprocess.

Python 3.9 compatible.
"""

from __future__ import annotations

import os
import sys
from typing import Callable, List, Optional, Sequence, TextIO, Tuple

__all__ = [
    "MENU_ITEMS", "render_menu", "handle_choice", "marker_path",
    "is_first_run", "mark_setup_complete", "should_show_menu", "run_menu",
    "render_status",
]

# ── launcher definition (v19 Phase 4 — replaces the dev-flavored list) ────

#: (key, label, argv) — empty argv means "start the main app".
MENU_ITEMS: List[Tuple[str, str, List[str]]] = [
    ("1", "Start AirMouse", []),
    ("2", "Test My Hardware", ["doctor"]),
    ("3", "Setup", ["setup"]),
    ("4", "Learn Gestures", ["academy", "gesture"]),
    ("5", "Settings", ["--settings"]),
    ("6", "Diagnostics (advanced)", ["doctor", "--advanced"]),
    ("7", "Privacy", ["privacy"]),
    ("0", "Help", ["--help"]),
]

_MENU_ATTEMPTS = 3


def render_menu(version: str) -> str:
    """Render the full launcher block (no trailing newline)."""
    lines = [f"AirMouse v{version}",
             "Human–Computer Intelligence", ""]
    for key, label, _argv in MENU_ITEMS:
        lines.append(f"[{key}] {label}")
    lines.append("")
    return "\n".join(lines)


def render_status() -> str:
    """The honest status panel (dots probed, never guessed).

    Uses the quick capability registry; ANY failure degrades to a
    question mark rather than a wrong claim.  The panel is pure output —
    it never blocks, never raises, never touches the network.
    """
    try:
        from .capability_registry import build_registry
        reg = build_registry(quick=True)
        marks = {"READY": "●", "OPTIONAL": "○", "WARNING": "◐"}
        order = (("gesture", "Camera"), ("voice", "Microphone"),
                 ("voice", "Voice"), ("gaze", "Gaze"))
        # row per user-facing item, deduplicated by display
        rows: List[Tuple[str, str]] = []
        seen = set()
        for key, display in order:
            cap = reg.get(key)
            if cap is None or display in seen:
                continue
            seen.add(display)
            state = cap.state
            dot = marks.get(state, "·" if state not in
                            ("FAILED", "INCOMPATIBLE") else "✗")
            rows.append((dot, display))
        for key, display in (("input", "Mouse control"),):
            cap = reg.get(key)
            if cap is not None:
                state = cap.state
                dot = marks.get(state, "·" if state not in
                                ("FAILED", "INCOMPATIBLE") else "✗")
                rows.append((dot, display))
        _mode, readiness = reg.best_mode()
        lines = ["Status:"]
        for dot, display in rows:
            lines.append(f"  {dot} {display}")
        verdict = ("System Ready" if readiness.level.value == "FULL"
                   else f"System: {readiness.mode.upper()}")
        lines.append(verdict)
        return "\n".join(lines)
    except Exception:  # noqa: BLE001 — the menu must never block the app
        return "Status:\n  ? (probe unavailable — run Test My Hardware)"


def handle_choice(choice: str) -> Optional[List[str]]:
    """Map raw user input to an argv list.

    Returns a fresh copy of the item's argv; ``None`` for empty or unknown
    input (caller re-prompts). Note ``"1"`` -> ``[]`` (valid, distinct from
    ``None``): it means "start the main app".
    """
    key = "" if choice is None else str(choice).strip()
    if not key:
        return None
    for item_key, _label, argv in MENU_ITEMS:
        if key == item_key:
            return list(argv)
    return None


# ── first-run marker ──────────────────────────────────────────────────────

_MARKER_NAME = ".setup_complete"


def _resolve_airmouse_home() -> str:
    """``$AIRMOUSE_HOME`` if set, else the persistence home, else ~/.airmouse.

    ``persistence`` is a sibling module; it may not exist yet (perfectly
    normal) or may be mid-write, so every step is guarded and the
    duplicated local default always wins as fallback.
    """
    env = os.environ.get("AIRMOUSE_HOME")
    if env and env.strip():
        return env
    try:  # defensive: persistence.py may not exist in this build stage
        from . import persistence  # type: ignore
    except Exception:  # noqa: BLE001 — ImportError or partially-written module
        persistence = None  # type: ignore[assignment]
    if persistence is not None:
        helper = getattr(persistence, "airmouse_home", None)
        if callable(helper):
            try:
                value = helper()
            except Exception:  # noqa: BLE001 — never let marker IO crash CLI
                value = None
            if isinstance(value, str) and value:
                return value
    return os.path.join(os.path.expanduser("~"), ".airmouse")


def marker_path() -> str:
    """Path of the first-run completion marker (str)."""
    return os.path.join(_resolve_airmouse_home(), _MARKER_NAME)


def is_first_run() -> bool:
    """True iff the marker is absent. IO errors count as first run (fail-open
    toward showing the friendly menu — the menu itself is always safe)."""
    try:
        return not os.path.exists(marker_path())
    except OSError:
        return True


def mark_setup_complete() -> bool:
    """Atomically drop the first-run marker; returns True on success.

    Creates parent dirs as needed; any failure (unwritable home, blocked
    path) is swallowed and reported as False — never raises.
    """
    marker = marker_path()
    tmp = marker + ".tmp"
    try:
        parent = os.path.dirname(marker)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write("setup-complete\n")
        os.replace(tmp, marker)
        return True
    except Exception:  # noqa: BLE001 — marker is best-effort, fail-closed
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


# ── coordinator hooks ─────────────────────────────────────────────────────


def should_show_menu(argv: Optional[Sequence[str]] = None) -> bool:
    """True iff: no CLI args AND first run AND stdout is a TTY.

    Safe to call unconditionally at the top of ``main()`` — every condition
    is individually guarded so it can never raise.
    """
    if argv is None:
        argv = sys.argv[1:]
    if argv:
        return False
    try:
        is_tty = sys.stdout.isatty()
    except (AttributeError, ValueError, OSError):
        return False
    if not is_tty:
        return False
    return is_first_run()


def run_menu(version: str, out: Optional[TextIO] = None,
             input_fn: Callable[..., str] = input) -> Optional[List[str]]:
    """Print the launcher and read a choice (up to 3 attempts).

    Returns the chosen argv (``[]`` = Start AirMouse), or ``None`` when the
    user gives up / EOFs / aborts — the caller then just proceeds normally.
    ``out=None`` resolves to the *current* ``sys.stdout`` at call time.
    """
    dest = sys.stdout if out is None else out
    for _attempt in range(_MENU_ATTEMPTS):
        try:
            print(render_menu(version), file=dest)
            try:
                print(render_status(), file=dest)
            except Exception:  # noqa: BLE001 — status is best-effort
                pass
            lines = ["Choose an option:"]
            print("\n".join(lines), file=dest)
            try:
                dest.flush()
            except Exception:  # noqa: BLE001 — streams without flush
                pass
            raw = input_fn()
        except (EOFError, KeyboardInterrupt):
            return None
        choice = handle_choice("" if raw is None else raw)
        if choice is not None:
            return choice
        # invalid input -> loop reprints the menu for the next attempt
    return None
