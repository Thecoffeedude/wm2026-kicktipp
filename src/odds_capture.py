"""
Kickoff-anchored odds capture — the ONLY consumer of the Odds API.

Runs frequently (every ~30 min) but spends credits only when a match is inside
an unfilled offset window. One API call returns every upcoming match at once, so
a single call serves all matches currently in-window.

Offsets per match (minutes before kickoff):
  closing  (8–55 min)   MANDATORY · markets h2h,totals (2 credits) → "Schlussquote"
  T-3h     (150–220)     optional  · markets h2h        (1 credit)
  T-24h    (1380–1500)   optional  · markets h2h        (1 credit)

Budget guard: the x-requests-remaining header is tracked in data/odds_budget.json.
Below SAFETY_MARGIN, optional early snapshots are dropped and only mandatory
closing lines are fetched. Below the per-call cost, closing degrades to h2h-only.

Writes:
  data/snapshots.jsonl  — append-only odds/uanalyse/result events
  docs/odds_latest.json — latest full payload for build_data.py + frontend
  data/odds_budget.json — last known remaining credits

The decision helpers (due_offsets_for, plan_capture) are pure and unit-tested.
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

import config
from src import snapshot_store as ss
from src import weighting
from src.fetch_odds import book_keys, request_odds
from src.fetch_uanalyse import fetch_uanalyse
from src.probabilities import process_match
from src.teams import resolve

logger = logging.getLogger(__name__)

DATA_JSON     = Path(__file__).parent.parent / "docs" / "data.json"
RESULTS_JSON  = Path(__file__).parent.parent / "docs" / "results.json"
ODDS_LATEST   = Path(__file__).parent.parent / "docs" / "odds_latest.json"
BUDGET_PATH   = Path(__file__).parent.parent / "data" / "odds_budget.json"

# Offset windows: (name, low_min, high_min, markets, mandatory)
OFFSETS = [
    ("closing", 8,    55,   "h2h,totals", True),
    ("T-3h",    150,  220,  "h2h",        False),
    ("T-24h",   1380, 1500, "h2h",        False),
]

# Below this many remaining credits, only mandatory closing lines are fetched.
SAFETY_MARGIN = 120


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Pure decision logic ────────────────────────────────────────────────────

def due_offsets_for(minutes_to_kickoff: float, have: set[str]) -> list[str]:
    """Offset names whose window contains `minutes_to_kickoff` and aren't filled."""
    due = []
    for name, low, high, _markets, _mand in OFFSETS:
        if low <= minutes_to_kickoff <= high and name not in have:
            due.append(name)
    return due


def plan_capture(
    due_by_match: dict[str, set[str]],
    remaining: int | None,
) -> dict:
    """
    Decide whether to spend a call and with which markets.

    Returns {"fetch": bool, "markets": str, "allowed": set[str], "reason": str}.
    `allowed` is the set of offset names that may be recorded from this call.
    """
    all_due = set().union(*due_by_match.values()) if due_by_match else set()
    if not all_due:
        return {"fetch": False, "markets": "", "allowed": set(), "reason": "no windows due"}

    need_closing = "closing" in all_due
    early_due = all_due - {"closing"}
    budget_low = remaining is not None and remaining < SAFETY_MARGIN

    allowed = set(all_due)
    reason = "all due windows"
    if budget_low:
        allowed = {"closing"} if need_closing else set()
        reason = f"budget low ({remaining}<{SAFETY_MARGIN}) — closing only"

    if not allowed:
        return {"fetch": False, "markets": "", "allowed": set(), "reason": reason}

    # Markets: totals only worthwhile for the mandatory closing line
    markets = "h2h,totals" if "closing" in allowed and need_closing else "h2h"

    # Degrade closing to h2h-only if we can't afford 2 credits
    if remaining is not None:
        cost = 2 if markets == "h2h,totals" else 1
        if remaining < cost:
            if remaining >= 1 and need_closing:
                markets = "h2h"
                reason += " · degraded to h2h (≤1 credit left)"
            else:
                return {"fetch": False, "markets": "", "allowed": set(),
                        "reason": f"insufficient credits ({remaining})"}

    return {"fetch": True, "markets": markets, "allowed": allowed, "reason": reason}


