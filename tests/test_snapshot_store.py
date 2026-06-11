"""Unit tests for src/snapshot_store.py — pure queries + append/load round-trip."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import snapshot_store as ss


_EVENTS = [
    {"type": "odds", "match_id": "M1", "offset": "T-24h", "captured_at": "2026-06-10T19:00:00Z",
     "p": {"home": 0.6, "draw": 0.25, "away": 0.15}},
    {"type": "odds", "match_id": "M1", "offset": "closing", "captured_at": "2026-06-11T18:35:00Z",
     "p": {"home": 0.67, "draw": 0.21, "away": 0.12}},
    {"type": "uanalyse", "match_id": "M1", "offset": "closing", "captured_at": "2026-06-11T09:00:00Z",
     "p": {"home": 0.65, "draw": 0.21, "away": 0.14}},
    {"type": "result", "match_id": "M1", "captured_at": "2026-06-11T21:30:00Z",
     "outcome": "home", "score_home": 2, "score_away": 0},
    # M2 has forecasts but no result yet → must not settle
    {"type": "odds", "match_id": "M2", "offset": "closing", "captured_at": "2026-06-12T18:00:00Z",
     "p": {"home": 0.3, "draw": 0.3, "away": 0.4}},
]


# ── has_snapshot ───────────────────────────────────────────────────────────

def test_has_snapshot_offset_specific():
    assert ss.has_snapshot(_EVENTS, "M1", "odds", "closing") is True
    assert ss.has_snapshot(_EVENTS, "M1", "odds", "T-3h") is False

def test_has_snapshot_any_offset():
    assert ss.has_snapshot(_EVENTS, "M1", "uanalyse") is True
    assert ss.has_snapshot(_EVENTS, "M2", "result") is False


# ── settled_forecasts ──────────────────────────────────────────────────────

def test_settled_uses_closing_snapshot():
    recs = ss.settled_forecasts(_EVENTS)
    by_src = {r["source"]: r for r in recs}
    assert set(by_src) == {"market", "uanalyse"}
    # Market record must be the closing (latest) snapshot, not T-24h
    assert by_src["market"]["p"]["home"] == 0.67
    assert by_src["market"]["outcome"] == "home"

def test_settled_excludes_unresolved_matches():
    recs = ss.settled_forecasts(_EVENTS)
    assert all(r["match_id"] != "M2" for r in recs)

def test_count_settled():
    assert ss.count_settled_matches(_EVENTS) == 1


# ── append / load round-trip ───────────────────────────────────────────────

def test_append_and_load(tmp_path):
    p = tmp_path / "snap.jsonl"
    ss.append_event({"type": "odds", "match_id": "X", "p": {"home": 0.5, "draw": 0.3, "away": 0.2}}, path=p)
    ss.append_event({"type": "result", "match_id": "X", "outcome": "draw"}, path=p)
    events = ss.load_events(path=p)
    assert len(events) == 2
    assert events[0]["captured_at"]  # auto-stamped
    assert events[1]["type"] == "result"

def test_load_missing_file_is_empty(tmp_path):
    assert ss.load_events(path=tmp_path / "nope.jsonl") == []

def test_append_rejects_bad_type(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        ss.append_event({"type": "bogus", "match_id": "X"}, path=tmp_path / "s.jsonl")
