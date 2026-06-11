"""
Orchestrator: merges uanalyse (primary) + Odds API (secondary) → docs/data.json.
uanalyse provides lambda / 1X2; odds provides per-bookmaker lines and consensus.
Scores are computed exclusively from uanalyse lambda where available.
Match deduplication uses FIFA team codes so name-variant duplicates are impossible.
"""

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
from src.fetch_live import fetch_live_scores, fetch_schedule
from src.fetch_odds import fetch_odds
from src.live_update import update_results, write_live
from src.fetch_uanalyse import fetch_uanalyse, fetch_tournament_probabilities
from src.probabilities import process_match
from src.scoreline import ev_optimize, poisson_matrix, derive_xg
from src.teams import resolve, canonical_en
from src.tournament import build_tournament_predictions

logger = logging.getLogger(__name__)

OUTPUT_PATH = Path(__file__).parent.parent / "docs" / "data.json"
ODDS_LATEST_PATH = Path(__file__).parent.parent / "docs" / "odds_latest.json"


def load_odds_latest(mock: bool) -> tuple[list[dict], bool, list[str]]:
    """
    Source the market odds payload. The capture workflow owns all Odds API calls
    and writes docs/odds_latest.json; build_data only reads it (no credit spend).
    Returns (raw_match_dicts, sharp_books_present, book_keys).
    """
    if mock:
        try:
            payload = fetch_odds(mock=True)
        except Exception as exc:
            logger.warning("Mock odds unavailable (%s)", exc)
            payload = []
        keys = sorted({b["key"] for m in payload for b in m.get("bookmakers", []) if b.get("key")})
        return payload, weighting.has_sharp_books(keys), keys

    if not ODDS_LATEST_PATH.exists():
        logger.info("odds_latest.json not present — building without market odds "
                    "(capture workflow has not run yet)")
        return [], False, []
    try:
        doc = json.loads(ODDS_LATEST_PATH.read_text(encoding="utf-8"))
        return doc.get("matches", []), bool(doc.get("sharp")), doc.get("book_keys", [])
    except (json.JSONDecodeError, AttributeError) as exc:
        logger.warning("Could not parse odds_latest.json (%s)", exc)
        return [], False, []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_team(name: str) -> tuple[str, str]:
    """Return (code, canonical_en) for a raw team name string."""
    code = resolve(name)
    return code, canonical_en(code)


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
            "key":           b["key"],
            "title":         b["title"],
            "last_update":   b["last_update"],
            "weight":        b["weight"],
            "raw_odds":      b["raw_odds"],
            "overround":     b["overround"],
            "probabilities": b["probabilities"],
        }
        for b in consensus_result["bookmakers"]
    ]


