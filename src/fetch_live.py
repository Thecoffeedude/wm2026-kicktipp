"""
football-data.org client (free tier, competition 2000 = FIFA World Cup).

Provides:
- fetch_live_scores(): today's matches with live/halftime/finished status
- fetch_schedule():    the full tournament schedule incl. kickoff times (utcDate)
                       and final scores of finished matches

Only makes requests when FOOTBALL_DATA_API_KEY env var is present.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import requests

from src.teams import canonical_en, resolve

logger = logging.getLogger(__name__)

FDORG_BASE = "https://api.football-data.org/v4"
WC_COMP_ID = "2000"   # FIFA World Cup (football-data.org internal ID)

# German status labels for the UI
STATUS_DE: dict[str, str] = {
    "SCHEDULED":         "Geplant",
    "TIMED":             "Bald",
    "IN_PLAY":           "Live",
    "PAUSED":            "Halbzeit",
    "EXTRA_TIME":        "Verlängerung",
    "PENALTY_SHOOTOUT":  "Elfmeter",
    "FINISHED":          "Beendet",
    "FINISHED_AET":      "n. V.",
    "FINISHED_PEN":      "n. E.",
    "POSTPONED":         "Verschoben",
    "SUSPENDED":         "Unterbrochen",
    "CANCELLED":         "Abgesagt",
}

LIVE_STATUSES = frozenset({"IN_PLAY", "EXTRA_TIME", "PENALTY_SHOOTOUT"})
HALF_STATUSES = frozenset({"PAUSED"})
DONE_STATUSES = frozenset({"FINISHED", "FINISHED_AET", "FINISHED_PEN"})


def _normalize_match(m: dict) -> dict | None:
    """Map one raw football-data.org match to our internal entry shape."""
    home_raw = (m.get("homeTeam") or {}).get("name", "")
    away_raw = (m.get("awayTeam") or {}).get("name", "")
    if not home_raw or not away_raw:
        return None

    home_code = resolve(home_raw)
    away_code = resolve(away_raw)
    score     = m.get("score") or {}
    ft        = score.get("fullTime") or {}
    ht        = score.get("halfTime") or {}
    status    = m.get("status", "SCHEDULED")

    return {
        "home_code":      home_code,
        "away_code":      away_code,
        "home_team":      canonical_en(home_code),
        "away_team":      canonical_en(away_code),
        "status":         status,
        "status_de":      STATUS_DE.get(status, status),
        "is_live":        status in LIVE_STATUSES,
        "is_halftime":    status in HALF_STATUSES,
        "is_done":        status in DONE_STATUSES,
        "minute":         m.get("minute"),
        "score_home":     ft.get("home"),   # None until first goal
        "score_away":     ft.get("away"),
        "halftime_home":  ht.get("home"),
        "halftime_away":  ht.get("away"),
        "utc_date":       m.get("utcDate", ""),
        "stage":          (m.get("stage") or "").replace("_", " ").title(),
    }


def _fetch_matches(params: dict | None = None) -> list[dict]:
    """
    GET /competitions/2000/matches with optional params, normalized.
    Returns [] when FOOTBALL_DATA_API_KEY is unset or the request fails.
    """
    api_key = os.getenv("FOOTBALL_DATA_API_KEY", "").strip()
    if not api_key:
        logger.debug("FOOTBALL_DATA_API_KEY not set — skipping football-data.org")
        return []

    url = f"{FDORG_BASE}/competitions/{WC_COMP_ID}/matches"
    try:
        resp = requests.get(
            url,
            headers={"X-Auth-Token": api_key},
            params=params or {},
            timeout=15,
        )
        resp.raise_for_status()
        raw_matches: list[dict] = resp.json().get("matches", [])
    except Exception as exc:
        logger.warning("football-data.org request failed: %s", exc)
        return []

    out = []
    for m in raw_matches:
        entry = _normalize_match(m)
        if entry is not None:
            out.append(entry)
    return out


def fetch_live_scores(mock: bool = False) -> list[dict]:
    """
    Return today's (UTC) WC matches with current scores/status.
    Empty list if FOOTBALL_DATA_API_KEY is not set.
    """
    if mock:
        return []

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = _fetch_matches({"dateFrom": today, "dateTo": today})

    live_n = sum(1 for e in out if e["is_live"])
    done_n = sum(1 for e in out if e["is_done"])
    logger.info(
        "fetch_live_scores: %d today (%d live, %d done)", len(out), live_n, done_n
    )
    return out


def fetch_schedule(mock: bool = False) -> list[dict]:
    """
    Return the full WC schedule (all matches of the competition), normalized.
    Includes exact kickoff times (utc_date) and scores of finished matches.
    Empty list if FOOTBALL_DATA_API_KEY is not set.
    """
    if mock:
        return []

    out = _fetch_matches()
    done_n = sum(1 for e in out if e["is_done"])
    logger.info("fetch_schedule: %d matches (%d finished)", len(out), done_n)
    return out
