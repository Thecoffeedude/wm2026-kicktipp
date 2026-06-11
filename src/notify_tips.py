"""
Send today's recommended tips as a push notification via ntfy.sh.
Reads docs/data.json (no recomputation). Run after build_data.py in
predict.yml when NTFY_TOPIC is set. No-op without NTFY_TOPIC or matches.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

DATA_PATH = Path("docs/data.json")
BERLIN = ZoneInfo("Europe/Berlin")


def _kickoff_berlin(commence_time: str) -> datetime | None:
    """Parse commence_time (date-only or ISO) → Berlin-localized datetime."""
    if not commence_time:
        return None
    try:
        if "T" in commence_time:
            dt = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
        else:
            # date-only: treat as UTC noon (same heuristic as the frontend)
            dt = datetime.fromisoformat(commence_time + "T12:00:00+00:00")
    except ValueError:
        return None
    return dt.astimezone(BERLIN)


def build_message(matches: list[dict], now: datetime) -> tuple[str, str] | None:
    """Return (title, body) for today's tips, or None if nothing to send."""
    today = now.astimezone(BERLIN).date()
    lines = []
    for m in matches:
        ko = _kickoff_berlin(m.get("commence_time", ""))
        if ko is None or ko.date() != today:
            continue
        tip = m.get("recommended_tip")
        if not tip:
            continue
        time_str = ko.strftime("%H:%M") if "T" in m["commence_time"] else "–:––"
        lines.append(
            f"{time_str} {m['home_team']} {tip['home']}:{tip['away']} {m['away_team']}"
        )

    if not lines:
        return None

    title = f"WM-Tipps heute ({today.strftime('%d.%m.')})"
    return title, "\n".join(lines)


def run() -> None:
    topic = os.getenv("NTFY_TOPIC", "").strip()
    if not topic:
        logger.info("NTFY_TOPIC not set — skipping notification")
        return

    if not DATA_PATH.exists():
        logger.error("%s missing — run build_data.py first", DATA_PATH)
        sys.exit(1)

    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    msg = build_message(data.get("matches", []), datetime.now(timezone.utc))
    if msg is None:
        logger.info("No matches today — nothing to send")
        return

    title, body = msg
    resp = requests.post(
        f"https://ntfy.sh/{topic}",
        data=body.encode("utf-8"),
        headers={
            "Title": title.encode("utf-8"),
            "Tags": "soccer",
        },
        timeout=10,
    )
    resp.raise_for_status()
    logger.info("Sent %d tip(s) to ntfy topic", body.count("\n") + 1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run()
