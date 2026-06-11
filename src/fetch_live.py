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
        "fd_id":          m.get("id"),       # football-data match id (detail endpoint)
        "fd_home_id":     (m.get("homeTeam") or {}).get("id"),
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


def fetch_match_details(fd_id: int) -> dict | None:
    """
    GET /v4/matches/{id} — goal scorers, current minute, and (paid tiers only)
    match statistics. Returns a partial dict to merge into a live entry, or
    None when the key is missing / the request fails. Free tier: goals+minute
    are included for most competitions; statistics usually are not — every
    field is therefore optional.
    """
    api_key = os.getenv("FOOTBALL_DATA_API_KEY", "").strip()
    if not api_key or not fd_id:
        return None
    try:
        resp = requests.get(
            f"{FDORG_BASE}/matches/{fd_id}",
            headers={"X-Auth-Token": api_key},
            timeout=15,
        )
        resp.raise_for_status()
        raw = resp.json()
    except Exception as exc:
        logger.warning("match detail %s failed: %s", fd_id, exc)
        return None

    m = raw.get("match", raw)  # v4 returns the object directly; be lenient
    home_id = (m.get("homeTeam") or {}).get("id")

    goals = []
    for g in m.get("goals") or []:
        team_id = (g.get("team") or {}).get("id")
        goals.append({
            "minute":      g.get("minute"),
            "injury_time": g.get("injuryTime"),
            "scorer":      ((g.get("scorer") or {}).get("name")) or "?",
            "type":        g.get("type", "REGULAR"),
            "side":        "home" if team_id == home_id else "away",
        })

    out: dict = {"goals": goals}
    if m.get("minute") is not None:
        out["minute"] = m.get("minute")
    if m.get("injuryTime") is not None:
        out["injury_time"] = m.get("injuryTime")

    # Statistics (ball possession, shots, …) — only present on paid tiers;
    # nested under homeTeam/awayTeam in the v4 match detail.
    stats_home = (m.get("homeTeam") or {}).get("statistics") or {}
    stats_away = (m.get("awayTeam") or {}).get("statistics") or {}
    if stats_home:
        out["stats_home"] = stats_home
    if stats_away:
        out["stats_away"] = stats_away
    return out


def enrich_live_details(entries: list[dict]) -> int:
    """
    Merge detail data (goals, minute, statistics) into live/halftime/finished
    entries in place. Returns the number of enriched entries. One request per
    eligible match — fine for the free-tier limit (10 req/min) since at most a
    handful of WC games run simultaneously.
    """
    enriched = 0
    for e in entries:
        if not (e.get("is_live") or e.get("is_halftime") or e.get("is_done")):
            continue
        detail = fetch_match_details(e.get("fd_id"))
        if detail:
            e.update(detail)
            enriched += 1
    return enriched


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
