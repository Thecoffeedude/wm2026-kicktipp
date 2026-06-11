"""
Highlightly (soccer.highlightly.net) — post-match detail statistics.

Structure verified live via CI discovery 2026-06-11 (run 27375034525):
  auth     x-rapidapi-key + x-rapidapi-host: soccer.highlightly.net
  quota    X-Ratelimit-Requests-Limit / -Remaining (100/day, reset 00:00 UTC)
  /matches?leagueId=1635&date=YYYY-MM-DD     → id, state, home/awayTeam.name
  /statistics/{id} → [{team, statistics: [{value, displayName}]}] (incl. xG,
                     Ball Possession, Shots …)
  /events/{id}     → [{team, time, type, player, assist, substituted}]
  /lineups/{id}    → {homeTeam: {formation, initialLineup?, substitutes}, …}

Budget rules (100 req/day):
  • Detail endpoints are fetched ONCE per match, only AFTER it is finished
    (docs/results.json is_done). Results cached PERMANENTLY in
    data/match_stats.json — finished matches are never re-fetched.
  • No live polling. Budget guard via the rate-limit headers: below
    MIN_BUDGET_FULL only statistics (no events/lineups); below MIN_BUDGET_STOP
    nothing at all.

Team matching strictly through the canonical registry (resolve()); unknown
names are WARNED about and the match is skipped — never guessed.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.teams import resolve

logger = logging.getLogger(__name__)

BASE = "https://soccer.highlightly.net"
WC_LEAGUE_ID = 1635          # verified via discovery: "World Cup", season 2026

STATS_CACHE_PATH = Path(__file__).parent.parent / "data" / "match_stats.json"
STATS_DOCS_PATH  = Path(__file__).parent.parent / "docs" / "stats.json"
RESULTS_PATH     = Path(__file__).parent.parent / "docs" / "results.json"
DATA_PATH        = Path(__file__).parent.parent / "docs" / "data.json"

MIN_BUDGET_FULL = 25   # below this: statistics only (skip events/lineups)
MIN_BUDGET_STOP = 8    # below this: stop fetching entirely

# Frontend-relevant statistics, keyed by Highlightly displayName
_STAT_KEYS = {
    "Ball Possession":  "possession",
    "Total Shots":      "shots",
    "Shots On Target":  "shots_on_target",
    "Expected Goals":   "xg",
    "Corners":          "corners",
    "Fouls":            "fouls",
    "Passes":           "passes",
}


def _headers() -> dict:
    key = os.getenv("HIGHLIGHTLY_KEY", "").strip()
    if not key:
        raise RuntimeError("HIGHLIGHTLY_KEY nicht gesetzt")
    return {"x-rapidapi-key": key, "x-rapidapi-host": "soccer.highlightly.net"}


def _get(path: str, params: dict | None = None) -> tuple[object, int | None]:
    """GET → (json, requests_remaining)."""
    r = requests.get(f"{BASE}{path}", headers=_headers(), params=params or {}, timeout=15)
    r.raise_for_status()
    remaining = r.headers.get("X-Ratelimit-Requests-Remaining")
    return r.json(), int(remaining) if remaining is not None else None


# ── Pure normalizers (unit-tested) ─────────────────────────────────────────

def _pct_to_int(value) -> int | None:
    """'56%' → 56, 56 → 56, 0.56 stays 56 only if already scaled."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip().rstrip("%")
        try:
            value = float(value)
        except ValueError:
            return None
    return int(round(float(value)))


def normalize_statistics(raw: list, home_code: str) -> dict | None:
    """
    Highlightly /statistics → {"home": {...}, "away": {...}} with our stat keys.
    Sides resolved via the team registry; returns None if sides can't be mapped.
    """
    if not isinstance(raw, list) or len(raw) < 2:
        return None
    sides: dict[str, dict] = {}
    for team_block in raw:
        name = (team_block.get("team") or {}).get("name", "")
        side = "home" if resolve(name) == home_code else "away"
        out = {}
        for stat in team_block.get("statistics") or []:
            key = _STAT_KEYS.get(stat.get("displayName", ""))
            if key is None:
                continue
            v = stat.get("value")
            out[key] = _pct_to_int(v) if key == "possession" else v
        sides[side] = out
    if set(sides) != {"home", "away"}:
        logger.warning("Statistik-Seiten nicht zuordenbar (home_code=%s)", home_code)
        return None
    return sides


def normalize_events(raw: list, home_code: str) -> list[dict]:
    """Highlightly /events → compact timeline entries with side mapping."""
    out = []
    for ev in raw or []:
        name = (ev.get("team") or {}).get("name", "")
        out.append({
            "time":   ev.get("time"),
            "type":   ev.get("type"),         # Goal / Yellow Card / Red Card / Substitution …
            "player": ev.get("player"),
            "assist": ev.get("assist"),
            "sub_in": ev.get("substituted"),
            "side":   "home" if resolve(name) == home_code else "away",
        })
    return out


