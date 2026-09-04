"""
airmouse.system_actions — v10 System + File Action Executors 🖥️
================================================================

Executors for the v10 canonical action vocabulary that go beyond
pointer/keyboard: system operations (volume, media, lock, power…)
and file operations (open, create, rename, copy, move, delete…).

SECURITY CONTRACT (mission §26)
-------------------------------
- NO shell.  Every subprocess is an argv LIST (never ``shell=True``);
  user-derived strings are passed as arguments, never interpolated
  into a command line by the shell.
- File operations are rooted in an explicit allowlist of base
  directories (default: the user's home).  Any path that resolves
  outside the roots is REFUSED.
- Destructive ops (delete_file, shutdown, restart, sleep, lock) are
  flagged ``destructive`` so the safety layer demands confirmation.
- Everything degrades gracefully: without the OS tooling the executor
  reports available()=False and execute() returns a failure dict —
  never raises, never blocks the interaction loop.

Deterministic doubles
---------------------
- :class:`MockSystemExecutor` / :class:`MockFileExecutor` record every
  call and support scripted failures for tests.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "SYSTEM_OPS", "DESTRUCTIVE_SYSTEM_OPS", "FILE_OPS", "DESTRUCTIVE_FILE_OPS",
    "SystemActionExecutor", "MockSystemExecutor",
    "FileActionExecutor", "MockFileExecutor", "sanitize_file_name",
    "validate_url",
]

#: allowed system operations (allowlist — nothing else executes)
SYSTEM_OPS: Tuple[str, ...] = (
    "volume_up", "volume_down", "mute", "unmute",
    "media_play", "media_pause", "media_next", "media_previous",
    "lock", "sleep", "shutdown", "restart",
    "brightness_up", "brightness_down", "bluetooth_on", "bluetooth_off",
)

#: ops that must pass through the safety confirmation gate
DESTRUCTIVE_SYSTEM_OPS: Tuple[str, ...] = (
    "shutdown", "restart", "sleep", "lock",
)

#: allowed file operations
FILE_OPS: Tuple[str, ...] = (
    "open", "create_folder", "rename", "copy", "paste", "move",
    "delete", "select",
)

DESTRUCTIVE_FILE_OPS: Tuple[str, ...] = ("delete", "move")

_IS_WINDOWS = platform.system() == "Windows"
_IS_LINUX = platform.system() == "Linux"
_IS_MAC = platform.system() == "Darwin"


# ---------------------------------------------------------------------------
# Input sanitization helpers (§26)
# ---------------------------------------------------------------------------


def sanitize_file_name(name: Any, max_len: int = 120) -> str:
    """Strip path separators/control chars from a USER-SUPPLIED name.

    Pure + total.  The result is safe to join under an allowlisted root:
    no separators, no traversal, no hidden control characters.
    """
    s = str(name or "")
    s = os.path.basename(s.replace("\\", "/"))
    s = "".join(ch for ch in s if ch.isprintable() and ch not in '<>:"|?*')
    s = s.strip().strip(".")
    if len(s) > max_len:
        s = s[:max_len]
    return s


def validate_url(url: Any) -> Tuple[bool, str]:
    """Validate a user/browser-supplied URL for OPEN_URL/NAVIGATE.

    Only http/https/file schemes pass.  Rejects whitespace, control
    characters and scheme shenanigans ("javascript:", "data:", …).
    Returns (ok, cleaned_url).
    """
    s = str(url or "").strip()
    if not s or len(s) > 2048:
        return False, ""
    if any(ord(ch) < 0x20 for ch in s) or any(ch.isspace() for ch in s):
        return False, ""
    low = s.lower()
    for prefix in ("http://", "https://", "file://"):
        if low.startswith(prefix):
            # file:// stays absolute; http(s) cleaned of stray spaces
            return True, s
    # bare "example.com" or "localhost:9222" → default to https
    if "://" in low:
        return False, ""       # other schemes are refused
    hostish = s.split("/")[0]
    if not hostish or "." not in hostish and hostish != "localhost":
        return False, ""
    return True, "https://" + s


# ---------------------------------------------------------------------------
# System executor
# ---------------------------------------------------------------------------


@dataclass
class SysResult:
    ok: bool
    op: str
    message: str = ""
    available: bool = True


class SystemActionExecutor:
    """Best-effort, shell-free system operations per platform.

    Media/volume: Windows uses the existing ctypes SendInput helpers in
    :mod:`airmouse.keyboard`; Linux prefers pactl/playerctl/xdotool;
    macOS prefers osascript.  Power ops (shutdown/restart/sleep/lock)
    are available() but ALWAYS destructive-flagged; the safety layer
    gates them (§18) — this executor never bypasses confirmation.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        cfg = dict(config or {})
        self.allow_power = bool(cfg.get("allow_power", True))
        self.timeout = float(cfg.get("timeout", 4.0))
        self._kb = None
        self._kb_tried = False

    # -- availability ---------------------------------------------------------

    def available(self) -> bool:
        try:
            if _IS_WINDOWS:
                return True  # ctypes SendInput path
            if _IS_LINUX:
                return any(shutil.which(t) for t in
                           ("pactl", "playerctl", "xdotool", "gnome-shell"))
            if _IS_MAC:
                return True  # osascript ships with macOS
        except Exception:
            return False
        return False

    def _keyboard(self):
        """Lazily load the v5 cross-platform keyboard helper."""
        if self._kb_tried:
            return self._kb
        self._kb_tried = True
        try:
            from .keyboard import CrossPlatformKeyboard  # guarded
            self._kb = CrossPlatformKeyboard()
        except Exception:
            try:
                from keyboard import CrossPlatformKeyboard  # repo-root layout
                self._kb = CrossPlatformKeyboard()
            except Exception:
                self._kb = None
        return self._kb

    def _argv_run(self, argv: List[str]) -> bool:
        """Run one argv list WITHOUT a shell; never raises."""
        try:
            r = subprocess.run(
                argv, shell=False,
                timeout=self.timeout,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return r.returncode == 0
        except Exception:
            return False

    def is_destructive(self, op: str) -> bool:
        return str(op or "") in DESTRUCTIVE_SYSTEM_OPS

    # -- dispatch -------------------------------------------------------------

    def execute(self, op: str, params: Optional[Dict[str, Any]] = None
                ) -> SysResult:
        """Execute one allowlisted system operation.  Never raises."""
        op = str(op or "")
        params = dict(params or {})
        if op not in SYSTEM_OPS:
            return SysResult(False, op, "op_not_allowed")
        if op in DESTRUCTIVE_SYSTEM_OPS and not self.allow_power:
            return SysResult(False, op, "power_ops_disabled")

        kb = self._keyboard()

        # media keys via the v5 cross-platform helper first
        if op == "media_play":
            if kb is not None and _kb_call(kb, "media_play_pause"):
                return SysResult(True, op)
            return SysResult(self._media_key("play"), op,
                             "" if _media_ok() else "media_unavailable")
        if op == "media_pause":
            if kb is not None and _kb_call(kb, "media_play_pause"):
                return SysResult(True, op)
            return SysResult(self._media_key("play"), op)
        if op == "media_next":
            return SysResult(self._media_key("next"), op)
        if op == "media_previous":
            return SysResult(self._media_key("previous"), op)

        # volume
        if op in ("volume_up", "volume_down", "mute", "unmute"):
            return self._volume(op)

        # power (destructive — safety layer confirms BEFORE calling here)
        if op == "lock":
            return SysResult(self._lock(), op)
        if op == "sleep":
            return SysResult(self._power(), op)
        if op == "shutdown":
            return SysResult(self._power("shutdown"), op)
        if op == "restart":
            return SysResult(self._power("restart"), op)

        # display / bluetooth (platform-dependent, may be unsupported)
        if op in ("brightness_up", "brightness_down"):
            direction = "up" if op.endswith("up") else "down"
            return SysResult(self._brightness(direction), op,
                             "" if True else "")
        if op in ("bluetooth_on", "bluetooth_off"):
            return self._bluetooth(op)

        return SysResult(False, op, "unsupported_op")

    # -- per-platform helpers ------------------------------------------------

    def _media_key(self, which: str) -> bool:
        try:
            if _IS_WINDOWS and self._kb is not None:
                fn = getattr(self._kb, "media_" + which, None)
                if callable(fn):
                    return bool(fn())
            if _IS_LINUX:
                playerctl = shutil.which("playerctl")
                if playerctl:
                    argv = [playerctl]
                    if which_arg := {"next": "next", "previous": "previous",
                                     "play": "play-pause"}.get(which):
                        argv.append(which_arg)
                    return self._argv_run(argv)
            if _IS_MAC:
                key = {"next": "next track", "previous": "previous track",
                       "play": "play"}.get(which, "play")
                return self._argv_run(
                    ["osascript", "-e", f'tell application "Music" to {key}'])
        except Exception:
            return False
        return False

    def _volume(self, op: str) -> SysResult:
        try:
            if _IS_LINUX:
                pactl = shutil.which("pactl")
                if pactl:
                    if op == "mute":
                        return SysResult(self._argv_run(
                            [pactl, "set-sink-mute", "@DEFAULT_SINK@", "toggle"]), op)
                    if op == "unmute":
                        return SysResult(self._argv_run(
                            [pactl, "set-sink-mute", "@DEFAULT_SINK@", "0"]), op)
                    pct = "+5%" if op == "volume_up" else "-5%"
                    return SysResult(self._argv_run(
                        [pactl, "set-sink-volume", "@DEFAULT_SINK@", pct]), op)
            if _IS_WINDOWS and self._kb is not None:
                fn = getattr(self._kb, op, None)
                if callable(fn):
                    return SysResult(bool(fn()), op)
            if _IS_MAC:
                script = {"volume_up": "set volume output volume ((output volume of (get volume settings)) + 5)",
                          "volume_down": "set volume output volume ((output volume of (get volume settings)) - 5)",
                          "mute": "set volume output muted true",
                          "unmute": "set volume output muted false"}[op]
                return SysResult(self._argv_run(["osascript", "-e", script]), op)
        except Exception:
            pass
        return SysResult(False, op, "volume_unavailable")

    def _lock(self) -> bool:
        try:
            if _IS_WINDOWS:
                return self._argv_run(["rundll32.exe", "user32.dll,LockWorkStation"])
            if _IS_MAC:
                return self._argv_run(["pmset", "displaysleepnow"])
            if _IS_LINUX:
                for argv in (
                        ["loginctl", "lock-session"],
                        ["gnome-screensaver-command", "-l"],
                        ["xdg-screensaver", "lock"]):
                    if shutil.which(argv[0]):
                        return self._argv_run(argv)
        except Exception:
            return False
        return False

    def _power(self, mode: str = "sleep") -> bool:
        if not self.allow_power:
            return False
        try:
            if _IS_WINDOWS:
                if mode == "restart":
                    return self._argv_run(["shutdown", "/r", "/t", "5"])
                if mode == "shutdown":
                    return self._argv_run(["shutdown", "/s", "/t", "5"])
                return self._argv_run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
            if _IS_MAC:
                if mode == "restart":
                    return self._argv_run(
                        ["osascript", "-e", 'tell app "System Events" to restart'])
                if mode == "shutdown":
                    return self._argv_run(
                        ["osascript", "-e", 'tell app "System Events" to shut down'])
                return self._argv_run(["pmset", "sleepnow"])
            if _IS_LINUX:
                systemctl = shutil.which("systemctl")
                if systemctl:
                    if mode == "restart":
                        return self._argv_run([systemctl, "reboot"])
                    if mode == "shutdown":
                        return self._argv_run([systemctl, "poweroff"])
                    return self._argv_run([systemctl, "suspend"])
        except Exception:
            return False
        return False

    def _brightness(self, direction: str) -> bool:
        try:
            if _IS_LINUX:
                if shutil.which("brightnessctl"):
                    sign = "+" if direction == "up" else "-"
                    return self._argv_run(["brightnessctl", "set", f"5{sign}"])
            if _IS_MAC:
                # macOS: brightness keys via System Events (key codes 113/114
                # with fn).  Best-effort only — returns False when it fails.
                code = "113" if direction == "up" else "114"
                return self._argv_run(
                    ["osascript", "-e",
                     f'tell application "System Events" to key code {code} '
                     f'using {"control"} down'])
        except Exception:
            return False
        return False

    def _bluetooth(self, op: str) -> SysResult:
        # Explicitly marked where_supported=False in the registry; keep a
        # best-effort Linux path and refuse elsewhere (honest degradation).
        try:
            if _IS_LINUX and shutil.which("rfkill"):
                state = "unblock" if op == "bluetooth_on" else "block"
                return SysResult(self._argv_run(
                    ["rfkill", state, "bluetooth"]), op)
        except Exception:
            pass
        return SysResult(False, op, "bluetooth_unsupported")


def _kb_call(kb: Any, method: str) -> bool:
    try:
        fn = getattr(kb, method, None)
        return bool(fn()) if callable(fn) else False
    except Exception:
        return False


def _media_ok() -> bool:
    return True


class MockSystemExecutor:
    """Deterministic test double: records calls, scripted failures."""

    def __init__(self, available: bool = True,
                 fail_for: Optional[set] = None) -> None:
        self.ok = bool(available)
        self.fail_for = set(fail_for or set())
        self.calls: List[Tuple[str, Dict[str, Any]]] = []

    def available(self) -> bool:
        return self.ok

    def is_destructive(self, op: str) -> bool:
        return str(op or "") in DESTRUCTIVE_SYSTEM_OPS

    def execute(self, op: str, params: Optional[Dict[str, Any]] = None
                ) -> SysResult:
        params = dict(params or {})
        op = str(op or "")
        if op not in SYSTEM_OPS:  # mock mirrors the real allowlist
            self.calls.append((op, params))
            return SysResult(False, op, "op_not_allowed")
        self.calls.append((op, params))
        if not self.ok or op in self.fail_for:
            return SysResult(False, op, "mock_failure")
        return SysResult(True, op)


# ---------------------------------------------------------------------------
# File executor
# ---------------------------------------------------------------------------


class FileActionExecutor:
    """Allowlisted, shell-free file operations (§6 FILES / §26).

    Every path is resolved against an explicit root allowlist.  Anything
    outside the roots, or containing traversal, is REFUSED.  Names from
    voice/browser sources are sanitized with :func:`sanitize_file_name`
    BEFORE resolution.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        cfg = dict(config or {})
        roots = cfg.get("roots") or [os.path.expanduser("~")]
        self.roots: List[str] = [os.path.realpath(str(r)) for r in roots]
        self.base_dir = os.path.realpath(str(cfg.get("base_dir")
                                             or self.roots[0]))
        self.dry_run = bool(cfg.get("dry_run", False))
        self._clipboard: List[str] = []   # staged paths for copy/paste/move
        self._clipboard_cut = False

    # -- path safety ------------------------------------------------------------

    def _resolve(self, name: Any) -> Optional[str]:
        """Resolve a user-supplied name INSIDE the allowlist roots.

        Returns the realpath or None when it escapes the roots.  The
        name is sanitized first (no separators survive sanitize).
        """
        clean = sanitize_file_name(name)
        if not clean:
            return None
        path = os.path.realpath(os.path.join(self.base_dir, clean))
        for root in self.roots:
            if path == root or path.startswith(root + os.sep):
                return path
        return None

    def _resolve_existing(self, name: Any) -> Optional[str]:
        path = self._resolve(name)
        if path is None or not os.path.exists(path):
            return None
        return path

    def available(self) -> bool:
        return bool(self.roots) and os.path.isdir(self.base_dir)

    def is_destructive(self, op: str) -> bool:
        return str(op or "") in DESTRUCTIVE_FILE_OPS

    # -- dispatch ------------------------------------------------------------

    def execute(self, op: str, params: Optional[Dict[str, Any]] = None
                ) -> SysResult:
        op = str(op or "")
        params = dict(params or {})
        if op not in FILE_OPS:
            return SysResult(False, op, "op_not_allowed")
        handler = getattr(self, "_op_" + op, None)
        if handler is None:
            return SysResult(False, op, "unsupported_op")
        return handler(params)

    # -- operations (each returns SysResult; none raise) ----------------------

    def _op_open(self, p: Dict[str, Any]) -> SysResult:
        path = self._resolve_existing(p.get("name"))
        if path is None:
            return SysResult(False, "open", "not_found")
        if self.dry_run:
            return SysResult(True, "open", "dry_run")
        try:
            if _IS_WINDOWS:
                os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606
                return SysResult(True, "open")
            if _IS_MAC:
                return SysResult(subprocess.run(
                    ["open", path], shell=False, timeout=5,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL).returncode == 0, "open")
            if _IS_LINUX and shutil.which("xdg-open"):
                return SysResult(subprocess.run(
                    ["xdg-open", path], shell=False, timeout=5,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL).returncode == 0, "open")
        except Exception:
            return SysResult(False, "open", "open_failed")
        return SysResult(False, "open", "no_opener")

    def _op_create_folder(self, p: Dict[str, Any]) -> SysResult:
        path = self._resolve(p.get("name"))
        if path is None:
            return SysResult(False, "create_folder", "invalid_name")
        if self.dry_run:
            return SysResult(True, "create_folder", "dry_run")
        try:
            os.makedirs(path, exist_ok=True)
            return SysResult(True, "create_folder")
        except Exception:
            return SysResult(False, "create_folder", "mkdir_failed")

    def _op_rename(self, p: Dict[str, Any]) -> SysResult:
        src = self._resolve_existing(p.get("name"))
        dst = self._resolve(p.get("new_name"))
        if src is None or dst is None or src == dst:
            return SysResult(False, "rename", "invalid_paths")
        if self.dry_run:
            return SysResult(True, "rename", "dry_run")
        try:
            os.rename(src, dst)
            return SysResult(True, "rename")
        except Exception:
            return SysResult(False, "rename", "rename_failed")

    def _op_copy(self, p: Dict[str, Any]) -> SysResult:
        name = p.get("name") or p.get("selection")
        path = self._resolve_existing(name)
        if path is None:
            return SysResult(False, "copy", "not_found")
        self._clipboard = [path]
        self._clipboard_cut = False
        return SysResult(True, "copy")

    def _op_paste(self, p: Dict[str, Any]) -> SysResult:
        if not self._clipboard:
            return SysResult(False, "paste", "clipboard_empty")
        if self.dry_run:
            return SysResult(True, "paste", "dry_run")
        results = []
        for src in self._clipboard:
            try:
                dst = self._unique_dest(src)
                if os.path.isdir(src):
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)
                if self._clipboard_cut:
                    os.remove(src)
                results.append(True)
            except Exception:
                results.append(False)
        if self._clipboard_cut:
            self._clipboard = []
            self._clipboard_cut = False
        return SysResult(all(results), "paste")

    def _op_move(self, p: Dict[str, Any]) -> SysResult:
        src = self._resolve_existing(p.get("name"))
        dst_dir = self._resolve(p.get("dest") or p.get("new_name"))
        if src is None or dst_dir is None:
            return SysResult(False, "move", "invalid_paths")
        if self.dry_run:
            return SysResult(True, "move", "dry_run")
        try:
            shutil.move(src, dst_dir)
            return SysResult(True, "move")
        except Exception:
            return SysResult(False, "move", "move_failed")

    def _op_delete(self, p: Dict[str, Any]) -> SysResult:
        path = self._resolve_existing(p.get("name"))
        if path is None:
            return SysResult(False, "delete", "not_found")
        if path in (os.path.realpath(self.base_dir), *self.roots):
            return SysResult(False, "delete", "refused_root")
        if self.dry_run:
            return SysResult(True, "delete", "dry_run")
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
            return SysResult(True, "delete")
        except Exception:
            return SysResult(False, "delete", "delete_failed")

    def _op_select(self, p: Dict[str, Any]) -> SysResult:
        path = self._resolve_existing(p.get("name"))
        if path is None:
            return SysResult(False, "select", "not_found")
        self._clipboard = [path]
        self._clipboard_cut = False
        return SysResult(True, "select")

    # -- helpers ------------------------------------------------------------

    def _unique_dest(self, src: str) -> str:
        base = os.path.join(self.base_dir, os.path.basename(src))
        if not os.path.exists(base):
            return base
        stem, ext = os.path.splitext(base)
        i = 1
        while os.path.exists(f"{stem} ({i}){ext}"):
            i += 1
        return f"{stem} ({i}){ext}"


class MockFileExecutor:
    """Deterministic test double for file operations."""

    def __init__(self, available: bool = True,
                 fail_for: Optional[set] = None) -> None:
        self.ok = bool(available)
        self.fail_for = set(fail_for or set())
        self.calls: List[Tuple[str, Dict[str, Any]]] = []

    def available(self) -> bool:
        return self.ok

    def is_destructive(self, op: str) -> bool:
        return str(op or "") in DESTRUCTIVE_FILE_OPS

    def execute(self, op: str, params: Optional[Dict[str, Any]] = None
                ) -> SysResult:
        params = dict(params or {})
        op = str(op or "")
        if op not in FILE_OPS:  # mock mirrors the real allowlist
            self.calls.append((op, params))
            return SysResult(False, op, "op_not_allowed")
        self.calls.append((op, params))
        if not self.ok or op in self.fail_for:
            return SysResult(False, op, "mock_failure")
        return SysResult(True, op)
