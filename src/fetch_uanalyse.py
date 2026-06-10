"""
Fetches match_predictions.csv and tournament_probabilities.csv from
uanalyse/world-cup-2026-predictions (CC BY 4.0).
No API key required. Use mock=True to load local data/ files instead.
"""

from __future__ import annotations

import csv
import io
import logging
from pathlib import Path

import requests

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from src.teams import resolve, canonical_en

logger = logging.getLogger(__name__)

MOCK_PATH        = Path(__file__).parent.parent / "data" / "mock_uanalyse.csv"
MOCK_TOURN_PATH  = Path(__file__).parent.parent / "data" / "mock_tournament.csv"


# ── Match predictions ──────────────────────────────────────────────────────

def _parse_row(row: dict) -> dict | None:
    required = ("kickoff_date", "home_team", "away_team",
                "prob_home_win", "prob_draw", "prob_away_win",
                "exp_home_goals", "exp_away_goals")
    for field in required:
        if not row.get(field):
            logger.warning("Skipping row — missing field %r: %s", field, row)
            return None

    try:
        p_home = float(row["prob_home_win"])
        p_draw = float(row["prob_draw"])
        p_away = float(row["prob_away_win"])
        lh     = float(row["exp_home_goals"])
        la     = float(row["exp_away_goals"])
    except ValueError as exc:
        logger.warning("Skipping row — non-numeric value: %s (%s)", row, exc)
        return None

    home_code = resolve(row["home_team"].strip())
    away_code = resolve(row["away_team"].strip())
    home = canonical_en(home_code)
    away = canonical_en(away_code)

    return {
        "home":          home,
        "away":          away,
        "home_code":     home_code,
        "away_code":     away_code,
        "kickoff_date":  row["kickoff_date"].strip(),
        "lambda_home":   round(lh, 4),
        "lambda_away":   round(la, 4),
        "p_home":        round(p_home, 4),
        "p_draw":        round(p_draw, 4),
        "p_away":        round(p_away, 4),
        "stage":         row.get("stage", "").strip(),
        "snapshot_date": row.get("snapshot_date", "").strip(),
    }


def fetch_uanalyse(mock: bool = False) -> list[dict]:
    """
    Return normalised match dicts from uanalyse match_predictions.csv.
    Each dict: {home, away, home_code, away_code, kickoff_date,
                lambda_home, lambda_away, p_home, p_draw, p_away, stage}.
    """
    if mock:
        logger.info("Mock mode: loading %s", MOCK_PATH)
        text = MOCK_PATH.read_text(encoding="utf-8")
    else:
        logger.info("Fetching uanalyse match predictions from GitHub…")
        try:
            resp = requests.get(config.UANALYSE_CSV_URL, timeout=15)
            resp.raise_for_status()
            text = resp.text
            logger.info("Fetched %d bytes from uanalyse (matches)", len(text))
        except requests.exceptions.RequestException as exc:
            logger.error("Failed to fetch uanalyse match data: %s", exc)
            raise

    reader = csv.DictReader(io.StringIO(text))
    results = []
    for row in reader:
        parsed = _parse_row(row)
        if parsed is not None:
            results.append(parsed)

    logger.info("uanalyse: %d match(es) loaded", len(results))
    return results


# ── Tournament probabilities ───────────────────────────────────────────────

def _parse_tourn_row(row: dict) -> dict | None:
    required = ("team", "group", "prob_win_group", "prob_runner_up",
                "prob_reach_round_of_32", "prob_champion")
    for field in required:
        if not row.get(field):
            logger.debug("Tournament row skipped — missing %r: %s", field, row)
            return None

    raw_team = row["team"].strip()
    code = resolve(raw_team)

    try:
        return {
            "code":                    code,
            "team":                    canonical_en(code),
            "group":                   row["group"].strip(),
            "prob_win_group":          float(row["prob_win_group"]),
            "prob_runner_up":          float(row["prob_runner_up"]),
            "prob_reach_round_of_32":  float(row.get("prob_reach_round_of_32", 0) or 0),
            "prob_reach_quarterfinals": float(row.get("prob_reach_quarterfinals", 0) or 0),
            "prob_reach_semifinals":   float(row.get("prob_reach_semifinals", 0) or 0),
            "prob_reach_final":        float(row.get("prob_reach_final", 0) or 0),
            "prob_champion":           float(row["prob_champion"]),
        }
    except ValueError as exc:
        logger.warning("Tournament row skipped — non-numeric: %s (%s)", row, exc)
        return None


def fetch_tournament_probabilities(mock: bool = False) -> list[dict]:
    """
    Return per-team tournament probability rows from tournament_probabilities.csv.
    Each dict: {code, team, group, prob_win_group, prob_runner_up,
                prob_reach_*, prob_champion}.
    """
    if mock:
        if MOCK_TOURN_PATH.exists():
            logger.info("Mock mode: loading %s", MOCK_TOURN_PATH)
            text = MOCK_TOURN_PATH.read_text(encoding="utf-8")
        else:
            logger.warning("Mock tournament file not found (%s) — fetching live", MOCK_TOURN_PATH)
            mock = False

    if not mock:
        logger.info("Fetching uanalyse tournament probabilities from GitHub…")
        try:
            resp = requests.get(config.UANALYSE_TOURNAMENT_URL, timeout=15)
            resp.raise_for_status()
            text = resp.text
            logger.info("Fetched %d bytes from uanalyse (tournament)", len(text))
        except requests.exceptions.RequestException as exc:
            logger.error("Failed to fetch tournament probabilities: %s", exc)
            raise

    reader = csv.DictReader(io.StringIO(text))
    results = []
    for row in reader:
        parsed = _parse_tourn_row(row)
        if parsed is not None:
            results.append(parsed)

    logger.info("uanalyse: %d tournament row(s) loaded", len(results))
    return results
