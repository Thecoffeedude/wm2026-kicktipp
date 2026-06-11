"""Unit tests for the deadline-anchored submit timing in kicktipp_submit.py."""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from datetime import timedelta

from kicktipp_submit import (
    FINISH_MARGIN_MIN, FRESH_MAX_MIN, FRESH_MIN_MIN,
    decide_action, parse_kicktipp_deadline, submit_window,
    uncovered_due_matches,
)

_TIP = {"home": 2, "away": 1}


# ─── submit_window classification ─────────────────────────────────────────────

def test_window_freshness():
    assert submit_window(45) == "freshness"

def test_window_safety():
    assert submit_window(480) == "safety"

def test_window_too_late():
    assert submit_window(10) == "too_late"

def test_window_closed():
    assert submit_window(-3) == "closed"

def test_window_waiting_between_bands():
    assert submit_window(200) == "waiting"

def test_window_unknown_is_safety():
    assert submit_window(None) == "safety"


# ─── decide_action — safety pass (empty field) ───────────────────────────────

def test_empty_field_safety_fills():
    action, reason = decide_action("", "", _TIP, 480)
    assert action == "tip"
    assert "safety" in reason

def test_empty_field_fills_even_in_freshness():
    action, _ = decide_action("", "", _TIP, 45)
    assert action == "tip"

def test_no_prediction_skips():
    action, _ = decide_action("", "", None, 480)
    assert action == "skip_no_match"


# ─── decide_action — freshness pass (already tipped) ─────────────────────────

def test_already_tipped_outside_window_skips():
    action, _ = decide_action("1", "0", _TIP, 480)   # different tip, but not freshness
    assert action == "skip_tipped"

def test_freshness_overwrites_changed_tip():
    action, reason = decide_action("1", "0", _TIP, 45)   # in [25,75], tip differs
    assert action == "tip"
    assert "freshness" in reason

def test_freshness_keeps_unchanged_tip():
    action, _ = decide_action("2", "1", _TIP, 45)    # same as _TIP → idempotent
    assert action == "skip_unchanged"

def test_unchanged_outside_window_also_idempotent():
    action, _ = decide_action("2", "1", _TIP, 480)
    assert action == "skip_unchanged"


# ─── decide_action — deadline guards ─────────────────────────────────────────

def test_too_late_blocks_submit():
    action, reason = decide_action("", "", _TIP, FINISH_MARGIN_MIN - 1)
    assert action == "skip_too_late"
    assert "margin" in reason

def test_closed_when_deadline_passed():
    action, _ = decide_action("", "", _TIP, -1)
    assert action == "skip_closed"

def test_force_overwrite_outside_window():
    action, reason = decide_action("1", "0", _TIP, 480, force_overwrite=True)
    assert action == "tip"
    assert "forced" in reason

def test_force_overwrite_still_idempotent():
    # Even forced, an unchanged tip is not rewritten
    action, _ = decide_action("2", "1", _TIP, 480, force_overwrite=True)
    assert action == "skip_unchanged"


# ─── parse_kicktipp_deadline (Berlin local → UTC) ────────────────────────────

_REF = datetime(2026, 6, 14, 10, 0, tzinfo=timezone.utc)

def test_parse_full_date_short_year():
    # 14.06.26 18:00 Berlin (CEST = UTC+2) → 16:00 UTC
    dt = parse_kicktipp_deadline("14.06.26 18:00", now=_REF)
    assert dt == datetime(2026, 6, 14, 16, 0, tzinfo=timezone.utc)

def test_parse_full_date_long_year():
    dt = parse_kicktipp_deadline("14.06.2026 18:00", now=_REF)
    assert dt == datetime(2026, 6, 14, 16, 0, tzinfo=timezone.utc)

def test_parse_date_no_year_uses_current():
    dt = parse_kicktipp_deadline("14.06. 21:00", now=_REF)
    assert dt == datetime(2026, 6, 14, 19, 0, tzinfo=timezone.utc)

def test_parse_empty_is_none():
    assert parse_kicktipp_deadline("", now=_REF) is None

def test_parse_garbage_is_none():
    assert parse_kicktipp_deadline("Mannschaft", now=_REF) is None


# ─── uncovered_due_matches (collapsed-matchday safety net) ───────────────────

def _m(hc, ac, mins_from_now, now):
    ko = now + timedelta(minutes=mins_from_now)
    return {"home_code": hc, "away_code": ac, "home_team": hc, "away_team": ac,
            "commence_time": ko.strftime("%Y-%m-%dT%H:%M:%SZ")}

def test_uncovered_flags_due_game_without_row():
    now = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)
    matches = [_m("CAN", "BIH", 45, now)]          # freshness window, no row
    out = uncovered_due_matches(matches, scraped_keys=set(), tipped_keys=set(), now=now)
    assert len(out) == 1 and out[0]["home_code"] == "CAN"

def test_uncovered_ignores_present_row():
    now = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)
    matches = [_m("CAN", "BIH", 45, now)]
    out = uncovered_due_matches(matches, scraped_keys={("CAN", "BIH")}, tipped_keys=set(), now=now)
    assert out == []

def test_uncovered_ignores_already_tipped():
    now = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)
    matches = [_m("CAN", "BIH", 480, now)]         # safety window but already tipped
    out = uncovered_due_matches(matches, scraped_keys=set(), tipped_keys={("CAN", "BIH")}, now=now)
    assert out == []

def test_uncovered_ignores_far_future_game():
    now = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)
    matches = [_m("CAN", "BIH", 3000, now)]        # ~50h out → waiting, not due
    out = uncovered_due_matches(matches, scraped_keys=set(), tipped_keys=set(), now=now)
    assert out == []
