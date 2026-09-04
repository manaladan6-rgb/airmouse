"""
airmouse.target_resolver — Universal Target Resolution (v14 §8).

One resolver for EVERY consumer (human voice, agent SDK, skills,
tasks, macros).  The user/agent says WHAT they want; the resolver
decides HOW to find it:

    1. accessibility     (a11y tree node)
    2. dom               (browser bridge selector)
    3. semantic_app_api  (application-provided semantic API)
    4. ocr               (on-screen text)
    5. vision            (appearance/template)
    6. geometry          (layout/region reasoning)
    7. coordinate        (LAST-RESORT, explicit flag required — §6/§8)

API (§8):
    resolve_target(request) -> ResolutionResult
    explain_target(result)  -> human-readable trace (no sensitive data)
    verify_target(resolved, expected) -> TargetVerification

PROTOCOLS: providers are registered with a priority; resolution walks
the chain deterministically, collects per-provider attempts, and
always explains itself (§24).

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

MAX_CHAIN = 8
MAX_VALUE = 160
MAX_ATTEMPTS_RECORDED = 16
RESOLVE_TIMEOUT_DEFAULT = 2.0


class TargetKind(enum.Enum):
    ACCESSIBILITY = "accessibility"
    DOM = "dom"
    SEMANTIC_APP_API = "semantic_app_api"
    OCR = "ocr"
    VISION = "vision"
    GEOMETRY = "geometry"
    COORDINATE = "coordinate"


# §8 resolution order (may be tuned via register order; defaults here)
DEFAULT_RESOLUTION_ORDER: Tuple[str, ...] = (
    TargetKind.ACCESSIBILITY.value,
    TargetKind.DOM.value,
    TargetKind.SEMANTIC_APP_API.value,
    TargetKind.OCR.value,
    TargetKind.VISION.value,
    TargetKind.GEOMETRY.value,
    TargetKind.COORDINATE.value,
)


@dataclass(frozen=True)
class TargetRequest:
    """WHAT the caller wants (never HOW) (§8)."""

    description: str = ""               # e.g. "the Submit button"
    kind: str = "semantic"              # requested descriptor kind
    value: str = ""                     # e.g. "submit"
    app: str = ""                       # application hint
    browser: bool = False               # browser context hint
    allow_coordinate_fallback: bool = False    # explicit flag (§6/§8)
    timeout: float = RESOLVE_TIMEOUT_DEFAULT
    context: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ResolvedTarget:
    """A located target with provenance (§8)."""

    kind: str = ""
    value: str = ""
    point: Optional[Tuple[float, float]] = None
    bbox: Optional[Tuple[float, float, float, float]] = None
    confidence: float = 0.0
    provider: str = ""                  # which chain link resolved it
    target_id: str = ""                 # provider-native id
    metadata: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "value": self.value[:MAX_VALUE],
            "point": list(self.point) if self.point else None,
            "bbox": list(self.bbox) if self.bbox else None,
            "confidence": round(self.confidence, 4),
            "provider": self.provider,
            "target_id": self.target_id[:40],
            "metadata": dict(sorted(self.metadata.items())),
        }


@dataclass
class ResolutionAttempt:
    provider: str = ""
    ok: bool = False
    confidence: float = 0.0
    detail: str = ""


@dataclass
class ResolutionResult:
    """Outcome of resolve_target (§8)."""

    resolved: Optional[ResolvedTarget] = None
    ok: bool = False
    attempts: List[ResolutionAttempt] = field(default_factory=list)
    chain_used: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "target": self.resolved.to_dict() if self.resolved else None,
            "attempts": [
                {"provider": a.provider, "ok": a.ok,
                 "confidence": round(a.confidence, 4),
                 "detail": a.detail[:60]} for a in
                self.attempts[:MAX_ATTEMPTS_RECORDED]],
            "chain_used": list(self.chain_used),
        }


@dataclass
class TargetVerification:
    verified: bool = False
    message: str = ""
    checks: Tuple[str, ...] = ()


# provider contract: callable(request) -> Optional[ResolvedTarget]
ProviderFn = Callable[[TargetRequest], Optional[ResolvedTarget]]


class UniversalTargetResolver:
    """§8 unified resolver over an ordered provider chain."""

    def __init__(self, resolution_order: Optional[Sequence[str]] = None,
                 min_confidence: float = 0.35) -> None:
        self.order = tuple(resolution_order or DEFAULT_RESOLUTION_ORDER)
        self.min_confidence = max(0.0, min(1.0, float(min_confidence)))
        self._providers: Dict[str, ProviderFn] = {}
        self._provider_meta: Dict[str, str] = {}

    # ── registration ────────────────────────────────────────────────────

    def register_provider(self, kind: str, provider: ProviderFn,
                          note: str = "") -> bool:
        try:
            TargetKind(str(kind))
        except ValueError:
            return False
        if not callable(provider):
            return False
        self._providers[str(kind)] = provider
        self._provider_meta[str(kind)] = str(note)[:60]
        return True

    def unregister_provider(self, kind: str) -> bool:
        return self._providers.pop(str(kind), None) is not None

    def available_kinds(self) -> Tuple[str, ...]:
        return tuple(k for k in self.order if k in self._providers)

    # ── resolution (§8) ─────────────────────────────────────────────────

    def resolve_target(self, request: TargetRequest) -> ResolutionResult:
        result = ResolutionResult()
        chain: List[str] = []
        for kind in self.order[:MAX_CHAIN]:
            provider = self._providers.get(kind)
            if provider is None:
                continue
            # §6/§8: coordinates only with the explicit flag
            if kind == TargetKind.COORDINATE.value and \
                    not request.allow_coordinate_fallback:
                result.attempts.append(ResolutionAttempt(
                    provider=kind, ok=False,
                    detail="coordinate fallback not permitted"))
                continue
            chain.append(kind)
            try:
                target = provider(request)
            except Exception as exc:
                target = None
                result.attempts.append(ResolutionAttempt(
                    provider=kind, ok=False, detail=f"provider raised"))
            if target is None:
                result.attempts.append(ResolutionAttempt(
                    provider=kind, ok=False, detail="no match"))
                continue
            try:
                target = _sanitize_target(target, kind)
            except Exception:
                result.attempts.append(ResolutionAttempt(
                    provider=kind, ok=False, detail="invalid target"))
                continue
            result.attempts.append(ResolutionAttempt(
                provider=kind, ok=True, confidence=target.confidence,
                detail=f"resolved via {kind}"))
            if target.confidence >= self.min_confidence:
                result.resolved = target
                result.ok = True
                result.chain_used = tuple(chain)
                return result
            # low confidence: keep best-effort, continue down the chain
            if result.resolved is None or \
                    target.confidence > result.resolved.confidence:
                result.resolved = target
        result.chain_used = tuple(chain)
        return result

    # ── explainability (§8 + §24) ───────────────────────────────────────

    def explain_target(self, result: ResolutionResult) -> Dict[str, Any]:
        """Structured, human-readable explanation with no sensitive
        target content beyond the descriptor itself (§24)."""
        if result.ok and result.resolved is not None:
            via = result.resolved.provider
            lines = [f"resolved '{result.resolved.kind}' target "
                     f"(confidence {result.resolved.confidence:.2f})"]
            for a in result.attempts:
                mark = "✓" if a.ok else ("skip" if "not permitted" in
                                         a.detail else "·")
                lines.append(f"  {mark} {a.provider}: {a.detail or 'tried'}")
            lines.append(f"winning provider: {via}")
            return {
                "ok": True,
                "why": "first provider above min_confidence",
                "trace": lines,
                "chain": list(result.chain_used),
            }
        return {
            "ok": False,
            "why": "no provider produced a target above min_confidence",
            "trace": [f"  · {a.provider}: {a.detail or 'tried'}"
                      for a in result.attempts],
            "chain": list(result.chain_used),
        }

    # ── verification (§8) ───────────────────────────────────────────────

    def verify_target(self, resolved: Optional[ResolvedTarget],
                      expected: Optional[Dict[str, Any]] = None,
                      still_visible_fn: Optional[Callable[[], bool]] = None
                      ) -> TargetVerification:
        """Confirm a resolved target is still valid/consistent (§8)."""
        checks: List[str] = []
        if resolved is None or not resolved.kind:
            return TargetVerification(False, "nothing resolved", ())
        checks.append("has_kind")
        if resolved.point is None and resolved.bbox is None:
            return TargetVerification(
                False, "target has no location", tuple(checks))
        checks.append("has_location")
        if resolved.confidence <= 0.0:
            return TargetVerification(
                False, "zero confidence", tuple(checks))
        checks.append("confidence_positive")
        if expected:
            exp_kind = expected.get("kind")
            if exp_kind and exp_kind != resolved.kind:
                return TargetVerification(
                    False, f"kind mismatch: {resolved.kind} != {exp_kind}",
                    tuple(checks))
            exp_value = expected.get("value")
            if exp_value and str(exp_value).lower() != \
                    str(resolved.value).lower():
                return TargetVerification(
                    False, "value mismatch", tuple(checks))
            checks.append("matches_expected")
        if still_visible_fn is not None:
            try:
                if not still_visible_fn():
                    return TargetVerification(
                        False, "target no longer visible", tuple(checks))
                checks.append("still_visible")
            except Exception:
                return TargetVerification(
                    False, "visibility check crashed", tuple(checks))
        return TargetVerification(True, "target verified", tuple(checks))


def _sanitize_target(target: Any, kind: str) -> ResolvedTarget:
    """Normalize a provider result; clamp everything (fail-closed)."""
    if isinstance(target, ResolvedTarget):
        t = target
        if t.kind != kind and not t.kind:
            t = ResolvedTarget(
                kind=kind, value=t.value, point=t.point, bbox=t.bbox,
                confidence=t.confidence, provider=t.provider or kind,
                target_id=t.target_id, metadata=t.metadata)
        return t
    if isinstance(target, dict):
        point = target.get("point")
        bbox = target.get("bbox")
        pt = None
        bb = None
        try:
            if isinstance(point, (list, tuple)) and len(point) == 2:
                pt = (float(point[0]), float(point[1]))
            if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
                bb = tuple(float(x) for x in bbox)
        except (TypeError, ValueError):
            pass
        conf = target.get("confidence", 0.5)
        try:
            conf = max(0.0, min(1.0, float(conf)))
        except (TypeError, ValueError):
            conf = 0.0
        meta = target.get("metadata", {})
        return ResolvedTarget(
            kind=str(target.get("kind", kind)),
            value=str(target.get("value", ""))[:MAX_VALUE],
            point=pt, bbox=bb, confidence=conf,
            provider=str(target.get("provider", kind)),
            target_id=str(target.get("target_id", ""))[:40],
            metadata={str(k)[:20]: str(v)[:60]
                      for k, v in list(meta.items())[:6]}
            if isinstance(meta, dict) else {})
    raise ValueError("unsupported provider result type")