def enrich_kickoff_times(matches: list[dict], schedule: list[dict]) -> int:
    """
    Replace date-only commence_time values with exact UTC kickoff times from
    the football-data.org schedule. Matches by (home_code, away_code) with a
    ±1 day tolerance on the date (timezone shifts around UTC midnight).
    Mutates matches in place; returns the number of enriched entries.
    """
    by_pair: dict[tuple, list[dict]] = {}
    for s in schedule:
        if s.get("utc_date"):
            by_pair.setdefault((s["home_code"], s["away_code"]), []).append(s)

    enriched = 0
    for m in matches:
        if "T" in m["commence_time"]:
            continue  # already has an exact time (e.g. from the odds API)
        candidates = by_pair.get((m["home_code"], m["away_code"]), [])
        try:
            ua_date = datetime.strptime(m["commence_time"][:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        for s in candidates:
            s_date = datetime.strptime(s["utc_date"][:10], "%Y-%m-%d").date()
            if abs((s_date - ua_date).days) <= 1:
                m["commence_time"] = s["utc_date"]
                enriched += 1
                break
    return enriched


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


def resolve_weighting() -> tuple[dict, int]:
    """
    Load the snapshot store and compute rolling forecast skill per source.
    Returns (rolling_performance, n_settled). The sharp flag + prior/performance
    selection happen at the call site via weighting.effective_weights.
    """
    events = ss.load_events()
    settled = ss.settled_forecasts(events)
    perf = weighting.rolling_performance(settled)
    n_settled = ss.count_settled_matches(events)
    return perf, n_settled


def blend_match(
    ua_p: dict[str, float],
    market_p: dict[str, float],
    lambda_total_hint: float,
    totals_line: float | None,
    totals_over_prob: float | None,
    weights: dict[str, float],
) -> tuple[dict[str, float], dict[str, float]]:
    """Logit-pool the two 1X2 distributions, then calibrate λ to the blend."""
    blended = weighting.logit_pool(
        [ua_p, market_p], [weights["uanalyse"], weights["market"]]
    )
    lam = weighting.calibrate_lambda(
        blended, totals_line, totals_over_prob, lambda_total_hint=lambda_total_hint
    )
    return blended, lam


# ---------------------------------------------------------------------------
# Core builder
# ---------------------------------------------------------------------------

def build(mock: bool = False) -> dict:
    # ── 1. Load uanalyse (primary) ─────────────────────────────────────────
    ua_rows = fetch_uanalyse(mock=mock)
    # Keyed by (home_code, away_code, kickoff_date) for code-based dedup
    ua_by_key: dict[tuple, dict] = {}
    for m in ua_rows:
        key = (m["home_code"], m["away_code"], m["kickoff_date"])
        if key in ua_by_key:
            logger.warning("Duplicate uanalyse key %s — keeping first", key)
            continue
        ua_by_key[key] = m

    # ── 2. Load odds from the capture store (no Odds API call here) ────────
    odds_raw, sharp_books, odds_book_keys = load_odds_latest(mock=mock)

    # Resolve dynamic source weights (prior now; performance once results flow)
    perf, n_settled = resolve_weighting()
    blend_weights, regime = weighting.effective_weights(sharp_books, perf, n_settled)
    logger.info(
        "Weighting: regime=%s sharp=%s weights=%s (settled=%d)",
        regime, sharp_books, blend_weights, n_settled,
    )

    odds_by_key: dict[tuple, tuple] = {}
    for match in odds_raw:
        home_code, home = _resolve_team(match["home_team"])
        away_code, away = _resolve_team(match["away_team"])
        date = match["commence_time"][:10]
        key  = (home_code, away_code, date)
        if key in odds_by_key:
            logger.warning("Duplicate odds key %s — keeping first", key)
            continue
        result = process_match(match)
        odds_by_key[key] = (match, result, home, away)

    # ── 3. Build output ────────────────────────────────────────────────────
    matches_out: list[dict] = []
    used_odds: set[tuple] = set()

    for ua in ua_rows:
        key = (ua["home_code"], ua["away_code"], ua["kickoff_date"])
        odds_entry = odds_by_key.get(key)

        lh = ua["lambda_home"]
        la = ua["lambda_away"]
        ua_p = {"home": ua["p_home"], "draw": ua["p_draw"], "away": ua["p_away"]}

        # Default: uanalyse-only tip (used as-is when no odds available)
        matrix = poisson_matrix(lh, la)
        tip, modal = ev_optimize(matrix)
        rec_tip, modal_out = _tip_entry(tip, modal, "uanalyse")
        lh_out, la_out = lh, la

        sources: dict = {
            "uanalyse": {
                "lambda": {"home": lh, "away": la},
                "p": ua_p,
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
            match_raw, odds_result, _, _ = odds_entry
            commence_time    = match_raw["commence_time"]
            bookmakers       = _bookmaker_entries(odds_result)
            divergence       = odds_result["divergence"]
            totals_line      = odds_result["totals_line"]
            totals_over_prob = odds_result["totals_over_prob"]
            market_p         = odds_result["consensus"]

            sources["odds_consensus"] = {"p": market_p}

            ua_tend   = _tendency(ua["p_home"], ua["p_draw"], ua["p_away"])
            odds_tend = _tendency(market_p["home"], market_p["draw"], market_p["away"])
            same = ua_tend == odds_tend
            note = "" if same else f"uanalyse: {ua_tend}, odds: {odds_tend}"
            agreement = {"same_tendency": same, "note": note}

            # ── Blend market + uanalyse → calibrated λ → EV-optimal tip ──────
            if config.ENABLE_BLEND:
                blended, blam = blend_match(
                    ua_p, market_p, lh + la, totals_line, totals_over_prob, blend_weights
                )
                b_matrix = poisson_matrix(blam["home"], blam["away"])
                b_tip, b_modal = ev_optimize(b_matrix)
                rec_tip, modal_out = _tip_entry(b_tip, b_modal, "blend")
                lh_out, la_out = blam["home"], blam["away"]
                sources["blend"] = {
                    "p": {k: round(v, 4) for k, v in blended.items()},
                    "lambda": blam,
                    "weights": {k: round(v, 4) for k, v in blend_weights.items()},
                    "regime": regime,
                }

        matches_out.append({
            "id":               f"ua_{ua['home_code']}_{ua['away_code']}_{ua['kickoff_date']}",
            "commence_time":    commence_time,
            "home_team":        ua["home"],
            "away_team":        ua["away"],
            "home_code":        ua["home_code"],
            "away_code":        ua["away_code"],
            "stage":            ua.get("stage", ""),
            "sources":          sources,
            "agreement":        agreement,
            "bookmakers":       bookmakers,
            "divergence":       divergence,
            "totals_line":      totals_line,
            "totals_over_prob": totals_over_prob,
            "expected_goals":   {"home": lh_out, "away": la_out},
            "recommended_tip":  rec_tip,
            "modal_scoreline":  modal_out,
        })

    # Odds-only matches (no uanalyse entry)
    for key, (match_raw, odds_result, home, away) in odds_by_key.items():
        if key in used_odds:
            continue
        home_code, away_code, date = key
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
            "id":               match_raw["id"],
            "commence_time":    match_raw["commence_time"],
            "home_team":        home,
            "away_team":        away,
            "home_code":        home_code,
            "away_code":        away_code,
            "stage":            "",
            "sources":          {"odds_consensus": {"p": odds_result["consensus"]}},
            "agreement":        {"same_tendency": None, "note": "no uanalyse data"},
            "bookmakers":       _bookmaker_entries(odds_result),
            "divergence":       odds_result["divergence"],
            "totals_line":      odds_result["totals_line"],
            "totals_over_prob": odds_result["totals_over_prob"],
            "expected_goals":   {"home": xg["home"], "away": xg["away"]},
            "recommended_tip":  rec_tip,
            "modal_scoreline":  modal_out,
        })

    # ── 3b. Kickoff times + results from football-data.org schedule ───────
    try:
        schedule = fetch_schedule(mock=mock)
    except Exception as exc:
        logger.warning("Could not fetch schedule: %s", exc)
        schedule = []

    if schedule:
        n = enrich_kickoff_times(matches_out, schedule)
        logger.info("Enriched %d matches with exact kickoff times", n)

    matches_out.sort(key=lambda m: m["commence_time"])

    # ── 4. Tournament predictions (match-based) ────────────────────────────
    tournament_match = build_tournament_predictions(matches_out)

    # ── 5. Tournament probabilities (uanalyse CSV) ─────────────────────────
    try:
        tourn_rows = fetch_tournament_probabilities(mock=mock)
        tournament_probs: dict[str, dict] = {}
        for row in tourn_rows:
            tournament_probs[row["code"]] = row
    except Exception as exc:
        logger.warning("Could not fetch tournament probabilities: %s", exc)
        tournament_probs = {}

    # ── 6. Live scores (today) + cumulative results ───────────────────────
    # Derived from the schedule when available (saves an API call);
    # falls back to the dedicated today-only endpoint.
    today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if schedule:
        live_scores = [s for s in schedule if s.get("utc_date", "")[:10] == today_utc]
    else:
        try:
            live_scores = fetch_live_scores(mock=mock)
        except Exception as exc:
            logger.warning("Could not fetch live scores: %s", exc)
            live_scores = []

    write_live(live_scores)
    finished = [s for s in (schedule or live_scores) if s.get("is_done")]
    total_results = update_results(finished)
    logger.info("results.json: %d finished matches stored", total_results)

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    output = {
        "metadata": {
            "generated_at":         now_utc,
            "live_updated_at":      now_utc,
            "source_primary":       "uanalyse/world-cup-2026-predictions (CC BY 4.0)",
            "source_secondary":     "the-odds-api",
            "source_live":          "football-data.org (CC BY)",
            "mock":                 mock,
            "sport":                config.SPORT_KEY,
            "normalization_method": "multiplicative",
            "weights":              config.BOOKMAKER_WEIGHTS,
            "kicktipp_rules":       config.KICKTIPP_POINTS,
            "match_count":          len(matches_out),
            "uanalyse_count":       len(ua_rows),
            "odds_count":           len(odds_raw),
            "weighting": {
                "regime":              regime,
                "blend_enabled":       config.ENABLE_BLEND,
                "reweighting_enabled": weighting.ENABLE_PERFORMANCE_REWEIGHTING,
                "sharp_books":         sharp_books,
                "book_keys":           odds_book_keys,
                "weights":             {k: round(v, 4) for k, v in blend_weights.items()},
                "performance":         perf,
                "n_settled":           n_settled,
            },
        },
        "matches":     matches_out,
        "tournament":  tournament_match,
        "tournament_probabilities": tournament_probs,
        "live":        live_scores,
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
    print(json.dumps(result["metadata"], indent=2, ensure_ascii=False))
