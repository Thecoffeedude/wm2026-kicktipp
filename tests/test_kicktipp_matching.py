"""Unit tests for the pure matching/planning logic in kicktipp_submit.py.
No browser, no playwright, no network required.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow importing from src/ without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from kicktipp_submit import (
    build_prediction_index,
    canonicalize,
    decide_action,
    match_row,
    plan_submissions,
)

# ─── Fixtures ────────────────────────────────────────────────────────────────

ALIASES = {
    "Turkey": "Türkiye",
    "USA": "United States",
    "Korea Republic": "South Korea",
}

_TIP = {"home": 2, "away": 1, "expected_points": 2.5, "based_on": "uanalyse"}

def _pred(home: str, away: str, hours_from_now: float = 6.0) -> dict:
    kickoff = datetime.now(timezone.utc) + timedelta(hours=hours_from_now)
    return {
        "home_team": home,
        "away_team": away,
        "commence_time": kickoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "recommended_tip": _TIP,
    }

MATCHES = [
    _pred("Germany", "Brazil"),
    _pred("Türkiye", "Japan"),
    _pred("United States", "Mexico"),
]


# ─── canonicalize ────────────────────────────────────────────────────────────

def test_canonicalize_known_alias():
    assert canonicalize("Turkey", ALIASES) == "Türkiye"

def test_canonicalize_passthrough():
    assert canonicalize("Germany", ALIASES) == "Germany"

def test_canonicalize_unknown_name():
    assert canonicalize("Fantasia FC", ALIASES) == "Fantasia FC"


# ─── build_prediction_index ──────────────────────────────────────────────────

def test_index_keys_are_canonical():
    # Index is keyed by FIFA codes since the teams.py migration
    index = build_prediction_index(MATCHES, ALIASES)
    assert ("GER", "BRA") in index
    assert ("TUR", "JPN") in index


# ─── match_row ───────────────────────────────────────────────────────────────

def test_match_exact():
    index = build_prediction_index(MATCHES, ALIASES)
    assert match_row("Germany", "Brazil", index, ALIASES) is not None

def test_match_via_alias():
    index = build_prediction_index(MATCHES, ALIASES)
    result = match_row("Turkey", "Japan", index, ALIASES)
    assert result is not None
    assert result["home_team"] == "Türkiye"

def test_match_alias_both_sides():
    index = build_prediction_index(MATCHES, ALIASES)
    result = match_row("USA", "Mexico", index, ALIASES)
    assert result is not None

def test_match_no_result():
    index = build_prediction_index(MATCHES, ALIASES)
    assert match_row("Foo", "Bar", index, ALIASES) is None


# ─── decide_action ───────────────────────────────────────────────────────────

_NOW = datetime.now(timezone.utc)
_PRED_OPEN = _pred("A", "B", hours_from_now=6)
_PRED_SOON = _pred("A", "B", hours_from_now=1)  # within default 2h buffer


def test_tip_open_game():
    action, _ = decide_action("", "", _PRED_OPEN, False, _NOW, 2.0)
    assert action == "tip"

def test_skip_no_prediction():
    action, reason = decide_action("", "", None, False, _NOW, 2.0)
    assert action == "skip_no_match"
    assert "data.json" in reason

def test_skip_already_tipped():
    action, reason = decide_action("2", "1", _PRED_OPEN, False, _NOW, 2.0)
    assert action == "skip_tipped"
    assert "OVERWRITE" in reason

def test_overwrite_true_retips():
    action, _ = decide_action("2", "1", _PRED_OPEN, True, _NOW, 2.0)
    assert action == "tip"

def test_skip_deadline():
    action, reason = decide_action("", "", _PRED_SOON, False, _NOW, 2.0)
    assert action == "skip_deadline"
    assert "buffer" in reason

def test_deadline_exactly_at_buffer_skipped():
    pred = _pred("A", "B", hours_from_now=2.0)
    action, _ = decide_action("", "", pred, False, _NOW, 2.0)
    assert action == "skip_deadline"

def test_game_just_outside_buffer_tips():
    pred = _pred("A", "B", hours_from_now=2.1)
    action, _ = decide_action("", "", pred, False, _NOW, 2.0)
    assert action == "tip"

def test_date_only_commence_time():
    # Some entries have "2026-06-14" without a time component
    future_date = (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y-%m-%d")
    pred = {**_PRED_OPEN, "commence_time": future_date}
    action, _ = decide_action("", "", pred, False, _NOW, 2.0)
    assert action == "tip"


# ─── plan_submissions ────────────────────────────────────────────────────────

def _row(home, away, home_val="", away_val=""):
    return {
        "home": home,
        "away": away,
        "home_value": home_val,
        "away_value": away_val,
        "home_input": f"{home}_heimTipp",
        "away_input": f"{away}_gastTipp",
    }


def test_plan_tips_open_games():
    rows = [_row("Germany", "Brazil"), _row("Foo", "Bar")]
    result = plan_submissions(rows, MATCHES, ALIASES, now=_NOW)
    ger_bra = next(r for r in result if r["kicktipp_home"] == "Germany")
    foo_bar = next(r for r in result if r["kicktipp_home"] == "Foo")
    assert ger_bra["action"] == "tip"
    assert foo_bar["action"] == "skip_no_match"

def test_plan_skips_tipped():
    rows = [_row("Germany", "Brazil", home_val="1", away_val="0")]
    result = plan_submissions(rows, MATCHES, ALIASES, overwrite=False, now=_NOW)
    assert result[0]["action"] == "skip_tipped"

def test_plan_overwrite_retips():
    rows = [_row("Germany", "Brazil", home_val="1", away_val="0")]
    result = plan_submissions(rows, MATCHES, ALIASES, overwrite=True, now=_NOW)
    assert result[0]["action"] == "tip"

def test_plan_tip_includes_correct_tip():
    rows = [_row("Germany", "Brazil")]
    result = plan_submissions(rows, MATCHES, ALIASES, now=_NOW)
    assert result[0]["tip"] == _TIP

def test_plan_no_match_tip_is_none():
    rows = [_row("Foo", "Bar")]
    result = plan_submissions(rows, MATCHES, ALIASES, now=_NOW)
    assert result[0]["tip"] is None

def test_plan_alias_resolution():
    rows = [_row("Turkey", "Japan")]
    result = plan_submissions(rows, MATCHES, ALIASES, now=_NOW)
    assert result[0]["action"] == "tip"

def test_plan_empty_rows():
    assert plan_submissions([], MATCHES, ALIASES) == []

def test_plan_empty_matches():
    rows = [_row("Germany", "Brazil")]
    result = plan_submissions(rows, [], ALIASES, now=_NOW)
    assert result[0]["action"] == "skip_no_match"
