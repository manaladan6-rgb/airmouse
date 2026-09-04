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
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

MAX_CHAIN = 8
MAX_VALUE = 160
MAX_ATTEMPTS_RECORDED = 16
RESOLVE_TIMEOUT_DEFAULT = 2.0

#: default virtual desktop used when no screen_perception engine is given
#: (geometry zones are proportional, so this is only a pixel frame)
DEFAULT_SCREEN_W = 1920
DEFAULT_SCREEN_H = 1080


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


# ═════════════════════════════════════════════════════════════════════════════
# Default provider factory (v15.1.1) — real screen-perception adapters
# ═════════════════════════════════════════════════════════════════════════════
#
# Out of the box the resolver used to ship with ZERO registered providers
# (every resolve returned ok=False).  :func:`build_default_resolver` wraps
# the real perception providers from :mod:`airmouse.screen_perception`
# into the resolver's provider contract (wrap, don't rewrite):
#
#     accessibility → dom (browser) → ocr (only when ITS OWN enabled flag
#     is on) → geometry → coordinate (only when the factory allows it AND
#     the request carries the explicit allow_coordinate_fallback flag)
#
# Every adapter reports the PROVIDER'S OWN honest confidence; an
# unavailable provider simply yields no candidates (existing degradation,
# never raises).

#: filler words stripped from request needles before token matching
_NEEDLE_FILLER = frozenset({
    "the", "a", "an", "click", "focus", "open", "press", "select", "on",
    "in", "to", "please", "button", "link", "tab", "window", "field",
    "box", "input", "screen", "region", "zone", "switch",
})

#: human aliases for the nine deterministic geometry zones
_GEO_ZONE_ALIASES: Dict[str, Tuple[str, ...]] = {
    "geo:center": ("center", "middle", "mid"),
    "geo:corner_tl": ("top left", "topleft", "upper left", "tl"),
    "geo:corner_tr": ("top right", "topright", "upper right", "tr"),
    "geo:corner_bl": ("bottom left", "bottomleft", "lower left", "bl"),
    "geo:corner_br": ("bottom right", "bottomright", "lower right", "br"),
    "geo:edge_top": ("top edge", "top strip", "top"),
    "geo:edge_bottom": ("bottom edge", "bottom strip", "bottom"),
    "geo:edge_left": ("left edge", "left strip", "left"),
    "geo:edge_right": ("right edge", "right strip", "right"),
}


def _request_needle(request: Any) -> str:
    """The user-visible needle of a request: ``value`` else ``description``.

    Lowercased + whitespace-normalized; '' when the request carries no
    usable needle.  Never raises.
    """
    for attr in ("value", "description"):
        try:
            raw = str(getattr(request, attr, "") or "").strip()
        except Exception:
            raw = ""
        if raw:
            return " ".join(raw.lower().split())
    return ""


def _text_matches(needle: str, haystack: str) -> bool:
    """Honest needle/haystack match: substring either way OR any
    significant token of the needle appears in the haystack.

    Filler words (``the``, ``button``, …) are never matched on their own.
    Never raises; '' haystack never matches.
    """
    n = " ".join(str(needle or "").split()).lower()
    h = " ".join(str(haystack or "").split()).lower()
    if not n or not h:
        return False
    if n in h:
        return True
    if len(h) >= 3 and h in n:
        return True
    tokens = [t for t in re.split(r"[^a-z0-9]+", n)
              if len(t) >= 3 and t not in _NEEDLE_FILLER]
    return any(t in h for t in tokens)


def _screen_target_to_result(target: Any, kind: str) -> Dict[str, Any]:
    """Convert a perception :class:`ScreenTarget` into the resolver's
    provider-result dict (data only; clamping happens in
    :func:`_sanitize_target`).  Never raises."""
    try:
        bbox = getattr(target, "bbox", None)
        bb = (float(bbox[0]), float(bbox[1]),
              float(bbox[2]), float(bbox[3])) if bbox else None
        point = None
        if bb is not None:
            point = (bb[0] + bb[2] / 2.0, bb[1] + bb[3] / 2.0)
        conf = float(getattr(target, "confidence", 0.0) or 0.0)
        return {
            "kind": kind,
            "value": str(getattr(target, "text", "") or ""),
            "point": point,
            "bbox": bb,
            "confidence": max(0.0, min(1.0, conf)),
            "provider": kind,
            "target_id": str(getattr(target, "id", "") or ""),
            "metadata": {
                "source": str(getattr(target, "source", "") or kind),
                "application": str(getattr(target, "application", "") or ""),
                "type": str(getattr(getattr(target, "type", ""), "value",
                                    "") or ""),
            },
        }
    except Exception:
        return {}


