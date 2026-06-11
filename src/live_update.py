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

from src.fetch_live import fetch_live_scores

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


def run() -> None:
    live = fetch_live_scores()

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
