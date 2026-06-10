"""
Orchestrator: merges uanalyse (primary) + Odds API (secondary) → docs/data.json.
uanalyse provides lambda / 1X2; odds provides per-bookmaker lines and consensus.
Scores are computed exclusively from uanalyse lambda where available.
"""

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

import config
from src.fetch_odds import fetch_odds
from src.fetch_uanalyse import fetch_uanalyse
from src.probabilities import process_match
from src.scoreline import ev_optimize, poisson_matrix, derive_xg

logger = logging.getLogger(__name__)

OUTPUT_PATH = Path(__file__).parent.parent / "docs" / "data.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _canonicalize(name: str) -> str:
    return config.TEAM_ALIASES.get(name.strip(), name.strip())


def _tendency(p_home: float, p_draw: float, p_away: float) -> str:
    m = max(p_home, p_draw, p_away)
    if m == p_home:
        return "home"
    if m == p_draw:
        return "draw"
    return "away"


def _bookmaker_entries(consensus_result: dict) -> list[dict]:
    return [
        {
            "key":          b["key"],
            "title":        b["title"],
            "last_update":  b["last_update"],
            "weight":       b["weight"],
            "raw_odds":     b["raw_odds"],
            "overround":    b["overround"],
            "probabilities": b["probabilities"],
        }
        for b in consensus_result["bookmakers"]
    ]


def _tip_entry(tip: dict, modal: dict, based_on: str) -> tuple[dict, dict]:
    return (
        {
            "home":            tip["home"],
            "away":            tip["away"],
            "expected_points": tip["expected_points"],
            "based_on":        based_on,
        },
        {
            "home":        modal["home"],
            "away":        modal["away"],
            "probability": modal["probability"],
        },
    )


# ---------------------------------------------------------------------------
# Core builder
# ---------------------------------------------------------------------------

