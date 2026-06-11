"""Unit tests for src/weighting.py — pure scoring, pooling, calibration."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import weighting as w


# ── Sharp books / prior ────────────────────────────────────────────────────

def test_sharp_present():
    assert w.has_sharp_books(["pinnacle", "everygame"]) is True

def test_sharp_absent():
    assert w.has_sharp_books(["everygame", "coolbet"]) is False

def test_prior_sharp_favours_market():
    p = w.prior_weights(True)
    assert p["market"] > p["uanalyse"]
    assert abs(p["market"] + p["uanalyse"] - 1.0) < 1e-9

def test_prior_parity():
    assert w.prior_weights(False) == {"market": 0.5, "uanalyse": 0.5}


# ── Metrics ────────────────────────────────────────────────────────────────

def test_brier_perfect_is_zero():
    assert w.brier_score({"home": 1.0, "draw": 0.0, "away": 0.0}, "home") == 0.0

def test_brier_worse_for_wrong():
    good = w.brier_score({"home": 0.7, "draw": 0.2, "away": 0.1}, "home")
    bad = w.brier_score({"home": 0.1, "draw": 0.2, "away": 0.7}, "home")
    assert bad > good

def test_log_loss_monotonic():
    assert w.log_loss({"home": 0.9, "draw": 0.05, "away": 0.05}, "home") < \
           w.log_loss({"home": 0.3, "draw": 0.3, "away": 0.4}, "home")

def test_log_loss_safe_on_zero():
    # No infinity even when the realised outcome had probability 0
    assert w.log_loss({"home": 0.0, "draw": 0.5, "away": 0.5}, "home") < 1e3


# ── Rolling performance & weights ──────────────────────────────────────────

_SETTLED = [
    {"source": "market", "p": {"home": 0.7, "draw": 0.2, "away": 0.1}, "outcome": "home"},
    {"source": "market", "p": {"home": 0.2, "draw": 0.3, "away": 0.5}, "outcome": "away"},
    {"source": "uanalyse", "p": {"home": 0.4, "draw": 0.3, "away": 0.3}, "outcome": "home"},
    {"source": "uanalyse", "p": {"home": 0.3, "draw": 0.4, "away": 0.3}, "outcome": "away"},
]

def test_rolling_performance_shape():
    perf = w.rolling_performance(_SETTLED)
    assert set(perf) == {"market", "uanalyse"}
    assert perf["market"]["n"] == 2
    assert 0 <= perf["market"]["hit_rate"] <= 1

def test_performance_weights_reward_lower_brier():
    perf = w.rolling_performance(_SETTLED)
    pw = w.performance_weights(perf)
    # Market is sharper here → should carry more weight
    assert pw["market"] > pw["uanalyse"]
    assert abs(sum(pw.values()) - 1.0) < 1e-9

def test_performance_weights_none_when_missing_source():
    perf = {"market": {"brier": 0.3, "log_loss": 0.5, "hit_rate": 0.6, "n": 5}}
    assert w.performance_weights(perf) is None


# ── effective_weights gating ───────────────────────────────────────────────

def test_effective_falls_back_to_prior_when_disabled(monkeypatch):
    monkeypatch.setattr(w, "ENABLE_PERFORMANCE_REWEIGHTING", False)
    perf = w.rolling_performance(_SETTLED)
    weights, regime = w.effective_weights(sharp=True, perf=perf, n_settled=50)
    assert regime == "prior"
    assert weights == w.prior_weights(True)

def test_effective_uses_performance_when_enabled(monkeypatch):
    monkeypatch.setattr(w, "ENABLE_PERFORMANCE_REWEIGHTING", True)
    monkeypatch.setattr(w, "MIN_SETTLED_FOR_PERF", 2)
    perf = w.rolling_performance(_SETTLED)
    weights, regime = w.effective_weights(sharp=True, perf=perf, n_settled=50)
    assert regime == "performance"

def test_effective_waits_for_min_settled(monkeypatch):
    monkeypatch.setattr(w, "ENABLE_PERFORMANCE_REWEIGHTING", True)
    monkeypatch.setattr(w, "MIN_SETTLED_FOR_PERF", 20)
    perf = w.rolling_performance(_SETTLED)
    weights, regime = w.effective_weights(sharp=False, perf=perf, n_settled=4)
    assert regime == "prior"


# ── Logit pooling ──────────────────────────────────────────────────────────

def test_logit_pool_sums_to_one():
    pooled = w.logit_pool(
        [{"home": 0.6, "draw": 0.25, "away": 0.15},
         {"home": 0.5, "draw": 0.3, "away": 0.2}],
        [0.575, 0.425],
    )
    assert abs(sum(pooled.values()) - 1.0) < 1e-9

def test_logit_pool_between_sources():
    a = {"home": 0.7, "draw": 0.2, "away": 0.1}
    b = {"home": 0.4, "draw": 0.3, "away": 0.3}
    pooled = w.logit_pool([a, b], [0.5, 0.5])
    assert b["home"] < pooled["home"] < a["home"]

def test_logit_pool_weight_dominance():
    a = {"home": 0.8, "draw": 0.15, "away": 0.05}
    b = {"home": 0.2, "draw": 0.3, "away": 0.5}
    pooled = w.logit_pool([a, b], [0.95, 0.05])
    assert pooled["home"] > 0.6  # heavily weighted toward a


# ── λ-calibration ──────────────────────────────────────────────────────────

def test_calibrate_reproduces_target_tendency():
    target = {"home": 0.65, "draw": 0.21, "away": 0.14}
    lam = w.calibrate_lambda(target, totals_line=2.5, totals_over_prob=0.45)
    assert lam["home"] > lam["away"]
    p = w._poisson_1x2(lam["home"], lam["away"])
    # Aggregated 1X2 should be close to the blend tendency
    assert abs(p["home"] - target["home"]) < 0.08

def test_calibrate_respects_total_hint():
    target = {"home": 0.45, "draw": 0.27, "away": 0.28}
    lam = w.calibrate_lambda(target, lambda_total_hint=3.0)
    assert abs((lam["home"] + lam["away"]) - 3.0) < 1e-3