def normalize_lineups(raw: dict) -> dict | None:
    """Highlightly /lineups → {home:{formation, xi, bench}, away:{…}} (names only)."""
    if not isinstance(raw, dict):
        return None

    def side(block: dict | None) -> dict:
        block = block or {}
        def names(lst):
            return [
                {"name": p.get("name"), "number": p.get("number"), "pos": p.get("position")}
                for p in (lst or [])
            ]
        # XI field name is not in the truncated discovery sample — accept variants
        xi = block.get("initialLineup") or block.get("startXI") or block.get("lineup") or []
        # Some responses nest the XI as formation rows (list of lists) — flatten
        if xi and isinstance(xi[0], list):
            xi = [p for row in xi for p in row]
        return {
            "formation": block.get("formation"),
            "xi": names(xi),
            "bench": names(block.get("substitutes")),
        }

    home, away = side(raw.get("homeTeam")), side(raw.get("awayTeam"))
    if not home["xi"] and not away["xi"] and not home["bench"]:
        return None
    return {"home": home, "away": away}


# ── Cache I/O ──────────────────────────────────────────────────────────────

def load_cache(path: Path = STATS_CACHE_PATH) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("match_stats.json unlesbar — starte leer")
        return {}


def save_cache(cache: dict, path: Path = STATS_CACHE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")


def write_docs_stats(cache: dict, path: Path = STATS_DOCS_PATH) -> None:
    """Frontend file: same content, plus timestamp."""
    payload = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "matches": cache,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


# ── Main run ───────────────────────────────────────────────────────────────

def _finished_matches() -> list[dict]:
    """Finished matches (with our canonical match ids) that need stats."""
    if not RESULTS_PATH.exists() or not DATA_PATH.exists():
        return []
    try:
        results = json.loads(RESULTS_PATH.read_text(encoding="utf-8")).get("results", [])
        matches = json.loads(DATA_PATH.read_text(encoding="utf-8")).get("matches", [])
    except json.JSONDecodeError:
        return []
    by_pair = {(m["home_code"], m["away_code"]): m for m in matches}
    out = []
    for r in results:
        if not r.get("is_done"):
            continue
        m = by_pair.get((r.get("home_code"), r.get("away_code")))
        if m:
            out.append(m)
    return out


def _find_hl_match_id(match: dict, date_cache: dict) -> int | None:
    """Resolve our match to a Highlightly match id via league+date+registry."""
    date = (match.get("commence_time") or "")[:10]
    if not date:
        return None
    if date not in date_cache:
        data, remaining = _get("/matches", {"leagueId": WC_LEAGUE_ID, "date": date, "limit": 40})
        date_cache["_remaining"] = remaining
        date_cache[date] = (data or {}).get("data", []) if isinstance(data, dict) else []
    for hl in date_cache[date]:
        h = resolve((hl.get("homeTeam") or {}).get("name", ""))
        a = resolve((hl.get("awayTeam") or {}).get("name", ""))
        if (h, a) == (match["home_code"], match["away_code"]):
            return hl.get("id")
    logger.warning("Highlightly: kein Match für %s-%s am %s",
                   match["home_code"], match["away_code"], date)
    return None


def run() -> dict:
    cache = load_cache()
    todo = [m for m in _finished_matches() if m["id"] not in cache]
    summary = {"fetched": 0, "skipped_cached": 0, "remaining": None}
    if not todo:
        logger.info("Highlightly: nichts zu tun (alle FT-Spiele gecacht)")
        return summary

    date_cache: dict = {}
    remaining: int | None = None

    for m in sorted(todo, key=lambda x: x.get("commence_time", "")):
        if remaining is not None and remaining < MIN_BUDGET_STOP:
            logger.warning("Budget-Stopp: nur noch %s Requests — Rest morgen", remaining)
            break
        hl_id = _find_hl_match_id(m, date_cache)
        remaining = date_cache.get("_remaining", remaining)
        if hl_id is None:
            continue

        entry: dict = {
            "hl_id": hl_id,
            "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        try:
            raw_stats, remaining = _get(f"/statistics/{hl_id}")
            stats = normalize_statistics(raw_stats, m["home_code"])
            if stats:
                entry["stats"] = stats
        except Exception as exc:
            logger.warning("Statistik %s fehlgeschlagen: %s", hl_id, exc)

        full_budget = remaining is None or remaining >= MIN_BUDGET_FULL
        if full_budget:
            try:
                raw_events, remaining = _get(f"/events/{hl_id}")
                entry["events"] = normalize_events(raw_events, m["home_code"])
            except Exception as exc:
                logger.warning("Events %s fehlgeschlagen: %s", hl_id, exc)
            try:
                raw_lineups, remaining = _get(f"/lineups/{hl_id}")
                lineups = normalize_lineups(raw_lineups)
                if lineups:
                    entry["lineups"] = lineups
            except Exception as exc:
                logger.warning("Lineups %s fehlgeschlagen: %s", hl_id, exc)
        else:
            logger.info("Budget knapp (%s) — nur Statistik für %s", remaining, m["id"])

        # Only cache when we actually got the core payload — otherwise retry next run
        if entry.get("stats") or entry.get("events"):
            cache[m["id"]] = entry
            summary["fetched"] += 1
            logger.info("Stats gecacht: %s (hl=%s, remaining=%s)", m["id"], hl_id, remaining)

    if summary["fetched"]:
        save_cache(cache)
        write_docs_stats(cache)
    summary["remaining"] = remaining
    logger.info("Highlightly: %d neu, %d Spiele im Cache, Budget übrig: %s",
                summary["fetched"], len(cache), remaining)
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    argparse.ArgumentParser(description="Post-match stats (once per match, cached forever)").parse_args()
    run()
