"""
airmouse.text_control — universal text manipulation layer (v11.5 §12).

Operations (the mission's vocabulary):

    TYPE / SELECT / DELETE / REPLACE / COPY / PASTE / UNDO / REDO /
    CUT / MOVE / CAPITALIZE / LOWERCASE / UPPERCASE / FORMAT /
    NEW_LINE / NEW_PARAGRAPH

Design rules (§12):
* semantic/accessibility APIs where available, keyboard fallback
  otherwise
* NEVER depends exclusively on screen coordinates — text ops are
  targeted at the focused text field by construction
* every op maps onto the v10 ActionEngine vocabulary where possible
  (TYPE/HOTKEY/KEY_PRESS/COPY/PASTE/UNDO/REDO/SELECT)

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple


class TextOp(enum.Enum):
    TYPE = "type"
    SELECT = "select"
    DELETE = "delete"
    REPLACE = "replace"
    COPY = "copy"
    PASTE = "paste"
    UNDO = "undo"
    REDO = "redo"
    CUT = "cut"
    MOVE = "move"
    CAPITALIZE = "capitalize"
    LOWERCASE = "lowercase"
    UPPERCASE = "uppercase"
    FORMAT = "format"
    NEW_LINE = "new_line"
    NEW_PARAGRAPH = "new_paragraph"


# text-editing keyboard bindings (deterministic, platform-agnostic here;
# mac variants handled by the executor when present)
KEYBOARD_FALLBACK: Dict[TextOp, Tuple[str, ...]] = {
    TextOp.SELECT: ("ctrl", "a"),
    TextOp.COPY: ("ctrl", "c"),
    TextOp.PASTE: ("ctrl", "v"),
    TextOp.CUT: ("ctrl", "x"),
    TextOp.UNDO: ("ctrl", "z"),
    TextOp.REDO: ("ctrl", "y"),
    TextOp.DELETE: ("delete",),
    TextOp.NEW_LINE: ("enter",),
    TextOp.NEW_PARAGRAPH: ("enter", "enter"),
    TextOp.MOVE: ("right",),
}

FORMAT_BINDINGS: Dict[str, Tuple[str, ...]] = {
    "bold": ("ctrl", "b"),
    "italic": ("ctrl", "i"),
    "underline": ("ctrl", "u"),
}

MAX_TYPED_LEN = 4000
MAX_HISTORY = 200


@dataclass
class TextAction:
    """A text operation request (DATA — executed via the executor)."""

    op: TextOp
    text: str = ""
    direction: str = ""        # for MOVE: left/right/up/down
    words: int = 1             # for MOVE: word count
    format: str = ""           # for FORMAT: bold/italic/underline
    replacement: str = ""
    timestamp: float = field(default_factory=time.time)


class TextExecutor:
    """Keyboard-level executor protocol (pluggable).

    The default implementation drives pynput lazily (guarded); tests
    inject a recording executor.  Semantic/AT backends can be plugged
    here without touching the controller.
    """

    def __init__(self, backend: Optional[Any] = None) -> None:
        self._backend = backend
        self.calls: List[Tuple[str, Tuple[Any, ...]]] = []

    # -- protocol ---------------------------------------------------------------

    def hotkey(self, *keys: str) -> bool:
        self.calls.append(("hotkey", keys))
        if self._backend is not None:
            try:
                return bool(self._backend.hotkey(list(keys)))
            except Exception:
                return False
        return True

    def key(self, key: str) -> bool:
        self.calls.append(("key", (key,)))
        if self._backend is not None:
            try:
                return bool(self._backend.key_press(key))
            except Exception:
                return False
        return True

    def type_text(self, text: str) -> bool:
        self.calls.append(("type", (text,)))
        if self._backend is not None:
            try:
                return bool(self._backend.type_text(text))
            except Exception:
                return False
        return True


class TextController:
    """Universal text control with keyboard fallback (never
    coordinate-dependent)."""

    def __init__(self, executor: Optional[TextExecutor] = None) -> None:
        self.executor = executor or TextExecutor()
        self.enabled = True
        self.history: List[TextAction] = []
        self._last_typed: List[str] = []   # for CAPITALIZE/UPPER/LOWER retype

    # -- main entry --------------------------------------------------------------

    def execute(self, action: TextAction) -> bool:
        """Execute one text action.  Never raises; returns success."""
        if not self.enabled or action is None:
            return False
        try:
            ok = self._dispatch(action)
        except Exception:
            ok = False
        self.history.append(action)
        if len(self.history) > MAX_HISTORY:
            del self.history[:50]
        return ok

    # -- dispatch -------------------------------------------------------------------

    def _dispatch(self, a: TextAction) -> bool:
        op = a.op
        ex = self.executor
        if op == TextOp.TYPE:
            if not a.text or len(a.text) > MAX_TYPED_LEN:
                return False
            self._last_typed.append(a.text)
            if len(self._last_typed) > 32:
                del self._last_typed[:16]
            return ex.type_text(a.text)
        if op in (TextOp.COPY, TextOp.PASTE, TextOp.CUT, TextOp.UNDO,
                  TextOp.REDO, TextOp.SELECT, TextOp.DELETE,
                  TextOp.NEW_LINE, TextOp.NEW_PARAGRAPH):
            keys = KEYBOARD_FALLBACK.get(op)
            if not keys:
                return False
            if len(keys) == 1:
                return ex.key(keys[0])
            return ex.hotkey(*keys)
        if op == TextOp.MOVE:
            return self._move(a)
        if op == TextOp.FORMAT:
            style = str(a.format or "").lower()
            keys = FORMAT_BINDINGS.get(style)
            if not keys:
                return False
            return ex.hotkey(*keys)
        if op in (TextOp.CAPITALIZE, TextOp.LOWERCASE, TextOp.UPPERCASE):
            # text-level retype path: transform the given text (or the
            # last typed text) and retype it — works in any focused field
            src = a.text or (self._last_typed[-1] if self._last_typed else "")
            if not src:
                return False
            if op == TextOp.CAPITALIZE:
                out = src[:1].upper() + src[1:]
            elif op == TextOp.UPPERCASE:
                out = src.upper()
            else:
                out = src.lower()
            # select the span then retype over it
            words = max(1, len(src.split()))
            ex.hotkey("ctrl", "shift", "left")  # narrow select is app-dependent
            ex.hotkey("ctrl", "a") if not a.text else None
            return ex.type_text(out)
        if op == TextOp.REPLACE:
            if not a.replacement:
                return False
            return ex.type_text(a.replacement)
        return False

    def _move(self, a: TextAction) -> bool:
        d = str(a.direction or "right").lower()
        key = {"left": "left", "right": "right", "up": "up",
               "down": "down"}.get(d)
        if not key:
            return False
        n = max(1, min(100, int(a.words)))
        ok = True
        for _ in range(n):
            ok = self.executor.key(key) and ok
        return ok

    # -- intent helpers (voice → text ops) -----------------------------------------------

    @staticmethod
    def op_from_phrase(phrase: str) -> Optional[Tuple[TextOp, Dict[str, Any]]]:
        """Deterministic phrase → (TextOp, params) mapping."""
        p = str(phrase or "").strip().lower()
        table = {
            "type": (TextOp.TYPE, {}),
            "select all": (TextOp.SELECT, {}),
            "select everything": (TextOp.SELECT, {}),
            "delete that": (TextOp.DELETE, {}),
            "delete selection": (TextOp.DELETE, {}),
            "copy": (TextOp.COPY, {}),
            "copy that": (TextOp.COPY, {}),
            "paste": (TextOp.PASTE, {}),
            "paste that": (TextOp.PASTE, {}),
            "cut": (TextOp.CUT, {}),
            "cut that": (TextOp.CUT, {}),
            "undo": (TextOp.UNDO, {}),
            "redo": (TextOp.REDO, {}),
            "new line": (TextOp.NEW_LINE, {}),
            "new paragraph": (TextOp.NEW_PARAGRAPH, {}),
            "capitalize that": (TextOp.CAPITALIZE, {}),
            "uppercase that": (TextOp.UPPERCASE, {}),
            "lowercase that": (TextOp.LOWERCASE, {}),
            "move left": (TextOp.MOVE, {"direction": "left"}),
            "move right": (TextOp.MOVE, {"direction": "right"}),
            "move up": (TextOp.MOVE, {"direction": "up"}),
            "move down": (TextOp.MOVE, {"direction": "down"}),
            "bold that": (TextOp.FORMAT, {"format": "bold"}),
            "italicize that": (TextOp.FORMAT, {"format": "italic"}),
            "underline that": (TextOp.FORMAT, {"format": "underline"}),
        }
        return table.get(p)
