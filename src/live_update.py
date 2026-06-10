"""
Lightweight live-update script: reads docs/data.json, patches the "live" key
with fresh scores, writes back. Run by the live.yml GitHub Actions workflow
every 5 minutes — does NOT rebuild predictions.
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
DATA_PATH = Path("docs/data.json")


def run() -> None:
    if not DATA_PATH.exists():
        logger.error("docs/data.json missing — run build_data.py first")
        sys.exit(1)

    live = fetch_live_scores()

    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    data["live"] = live
    data["metadata"]["live_updated_at"] = (
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    DATA_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    live_n = sum(1 for m in live if m.get("is_live"))
    ht_n   = sum(1 for m in live if m.get("is_halftime"))
    done_n = sum(1 for m in live if m.get("is_done"))
    logger.info(
        "live_update: %d heute (%d live, %d halbzeit, %d beendet)",
        len(live), live_n, ht_n, done_n,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run()