def _outcome(score_home: int, score_away: int) -> str:
    if score_home > score_away:
        return "home"
    if score_home < score_away:
        return "away"
    return "draw"


# ── I/O helpers ────────────────────────────────────────────────────────────

def _load_matches() -> list[dict]:
    if not DATA_JSON.exists():
        logger.warning("%s missing — run build_data.py first", DATA_JSON)
        return []
    try:
        return json.loads(DATA_JSON.read_text(encoding="utf-8")).get("matches", [])
    except (json.JSONDecodeError, AttributeError):
        logger.error("Could not parse %s", DATA_JSON)
        return []


def _load_budget() -> int | None:
    if not BUDGET_PATH.exists():
        return None
    try:
        return json.loads(BUDGET_PATH.read_text(encoding="utf-8")).get("remaining")
    except (json.JSONDecodeError, AttributeError):
        return None


def _save_budget(remaining: int | None) -> None:
    if remaining is None:
        return
    BUDGET_PATH.parent.mkdir(parents=True, exist_ok=True)
    BUDGET_PATH.write_text(
        json.dumps({"remaining": remaining, "updated_at": _now_iso()}, indent=2),
        encoding="utf-8",
    )


def _index_payload_by_pair(payload: list[dict]) -> dict[tuple[str, str], dict]:
    """Map (home_code, away_code) → raw API match dict."""
    idx: dict[tuple[str, str], dict] = {}
    for m in payload:
        try:
            key = (resolve(m["home_team"]), resolve(m["away_team"]))
        except Exception:
            continue
        idx.setdefault(key, m)
    return idx


# ── Backfill results from the live results feed (no extra API call) ────────

def backfill_results(events: list[dict], matches: list[dict]) -> int:
    """
    Append result events for finished matches that have a forecast snapshot but
    no result yet. Results come from docs/results.json (written by live.yml).
    Returns the number of result events appended.
    """
    if not RESULTS_JSON.exists():
        return 0
    try:
        results = json.loads(RESULTS_JSON.read_text(encoding="utf-8")).get("results", [])
    except (json.JSONDecodeError, AttributeError):
        return 0

    by_pair = {(m["home_code"], m["away_code"]): m for m in matches}
    appended = 0
    for r in results:
        if not r.get("is_done") or r.get("score_home") is None:
            continue
        match = by_pair.get((r.get("home_code"), r.get("away_code")))
        if not match:
            continue
        mid = match["id"]
        # Only settle matches we actually forecast, and only once
        if ss.has_snapshot(events, mid, "result"):
            continue
        if not (ss.has_snapshot(events, mid, "odds") or ss.has_snapshot(events, mid, "uanalyse")):
            continue
        outcome = _outcome(r["score_home"], r["score_away"])
        ss.append_event({
            "type": "result", "match_id": mid,
            "home_code": match["home_code"], "away_code": match["away_code"],
            "score_home": r["score_home"], "score_away": r["score_away"],
            "outcome": outcome,
        })
        events.append({"type": "result", "match_id": mid, "outcome": outcome})
        appended += 1
    if appended:
        logger.info("Backfilled %d result(s) into snapshot store", appended)
    return appended


# ── Main capture run ───────────────────────────────────────────────────────