def build(mock: bool = False) -> dict:
    # ── 1. Load uanalyse (primary) ─────────────────────────────────────────
    ua_rows = fetch_uanalyse(mock=mock)
    ua_by_key: dict[tuple, dict] = {}
    for m in ua_rows:
        key = (m["home"], m["away"], m["kickoff_date"])
        ua_by_key[key] = m

    # ── 2. Load odds (secondary) ───────────────────────────────────────────
    odds_raw = fetch_odds(mock=mock)
    odds_by_key: dict[tuple, tuple] = {}
    for match in odds_raw:
        home = _canonicalize(match["home_team"])
        away = _canonicalize(match["away_team"])
        date = match["commence_time"][:10]
        key  = (home, away, date)
        if key in odds_by_key:
            logger.warning("Duplicate odds match key %s — keeping first", key)
            continue
        result = process_match(match)
        odds_by_key[key] = (match, result)

    # ── 3. Build output ────────────────────────────────────────────────────
    matches_out: list[dict] = []
    used_odds: set[tuple] = set()

    # Primary loop: all uanalyse matches
    for ua in ua_rows:
        key = (ua["home"], ua["away"], ua["kickoff_date"])
        odds_entry = odds_by_key.get(key)

        lh = ua["lambda_home"]
        la = ua["lambda_away"]
        matrix = poisson_matrix(lh, la)
        tip, modal = ev_optimize(matrix)
        rec_tip, modal_out = _tip_entry(tip, modal, "uanalyse")

        sources: dict = {
            "uanalyse": {
                "lambda": {"home": lh, "away": la},
                "p": {
                    "home":  ua["p_home"],
                    "draw":  ua["p_draw"],
                    "away":  ua["p_away"],
                },
            }
        }

        agreement: dict = {"same_tendency": None, "note": "no odds data"}
        bookmakers: list = []
        commence_time: str = ua["kickoff_date"]
        divergence: dict = {"home": 0.0, "draw": 0.0, "away": 0.0}
        totals_line = None
        totals_over_prob = None

        if odds_entry:
            used_odds.add(key)
            match_raw, odds_result = odds_entry
            commence_time    = match_raw["commence_time"]
            bookmakers       = _bookmaker_entries(odds_result)
            divergence       = odds_result["divergence"]
            totals_line      = odds_result["totals_line"]
            totals_over_prob = odds_result["totals_over_prob"]

            sources["odds_consensus"] = {"p": odds_result["consensus"]}

            ua_tend   = _tendency(ua["p_home"], ua["p_draw"], ua["p_away"])
            odds_tend = _tendency(
                odds_result["consensus"]["home"],
                odds_result["consensus"]["draw"],
                odds_result["consensus"]["away"],
            )
            same = ua_tend == odds_tend
            note = "" if same else f"uanalyse: {ua_tend}, odds: {odds_tend}"
            agreement = {"same_tendency": same, "note": note}

        matches_out.append({
            "id":           f"ua_{ua['home']}_{ua['away']}_{ua['kickoff_date']}",
            "commence_time": commence_time,
            "home_team":    ua["home"],
            "away_team":    ua["away"],
            "stage":        ua.get("stage", ""),
            "sources":      sources,
            "agreement":    agreement,
            "bookmakers":   bookmakers,
            "divergence":   divergence,
            "totals_line":  totals_line,
            "totals_over_prob": totals_over_prob,
            "expected_goals":   {"home": lh, "away": la},
            "recommended_tip":  rec_tip,
            "modal_scoreline":  modal_out,
        })

    # Secondary loop: odds-only matches (no uanalyse entry)
    for key, (match_raw, odds_result) in odds_by_key.items():
        if key in used_odds:
            continue
        home, away, date = key
        logger.warning(
            "Odds-only match (no uanalyse data): %s vs %s on %s — "
            "using odds-derived lambda for tip", home, away, date
        )

        xg = derive_xg(
            odds_result["consensus"],
            odds_result["totals_line"],
            odds_result["totals_over_prob"],
        )
        matrix  = poisson_matrix(xg["home"], xg["away"])
        tip, modal = ev_optimize(matrix)
        rec_tip, modal_out = _tip_entry(tip, modal, "odds_derived")

        matches_out.append({
            "id":           match_raw["id"],
            "commence_time": match_raw["commence_time"],
            "home_team":    home,
            "away_team":    away,
            "stage":        "",
            "sources":      {"odds_consensus": {"p": odds_result["consensus"]}},
            "agreement":    {"same_tendency": None, "note": "no uanalyse data"},
            "bookmakers":   _bookmaker_entries(odds_result),
            "divergence":   odds_result["divergence"],
            "totals_line":  odds_result["totals_line"],
            "totals_over_prob": odds_result["totals_over_prob"],
            "expected_goals":   {"home": xg["home"], "away": xg["away"]},
            "recommended_tip":  rec_tip,
            "modal_scoreline":  modal_out,
        })

    # Sort chronologically
    matches_out.sort(key=lambda m: m["commence_time"])

    output = {
        "metadata": {
            "generated_at":        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source_primary":      "uanalyse/world-cup-2026-predictions (CC BY 4.0)",
            "source_secondary":    "the-odds-api",
            "mock":                mock,
            "sport":               config.SPORT_KEY,
            "normalization_method": "multiplicative",
            "weights":             config.BOOKMAKER_WEIGHTS,
            "kicktipp_rules":      config.KICKTIPP_POINTS,
            "match_count":         len(matches_out),
            "uanalyse_count":      len(ua_rows),
            "odds_count":          len(odds_raw),
        },
        "matches": matches_out,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    logger.info(
        "Wrote %s (%d matches: %d uanalyse, %d odds-only)",
        OUTPUT_PATH, len(matches_out),
        len(ua_rows),
        len(matches_out) - len(ua_rows),
    )
    return output


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Build docs/data.json")
    parser.add_argument("--mock", action="store_true", help="Use local mock data")
    args = parser.parse_args()
    result = build(mock=args.mock)
    print(json.dumps(result, indent=2, ensure_ascii=False))
