"""
Cheap pre-gate for the Kicktipp submit workflow — NO browser, NO Playwright.

Decides whether the (expensive) Playwright submit should run this tick, based on
each match's kickoff time as a deadline proxy. The actual deadline is read from
Kicktipp inside the submit itself; this gate only avoids spinning a browser when
nothing is actionable.

Writes `should_run` / `phase` / `reason` to $GITHUB_OUTPUT and logs a summary.

A match triggers a run when it is in:
  • the FRESHNESS window (25–75 min to kickoff), or
  • the SAFETY window (6–12 h) AND it is not yet marked tipped in submit_state.json.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.kicktipp_submit import (
    load_submit_state, submit_window, _state_key,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATA_JSON = Path(__file__).parent.parent / "docs" / "data.json"


def plan_gate(matches: list[dict], state: dict, now: datetime) -> dict:
    """
    Pure gate decision. Returns {"should_run", "phase", "reason", "matches": [...]}.
    `phase` is "freshness" if any freshness game is due, else "safety", else "none".
    """
    fresh, safety = [], []
    for m in matches:
        ct = m.get("commence_time", "")
        if "T" not in ct:
            continue
        try:
            kickoff = datetime.fromisoformat(ct.replace("Z", "+00:00"))
        except ValueError:
            continue
        ttd = (kickoff - now).total_seconds() / 60.0
        phase = submit_window(ttd)
        label = f"{m.get('home_code')}-{m.get('away_code')} ({ttd:.0f}m)"
        if phase == "freshness":
            fresh.append(label)
        elif phase == "safety":
            key = _state_key(m["home_team"], m["away_team"])
            if not state.get(key, {}).get("tipped"):
                safety.append(label)

    if fresh:
        return {"should_run": True, "phase": "freshness",
                "reason": f"freshness window: {', '.join(fresh)}", "matches": fresh}
    if safety:
        return {"should_run": True, "phase": "safety",
                "reason": f"safety window (untipped): {', '.join(safety)}", "matches": safety}
    return {"should_run": False, "phase": "none", "reason": "no game in a submit window", "matches": []}


def run() -> dict:
    if not DATA_JSON.exists():
        logger.warning("%s missing — gate returns should_run=false", DATA_JSON)
        gate = {"should_run": False, "phase": "none", "reason": "no data.json", "matches": []}
    else:
        matches = json.loads(DATA_JSON.read_text(encoding="utf-8")).get("matches", [])
        gate = plan_gate(matches, load_submit_state(), datetime.now(timezone.utc))

    logger.info("Submit gate: should_run=%s phase=%s (%s)",
                gate["should_run"], gate["phase"], gate["reason"])

    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"should_run={'true' if gate['should_run'] else 'false'}\n")
            f.write(f"phase={gate['phase']}\n")
    return gate


if __name__ == "__main__":
    run()
