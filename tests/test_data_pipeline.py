"""Unit tests for schedule enrichment, results merging, and ntfy message building."""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import json

from src.build_data import (carry_forward_finished, enrich_kickoff_times,
                            reconstruct_history)
from src.live_update import merge_live, merge_results
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


# ── carry_forward_finished ────────────────────────────────────────────────

NOW = datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc)


def _write_prev(tmp_path, matches):
    p = tmp_path / "data.json"
    p.write_text(json.dumps({"matches": matches}), encoding="utf-8")
    return p


def test_carry_forward_appends_past_match(tmp_path):
    prev = _write_prev(tmp_path, [
        {"id": "ua_MEX_RSA_2026-06-11", "commence_time": "2026-06-11T19:00:00Z"},
    ])
    matches = [{"id": "ua_GER_FRA_2026-06-20", "commence_time": "2026-06-20T19:00:00Z"}]
    n = carry_forward_finished(matches, prev, NOW)
    assert n == 1
    assert any(m["id"] == "ua_MEX_RSA_2026-06-11" for m in matches)
    assert matches[-1]["carried_forward"] is True


def test_carry_forward_skips_future_and_present(tmp_path):
    prev = _write_prev(tmp_path, [
        {"id": "ua_GER_FRA_2026-06-20", "commence_time": "2026-06-20T19:00:00Z"},  # future
        {"id": "ua_MEX_RSA_2026-06-11", "commence_time": "2026-06-11T19:00:00Z"},  # already present
    ])
    matches = [{"id": "ua_MEX_RSA_2026-06-11", "commence_time": "2026-06-11T19:00:00Z"}]
    n = carry_forward_finished(matches, prev, NOW)
    assert n == 0
    assert len(matches) == 1


def test_carry_forward_no_previous_file(tmp_path):
    assert carry_forward_finished([], tmp_path / "missing.json", NOW) == 0


# ── reconstruct_history ───────────────────────────────────────────────────

def test_reconstruct_blend_match():
    events = [
        {"type": "uanalyse", "match_id": "ua_MEX_RSA_2026-06-11",
         "home_code": "MEX", "away_code": "RSA", "kickoff": "2026-06-11T19:00:00Z",
         "p": {"home": 0.64, "draw": 0.21, "away": 0.15},
         "lambda": {"home": 1.9, "away": 0.8}, "captured_at": "2026-06-11T18:36Z"},
        {"type": "odds", "match_id": "ua_MEX_RSA_2026-06-11",
         "home_code": "MEX", "away_code": "RSA", "kickoff": "2026-06-11T19:00:00Z",
         "p": {"home": 0.67, "draw": 0.22, "away": 0.11},
         "totals_line": 2.5, "totals_over_prob": 0.49, "captured_at": "2026-06-11T18:36Z"},
        {"type": "result", "match_id": "ua_MEX_RSA_2026-06-11",
         "home_code": "MEX", "away_code": "RSA",
         "score_home": 2, "score_away": 0, "outcome": "home"},
    ]
    weights = {"market": 0.5, "uanalyse": 0.5}
    out = reconstruct_history(events, set(), weights, kappa=1.1, rho=-0.1, gamma=0.0)
    assert len(out) == 1
    m = out[0]
    assert m["id"] == "ua_MEX_RSA_2026-06-11"
    assert m["carried_forward"] is True
    assert "blend" in m["sources"] and "uanalyse" in m["sources"]
    assert m["recommended_tip"]["based_on"] == "blend"
    assert m["expected_goals"]["home"] > 0


def test_reconstruct_skips_present_and_unsettled():
    events = [
        {"type": "uanalyse", "match_id": "ua_A_B_2026-06-11", "home_code": "A",
         "away_code": "B", "kickoff": "2026-06-11T19:00:00Z",
         "p": {"home": 0.5, "draw": 0.3, "away": 0.2},
         "lambda": {"home": 1.4, "away": 1.0}, "captured_at": "x"},
        # no result for A_B → unsettled, must be skipped
        {"type": "result", "match_id": "ua_C_D_2026-06-11", "home_code": "C",
         "away_code": "D", "score_home": 1, "score_away": 1, "outcome": "draw"},
        # C_D has a result but no forecast → skipped
    ]
    out = reconstruct_history(events, set(), {"market": 0.5, "uanalyse": 0.5},
                              kappa=1.0, rho=0.0, gamma=0.0)
    assert out == []


def test_reconstruct_respects_present_ids():
    events = [
        {"type": "odds", "match_id": "ua_A_B_2026-06-11", "home_code": "A",
         "away_code": "B", "kickoff": "2026-06-11T19:00:00Z",
         "p": {"home": 0.5, "draw": 0.3, "away": 0.2},
         "totals_line": 2.5, "totals_over_prob": 0.5, "captured_at": "x"},
        {"type": "result", "match_id": "ua_A_B_2026-06-11", "home_code": "A",
         "away_code": "B", "score_home": 1, "score_away": 0, "outcome": "home"},
    ]
    out = reconstruct_history(events, {"ua_A_B_2026-06-11"},
                              {"market": 0.5, "uanalyse": 0.5}, 1.0, 0.0, 0.0)
    assert out == []


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


# ── merge_live (anti-regression against flapping API states) ─────────────

def _live(hc, ac, status, sh=None, sa=None, goals=None):
    rank_live = status in ("IN_PLAY", "PAUSED")
    done = status.startswith("FINISHED")
    return {
        "home_code": hc, "away_code": ac, "utc_date": "2026-06-11T19:00:00Z",
        "status": status, "is_live": rank_live, "is_halftime": status == "PAUSED",
        "is_done": done, "score_home": sh, "score_away": sa,
        "goals": goals or [],
    }


def test_merge_live_keeps_state_on_status_regression():
    old = [_live("MEX", "RSA", "IN_PLAY", 1, 0)]
    new = [_live("MEX", "RSA", "TIMED")]          # API glitch: back to scheduled
    merged = merge_live(old, new)
    assert merged[0]["status"] == "IN_PLAY"
    assert merged[0]["score_home"] == 1


def test_merge_live_accepts_progression():
    old = [_live("MEX", "RSA", "IN_PLAY", 1, 0)]
    new = [_live("MEX", "RSA", "FINISHED", 2, 0)]
    merged = merge_live(old, new)
    assert merged[0]["status"] == "FINISHED"
    assert merged[0]["score_home"] == 2


def test_merge_live_keeps_score_when_api_drops_it():
    old = [_live("MEX", "RSA", "IN_PLAY", 1, 0)]
    new = [_live("MEX", "RSA", "IN_PLAY")]        # same rank, score vanished
    merged = merge_live(old, new)
    assert merged[0]["score_home"] == 1


def test_merge_live_allows_var_score_correction():
    old = [_live("MEX", "RSA", "IN_PLAY", 1, 0)]
    new = [_live("MEX", "RSA", "IN_PLAY", 0, 0)]  # goal disallowed via VAR
    merged = merge_live(old, new)
    assert merged[0]["score_home"] == 0


def test_merge_live_preserves_goal_enrichment():
    goals = [{"minute": 23, "scorer": "R. Jiménez", "side": "home"}]
    old = [_live("MEX", "RSA", "IN_PLAY", 1, 0, goals=goals)]
    new = [_live("MEX", "RSA", "IN_PLAY", 1, 0)]  # fresh poll without details
    merged = merge_live(old, new)
    assert merged[0]["goals"] == goals


def test_merge_live_new_match_passes_through():
    new = [_live("KOR", "CZE", "TIMED")]
    assert merge_live([], new) == new
