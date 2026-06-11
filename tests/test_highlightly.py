"""Unit tests for the pure normalizers in fetch_highlightly.py (no network)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.fetch_highlightly import (
    _pct_to_int, normalize_events, normalize_lineups, normalize_statistics,
)

# Shapes taken from the live CI discovery (run 27375034525)

_RAW_STATS = [
    {"team": {"id": 14400, "name": "Mexico"},
     "statistics": [
         {"value": "56%", "displayName": "Ball Possession"},
         {"value": 11, "displayName": "Total Shots"},
         {"value": 4, "displayName": "Shots On Target"},
         {"value": 0.89, "displayName": "Expected Goals"},
         {"value": 55, "displayName": "Attacks"},          # not mapped → dropped
     ]},
    {"team": {"id": 1303665, "name": "South Africa"},
     "statistics": [
         {"value": "44%", "displayName": "Ball Possession"},
         {"value": 6, "displayName": "Total Shots"},
         {"value": 1, "displayName": "Shots On Target"},
     ]},
]

_RAW_EVENTS = [
    {"team": {"name": "Mexico"}, "time": "9", "type": "Goal",
     "player": "J. Quinones", "assist": "E. Lira", "substituted": None},
    {"team": {"name": "South Africa"}, "time": "17", "type": "Yellow Card",
     "player": "T. Mokoena", "assist": None, "substituted": None},
    {"team": {"name": "Mexico"}, "time": "60", "type": "Substitution",
     "player": "A. Vega", "assist": None, "substituted": "C. Huerta"},
]

_RAW_LINEUPS = {
    "homeTeam": {
        "formation": "4-1-4-1",
        "initialLineup": [
            {"name": "Luis Malagón", "number": 1, "position": "Goalkeeper", "id": 1},
        ],
        "substitutes": [
            {"name": "Guillermo Ochoa", "number": 13, "position": "Goalkeeper", "id": 2},
        ],
    },
    "awayTeam": {
        "formation": "4-3-3",
        "initialLineup": [
            {"name": "R. Williams", "number": 1, "position": "Goalkeeper", "id": 3},
        ],
        "substitutes": [],
    },
}


# ── _pct_to_int ────────────────────────────────────────────────────────────

def test_pct_string():
    assert _pct_to_int("56%") == 56

def test_pct_int_passthrough():
    assert _pct_to_int(44) == 44

def test_pct_garbage_none():
    assert _pct_to_int("n/a") is None


# ── normalize_statistics ───────────────────────────────────────────────────

def test_stats_sides_and_keys():
    out = normalize_statistics(_RAW_STATS, home_code="MEX")
    assert out["home"]["possession"] == 56
    assert out["away"]["possession"] == 44
    assert out["home"]["shots"] == 11
    assert out["away"]["shots_on_target"] == 1
    assert out["home"]["xg"] == 0.89
    assert "Attacks" not in str(out)   # unmapped stats dropped

def test_stats_rounding_not_forced_to_100():
    # 56 + 44 = 100 here, but the normalizer must not force it
    raw = [dict(_RAW_STATS[0]), dict(_RAW_STATS[1])]
    raw[0]["statistics"] = [{"value": "57%", "displayName": "Ball Possession"}]
    raw[1]["statistics"] = [{"value": "44%", "displayName": "Ball Possession"}]
    out = normalize_statistics(raw, "MEX")
    assert out["home"]["possession"] + out["away"]["possession"] == 101

def test_stats_unmappable_side_returns_none():
    raw = [{"team": {"name": "Fantasia"}, "statistics": []},
           {"team": {"name": "Atlantis"}, "statistics": []}]
    assert normalize_statistics(raw, "MEX") is None

def test_stats_short_payload_none():
    assert normalize_statistics([], "MEX") is None


# ── normalize_events ───────────────────────────────────────────────────────

def test_events_side_mapping():
    out = normalize_events(_RAW_EVENTS, home_code="MEX")
    assert out[0]["side"] == "home" and out[0]["type"] == "Goal"
    assert out[1]["side"] == "away"
    assert out[2]["sub_in"] == "C. Huerta"

def test_events_empty():
    assert normalize_events([], "MEX") == []


# ── normalize_lineups ──────────────────────────────────────────────────────

def test_lineups_shape():
    out = normalize_lineups(_RAW_LINEUPS)
    assert out["home"]["formation"] == "4-1-4-1"
    assert out["home"]["xi"][0]["name"] == "Luis Malagón"
    assert out["home"]["bench"][0]["number"] == 13
    assert out["away"]["formation"] == "4-3-3"

def test_lineups_nested_rows_flattened():
    raw = {"homeTeam": {"formation": "4-4-2",
                        "initialLineup": [[{"name": "A", "number": 1, "position": "G"}],
                                          [{"name": "B", "number": 2, "position": "D"}]],
                        "substitutes": []},
           "awayTeam": {"formation": None, "initialLineup": [], "substitutes": []}}
    out = normalize_lineups(raw)
    assert [p["name"] for p in out["home"]["xi"]] == ["A", "B"]

def test_lineups_empty_none():
    assert normalize_lineups({"homeTeam": {}, "awayTeam": {}}) is None