def _accessibility_provider_adapter(provider: Any) -> ProviderFn:
    """Adapt the perception AccessibilityProvider (active window).

    Matches the request needle against the window title / application
    name.  A vague request (no needle) does NOT claim the whole window —
    returns None.  Unavailable/empty → no candidates.
    """
    def resolve(request: TargetRequest) -> Optional[ResolvedTarget]:
        if provider is None or \
                not bool(getattr(provider, "available", False)):
            return None
        try:
            targets = list(provider.update() or [])
        except Exception:
            return None
        needle = _request_needle(request)
        if not needle:
            return None
        for t in targets:
            hay = " ".join([str(getattr(t, "text", "") or ""),
                            str(getattr(t, "application", "") or ""),
                            str(getattr(t, "id", "") or "")])
            if _text_matches(needle, hay):
                out = _screen_target_to_result(t, "accessibility")
                if out:
                    return out
        return None
    return resolve


def _ocr_provider_adapter(provider: Any) -> ProviderFn:
    """Adapt the perception OCRProvider (opt-in text targets)."""
    def resolve(request: TargetRequest) -> Optional[ResolvedTarget]:
        if provider is None or \
                not bool(getattr(provider, "available", False)):
            return None
        try:
            targets = list(provider.update() or [])
        except Exception:
            return None
        needle = _request_needle(request)
        best = None
        for t in targets:
            if not needle or _text_matches(
                    needle, str(getattr(t, "text", "") or "")):
                conf = float(getattr(t, "confidence", 0.0) or 0.0)
                if best is None or conf > \
                        float(getattr(best, "confidence", 0.0) or 0.0):
                    best = t
        if best is None:
            return None
        return _screen_target_to_result(best, "ocr") or None
    return resolve


def _tokens(text: str) -> List[str]:
    """Lowercased alphanumeric tokens of a phrase; never raises."""
    try:
        return [t for t in re.split(r"[^a-z0-9]+", str(text or "").lower())
                if t]
    except Exception:
        return []


def _contig_subsequence(small: List[str], big: List[str]) -> bool:
    """True when ``small`` appears inside ``big`` as contiguous tokens."""
    if not small or len(small) > len(big):
        return False
    for i in range(len(big) - len(small) + 1):
        if big[i:i + len(small)] == small:
            return True
    return False


def _geometry_provider_adapter(provider: Any) -> ProviderFn:
    """Adapt the perception GeometryProvider (nine deterministic zones).

    Matching passes, most specific first: exact zone id → exact zone
    alias → contiguous-token containment between the needle and a zone
    alias → zone text.  A vague request falls back to the documented
    safe default (the center zone).  Reports the ZONE'S OWN confidence.
    """
    def resolve(request: TargetRequest) -> Optional[ResolvedTarget]:
        if provider is None:
            return None
        try:
            targets = list(provider.update() or [])
        except Exception:
            return None
        by_id = {str(getattr(t, "id", "") or ""): t for t in targets}
        needle = _request_needle(request)
        chosen = None
        if not needle:
            chosen = by_id.get("geo:center")      # documented safe default
        else:
            ntok = _tokens(needle)
            if needle in by_id:                    # "geo:center"
                chosen = by_id[needle]
            if chosen is None:                     # exact alias
                for zid, aliases in _GEO_ZONE_ALIASES.items():
                    if needle in aliases:
                        chosen = by_id.get(zid)
                        break
            if chosen is None and ntok:            # phrase containment
                for zid, aliases in _GEO_ZONE_ALIASES.items():
                    atoks_sets = [_tokens(a) for a in aliases]
                    atoks_sets.append(_tokens(zid))
                    if any(_contig_subsequence(a, ntok)
                           or _contig_subsequence(ntok, a)
                           for a in atoks_sets if a):
                        chosen = by_id.get(zid)
                        break
            if chosen is None:                     # zone text ("center")
                for t in targets:
                    if _text_matches(needle,
                                     str(getattr(t, "text", "") or "")):
                        chosen = t
                        break
        if chosen is None:
            return None
        return _screen_target_to_result(chosen, "geometry") or None
    return resolve


def _browser_mapper_provider_adapter(browser: Any) -> ProviderFn:
    """Adapt a BrowserController/BrowserTargetMapper element map.

    Data-only: reads the mapper's collected ScreenTargets (source 'dom');
    no JS beyond what :mod:`airmouse.browser` already evaluates.
    """
    mapper = getattr(browser, "mapper", None) or browser

    def resolve(request: TargetRequest) -> Optional[ResolvedTarget]:
        targets_fn = getattr(mapper, "targets", None)
        if not callable(targets_fn):
            return None
        try:
            targets = list(targets_fn() or [])
        except Exception:
            return None
        needle = _request_needle(request)
        if not needle:
            return None
        for t in targets:
            hay = " ".join([str(getattr(t, "text", "") or ""),
                            str(getattr(t, "id", "") or "")])
            if _text_matches(needle, hay):
                out = _screen_target_to_result(t, "dom")
                if out:
                    return out
        return None
    return resolve


def _coordinate_provider(screen_w: int, screen_h: int) -> ProviderFn:
    """LAST-RESORT coordinate provider (center of the virtual desktop).

    Registered ONLY when the factory explicitly allows it; the resolver's
    per-request ``allow_coordinate_fallback`` gate still applies (§6/§8),
    so this can never fire silently.
    """
    w, h = max(1, int(screen_w)), max(1, int(screen_h))

    def resolve(request: TargetRequest) -> Optional[ResolvedTarget]:
        return {
            "kind": "coordinate",
            "value": "",
            "point": (w / 2.0, h / 2.0),
            "bbox": (0.0, 0.0, float(w), float(h)),
            "confidence": 0.40,     # honest: an explicit last-resort guess
            "provider": "coordinate",
            "target_id": "coordinate:center",
            "metadata": {"source": "coordinate"},
        }
    return resolve


