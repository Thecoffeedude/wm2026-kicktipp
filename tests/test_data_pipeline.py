"""Unit tests for schedule enrichment, results merging, and ntfy message building."""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.build_data import enrich_kickoff_times
from src.live_update import merge_results
from src.notify_tips import build_message


# ── enrich_kickoff_times ──────────────────────────────────────────────────

def _match(home, away, ct):
    return {"home_code": home, "away_code": away, "commence_time": ct}


def _sched(home, away, utc_date, done=False, sh=None, sa=None):
    return {
        "home_code": home, "away_code": away, "utc_date": utc_date,
        "is_done": done, "score_home": sh, "score_away": sa,
    }


def test_enrich_replaces_date_only():
    matches = [_match("MEX", "RSA", "2026-06-11")]
    schedule = [_sched("MEX", "RSA", "2026-06-11T19:00:00Z")]
    assert enrich_kickoff_times(matches, schedule) == 1
    assert matches[0]["commence_time"] == "2026-06-11T19:00:00Z"


def test_enrich_tolerates_one_day_shift():
    # Late kickoff crosses UTC midnight: uanalyse says 11th, schedule says 12th
    matches = [_match("USA", "PAR", "2026-06-11")]
    schedule = [_sched("USA", "PAR", "2026-06-12T02:00:00Z")]
    assert enrich_kickoff_times(matches, schedule) == 1
    assert matches[0]["commence_time"] == "2026-06-12T02:00:00Z"


def test_enrich_rejects_distant_date():
    # Same pairing but a different match (e.g. hypothetical KO rematch)
    matches = [_match("GER", "FRA", "2026-06-11")]
    schedule = [_sched("GER", "FRA", "2026-07-05T20:00:00Z")]
    assert enrich_kickoff_times(matches, schedule) == 0
    assert matches[0]["commence_time"] == "2026-06-11"


def test_enrich_keeps_existing_exact_time():
    matches = [_match("MEX", "RSA", "2026-06-11T18:00:00Z")]
    schedule = [_sched("MEX", "RSA", "2026-06-11T19:00:00Z")]
    assert enrich_kickoff_times(matches, schedule) == 0
    assert matches[0]["commence_time"] == "2026-06-11T18:00:00Z"


def test_enrich_no_schedule_entry():
    matches = [_match("MEX", "RSA", "2026-06-11")]
    assert enrich_kickoff_times(matches, []) == 0


# ── merge_results ─────────────────────────────────────────────────────────

def test_merge_keeps_old_results():
    old = [_sched("MEX", "RSA", "2026-06-11T19:00:00Z", done=True, sh=2, sa=0)]
    new = [_sched("CAN", "QAT", "2026-06-12T19:00:00Z", done=True, sh=1, sa=1)]
    merged = merge_results(old, new)
    assert len(merged) == 2
    assert merged[0]["home_code"] == "MEX"  # sorted by utc_date


def test_merge_updates_in_place():
    old = [_sched("MEX", "RSA", "2026-06-11T19:00:00Z", done=True, sh=1, sa=0)]
    new = [_sched("MEX", "RSA", "2026-06-11T19:00:00Z", done=True, sh=2, sa=0)]
    merged = merge_results(old, new)
    assert len(merged) == 1
    assert merged[0]["score_home"] == 2


def test_merge_ignores_unfinished():
    new = [_sched("MEX", "RSA", "2026-06-11T19:00:00Z", done=False)]
    assert merge_results([], new) == []


# ── notify_tips.build_message ─────────────────────────────────────────────

def _tip_match(home, away, ct, th, ta):
    return {
        "home_team": home, "away_team": away, "commence_time": ct,
        "recommended_tip": {"home": th, "away": ta},
    }


def test_build_message_today_only():
    now = datetime(2026, 6, 11, 8, 0, tzinfo=timezone.utc)
    matches = [
        _tip_match("Mexico", "South Africa", "2026-06-11T19:00:00Z", 1, 0),
        _tip_match("Canada", "Qatar", "2026-06-12T19:00:00Z", 2, 0),
    ]
    title, body = build_message(matches, now)
    assert "11.06." in title
    assert "Mexico 1:0 South Africa" in body
    assert "Canada" not in body


def test_build_message_none_when_no_matches():
    now = datetime(2026, 6, 11, 8, 0, tzinfo=timezone.utc)
    assert build_message([], now) is None


def test_build_message_date_only_kickoff():
    now = datetime(2026, 6, 11, 8, 0, tzinfo=timezone.utc)
    matches = [_tip_match("Mexico", "South Africa", "2026-06-11", 1, 0)]
    result = build_message(matches, now)
    assert result is not None
    assert "–:––" in result[1]
