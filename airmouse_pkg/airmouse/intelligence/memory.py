"""
airmouse.intelligence.memory — privacy-conscious local Interaction Memory.

v11.5 §6: stores PATTERNS, not private content.

Pattern schema (per mission):
    Pattern / Frequency / Confidence / LastSeen / Context /
    SuccessRate / CorrectionCount / PreferredAction

Examples of what it learns:
    "chrome -> vscode" app-transition patterns
    "click that -> current gaze target" context-command habits
    "ctrl+c after selecting code" action patterns
    "user prefers 😂 over 😄" preference patterns

What it must NEVER persist (hard-scrubbed, tested):
    passwords / credentials / tokens / private files /
    full private conversations / arbitrary clipboard / sensitive docs

Provides: enable/disable, inspection, deletion, reset, learning pause,
privacy mode, bounded size, export/import (scrubbed + validated).

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# limits (§44)
# ─────────────────────────────────────────────────────────────────────────────

MAX_PATTERNS_DEFAULT = 5_000
MAX_PATTERN_LEN = 200           # patterns are short descriptors, not content
MAX_CONTEXT_LEN = 200
MAX_CONTEXT_KEYS = 8
MAX_EXPORT_BYTES = 4 * 1024 * 1024

# ─────────────────────────────────────────────────────────────────────────────
# sensitive-content scrubbing
# ─────────────────────────────────────────────────────────────────────────────

_SENSITIVE_KEY_HINTS = (
    "password", "passwd", "passphrase", "pwd", "secret", "token", "api_key",
    "apikey", "api-key", "credential", "credentials", "auth", "authorization",
    "bearer", "session", "cookie", "private_key", "privatekey", "access_key",
    "client_secret", "ssh", "gpg", "wallet", "seed", "pin", "otp", "cvv",
    "card_number", "creditcard", "credit_card", "ssn", "ghp_", "github_pat",
    "sk_", "aws_secret", "private",
)

# token-like strings: long hex, base64-ish, url-credentials, key prefixes
_HEX64 = re.compile(r"\b[0-9a-fA-F]{32,}\b")
_B64ISH = re.compile(r"\b[A-Za-z0-9+/=_-]{40,}\b")
_URL_CREDS = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s/@]+:[^\s/@]+@")
_KNOWN_PREFIX = re.compile(
    r"\b(ghp|gho|ghu|ghs|ghr|github_pat|sk|pk_live|rk_live|AKIA|xox[baprs]|"
    r"eyJ)[A-Za-z0-9_-]{8,}\b")
_ASSIGN = re.compile(r"^\s*([A-Za-z0-9_ .-]{2,40})\s*[:=]\s*(\S+)\s*$")


def is_sensitive(text: str) -> bool:
    """True when a pattern looks like it carries credentials/secrets."""
    if not isinstance(text, str) or not text:
        return False
    low = text.lower()
    for hint in _SENSITIVE_KEY_HINTS:
        if hint in low:
            # "password manager" style nouns alone are still risky to store
            # with a value; treat any hit as sensitive (fail-closed).
            return True
    if _URL_CREDS.search(text) or _KNOWN_PREFIX.search(text):
        return True
    if _HEX64.search(text):
        return True
    m = _ASSIGN.match(text)
    if m and m.group(1).lower().strip() in _SENSITIVE_KEY_HINTS:
        return True
    return False


def scrub_pattern(pattern: str) -> Optional[str]:
    """Return a safe pattern string, or None when it must not be stored.

    Redacts token-like substrings and refuses credential-shaped input
    outright (fail-closed privacy).
    """
    if not isinstance(pattern, str):
        return None
    p = pattern.strip()
    if not p:
        return None
    if is_sensitive(p):
        return None
    p = _URL_CREDS.sub("[redacted-url-credentials]", p)
    p = _KNOWN_PREFIX.sub("[redacted-token]", p)
    p = _HEX64.sub("[redacted-hex]", p)
    p = _B64ISH.sub("[redacted-blob]", p)
    p = p[:MAX_PATTERN_LEN]
    return p or None


# ─────────────────────────────────────────────────────────────────────────────
# record
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PatternRecord:
    """One learned interaction pattern (the §6 schema)."""

    pattern: str
    frequency: int = 0
    confidence: float = 0.5
    last_seen: float = 0.0
    context: Dict[str, str] = field(default_factory=dict)
    success_rate: float = 0.0
    correction_count: int = 0
    preferred_action: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PatternRecord":
        if not isinstance(d, dict):
            raise ValueError("not a dict")
        raw = d.get("pattern")
        if not isinstance(raw, str):
            raise ValueError("pattern must be a string")
        if len(raw) > MAX_PATTERN_LEN:
            raise ValueError("pattern exceeds maximum length")
        p = scrub_pattern(raw)
        if p is None:
            raise ValueError("pattern rejected by privacy scrubber")
        ctx_in = d.get("context") or {}
        if not isinstance(ctx_in, dict):
            ctx_in = {}
        ctx: Dict[str, str] = {}
        for k in sorted(ctx_in.keys(), key=str)[:MAX_CONTEXT_KEYS]:
            v = str(ctx_in[k])[:MAX_CONTEXT_LEN]
            if is_sensitive(f"{k}={v}"):
                continue
            ctx[str(k)[:40]] = v
        conf = float(d.get("confidence", 0.5))
        sr = float(d.get("success_rate", 0.0))
        return cls(
            pattern=p,
            frequency=max(0, min(1_000_000, int(d.get("frequency", 0)))),
            confidence=min(1.0, max(0.0, conf if conf == conf else 0.5)),
            last_seen=float(d.get("last_seen", 0.0) or 0.0),
            context=ctx,
            success_rate=min(1.0, max(0.0, sr if sr == sr else 0.0)),
            correction_count=max(0, min(1_000_000, int(d.get("correction_count", 0)))),
            preferred_action=str(d.get("preferred_action", ""))[:64],
        )


# ─────────────────────────────────────────────────────────────────────────────
# InteractionMemory
# ─────────────────────────────────────────────────────────────────────────────

class InteractionMemory:
    """Bounded, privacy-scrubbed pattern memory.

    Lifecycle flags (all thread-friendly, no locks on read paths):
        enabled          — master switch (default True)
        learning_paused  — temporary pause (records dropped)
        privacy_mode     — strict pause + inspection freeze

    Determinism: all ordering is (frequency desc, pattern asc); wall-clock
    only fills ``last_seen`` and never affects logic decisions.
    """

    def __init__(self, max_patterns: int = MAX_PATTERNS_DEFAULT) -> None:
        self.max_patterns = max(10, int(max_patterns))
        self._patterns: Dict[str, PatternRecord] = {}
        self.enabled = True
        self.learning_paused = False
        self.privacy_mode = False
        self.rejected_sensitive = 0
        self.capacity_drops = 0

    # -- state ------------------------------------------------------------------

    @property
    def learning_active(self) -> bool:
        return self.enabled and not self.learning_paused and not self.privacy_mode

    def pause_learning(self) -> None:
        self.learning_paused = True

    def resume_learning(self) -> None:
        self.learning_paused = False

    def set_privacy_mode(self, on: bool) -> None:
        self.privacy_mode = bool(on)

    # -- learning -----------------------------------------------------------------

    def record(self,
               pattern: str,
               context: Optional[Dict[str, str]] = None,
               success: Optional[bool] = None,
               correction: bool = False,
               preferred_action: str = "",
               confidence: float = 0.5,
               now: Optional[float] = None) -> Optional[PatternRecord]:
        """Record/refresh a pattern.  Returns the record or None when the
        input was scrubbed/refused or learning is inactive."""
        if not self.learning_active:
            return None
        safe = scrub_pattern(pattern)
        if safe is None:
            self.rejected_sensitive += 1
            return None
        ctx: Dict[str, str] = {}
        for k, v in sorted((context or {}).items())[:MAX_CONTEXT_KEYS]:
            kk, vv = str(k)[:40], str(v)[:MAX_CONTEXT_LEN]
            if is_sensitive(f"{kk}={vv}"):
                continue
            ctx[kk] = vv
        rec = self._patterns.get(safe)
        if rec is None:
            if len(self._patterns) >= self.max_patterns:
                self._evict()
            if len(self._patterns) >= self.max_patterns:
                self.capacity_drops += 1
                return None
            rec = PatternRecord(pattern=safe)
            self._patterns[safe] = rec
        rec.frequency = min(1_000_000, rec.frequency + 1)
        rec.last_seen = float(now if now is not None else time.time())
        rec.confidence = min(1.0, max(0.0, float(confidence)))
        for k, v in ctx.items():
            rec.context[k] = v
        rec.context = dict(sorted(rec.context.items())[:MAX_CONTEXT_KEYS])
        if success is not None:
            n = rec.frequency
            prev = rec.success_rate
            rec.success_rate = round(
                (prev * (n - 1) + (1.0 if success else 0.0)) / max(1, n), 4)
        if correction:
            rec.correction_count = min(1_000_000, rec.correction_count + 1)
        if preferred_action:
            rec.preferred_action = str(preferred_action)[:64]
        return rec

    def _evict(self) -> None:
        """Deterministic eviction: lowest frequency, then oldest, then name."""
        if not self._patterns:
            return
        victim = min(self._patterns.items(),
                     key=lambda kv: (kv[1].frequency, kv[1].last_seen, kv[0]))
        del self._patterns[victim[0]]

    # -- inspection ----------------------------------------------------------------

    def size(self) -> int:
        return len(self._patterns)

    def get(self, pattern: str) -> Optional[PatternRecord]:
        safe = scrub_pattern(pattern)
        return self._patterns.get(safe) if safe else None

    def top(self, k: int = 10) -> List[PatternRecord]:
        rows = sorted(self._patterns.values(),
                      key=lambda r: (-r.frequency, r.pattern))
        return rows[:max(0, int(k))]

    def query(self, substring: str, k: int = 10) -> List[PatternRecord]:
        q = scrub_pattern(substring) or ""
        rows = [r for p, r in self._patterns.items() if q and q in p]
        rows.sort(key=lambda r: (-r.frequency, r.pattern))
        return rows[:max(0, int(k))]

    def by_context(self, key: str, value: str, k: int = 10) -> List[PatternRecord]:
        rows = [r for r in self._patterns.values()
                if r.context.get(str(key)[:40]) == str(value)[:MAX_CONTEXT_LEN]]
        rows.sort(key=lambda r: (-r.frequency, r.pattern))
        return rows[:max(0, int(k))]

    # -- deletion / reset -----------------------------------------------------------

    def forget(self, pattern: str) -> bool:
        safe = scrub_pattern(pattern)
        if safe and safe in self._patterns:
            del self._patterns[safe]
            return True
        return False

    def reset(self) -> int:
        n = len(self._patterns)
        self._patterns.clear()
        return n

    # -- export / import (scrubbed + validated) --------------------------------------

    def export_data(self) -> Dict[str, Any]:
        return {
            "version": 1,
            "kind": "airmouse-interaction-memory",
            "privacy_mode": self.privacy_mode,
            "patterns": [r.to_dict() for r in self.top(self.max_patterns)],
        }

    def export_json(self) -> str:
        return json.dumps(self.export_data(), ensure_ascii=False, sort_keys=True)

    def import_data(self, data: Dict[str, Any]) -> int:
        """Import previously exported data.  VALIDATED + SCRUBBED.

        Returns the number of patterns accepted.  Malformed or
        sensitive entries are skipped, never trusted.
        """
        if not isinstance(data, dict) or data.get("kind") not in (
                "airmouse-interaction-memory", None):
            return 0
        rows = data.get("patterns")
        if not isinstance(rows, list):
            return 0
        if len(json.dumps(rows)) > MAX_EXPORT_BYTES:
            return 0
        accepted = 0
        for row in rows[: self.max_patterns]:
            try:
                rec = PatternRecord.from_dict(row)
            except Exception:
                continue
            existing = self._patterns.get(rec.pattern)
            if existing is None and len(self._patterns) >= self.max_patterns:
                self._evict()
            self._patterns[rec.pattern] = rec
            accepted += 1
        return accepted

    def import_json(self, text: str) -> int:
        try:
            data = json.loads(text or "")
        except Exception:
            return 0
        return self.import_data(data)

    # -- persistence -------------------------------------------------------------------

    def save(self, path: str) -> int:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        payload = self.export_json()
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, path)
        return len(payload)

    @classmethod
    def load(cls, path: str,
             max_patterns: int = MAX_PATTERNS_DEFAULT) -> "InteractionMemory":
        mem = cls(max_patterns=max_patterns)
        try:
            with open(path, "r", encoding="utf-8") as f:
                mem.import_json(f.read())
        except FileNotFoundError:
            pass
        except Exception:
            pass  # corrupted store: start empty (never break the core)
        return mem
