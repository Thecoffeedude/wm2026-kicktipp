"""Unit tests for the pure capture decision logic in odds_capture.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import odds_capture as oc


# ── due_offsets_for ────────────────────────────────────────────────────────

def test_closing_window():
    assert oc.due_offsets_for(30, set()) == ["closing"]

def test_closing_already_have():
    assert oc.due_offsets_for(30, {"closing"}) == []

def test_t3h_window():
    assert oc.due_offsets_for(180, set()) == ["T-3h"]

def test_t24h_window():
    assert oc.due_offsets_for(1440, set()) == ["T-24h"]

def test_no_window_between_offsets():
    assert oc.due_offsets_for(600, set()) == []   # 10h out — nothing due

def test_kickoff_too_close_no_window():
    assert oc.due_offsets_for(3, set()) == []     # <8 min, missed closing window


# ── plan_capture ───────────────────────────────────────────────────────────

def test_plan_no_due():
    plan = oc.plan_capture({}, remaining=400)
    assert plan["fetch"] is False

def test_plan_closing_uses_totals():
    plan = oc.plan_capture({"M1": {"closing"}}, remaining=400)
    assert plan["fetch"] is True
    assert plan["markets"] == "h2h,totals"

def test_plan_early_only_uses_h2h():
    plan = oc.plan_capture({"M1": {"T-24h"}}, remaining=400)
    assert plan["fetch"] is True
    assert plan["markets"] == "h2h"

def test_plan_budget_low_drops_early():
    # Low budget + only early due → skip entirely (early is optional)
    plan = oc.plan_capture({"M1": {"T-3h"}}, remaining=50)
    assert plan["fetch"] is False
    assert "closing only" in plan["reason"]

def test_plan_budget_low_keeps_closing():
    plan = oc.plan_capture({"M1": {"closing"}, "M2": {"T-24h"}}, remaining=50)
    assert plan["fetch"] is True
    assert plan["allowed"] == {"closing"}
    assert plan["markets"] == "h2h,totals"

def test_plan_degrades_when_one_credit_left():
    plan = oc.plan_capture({"M1": {"closing"}}, remaining=1)
    assert plan["fetch"] is True
    assert plan["markets"] == "h2h"
    assert "degraded" in plan["reason"]

def test_plan_no_credits_no_fetch():
    plan = oc.plan_capture({"M1": {"closing"}}, remaining=0)
    assert plan["fetch"] is False

def test_plan_unknown_budget_proceeds():
    plan = oc.plan_capture({"M1": {"closing"}}, remaining=None)
    assert plan["fetch"] is True
    assert plan["markets"] == "h2h,totals"