def run(mock: bool = False) -> dict:
    matches = _load_matches()
    events = ss.load_events()

    # Always try to settle finished matches first (free)
    backfill_results(events, matches)

    now = _now()
    due_by_match: dict[str, set[str]] = {}
    match_by_id = {m["id"]: m for m in matches}

    for m in matches:
        ct = m.get("commence_time", "")
        if "T" not in ct:
            continue  # no exact kickoff time yet
        try:
            kickoff = datetime.fromisoformat(ct.replace("Z", "+00:00"))
        except ValueError:
            continue
        minutes_to = (kickoff - now).total_seconds() / 60.0
        if minutes_to <= 0:
            continue  # already kicked off
        have = {e.get("offset") for e in events
                if e.get("match_id") == m["id"] and e.get("type") == "odds" and e.get("offset")}
        due = due_offsets_for(minutes_to, have)
        if due:
            due_by_match[m["id"]] = set(due)

    remaining = _load_budget()
    plan = plan_capture(due_by_match, remaining)
    logger.info(
        "Capture plan: %d match(es) due, remaining=%s → fetch=%s markets=%s (%s)",
        len(due_by_match), remaining, plan["fetch"], plan["markets"], plan["reason"],
    )

    summary = {
        "fetched": False, "markets": plan["markets"], "reason": plan["reason"],
        "odds_snapshots": 0, "uanalyse_snapshots": 0,
        "sharp": None, "book_keys": [], "remaining": remaining,
    }

    if not plan["fetch"]:
        return summary

    # ── Spend ONE call ──────────────────────────────────────────────────────
    if mock:
        payload = json.loads((Path(__file__).parent.parent / "data" / "mock_response.json").read_text())
        new_remaining = remaining
    else:
        payload, new_remaining = request_odds(markets=plan["markets"])
    keys = book_keys(payload)
    sharp = weighting.has_sharp_books(keys)
    summary.update(fetched=True, sharp=sharp, book_keys=keys, remaining=new_remaining)

    # SHARP-BOOK CHECK — explicit, visible log
    logger.info("SHARP-BOOK CHECK · books=%s", keys)
    if sharp:
        logger.info("SHARP-BOOK CHECK · sharp books present → market prior 57.5/42.5")
    else:
        logger.warning("SHARP-BOOK CHECK · NO sharp books (Pinnacle/Betfair) → prior set to parity 50/50")

    pair_idx = _index_payload_by_pair(payload)

    # uanalyse data is only fetched if we record at least one uanalyse snapshot
    ua_rows = None

    allowed = plan["allowed"]
    for mid, due in due_by_match.items():
        record_offsets = due & allowed
        if not record_offsets:
            continue
        match = match_by_id[mid]
        raw = pair_idx.get((match["home_code"], match["away_code"]))
        if raw is None:
            continue  # match not in this payload (e.g. not yet listed)
        result = process_match(raw)
        p = result["consensus"]
        for offset in record_offsets:
            ss.append_event({
                "type": "odds", "match_id": mid,
                "home_code": match["home_code"], "away_code": match["away_code"],
                "kickoff": match.get("commence_time", ""), "offset": offset,
                "markets": plan["markets"], "p": p,
                "totals_line": result["totals_line"],
                "totals_over_prob": result["totals_over_prob"],
                "books": keys, "n_books": len(result["bookmakers"]), "sharp": sharp,
            })
            summary["odds_snapshots"] += 1

        # Capture the kickoff-closest uanalyse snapshot alongside the closing line
        if "closing" in record_offsets and not ss.has_snapshot(events, mid, "uanalyse", "closing"):
            if ua_rows is None:
                try:
                    ua_rows = {(r["home_code"], r["away_code"]): r for r in fetch_uanalyse(mock=mock)}
                except Exception as exc:
                    logger.warning("uanalyse snapshot fetch failed: %s", exc)
                    ua_rows = {}
            ua = ua_rows.get((match["home_code"], match["away_code"]))
            if ua:
                ss.append_event({
                    "type": "uanalyse", "match_id": mid,
                    "home_code": match["home_code"], "away_code": match["away_code"],
                    "kickoff": match.get("commence_time", ""), "offset": "closing",
                    "snapshot_date": ua.get("snapshot_date", ""),
                    "p": {"home": ua["p_home"], "draw": ua["p_draw"], "away": ua["p_away"]},
                    "lambda": {"home": ua["lambda_home"], "away": ua["lambda_away"]},
                })
                summary["uanalyse_snapshots"] += 1

    # Refresh odds_latest.json for build_data + frontend
    ODDS_LATEST.parent.mkdir(parents=True, exist_ok=True)
    ODDS_LATEST.write_text(json.dumps({
        "captured_at": _now_iso(),
        "markets": plan["markets"],
        "sharp": sharp,
        "book_keys": keys,
        "matches": payload,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    _save_budget(new_remaining)

    logger.info(
        "Capture done: %d odds + %d uanalyse snapshots, remaining=%s",
        summary["odds_snapshots"], summary["uanalyse_snapshots"], new_remaining,
    )
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Kickoff-anchored odds capture")
    parser.add_argument("--mock", action="store_true", help="Use local mock odds payload")
    args = parser.parse_args()
    out = run(mock=args.mock)
    print(json.dumps(out, indent=2, ensure_ascii=False))
