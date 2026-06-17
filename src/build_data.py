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
from src import calibration
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
WIDGET_PATH = Path(__file__).parent.parent / "docs" / "widget.json"
ODDS_LATEST_PATH = Path(__file__).parent.parent / "docs" / "odds_latest.json"

SITE_URL = "https://thecoffeedude.github.io/wm2026-kicktipp/"


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


def carry_forward_finished(matches: list[dict], output_path: Path,
                           now: datetime) -> int:
    """
    uanalyse drops matches from its feed once they are played, so finished games
    would vanish from the Verlauf timeline. Re-append previously-built matches
    whose kickoff is already in the past and that are no longer in the feed,
    preserving their stored prediction (recommended_tip, sources, modal). The
    frontend overlays the real score from results.json. Returns the count added.
    """
    if not output_path.exists():
        return 0
    try:
        prev = json.loads(output_path.read_text(encoding="utf-8")).get("matches", [])
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read previous data.json for carry-forward: %s", exc)
        return 0

    present = {m["id"] for m in matches}
    carried = 0
    for pm in prev:
        if pm.get("id") in present:
            continue
        ct = pm.get("commence_time", "")
        try:
            if "T" in ct:
                ko = datetime.fromisoformat(ct.replace("Z", "+00:00"))
            else:
                ko = datetime.strptime(ct[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if ko > now:
            continue  # only carry forward matches that have already kicked off
        pm["carried_forward"] = True
        tip = pm.get("recommended_tip")
        if tip and isinstance(tip.get("expected_points"), (int, float)):
            tip["expected_points"] = round(tip["expected_points"], 2)  # legacy 4-dp → 2-dp
        matches.append(pm)
        carried += 1
    return carried


def reconstruct_history(events: list[dict], present_ids: set,
                        weights: dict, kappa: float, rho: float,
                        gamma: float) -> list[dict]:
    """
    Rebuild finished matches that already dropped out of the uanalyse feed BEFORE
    carry-forward existed, from the append-only snapshot store. Produces the same
    frontend shape (sources / recommended_tip / modal / expected_goals) using the
    live blend+calibration path, so the Verlauf history and its ported prediction
    analysis are restored. Idempotent: skips matches already present.
    """
    by: dict[str, dict] = {}
    for e in events:
        mid = e.get("match_id")
        if not mid:
            continue
        slot = by.setdefault(mid, {})
        t = e.get("type")
        if t == "result":
            slot["result"] = e
        elif t in ("uanalyse", "odds"):
            key = "ua" if t == "uanalyse" else "odds"
            prev = slot.get(key)
            if prev is None or e.get("captured_at", "") >= prev.get("captured_at", ""):
                slot[key] = e

    out: list[dict] = []
    for mid, slot in by.items():
        if mid in present_ids or "result" not in slot:
            continue
        ua, od, res = slot.get("ua"), slot.get("odds"), slot["result"]
        if not ua and not od:
            continue  # no forecast to reconstruct

        hc = res.get("home_code") or (ua or od).get("home_code")
        ac = res.get("away_code") or (ua or od).get("away_code")
        commence = (ua or od).get("kickoff") or (mid.split("_")[-1])
        totals_line = od.get("totals_line") if od else None
        totals_over = od.get("totals_over_prob") if od else None

        sources: dict = {}
        if ua:
            sources["uanalyse"] = {"lambda": ua["lambda"], "p": ua["p"]}
        if od:
            sources["odds_consensus"] = {"p": od["p"]}

        if ua and od:
            lam_hint = ua["lambda"]["home"] + ua["lambda"]["away"]
            blended, blam = blend_match(
                ua["p"], od["p"], lam_hint, totals_line, totals_over, weights
            )
            bh, ba = calibration.apply_kappa(blam["home"], blam["away"], kappa)
            tip, modal = ev_optimize(poisson_matrix(bh, ba, rho=rho), variance_aggression=gamma)
            rec, modal_out = _tip_entry(tip, modal, "blend")
            lh_out, la_out = bh, ba
            sources["blend"] = {
                "p": {k: round(v, 4) for k, v in blended.items()},
                "lambda": {"home": bh, "away": ba}, "lambda_raw": blam,
                "weights": {k: round(v, 4) for k, v in weights.items()},
                "regime": "reconstructed",
            }
        elif ua:
            lam = weighting.calibrate_lambda(
                ua["p"], None, None,
                lambda_total_hint=ua["lambda"]["home"] + ua["lambda"]["away"])
            lh_out, la_out = calibration.apply_kappa(lam["home"], lam["away"], kappa)
            tip, modal = ev_optimize(poisson_matrix(lh_out, la_out, rho=rho), variance_aggression=gamma)
            rec, modal_out = _tip_entry(tip, modal, "uanalyse")
        else:
            xg = derive_xg(od["p"], totals_line, totals_over)
            lh_out, la_out = calibration.apply_kappa(xg["home"], xg["away"], kappa)
            tip, modal = ev_optimize(poisson_matrix(lh_out, la_out, rho=rho), variance_aggression=gamma)
            rec, modal_out = _tip_entry(tip, modal, "odds_derived")

        out.append({
            "id": mid,
            "commence_time": commence,
            "home_team": canonical_en(hc),
            "away_team": canonical_en(ac),
            "home_code": hc,
            "away_code": ac,
            "stage": res.get("stage", ""),
            "sources": sources,
            "agreement": {"same_tendency": None, "note": "reconstructed from snapshots"},
            "bookmakers": [],
            "divergence": {"home": 0.0, "draw": 0.0, "away": 0.0},
            "totals_line": totals_line,
            "totals_over_prob": totals_over,
            "expected_goals": {"home": lh_out, "away": la_out},
            "recommended_tip": rec,
            "modal_scoreline": modal_out,
            "carried_forward": True,
        })
    return out


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


def _parse_ko(ct: str) -> datetime | None:
    """Parse a commence_time (ISO or date-only) into an aware datetime."""
    try:
        if "T" in ct:
            return datetime.fromisoformat(ct.replace("Z", "+00:00"))
        return datetime.strptime(ct[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _favorite(match: dict) -> dict | None:
    """Most-likely outcome label + percent from the best available 1X2."""
    src = match.get("sources", {})
    p = (src.get("blend") or {}).get("p") or (src.get("uanalyse") or {}).get("p") \
        or (src.get("odds_consensus") or {}).get("p")
    if not p:
        return None
    if p["home"] >= p["draw"] and p["home"] >= p["away"]:
        return {"label": match["home_team"], "pct": round(p["home"] * 100)}
    if p["away"] > p["home"] and p["away"] >= p["draw"]:
        return {"label": match["away_team"], "pct": round(p["away"] * 100)}
    return {"label": "Unentschieden", "pct": round(p["draw"] * 100)}


def build_widget_payload(matches: list[dict], now: datetime, n_next: int = 3) -> dict:
    """
    Compact payload for an iOS widget (Scriptable etc.). Carries the recommended
    tips (so a widget can compute the points balance against the 5-min-fresh
    results.json itself) plus the next few upcoming fixtures for display. Live
    scores and the live points total are derived client-side from live.json /
    results.json, so this file only needs the daily predict refresh.
    """
    tips: dict[str, list[int]] = {}
    for m in matches:
        tip = m.get("recommended_tip")
        if tip:
            tips[f"{m['home_code']}:{m['away_code']}"] = [tip["home"], tip["away"]]

    upcoming = sorted(
        ((ko, m) for m in matches if (ko := _parse_ko(m["commence_time"])) and ko > now),
        key=lambda x: x[0],
    )
    nxt = []
    for ko, m in upcoming[:n_next]:
        tip = m.get("recommended_tip") or {}
        nxt.append({
            "home": m["home_team"], "away": m["away_team"],
            "hc": m["home_code"], "ac": m["away_code"],
            "kickoff": m["commence_time"],
            "stage": m.get("stage", ""),
            "tip": [tip.get("home"), tip.get("away")] if tip else None,
            "fav": _favorite(m),
        })

    return {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "site": SITE_URL,
        "tips": tips,
        "next": nxt,
    }


def resolve_weighting(events: list[dict]) -> tuple[dict, int]:
    """
    Compute rolling forecast skill per source from the snapshot store events.
    Returns (rolling_performance, n_settled). The sharp flag + prior/performance
    selection happen at the call site via weighting.effective_weights.
    """
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

    # Snapshot store drives both source weighting and goal-scaling calibration.
    store_events = ss.load_events()

    # Resolve dynamic source weights (prior now; performance once results flow)
    perf, n_settled = resolve_weighting(store_events)
    blend_weights, regime = weighting.effective_weights(sharp_books, perf, n_settled)
    logger.info(
        "Weighting: regime=%s sharp=%s weights=%s (settled=%d)",
        regime, sharp_books, blend_weights, n_settled,
    )

    # Resolve scoreline calibration (κ goal-scaling, ρ Dixon-Coles, γ variance)
    kappa, kappa_meta = calibration.resolve_kappa(store_events)
    rho = calibration.rho_value()
    gamma = calibration.variance_value()
    logger.info("Calibration: κ=%.3f ρ=%.3f γ=%.2f", kappa, rho, gamma)

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

        # Default: uanalyse-only tip (used as-is when no odds available).
        # Calibrate λ to uanalyse's OWN 1X2 first: the raw uanalyse λ can
        # contradict its 1X2 (e.g. USA–PAR: 1X2 home-favoured but λ away-heavy),
        # which would flip the tip to the wrong side. Then apply goal-scaling κ.
        ua_lam = weighting.calibrate_lambda(ua_p, None, None, lambda_total_hint=lh + la)
        lh_s, la_s = calibration.apply_kappa(ua_lam["home"], ua_lam["away"], kappa)
        matrix = poisson_matrix(lh_s, la_s, rho=rho)
        tip, modal = ev_optimize(matrix, variance_aggression=gamma)
        rec_tip, modal_out = _tip_entry(tip, modal, "uanalyse")
        lh_out, la_out = lh_s, la_s

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
                bh, ba = calibration.apply_kappa(blam["home"], blam["away"], kappa)
                b_matrix = poisson_matrix(bh, ba, rho=rho)
                b_tip, b_modal = ev_optimize(b_matrix, variance_aggression=gamma)
                rec_tip, modal_out = _tip_entry(b_tip, b_modal, "blend")
                lh_out, la_out = bh, ba
                sources["blend"] = {
                    "p": {k: round(v, 4) for k, v in blended.items()},
                    "lambda": {"home": bh, "away": ba},
                    "lambda_raw": blam,
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
        xh, xa = calibration.apply_kappa(xg["home"], xg["away"], kappa)
        matrix  = poisson_matrix(xh, xa, rho=rho)
        tip, modal = ev_optimize(matrix, variance_aggression=gamma)
        rec_tip, modal_out = _tip_entry(tip, modal, "odds_derived")
        xg = {"home": xh, "away": xa}

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

    # Keep already-played matches in the timeline even after uanalyse drops them
    n_cf = carry_forward_finished(matches_out, OUTPUT_PATH, datetime.now(timezone.utc))
    if n_cf:
        logger.info("Carried forward %d finished matches no longer in the feed", n_cf)

    # Recover finished matches that dropped out before carry-forward existed
    present_ids = {m["id"] for m in matches_out}
    recovered = reconstruct_history(store_events, present_ids, blend_weights, kappa, rho, gamma)
    if recovered:
        matches_out.extend(recovered)
        logger.info("Reconstructed %d finished matches from the snapshot store", len(recovered))

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

    # ── 6a. Compact widget payload (tips + next fixtures for an iOS widget) ─
    widget = build_widget_payload(matches_out, datetime.now(timezone.utc))
    WIDGET_PATH.write_text(
        json.dumps(widget, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info("widget.json: %d tips, %d next", len(widget["tips"]), len(widget["next"]))

    # ── 6b. Team artwork (TheSportsDB badges, cached forever) ─────────────
    team_assets: dict[str, dict] = {}
    artwork_path = Path(__file__).parent.parent / "data" / "team_artwork.json"
    if artwork_path.exists():
        try:
            raw_art = json.loads(artwork_path.read_text(encoding="utf-8"))
            team_assets = {
                code: {"badge": a.get("badge", ""), "badge_small": a.get("badge_small", "")}
                for code, a in raw_art.items() if a.get("badge")
            }
        except json.JSONDecodeError:
            logger.warning("team_artwork.json unlesbar — ohne Wappen weiter")

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
            "calibration": {
                "kappa":               kappa_meta,
                "dixon_coles_rho":     rho,
                "variance_aggression": gamma,
            },
        },
        "matches":     matches_out,
        "tournament":  tournament_match,
        "tournament_probabilities": tournament_probs,
        "team_assets": team_assets,
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
