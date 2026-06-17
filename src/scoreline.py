"""
xG derivation, Poisson score matrix, and EV-optimal Kicktipp recommendation.
Scoring logic lives exclusively in config.kicktipp_points / config.KICKTIPP_POINTS.
"""

import math
from typing import TypedDict

import numpy as np
from scipy import optimize, stats

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import config


class ExpectedGoals(TypedDict):
    home: float
    away: float


class ScoretipResult(TypedDict):
    home: int
    away: int
    expected_points: float


class ModalScoreline(TypedDict):
    home: int
    away: int
    probability: float


# ---------------------------------------------------------------------------
# xG derivation
# ---------------------------------------------------------------------------

def _lambda_from_totals(totals_line: float, over_prob: float) -> float:
    """
    Find λ_total such that P(Poisson(λ) > totals_line) ≈ over_prob.
    For a half-line (2.5, 3.5…): clean CDF inversion via Brent's method.
    """
    floor_line = int(totals_line)

    def residual(lam: float) -> float:
        return stats.poisson.cdf(floor_line, lam) - (1.0 - over_prob)

    try:
        return float(optimize.brentq(residual, 0.05, 20.0, xtol=1e-6))
    except ValueError:
        return float(totals_line)


def derive_xg(
    consensus: dict[str, float],
    totals_line: float | None,
    totals_over_prob: float | None,
) -> ExpectedGoals:
    """
    Convert consensus probabilities + totals market into λ_home, λ_away.
    Falls back to 2.5 total goals if totals data is missing.
    """
    if totals_line is not None and totals_over_prob is not None:
        lambda_total = _lambda_from_totals(totals_line, totals_over_prob)
    else:
        lambda_total = 2.5

    p_home = consensus.get("home", 1 / 3)
    p_away = consensus.get("away", 1 / 3)
    denom = p_home + p_away
    ratio_home = p_home / denom if denom > 1e-9 else 0.5

    lambda_home = round(lambda_total * ratio_home, 4)
    lambda_away = round(lambda_total - lambda_home, 4)
    return ExpectedGoals(home=lambda_home, away=lambda_away)


# ---------------------------------------------------------------------------
# Poisson score matrix
# ---------------------------------------------------------------------------

def _dixon_coles_tau(lambda_home: float, lambda_away: float, rho: float) -> np.ndarray:
    """
    Dixon & Coles (1997) low-score dependence factor τ for the four cells
    (0,0), (0,1), (1,0), (1,1). ρ<0 lifts the draw cells (0:0, 1:1) and lowers
    the 1-0/0-1 cells, correcting the independent-Poisson draw under-dispersion.
    Returns a (MAX_GOALS+1)² matrix of multipliers (1.0 everywhere else).
    """
    n = config.MAX_GOALS + 1
    tau = np.ones((n, n))
    tau[0, 0] = 1.0 - lambda_home * lambda_away * rho
    tau[0, 1] = 1.0 + lambda_home * rho
    tau[1, 0] = 1.0 + lambda_away * rho
    tau[1, 1] = 1.0 - rho
    # Guard against negative probabilities for large λ / large |ρ|.
    np.clip(tau, 0.0, None, out=tau)
    return tau


def poisson_matrix(lambda_home: float, lambda_away: float,
                   rho: float = 0.0) -> np.ndarray:
    """
    Return (MAX_GOALS+1) × (MAX_GOALS+1) matrix where entry [a, b] =
    P(home scores a) * P(away scores b).
    Rows = home goals, columns = away goals.

    With rho != 0 the Dixon-Coles τ low-score correction is applied and the
    matrix is renormalised so it remains a proper distribution.
    """
    n = config.MAX_GOALS + 1
    home_probs = stats.poisson.pmf(np.arange(n), lambda_home)
    away_probs = stats.poisson.pmf(np.arange(n), lambda_away)
    matrix = np.outer(home_probs, away_probs)
    if rho:
        matrix = matrix * _dixon_coles_tau(lambda_home, lambda_away, rho)
        total = matrix.sum()
        if total > 0:
            matrix = matrix / total
    return matrix


# ---------------------------------------------------------------------------
# EV optimisation (brute-force over all results 0..MAX_GOALS)
# ---------------------------------------------------------------------------

def ev_optimize(matrix: np.ndarray,
                variance_aggression: float = 0.0) -> tuple[ScoretipResult, ModalScoreline]:
    """
    Find the tip (a, b) that maximises a mean–variance objective over Kicktipp
    points: score = E[pts] + γ · σ[pts], where γ = `variance_aggression`.

    γ = 0 → pure EV-optimal (the safe, mid-table tip; default, unchanged).
    γ > 0 → rewards point-variance / upside (exact-scoreline and draw gambles)
            to buy rank upside in a pool of upsets ("Rang statt EV"). The
            reported expected_points always remains the *true* EV of the chosen
            tip, never the inflated objective.
    Scoring is read exclusively from config.kicktipp_points.

    Also returns the modal (most probable) scoreline for comparison.
    """
    n = matrix.shape[0]
    best_score = -1e18
    best_tip = (1, 0)
    best_ev = 0.0

    for tip_h in range(n):
        for tip_a in range(n):
            ev = 0.0
            ev_sq = 0.0
            for real_h in range(n):
                for real_a in range(n):
                    p = matrix[real_h, real_a]
                    if p < 1e-12:
                        continue
                    pts = config.kicktipp_points(
                        (tip_h, tip_a), (real_h, real_a)
                    )
                    ev += p * pts
                    ev_sq += p * pts * pts
            if variance_aggression:
                std = math.sqrt(max(ev_sq - ev * ev, 0.0))
                score = ev + variance_aggression * std
            else:
                score = ev
            if score > best_score:
                best_score = score
                best_tip = (tip_h, tip_a)
                best_ev = ev

    modal_idx = np.unravel_index(np.argmax(matrix), matrix.shape)

    return (
        ScoretipResult(
            home=best_tip[0],
            away=best_tip[1],
            expected_points=round(best_ev, 4),
        ),
        ModalScoreline(
            home=int(modal_idx[0]),
            away=int(modal_idx[1]),
            probability=round(float(matrix[modal_idx]), 4),
        ),
    )


# ---------------------------------------------------------------------------
# Full pipeline for one match
# ---------------------------------------------------------------------------

def process_scoreline(
    consensus: dict[str, float],
    totals_line: float | None,
    totals_over_prob: float | None,
) -> tuple[ExpectedGoals, ScoretipResult, ModalScoreline]:
    xg = derive_xg(consensus, totals_line, totals_over_prob)
    matrix = poisson_matrix(xg["home"], xg["away"])
    tip, modal = ev_optimize(matrix)
    return xg, tip, modal
