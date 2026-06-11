"""
Dynamic source weighting: market (bookmakers) vs uanalyse.

Two regimes, selected by RESULTS availability:
  1. PRIOR  — before any settled matches. Sharp books present → market gets the
              edge (≈57.5 / 42.5), otherwise parity (50/50).
  2. PERF   — once real results flow, weights track rolling forecast skill
              (inverse Brier). Gated behind ENABLE_PERFORMANCE_REWEIGHTING so it
              can be switched on the moment the results feed (Phase B) is trusted.

The 1X2 distributions are combined with logit (log-opinion) pooling, then the
Poisson λ are calibrated so the aggregated 1X2 matches the blend — no source is
discarded. All functions here are pure and unit-tested.
"""

from __future__ import annotations

import math
from typing import Iterable

import numpy as np
from scipy import optimize, stats

# ── Configuration ──────────────────────────────────────────────────────────

# Books regarded as "sharp" (efficient, low-margin). If none of these appear in
# the live odds response, the prior collapses to parity.
SHARP_BOOKS: frozenset[str] = frozenset({
    "pinnacle", "betfair_ex_eu", "betfair_ex_uk", "betfair", "matchbook",
})

# Prior split when sharp books are present (market, uanalyse).
PRIOR_SHARP = {"market": 0.575, "uanalyse": 0.425}
PRIOR_PARITY = {"market": 0.5, "uanalyse": 0.5}

# Master switch for performance-based reweighting. Stays False until the
# results feed is trusted; flipping to True activates the rolling-skill weights
# with no other code change required.
ENABLE_PERFORMANCE_REWEIGHTING = False

# Minimum settled matches before performance weights are allowed to engage.
MIN_SETTLED_FOR_PERF = 8

OUTCOMES = ("home", "draw", "away")
_EPS = 1e-9


# ── Sharp-book detection & prior ───────────────────────────────────────────

def has_sharp_books(book_keys: Iterable[str]) -> bool:
    """True if any sharp book key is present in the response."""
    return any(k in SHARP_BOOKS for k in book_keys)


def prior_weights(sharp: bool) -> dict[str, float]:
    """Return the prior (market, uanalyse) split."""
    return dict(PRIOR_SHARP if sharp else PRIOR_PARITY)


# ── Scoring metrics ────────────────────────────────────────────────────────

def _onehot(outcome: str) -> dict[str, float]:
    return {o: (1.0 if o == outcome else 0.0) for o in OUTCOMES}


def brier_score(p: dict[str, float], outcome: str) -> float:
    """Multiclass Brier score (0 = perfect, 2 = worst). Lower is better."""
    y = _onehot(outcome)
    return sum((p.get(o, 0.0) - y[o]) ** 2 for o in OUTCOMES)


def log_loss(p: dict[str, float], outcome: str) -> float:
    """Negative log-likelihood of the realised outcome. Lower is better."""
    return -math.log(max(p.get(outcome, 0.0), _EPS))


def rolling_performance(settled: list[dict]) -> dict[str, dict]:
    """
    Aggregate per-source skill over a list of settled forecasts.

    `settled` items: {"source": str, "p": {home,draw,away}, "outcome": str}.
    Returns {source: {n, brier, log_loss, hit_rate}} (means; lower brier/ll better).
    """
    acc: dict[str, dict] = {}
    for s in settled:
        src = s["source"]
        p = s["p"]
        outcome = s["outcome"]
        bucket = acc.setdefault(src, {"n": 0, "brier": 0.0, "log_loss": 0.0, "hits": 0})
        bucket["n"] += 1
        bucket["brier"] += brier_score(p, outcome)
        bucket["log_loss"] += log_loss(p, outcome)
        pred = max(OUTCOMES, key=lambda o: p.get(o, 0.0))
        bucket["hits"] += int(pred == outcome)

    out: dict[str, dict] = {}
    for src, b in acc.items():
        n = b["n"] or 1
        out[src] = {
            "n": b["n"],
            "brier": round(b["brier"] / n, 4),
            "log_loss": round(b["log_loss"] / n, 4),
            "hit_rate": round(b["hits"] / n, 4),
        }
    return out


def performance_weights(perf: dict[str, dict]) -> dict[str, float] | None:
    """
    Inverse-Brier weights for {'market','uanalyse'} from rolling_performance output.
    Returns None if either source lacks data (caller falls back to the prior).
    """
    if "market" not in perf or "uanalyse" not in perf:
        return None
    inv = {}
    for src in ("market", "uanalyse"):
        brier = max(perf[src]["brier"], _EPS)
        inv[src] = 1.0 / brier
    total = inv["market"] + inv["uanalyse"]
    if total <= 0:
        return None
    return {src: inv[src] / total for src in ("market", "uanalyse")}


