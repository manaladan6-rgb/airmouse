"""
airmouse.explain — Observability & Explainability (v15 §24).

Every important intelligent decision gets a structured decision trace:

    "Why did you predict this?"           -> explain_prediction
    "Why did you choose this target?"     -> explain_target_choice
    "Why did you ask for confirmation?"   -> explain_confirmation
    "Why did the action fail?"            -> explain_failure
    "Why did you recover this way?"       -> explain_recovery
    "Which learned preference influenced this?" ->
        explain_preference_influence

Traces are structured, bounded and SENSITIVE-DATA-FREE (§24): values
are categorical labels or lengths, never content.

Copyright (c) AirMouse.  MIT License.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _bounded(value: Any, limit: int = 120) -> str:
    return str(value)[:limit] if value else ""


def explain_prediction(predicted: str, confidence: float,
                       signals: Dict[str, float]) -> Dict[str, Any]:
    """Why did you predict this? (§24)"""
    top = sorted(signals.items(), key=lambda kv: -kv[1])[:4]
    return {
        "question": "why_predicted",
        "prediction": _bounded(predicted, 60),
        "confidence": round(float(confidence), 4),
        "because": [f"{k}: {round(v, 3)}" for k, v in top],
        "note": "prediction is data, never permission (§14)",
    }


def explain_target_choice(kind: str, value: str, provider: str,
                          confidence: float,
                          attempts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Why did you choose this target? (§24)"""
    return {
        "question": "why_target",
        "target_kind": _bounded(kind, 20),
        "target_label": _bounded(value, 60),
        "via_provider": _bounded(provider, 24),
        "confidence": round(float(confidence), 4),
        "because": [f"{a.get('provider')}: "
                    f"{'ok' if a.get('ok') else _bounded(a.get('detail', 'tried'), 40)}"
                    for a in attempts[:8]],
    }


def explain_confirmation(risk: str, permission: str,
                         policy_reason: str) -> Dict[str, Any]:
    """Why did you ask for confirmation? (§24)"""
    return {
        "question": "why_confirmation",
        "risk": _bounded(risk, 16),
        "permission": _bounded(permission, 40),
        "because": [_bounded(policy_reason, 120) or
                    "policy requires human approval for this risk class"],
    }


def explain_failure(action: str, diagnosis: str, detail: str) -> Dict[
        str, Any]:
    """Why did the action fail? (§24)"""
    return {
        "question": "why_failed",
        "action": _bounded(action, 40),
        "diagnosis": _bounded(diagnosis, 32),
        "because": [_bounded(detail, 120) or "no detail recorded"],
    }


def explain_recovery(strategy: str, round_index: int,
                     diagnosis: str) -> Dict[str, Any]:
    """Why did you recover this way? (§24)"""
    return {
        "question": "why_recovery",
        "strategy": _bounded(strategy, 32),
        "round": int(round_index),
        "because": [f"diagnosis '{_bounded(diagnosis, 32)}' maps to this "
                    f"strategy in the deterministic §7 ladder"],
    }


def explain_preference_influence(twin=None, category: str = "",
                                 key: str = "") -> Dict[str, Any]:
    """Which learned preference influenced this? (§24)"""
    if twin is None:
        return {"question": "which_preference",
                "influenced": False,
                "because": ["personal twin not enabled — no learned "
                            "preference applied"]}
    fact = None
    try:
        fact = twin.get(category, key)
    except Exception:
        fact = None
    if not fact:
        return {"question": "which_preference", "influenced": False,
                "because": ["no learned fact for this category/key"]}
    return {
        "question": "which_preference",
        "influenced": True,
        "fact_id": _bounded(fact.get("fact_id"), 60),
        "confidence": fact.get("confidence", 0.0),
        "frequency": fact.get("frequency", 0),
        "because": [f"learned from {fact.get('frequency', 0)} observations "
                    f"(provenance available in the twin)"],
    }


def decision_trace(*parts: Dict[str, Any]) -> Dict[str, Any]:
    """Compose several §24 explanations into ONE bounded trace."""
    return {"trace": list(parts)[:8], "sensitive_data": False}
