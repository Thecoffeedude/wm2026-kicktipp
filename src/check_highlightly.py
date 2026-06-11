"""
1B Discovery — Highlightly API structure probe (read-only diagnostic).

Probes auth header variants and the league/match/statistics endpoints so the
real integration can be built against observed structure instead of guesses.
Logs HTTP status, top-level keys and truncated samples. Never logs the key.
Budget: ~4-6 requests of the 100/day.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time

import requests

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Variants: direct Highlightly API (expects RapidAPI-style headers per their
# gateway) and the RapidAPI-distributed edition. 2.5 s gap → no 429 bursts.
_REQUEST_GAP_S = 2.5
_VARIANTS = [
    ("direct-rapidhdr", "https://soccer.highlightly.net",
     lambda k: {"x-rapidapi-key": k, "x-rapidapi-host": "soccer.highlightly.net"}),
    ("rapidapi", "https://sport-highlights-api.p.rapidapi.com/football",
     lambda k: {"x-rapidapi-key": k, "x-rapidapi-host": "sport-highlights-api.p.rapidapi.com"}),
    ("direct-xapikey", "https://soccer.highlightly.net", lambda k: {"x-api-key": k}),
]


def _trunc(obj, n=700) -> str:
    s = json.dumps(obj, ensure_ascii=False)
    return s[:n] + ("…" if len(s) > n else "")


_working_variant: int | None = None


def probe(path: str, key: str, params: dict | None = None) -> dict | list | None:
    """Try auth/base variants (sticky once one works); return parsed JSON or None."""
    global _working_variant
    order = [_working_variant] if _working_variant is not None else range(len(_VARIANTS))
    for idx in order:
        name, base, mk_headers = _VARIANTS[idx]
        time.sleep(_REQUEST_GAP_S)
        try:
            r = requests.get(f"{base}{path}", headers=mk_headers(key), params=params or {}, timeout=15)
        except Exception as exc:
            logger.warning("%s [%s] → Netzwerkfehler %s", path, name, exc)
            continue
        quota = {k: v for k, v in r.headers.items() if "limit" in k.lower() or "remaining" in k.lower()}
        logger.info("%s [%s] → HTTP %s, Quota-Header: %s", path, name, r.status_code, quota or "-")
        if r.status_code == 200:
            _working_variant = idx
            try:
                data = r.json()
            except ValueError:
                logger.warning("  Antwort kein JSON: %r", r.text[:200])
                return None
            if isinstance(data, dict):
                logger.info("  Top-Level-Keys: %s", list(data.keys()))
            logger.info("  Sample: %s", _trunc(data))
            return data
        logger.info("  Body: %r", r.text[:200])
    return None


def main() -> int:
    key = os.getenv("HIGHLIGHTLY_KEY", "").strip()
    if not key:
        logger.error("HIGHLIGHTLY_KEY nicht gesetzt")
        return 2

    # 1. Ligen — World Cup suchen
    data = probe("/leagues", key, {"leagueName": "World Cup", "limit": 10})
    league_id = None
    if isinstance(data, dict):
        for lg in data.get("data", data.get("leagues", [])) or []:
            logger.info("  Liga: id=%s name=%r seasons=%s",
                        lg.get("id"), lg.get("name"),
                        [s.get("season") for s in (lg.get("seasons") or [])][-4:])
            if lg.get("name") in ("World Cup", "FIFA World Cup") and league_id is None:
                league_id = lg.get("id")
    if league_id is None:
        logger.warning("Keine World-Cup-Liga identifiziert — Matches-Probe mit Datum statt Liga")

    # 2. Matches — heutiges WM-Spiel finden (Eröffnungsspiel 2026-06-11)
    params = {"date": "2026-06-11", "limit": 10}
    if league_id is not None:
        params["leagueId"] = league_id
    data = probe("/matches", key, params)
    match_id = None
    if isinstance(data, dict):
        for m in data.get("data", data.get("matches", [])) or []:
            home = (m.get("homeTeam") or {}).get("name") or m.get("home")
            away = (m.get("awayTeam") or {}).get("name") or m.get("away")
            state = m.get("state") or m.get("status")
            logger.info("  Match: id=%s %s vs %s state=%s", m.get("id"), home, away, state)
            if match_id is None:
                match_id = m.get("id")

    # 3. Statistik + Events für das gefundene Match
    if match_id is not None:
        probe(f"/statistics/{match_id}", key)
        probe(f"/events/{match_id}", key)
        probe(f"/lineups/{match_id}", key)
    else:
        logger.warning("Kein Match gefunden — Statistik-Probe übersprungen")

    logger.info("Discovery abgeschlossen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
