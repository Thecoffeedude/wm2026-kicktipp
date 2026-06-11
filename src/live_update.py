"""
Lightweight live-update script, run by live.yml every 5 minutes during
match hours. Writes two small files — it does NOT touch docs/data.json,
so it can never race with the daily predict.yml build:

- docs/live.json:    today's matches with live scores/status
- docs/results.json: cumulative store of all finished matches
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.fetch_live import enrich_live_details, fetch_live_scores

logger = logging.getLogger(__name__)
DOCS_DIR     = Path("docs")
LIVE_PATH    = DOCS_DIR / "live.json"
RESULTS_PATH = DOCS_DIR / "results.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _result_key(entry: dict) -> tuple:
    return (entry["home_code"], entry["away_code"], entry.get("utc_date", "")[:10])


def merge_results(existing: list[dict], finished: list[dict]) -> list[dict]:
    """
    Merge newly finished matches into the existing results list.
    Keyed by (home_code, away_code, date) — re-runs update in place,
    older results are never dropped.
    """
    by_key = {_result_key(r): r for r in existing}
    for entry in finished:
        if entry.get("is_done"):
            by_key[_result_key(entry)] = entry
    return sorted(by_key.values(), key=lambda r: r.get("utc_date", ""))


# Status progression rank: API sometimes regresses (IN_PLAY → TIMED glitches,
# observed live 2026-06-11). A lower-ranked update must not clobber what we saw.
_STATUS_RANK = {"SCHEDULED": 0, "TIMED": 0, "IN_PLAY": 1, "PAUSED": 1,
                "EXTRA_TIME": 1, "PENALTY_SHOOTOUT": 1,
                "FINISHED": 2, "FINISHED_AET": 2, "FINISHED_PEN": 2}


def merge_live(old: list[dict], new: list[dict]) -> list[dict]:
    """
    Merge the fresh API snapshot with the previous live.json state, keeping the
    previous entry when the API regresses (e.g. a running match suddenly
    reported as TIMED again with no score). Score changes within the same
    status rank are taken from the API (covers VAR-disallowed goals).
    """
    old_by_key = {_result_key(e): e for e in old}
    merged = []
    for e in new:
        prev = old_by_key.get(_result_key(e))
        if prev is not None:
            new_rank = _STATUS_RANK.get(e.get("status", ""), 0)
            old_rank = _STATUS_RANK.get(prev.get("status", ""), 0)
            if new_rank < old_rank:
                logger.warning(
                    "API-Regression %s-%s: %s → %s — behalte alten Stand",
                    e.get("home_code"), e.get("away_code"),
                    prev.get("status"), e.get("status"),
                )
                merged.append(prev)
                continue
            # Same rank but score vanished (None after a number) → keep old score
            if new_rank == old_rank and e.get("score_home") is None \
                    and prev.get("score_home") is not None:
                merged.append(prev)
                continue
            # Preserve enrichment (goals/stats) if the fresh poll lacks it
            if not e.get("goals") and prev.get("goals"):
                e = {**e, "goals": prev["goals"]}
        merged.append(e)
    return merged


def _payload_unchanged(path: Path, payload: dict, content_key: str) -> bool:
    """True if the file already holds the same content (timestamp ignored).
    Avoids no-op commits from the 5-minute live workflow."""
    if not path.exists():
        return False
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return existing.get(content_key) == payload.get(content_key)


def write_live(live: list[dict], path: Path = LIVE_PATH) -> None:
    payload = {"updated_at": _now_iso(), "live": live}
    if _payload_unchanged(path, payload, "live"):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def update_results(finished: list[dict], path: Path = RESULTS_PATH) -> int:
    """Merge finished entries into the results file. Returns total count."""
    existing: list[dict] = []
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8")).get("results", [])
        except (json.JSONDecodeError, AttributeError):
            logger.warning("Could not parse %s — starting fresh", path)

    merged = merge_results(existing, finished)
    payload = {"updated_at": _now_iso(), "results": merged}
    if _payload_unchanged(path, payload, "results"):
        return len(merged)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return len(merged)


def _existing_results() -> dict[tuple, dict]:
    if not RESULTS_PATH.exists():
        return {}
    try:
        results = json.loads(RESULTS_PATH.read_text(encoding="utf-8")).get("results", [])
    except (json.JSONDecodeError, AttributeError):
        return {}
    return {_result_key(r): r for r in results}


def _previous_live() -> list[dict]:
    if not LIVE_PATH.exists():
        return []
    try:
        return json.loads(LIVE_PATH.read_text(encoding="utf-8")).get("live", [])
    except (json.JSONDecodeError, AttributeError):
        return []


def run() -> None:
    live = fetch_live_scores()

    # Anti-Regression: flatternde API-Stände (live → wieder "geplant") dürfen
    # einen bereits gesehenen Spielstand nicht zurücksetzen.
    live = merge_live(_previous_live(), live)

    # Detail-Anreicherung (Torschützen, Minute, ggf. Statistiken):
    # laufende Spiele immer; beendete nur, solange goals noch nicht persistiert
    # sind (spart Detail-Calls bei jedem Loop-Tick).
    existing = _existing_results()
    eligible = [
        e for e in live
        if (e.get("is_live") or e.get("is_halftime"))
        or (e.get("is_done") and not existing.get(_result_key(e), {}).get("goals"))
    ]
    if eligible:
        n = enrich_live_details(eligible)
        logger.info("Detail-Anreicherung: %d Spiel(e)", n)

    write_live(live)
    total_results = update_results(live)

    live_n = sum(1 for m in live if m.get("is_live"))
    ht_n   = sum(1 for m in live if m.get("is_halftime"))
    done_n = sum(1 for m in live if m.get("is_done"))
    logger.info(
        "live_update: %d heute (%d live, %d halbzeit, %d beendet) — %d Ergebnisse gesamt",
        len(live), live_n, ht_n, done_n, total_results,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run()
