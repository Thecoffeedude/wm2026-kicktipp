"""
Scoreline calibration helpers: goal-level scaling (κ), Dixon-Coles ρ and the
variance dial γ. All tunables live in config; this module only resolves the
*effective* values (incl. the adaptive κ computed from the snapshot store) and
applies κ to a λ pair.

Empirical context (first 20 games): model λ_total was already well calibrated
(Ø 3.19 predicted vs 3.00 realised). κ therefore exists to counteract the
EV-optimiser's scoreline shrinkage, not to recalibrate λ — so the adaptive term
is heavily shrunk toward the static base and bounded. Pure, unit-tested.
"""

from __future__ import annotations

import logging

import config
from src.scoreline import _lambda_from_totals

logger = logging.getLogger(__name__)


def rho_value() -> float:
    """Effective Dixon-Coles ρ (0.0 when disabled)."""
    return config.DIXON_COLES_RHO if config.ENABLE_DIXON_COLES else 0.0


def variance_value() -> float:
    """Effective variance-dial γ for ev_optimize."""
    return float(config.VARIANCE_AGGRESSION)


def apply_kappa(lambda_home: float, lambda_away: float, kappa: float) -> tuple[float, float]:
    """Scale both λ by κ, preserving the home/away split. Returns rounded λ."""
    return round(lambda_home * kappa, 4), round(lambda_away * kappa, 4)


def _predicted_total(events_by_match: dict, mid: str) -> float | None:
    """
    Model-predicted total goals for one match: mean of the uanalyse λ_total and
    the totals-market-implied λ_total (whichever are present). None if neither.
    """
    preds: list[float] = []
    ev = events_by_match.get(mid, {})
    ua = ev.get("uanalyse")
    if ua and ua.get("lambda"):
        preds.append(ua["lambda"]["home"] + ua["lambda"]["away"])
    od = ev.get("odds")
    if od and od.get("totals_line") and od.get("totals_over_prob") is not None:
        preds.append(_lambda_from_totals(od["totals_line"], od["totals_over_prob"]))
    if not preds:
        return None
    return sum(preds) / len(preds)


def empirical_goal_ratio(events: list[dict]) -> tuple[float | None, int]:
    """
    Running realised/predicted total-goal ratio over settled matches.
    Returns (ratio, n_matches); ratio is None when no match can be paired.
    """
    results: dict[str, int] = {}
    by_match: dict[str, dict] = {}
    for e in events:
        mid = e.get("match_id")
        if not mid:
            continue
        etype = e.get("type")
        if etype == "result":
            results[mid] = e.get("score_home", 0) + e.get("score_away", 0)
        elif etype in ("uanalyse", "odds"):
            slot = by_match.setdefault(mid, {})
            prev = slot.get(etype)
            # keep the snapshot closest to kickoff (latest captured)
            if prev is None or e.get("captured_at", "") >= prev.get("captured_at", ""):
                slot[etype] = e

    real_sum = 0.0
    pred_sum = 0.0
    n = 0
    for mid, real_total in results.items():
        pred = _predicted_total(by_match, mid)
        if pred is None or pred <= 0:
            continue
        real_sum += real_total
        pred_sum += pred
        n += 1
    if n == 0 or pred_sum <= 0:
        return None, 0
    return real_sum / pred_sum, n


def resolve_kappa(events: list[dict]) -> tuple[float, dict]:
    """
    Effective goal-scaling κ and an audit dict for metadata.

    κ = clip( (1-s)·base + s·empirical_ratio , KAPPA_BOUNDS ) once enough
    matches have settled; otherwise the static base. The empirical ratio is the
    realised/predicted goal ratio — a true calibration signal that keeps κ from
    drifting away from a level the data supports.
    """
    base = float(config.GOAL_SCALE_KAPPA)
    meta = {
        "base": round(base, 4),
        "adaptive": False,
        "empirical_ratio": None,
        "n_matches": 0,
        "rho": rho_value(),
        "variance_aggression": variance_value(),
    }
    if not config.ENABLE_ADAPTIVE_KAPPA:
        meta["effective"] = round(base, 4)
        return base, meta

    ratio, n = empirical_goal_ratio(events)
    meta["empirical_ratio"] = round(ratio, 4) if ratio is not None else None
    meta["n_matches"] = n
    if ratio is None or n < config.KAPPA_MIN_SETTLED:
        meta["effective"] = round(base, 4)
        return base, meta

    s = float(config.KAPPA_SHRINK)
    raw = (1.0 - s) * base + s * ratio
    lo, hi = config.KAPPA_BOUNDS
    kappa = min(max(raw, lo), hi)
    meta["adaptive"] = True
    meta["effective"] = round(kappa, 4)
    logger.info("Adaptive κ=%.3f (base=%.2f, realised/pred=%.3f over %d matches)",
                kappa, base, ratio, n)
    return kappa, meta