def _providers_from_screen_perception(
        screen_perception: Any
) -> Tuple[List[Any], int, int]:
    """Extract the perception provider list (+ screen size) from whatever
    the caller passed: None → fresh default providers; a
    ScreenPerceptionEngine-like object → its own providers (test-injected
    stubs honoured); a plain list/tuple → used as-is.  Never raises."""
    try:
        from .screen_perception import (AccessibilityProvider,  # noqa: F401
                                        GeometryProvider, OCRProvider)
    except ImportError:  # pragma: no cover - direct file execution fallback
        from airmouse.screen_perception import (AccessibilityProvider,
                                                GeometryProvider,
                                                OCRProvider)

    screen_w, screen_h = DEFAULT_SCREEN_W, DEFAULT_SCREEN_H
    providers: Optional[List[Any]] = None
    if screen_perception is not None:
        if isinstance(screen_perception, (list, tuple)):
            providers = list(screen_perception)
        else:
            injected = getattr(screen_perception, "providers", None)
            if isinstance(injected, (list, tuple)):
                providers = list(injected)
            try:
                screen_w = int(getattr(screen_perception, "screen_w",
                                       screen_w))
                screen_h = int(getattr(screen_perception, "screen_h",
                                       screen_h))
            except (TypeError, ValueError):
                pass
    if providers is None:
        providers = [
            AccessibilityProvider(screen_w, screen_h),
            OCRProvider(None),          # dormant unless ITS config enables
            GeometryProvider(screen_w, screen_h),
        ]
    return providers, max(1, screen_w), max(1, screen_h)


def build_default_resolver(screen_perception: Any = None,
                           allow_coordinate_fallback: bool = False,
                           min_confidence: float = 0.35,
                           browser: Any = None) -> UniversalTargetResolver:
    """Build a :class:`UniversalTargetResolver` with REAL providers
    registered, so ``resolve_target()`` works out of the box.

    Parameters
    ----------
    screen_perception :
        Optional :class:`~airmouse.screen_perception.ScreenPerceptionEngine`
        (its own provider list — including test-injected stubs — is
        reused), or a plain provider list.  ``None`` constructs fresh
        default providers on a 1920×1080 virtual desktop.
    allow_coordinate_fallback :
        When True a last-resort ``coordinate`` provider (screen center,
        confidence 0.40) is registered; the resolver's EXPLICIT per-request
        gate still applies (§6/§8), so it can never fire silently.
    min_confidence :
        Forwarded to the resolver (default 0.35).
    browser :
        Optional BrowserController / BrowserTargetMapper — its element map
        is adapted into the ``dom`` provider (data-only).

    Provider order (the resolver's default chain):
    accessibility → dom (if ``browser`` given) → ocr (only when the OCR
    provider's own enabled flag is on AND the tesseract stack probes OK)
    → geometry (always) → coordinate (only when allowed above).

    Every adapter reports the provider's own honest confidence; an
    unavailable provider simply yields no candidates.  Never raises.
    """
    try:
        providers, screen_w, screen_h = \
            _providers_from_screen_perception(screen_perception)
    except Exception:
        providers, screen_w, screen_h = [], DEFAULT_SCREEN_W, DEFAULT_SCREEN_H

    resolver = UniversalTargetResolver(min_confidence=min_confidence)

    try:
        from .screen_perception import (AccessibilityProvider, GeometryProvider,
                                        OCRProvider)
    except ImportError:  # pragma: no cover - direct file execution fallback
        from airmouse.screen_perception import (AccessibilityProvider,
                                                GeometryProvider, OCRProvider)

    for provider in providers:
        name = str(getattr(provider, "name", "") or "").lower()
        if isinstance(provider, AccessibilityProvider) or \
                name == "accessibility":
            resolver.register_provider(
                "accessibility", _accessibility_provider_adapter(provider),
                note="active window (perception)")
        elif isinstance(provider, OCRProvider) or name == "ocr":
            # register ONLY when the provider's own enabled flag is on
            if bool(getattr(provider, "available", False)):
                resolver.register_provider(
                    "ocr", _ocr_provider_adapter(provider),
                    note="local OCR (user opt-in)")
        elif isinstance(provider, GeometryProvider) or name == "geometry":
            resolver.register_provider(
                "geometry", _geometry_provider_adapter(provider),
                note="nine deterministic zones")

    if browser is not None:
        resolver.register_provider(
            "dom", _browser_mapper_provider_adapter(browser),
            note="browser element map (data-only)")

    if allow_coordinate_fallback:
        resolver.register_provider(
            "coordinate", _coordinate_provider(screen_w, screen_h),
            note="last-resort screen center (explicit request flag)")

    return resolver
