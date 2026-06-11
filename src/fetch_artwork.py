"""
Team artwork (badges) from TheSportsDB — fetched ONCE per team, cached forever.

National-team badges effectively never change, so data/team_artwork.json is a
permanent cache: teams already present are never re-fetched. Matching goes
through the canonical team registry (resolve()); a TSDB hit only counts when
strSport/strLeague identify the men's A-team AND resolve(strTeam) maps back to
the expected FIFA code — otherwise we WARN and skip (never guess).

Key: TSDB_KEY env var. Falls back to TheSportsDB's public documented test key
("3") which is fine for this one-shot, low-volume use.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.teams import all_teams, resolve

logger = logging.getLogger(__name__)

ARTWORK_PATH = Path(__file__).parent.parent / "data" / "team_artwork.json"
TSDB_BASE = "https://www.thesportsdb.com/api/v1/json"

# The men's national A-team is listed under "FIFA World Cup" — debutants may
# still carry their qualifying league (observed: Norway). Youth/women teams are
# excluded by the resolve()-guard, not by league.
def _league_ok(league: str) -> bool:
    return league == "FIFA World Cup" or league.startswith("World Cup Qualifying")
_REQUEST_GAP_S = 2.1   # free tier allows ~30 req/min — stay safely under


def _api_key() -> str:
    return os.getenv("TSDB_KEY", "").strip() or "3"


def load_artwork(path: Path = ARTWORK_PATH) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("Could not parse %s — starting fresh", path)
        return {}


def save_artwork(artwork: dict, path: Path = ARTWORK_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artwork, indent=2, ensure_ascii=False), encoding="utf-8")


def _search_team(name: str) -> list[dict]:
    for attempt in range(3):
        resp = requests.get(
            f"{TSDB_BASE}/{_api_key()}/searchteams.php",
            params={"t": name}, timeout=10,
        )
        if resp.status_code == 429:
            wait = 65
            logger.info("TSDB rate-limited — waiting %ds (attempt %d/3)", wait, attempt + 1)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return (resp.json() or {}).get("teams") or []
    raise RuntimeError("TSDB: still rate-limited after 3 attempts")


def _pick_national_team(results: list[dict], expected_code: str) -> dict | None:
    """Filter search results down to the men's A-team for `expected_code`."""
    for t in results:
        if t.get("strSport") != "Soccer":
            continue
        if not _league_ok(t.get("strLeague") or ""):
            continue
        # Final guard: the TSDB name must resolve back to the expected code
        if resolve(t.get("strTeam", "")) != expected_code:
            continue
        return t
    return None


def _search_candidates(entry: dict) -> list[str]:
    """Search names to try: canonical English first, then ASCII-ish aliases."""
    seen, out = set(), []
    for name in [entry["canonical_en"], *entry.get("aliases", [])]:
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def fetch_missing(artwork: dict) -> int:
    """Fetch badges for registry teams not yet cached. Returns #added."""
    added = 0
    for entry in all_teams():
        code = entry["code"]
        if code in artwork:
            continue  # cache forever — never re-fetch
        found = None
        for name in _search_candidates(entry):
            try:
                results = _search_team(name)
            except Exception as exc:
                logger.warning("TSDB search %r failed: %s", name, exc)
                continue
            finally:
                time.sleep(_REQUEST_GAP_S)
            found = _pick_national_team(results, code)
            if found:
                break
        if not found:
            logger.warning("TSDB: no A-team match for %s (%s) — skipped",
                           code, entry["canonical_en"])
            continue
        badge = found.get("strBadge") or ""
        if not badge:
            logger.warning("TSDB: %s matched but has no badge", code)
            continue
        artwork[code] = {
            "badge": badge,
            "badge_small": badge + "/small",   # TSDB size variant
            "tsdb_team": found.get("strTeam", ""),
            "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        added += 1
        logger.info("TSDB: %s → %s", code, badge)
    return added


def run() -> dict:
    artwork = load_artwork()
    added = fetch_missing(artwork)
    if added:
        save_artwork(artwork)
    logger.info("Artwork: %d Team(s) gecacht (%d neu)", len(artwork), added)
    return artwork


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Fetch team badges (once, cached forever)")
    parser.parse_args()
    run()