def effective_weights(
    sharp: bool,
    perf: dict[str, dict] | None,
    n_settled: int,
) -> tuple[dict[str, float], str]:
    """
    Resolve the (market, uanalyse) weights to use right now.
    Returns (weights, regime) where regime ∈ {"prior", "performance"}.

    Performance weights engage only when:
      ENABLE_PERFORMANCE_REWEIGHTING and n_settled ≥ MIN_SETTLED_FOR_PERF and
      both sources have rolling data.
    """
    if ENABLE_PERFORMANCE_REWEIGHTING and n_settled >= MIN_SETTLED_FOR_PERF and perf:
        pw = performance_weights(perf)
        if pw is not None:
            return pw, "performance"
    return prior_weights(sharp), "prior"


# ── Blending & λ-calibration ───────────────────────────────────────────────

def logit_pool(
    dists: list[dict[str, float]],
    weights: list[float],
) -> dict[str, float]:
    """
    Logit (log-opinion) pool of 1X2 distributions:
        p_c ∝ Π_i  p_i,c ^ w_i
    Weights are normalised internally. Returns a distribution summing to 1.
    """
    wsum = sum(weights) or 1.0
    w = [x / wsum for x in weights]
    pooled = {}
    for o in OUTCOMES:
        logp = 0.0
        for dist, wi in zip(dists, w):
            logp += wi * math.log(max(dist.get(o, 0.0), _EPS))
        pooled[o] = math.exp(logp)
    z = sum(pooled.values()) or 1.0
    return {o: pooled[o] / z for o in OUTCOMES}


def _poisson_1x2(lambda_home: float, lambda_away: float, max_goals: int = 10) -> dict[str, float]:
    """1X2 probabilities implied by two independent Poisson scorers."""
    n = max_goals + 1
    hp = stats.poisson.pmf(np.arange(n), lambda_home)
    ap = stats.poisson.pmf(np.arange(n), lambda_away)
    grid = np.outer(hp, ap)
    home = float(np.tril(grid, -1).sum())   # home goals > away goals
    away = float(np.triu(grid, 1).sum())    # away goals > home goals
    draw = float(np.trace(grid))
    z = home + draw + away or 1.0
    return {"home": home / z, "draw": draw / z, "away": away / z}


def calibrate_lambda(
    target_1x2: dict[str, float],
    totals_line: float | None = None,
    totals_over_prob: float | None = None,
    lambda_total_hint: float | None = None,
) -> dict[str, float]:
    """
    Find (λ_home, λ_away) whose Poisson-implied 1X2 best matches `target_1x2`.

    The total goal rate is anchored to the totals market when available, else to
    `lambda_total_hint` (e.g. the sum of uanalyse λ), else 2.5. Only the home/away
    split is then optimised so the aggregated 1X2 reproduces the blend.
    """
    if totals_line is not None and totals_over_prob is not None:
        lambda_total = _lambda_from_totals(totals_line, totals_over_prob)
    elif lambda_total_hint is not None and lambda_total_hint > 0:
        lambda_total = lambda_total_hint
    else:
        lambda_total = 2.5

    t_home = target_1x2.get("home", 1 / 3)
    t_away = target_1x2.get("away", 1 / 3)

    def residual(split: float) -> float:
        split = min(max(split, 0.02), 0.98)
        lh = lambda_total * split
        la = lambda_total - lh
        p = _poisson_1x2(lh, la)
        return (p["home"] - t_home) ** 2 + (p["away"] - t_away) ** 2

    # Initial guess from the raw probability ratio
    denom = t_home + t_away
    init = t_home / denom if denom > _EPS else 0.5
    res = optimize.minimize_scalar(residual, bounds=(0.02, 0.98), method="bounded")
    split = float(res.x) if res.success else init

    lambda_home = round(lambda_total * split, 4)
    lambda_away = round(lambda_total - lambda_home, 4)
    return {"home": lambda_home, "away": lambda_away}


def _lambda_from_totals(totals_line: float, over_prob: float) -> float:
    """Invert P(Poisson(λ) > line) = over_prob for the total goal rate."""
    floor_line = int(totals_line)

    def residual(lam: float) -> float:
        return float(stats.poisson.cdf(floor_line, lam)) - (1.0 - over_prob)

    try:
        return float(optimize.brentq(residual, 0.05, 20.0, xtol=1e-6))
    except ValueError:
        return float(totals_line)
