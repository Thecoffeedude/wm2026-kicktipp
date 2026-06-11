"""
GATE 0 — API-Football season availability check (read-only diagnostic).

Free plans restrict which seasons return data. Before building any integration:
  1. /leagues?search=world cup  → find the World Cup league id
  2. /fixtures?league=<id>&season=2026 → do real 2026 fixtures come back?

Logs the verdict + remaining daily quota. Never logs the key.
"""

from __future__ import annotations

import json
import logging
import os
import sys

import requests

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BASE = "https://v3.football.api-sports.io"


def _get(path: str, key: str, params: dict | None = None) -> tuple[dict, dict]:
    resp = requests.get(
        f"{BASE}{path}",
        headers={"x-apisports-key": key},
        params=params or {},
        timeout=15,
    )
    resp.raise_for_status()
    headers = {
        "remaining_day": resp.headers.get("x-ratelimit-requests-remaining", "?"),
        "limit_day": resp.headers.get("x-ratelimit-requests-limit", "?"),
    }
    return resp.json(), headers


def main() -> int:
    key = os.getenv("APIFOOTBALL_KEY", "").strip()
    if not key:
        logger.error("APIFOOTBALL_KEY nicht gesetzt")
        return 2

    # 1. League-ID ermitteln
    data, quota = _get("/leagues", key, {"search": "world cup"})
    leagues = data.get("response", [])
    logger.info("Quota: %s/%s übrig heute", quota["remaining_day"], quota["limit_day"])
    candidates = []
    for entry in leagues:
        lg = entry.get("league", {})
        seasons = [s.get("year") for s in entry.get("seasons", [])]
        if lg.get("type") == "Cup" and "world cup" in lg.get("name", "").lower():
            candidates.append((lg.get("id"), lg.get("name"), seasons))
    for lid, name, seasons in candidates:
        has2026 = 2026 in seasons
        logger.info("League %s: %r — Saisons %s … 2026 %s",
                    lid, name, seasons[-4:], "✓ GELISTET" if has2026 else "✗ fehlt")

    # FIFA World Cup ist klassisch id=1
    wc = next(((lid, name) for lid, name, s in candidates
               if name == "World Cup" or lid == 1), None)
    if not wc:
        logger.error("GATE 0: keine World-Cup-Liga gefunden → FEHLGESCHLAGEN")
        return 1
    lid, name = wc

    # 2. Echte Fixtures für 2026?
    data, quota = _get("/fixtures", key, {"league": lid, "season": 2026})
    fixtures = data.get("response", [])
    errors = data.get("errors")
    logger.info("Quota: %s/%s übrig heute", quota["remaining_day"], quota["limit_day"])
    if errors and not isinstance(errors, list):
        logger.warning("API errors-Feld: %s", json.dumps(errors, ensure_ascii=False))

    logger.info("League %s (%s), season=2026 → %d Fixture(s)", lid, name, len(fixtures))
    for fx in fixtures[:3]:
        f = fx.get("fixture", {})
        t = fx.get("teams", {})
        logger.info("  Beispiel: id=%s %s vs %s @ %s [%s]",
                    f.get("id"),
                    (t.get("home") or {}).get("name"),
                    (t.get("away") or {}).get("name"),
                    f.get("date"),
                    (f.get("status") or {}).get("short"))

    if fixtures:
        logger.info("GATE 0: BESTANDEN — Saison 2026 liefert echte Fixtures → 1A bauen")
        return 0
    logger.error("GATE 0: FEHLGESCHLAGEN — keine 2026-Fixtures (Free-Plan-Limit?) → STOPP, 1B nur nach OK")
    return 1


if __name__ == "__main__":
    sys.exit(main())
