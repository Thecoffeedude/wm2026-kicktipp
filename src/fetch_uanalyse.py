"""
Fetches match_predictions.csv from uanalyse/world-cup-2026-predictions (CC BY 4.0).
No API key required. Use --mock to load data/mock_uanalyse.csv instead.
"""

import csv
import io
import logging
from pathlib import Path

import requests

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
import config

logger = logging.getLogger(__name__)

MOCK_PATH = Path(__file__).parent.parent / "data" / "mock_uanalyse.csv"


def _canonicalize(name: str) -> str:
    """Map a team name variant to its canonical (uanalyse) spelling."""
    return config.TEAM_ALIASES.get(name.strip(), name.strip())


def _parse_row(row: dict) -> dict | None:
    """
    Parse one CSV row into a normalised match dict.
    Returns None and logs a warning if any required field is missing or unparseable.
    """
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

    home = _canonicalize(row["home_team"])
    away = _canonicalize(row["away_team"])

    if home != row["home_team"].strip():
        logger.debug("Alias applied: %r → %r", row["home_team"], home)
    if away != row["away_team"].strip():
        logger.debug("Alias applied: %r → %r", row["away_team"], away)

    return {
        "home":         home,
        "away":         away,
        "kickoff_date": row["kickoff_date"].strip(),  # "YYYY-MM-DD", no time
        "lambda_home":  round(lh, 4),
        "lambda_away":  round(la, 4),
        "p_home":       round(p_home, 4),
        "p_draw":       round(p_draw, 4),
        "p_away":       round(p_away, 4),
        "stage":        row.get("stage", "").strip(),
        "snapshot_date": row.get("snapshot_date", "").strip(),
    }


def fetch_uanalyse(mock: bool = False) -> list[dict]:
    """
    Return a list of normalised match dicts from the uanalyse CSV.
    Each dict: {home, away, kickoff_date, lambda_home, lambda_away, p_home, p_draw, p_away, stage}.
    """
    if mock:
        logger.info("Mock mode: loading %s", MOCK_PATH)
        text = MOCK_PATH.read_text(encoding="utf-8")
    else:
        logger.info("Fetching uanalyse predictions from GitHub…")
        try:
            resp = requests.get(config.UANALYSE_CSV_URL, timeout=15)
            resp.raise_for_status()
            text = resp.text
            logger.info("Fetched %d bytes from uanalyse", len(text))
        except requests.exceptions.RequestException as exc:
            logger.error("Failed to fetch uanalyse data: %s", exc)
            raise

    reader = csv.DictReader(io.StringIO(text))
    results = []
    for row in reader:
        parsed = _parse_row(row)
        if parsed is not None:
            results.append(parsed)

    logger.info("uanalyse: %d match(es) loaded", len(results))
    return results
