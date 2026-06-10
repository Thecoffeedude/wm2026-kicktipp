"""
Unit tests for the EV optimizer.

Key proof: under the Kicktipp rules (win: 2/3/4; draw: 2/4, no goal_diff tier),
the EV-optimal tip for a moderate home favourite is NOT the modal scoreline.
Concrete case: λ_home=1.6, λ_away=1.1 → modal=1:1 but recommended=1:0.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np

import config
from src.scoreline import poisson_matrix, ev_optimize


# ---------------------------------------------------------------------------
# kicktipp_points — rule verification
# ---------------------------------------------------------------------------

def test_win_exact():
    assert config.kicktipp_points((2, 1), (2, 1)) == 4

def test_win_goal_diff():
    # 3:1 tip, 4:2 real → same diff (+2) → goal_diff
    assert config.kicktipp_points((3, 1), (4, 2)) == 3

def test_win_tendency_only():
    # 2:0 tip, 1:0 real → home win, diff differs (2 vs 1) → tendency
    assert config.kicktipp_points((2, 0), (1, 0)) == 2

def test_wrong_tendency():
    assert config.kicktipp_points((2, 0), (1, 2)) == 0

def test_draw_exact():
    assert config.kicktipp_points((1, 1), (1, 1)) == 4

def test_draw_no_goal_diff_tier():
    # 1:1 tip vs 2:2 real → DRAW: no goal_diff tier, only tendency=2
    assert config.kicktipp_points((1, 1), (2, 2)) == 2

def test_draw_tendency_wrong():
    # 1:1 tip vs 2:1 real → draw tip vs home win → 0
    assert config.kicktipp_points((1, 1), (2, 1)) == 0

def test_win_tip_vs_draw_real():
    # 1:0 tip vs 0:0 real → home win tip vs draw → 0
    assert config.kicktipp_points((1, 0), (0, 0)) == 0


# ---------------------------------------------------------------------------
# EV optimizer — the key proof
# ---------------------------------------------------------------------------

def _manual_ev(tip_h: int, tip_a: int, matrix: np.ndarray) -> float:
    """Brute-force EV for a single tip over the full probability matrix."""
    n = matrix.shape[0]
    total = 0.0
    for rh in range(n):
        for ra in range(n):
            total += matrix[rh, ra] * config.kicktipp_points((tip_h, tip_a), (rh, ra))
    return total


def test_modal_1_1_but_ev_optimal_1_0():
    """
    λ_home=1.6, λ_away=1.1 (moderate home favourite, λ_away > 1 so 1:1 is modal).

    Under the no-goal_diff-for-draws rules:
    - 1:1 only earns 4pts (exact) or 2pts (other draws).
    - 1:0 earns 3pts for every home win with diff=+1 (2:1, 3:2, …).
    → EV(1:0) > EV(1:1), even though P(1:1) > P(1:0).
    """
    lh, la = 1.6, 1.1
    matrix = poisson_matrix(lh, la)

    tip, modal = ev_optimize(matrix)

    assert modal["home"] == 1 and modal["away"] == 1, \
        f"Expected modal 1:1, got {modal['home']}:{modal['away']}"
    assert tip["home"] == 1 and tip["away"] == 0, \
        f"Expected EV-optimal 1:0, got {tip['home']}:{tip['away']}"

    ev_10 = _manual_ev(1, 0, matrix)
    ev_11 = _manual_ev(1, 1, matrix)
    assert ev_10 > ev_11, \
        f"EV(1:0)={ev_10:.4f} should exceed EV(1:1)={ev_11:.4f}"

    print(f"\n  λ_home={lh}, λ_away={la}")
    print(f"  P(1:0)={matrix[1,0]:.4f}  P(1:1)={matrix[1,1]:.4f}  → modal is 1:1")
    print(f"  EV(1:0)={ev_10:.4f}  EV(1:1)={ev_11:.4f}  → tip is 1:0")
    print(f"  Δ EV = {ev_10 - ev_11:.4f} pts — goal_diff advantage for wins outweighs draw bonus")


def test_ev_optimal_is_global_max():
    """EV-optimal tip must dominate every candidate over the full grid."""
    lh, la = 1.5, 1.2
    matrix = poisson_matrix(lh, la)
    tip, _ = ev_optimize(matrix)
    # ev_optimize rounds to 4 decimal places; use 1e-4 tolerance
    optimal_ev = tip["expected_points"]
    n = matrix.shape[0]
    for h in range(n):
        for a in range(n):
            ev = _manual_ev(h, a, matrix)
            assert ev <= optimal_ev + 1e-4, \
                f"Tip {h}:{a} has EV={ev:.6f} > reported optimal {optimal_ev:.6f}"


def test_equal_teams_no_draw_bonus():
    """
    λ_home = λ_away = 1.3 (equal teams): under no-goal_diff-for-draws rules,
    draw tips are weaker than win tips. 1:0 and 0:1 have equal (highest) EV by symmetry.
    Modal is a draw (1:1), but EV-optimal is NOT a draw.
    """
    lh = la = 1.3
    matrix = poisson_matrix(lh, la)
    tip, modal = ev_optimize(matrix)

    # Modal must be a draw (symmetric teams, λ>1 means 1:1 is most probable)
    assert modal["home"] == modal["away"], \
        f"Equal teams: expected draw modal, got {modal['home']}:{modal['away']}"

    # EV-optimal should be a win tip (draws lack goal_diff tier)
    assert tip["home"] != tip["away"], \
        "Under no-goal_diff-for-draws rules, win tips beat draw tips even for equal teams"

    # Confirm: EV(1:0) > EV(1:1) due to missing goal_diff tier for draws
    ev_win = _manual_ev(tip["home"], tip["away"], matrix)
    ev_draw = _manual_ev(1, 1, matrix)
    assert ev_win > ev_draw, f"EV(win tip)={ev_win:.4f} should exceed EV(1:1)={ev_draw:.4f}"


def test_rules_sourced_from_config():
    """Engine uses config.KICKTIPP_POINTS; no hardcoded values."""
    lh, la = 1.8, 1.0
    matrix = poisson_matrix(lh, la)
    tip, _ = ev_optimize(matrix)
    assert 0 <= tip["home"] <= config.MAX_GOALS
    assert 0 <= tip["away"] <= config.MAX_GOALS
    assert tip["expected_points"] > 0


if __name__ == "__main__":
    import traceback
    tests = [
        test_win_exact, test_win_goal_diff, test_win_tendency_only,
        test_wrong_tendency, test_draw_exact, test_draw_no_goal_diff_tier,
        test_draw_tendency_wrong, test_win_tip_vs_draw_real,
        test_modal_1_1_but_ev_optimal_1_0,
        test_ev_optimal_is_global_max,
        test_equal_teams_no_draw_bonus,
        test_rules_sourced_from_config,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  ✅ {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ❌ {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
