"""Unit tests for src/calibration.py (pure; no I/O, no network)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
from src import calibration


def _events(pairs):
    """Build a minimal event list: pairs = [(mid, real_total, pred_total)]."""
    out = []
    for mid, real, pred in pairs:
        out.append({"type": "result", "match_id": mid,
                    "score_home": real, "score_away": 0,
                    "captured_at": "2026-06-11T21:00:00Z"})
        # express the predicted total purely via uanalyse λ
        out.append({"type": "uanalyse", "match_id": mid,
                    "lambda": {"home": pred, "away": 0.0},
                    "p": {"home": 0.5, "draw": 0.25, "away": 0.25},
                    "captured_at": "2026-06-11T18:00:00Z"})
    return out


# ── apply_kappa ─────────────────────────────────────────────────────────────

def test_apply_kappa_scales_and_preserves_split():
    lh, la = calibration.apply_kappa(1.5, 1.0, 1.2)
    assert lh == 1.8 and la == 1.2

def test_apply_kappa_identity():
    assert calibration.apply_kappa(1.4, 1.1, 1.0) == (1.4, 1.1)


# ── empirical_goal_ratio ────────────────────────────────────────────────────

def test_empirical_ratio_basic():
    # realised 3+1=4, predicted 2+2=4 → ratio 1.0 over 2 matches
    ev = _events([("m1", 3, 2.0), ("m2", 1, 2.0)])
    ratio, n = calibration.empirical_goal_ratio(ev)
    assert n == 2 and abs(ratio - 1.0) < 1e-9

def test_empirical_ratio_under_prediction():
    ev = _events([("m1", 4, 2.0)])  # realised 4 vs predicted 2 → 2.0
    ratio, n = calibration.empirical_goal_ratio(ev)
    assert n == 1 and abs(ratio - 2.0) < 1e-9

def test_empirical_ratio_none_without_pairs():
    ratio, n = calibration.empirical_goal_ratio([])
    assert ratio is None and n == 0


# ── resolve_kappa ───────────────────────────────────────────────────────────

def test_resolve_kappa_below_min_settled_uses_base(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_ADAPTIVE_KAPPA", True)
    monkeypatch.setattr(config, "GOAL_SCALE_KAPPA", 1.15)
    monkeypatch.setattr(config, "KAPPA_MIN_SETTLED", 6)
    ev = _events([("m1", 3, 2.0)])  # only 1 settled
    kappa, meta = calibration.resolve_kappa(ev)
    assert kappa == 1.15 and meta["adaptive"] is False

def test_resolve_kappa_static_when_disabled(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_ADAPTIVE_KAPPA", False)
    monkeypatch.setattr(config, "GOAL_SCALE_KAPPA", 1.2)
    ev = _events([("m%d" % i, 3, 2.0) for i in range(10)])
    kappa, meta = calibration.resolve_kappa(ev)
    assert kappa == 1.2 and meta["adaptive"] is False

def test_resolve_kappa_adaptive_blends_and_bounds(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_ADAPTIVE_KAPPA", True)
    monkeypatch.setattr(config, "GOAL_SCALE_KAPPA", 1.15)
    monkeypatch.setattr(config, "KAPPA_MIN_SETTLED", 6)
    monkeypatch.setattr(config, "KAPPA_SHRINK", 0.4)
    monkeypatch.setattr(config, "KAPPA_BOUNDS", (1.0, 1.5))
    # realised == predicted → ratio 1.0; blended = 0.6*1.15 + 0.4*1.0 = 1.09
    ev = _events([("m%d" % i, 2, 2.0) for i in range(8)])
    kappa, meta = calibration.resolve_kappa(ev)
    assert meta["adaptive"] is True
    assert abs(kappa - 1.09) < 1e-9

def test_resolve_kappa_respects_upper_bound(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_ADAPTIVE_KAPPA", True)
    monkeypatch.setattr(config, "GOAL_SCALE_KAPPA", 1.4)
    monkeypatch.setattr(config, "KAPPA_MIN_SETTLED", 6)
    monkeypatch.setattr(config, "KAPPA_SHRINK", 0.8)
    monkeypatch.setattr(config, "KAPPA_BOUNDS", (1.0, 1.3))
    # huge under-prediction would push κ high; bound caps at 1.3
    ev = _events([("m%d" % i, 8, 2.0) for i in range(8)])
    kappa, meta = calibration.resolve_kappa(ev)
    assert kappa == 1.3

def test_resolve_kappa_never_below_one(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_ADAPTIVE_KAPPA", True)
    monkeypatch.setattr(config, "GOAL_SCALE_KAPPA", 1.15)
    monkeypatch.setattr(config, "KAPPA_MIN_SETTLED", 6)
    monkeypatch.setattr(config, "KAPPA_SHRINK", 1.0)
    monkeypatch.setattr(config, "KAPPA_BOUNDS", (1.0, 1.5))
    # over-prediction (realised 1 vs predicted 2 → ratio 0.5) clamps to 1.0
    ev = _events([("m%d" % i, 1, 2.0) for i in range(8)])
    kappa, _ = calibration.resolve_kappa(ev)
    assert kappa == 1.0
