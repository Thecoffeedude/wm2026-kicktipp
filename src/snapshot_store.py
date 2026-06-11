"""
Append-only snapshot store (JSON Lines) — the reproducible record of what each
source predicted BEFORE kickoff, plus the real result afterwards.

One event per line in data/snapshots.jsonl; lines are NEVER rewritten, so the
store is fully reproducible and auditable. Event types:

  odds      — margin-adjusted market 1X2 at a capture offset (e.g. closing line)
  uanalyse  — uanalyse 1X2 + λ at the snapshot closest to kickoff
  result    — the realised outcome once the match is finished

Pure helpers (`has_snapshot`, `settled_forecasts`) are unit-tested without I/O.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

STORE_PATH = Path(__file__).parent.parent / "data" / "snapshots.jsonl"

VALID_TYPES = frozenset({"odds", "uanalyse", "result"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── I/O ────────────────────────────────────────────────────────────────────

def append_event(event: dict, path: Path = STORE_PATH) -> None:
    """Append one event as a JSON line. Stamps captured_at if absent."""
    if event.get("type") not in VALID_TYPES:
        raise ValueError(f"Unknown snapshot event type: {event.get('type')!r}")
    event.setdefault("captured_at", _now_iso())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def load_events(path: Path = STORE_PATH) -> list[dict]:
    """Read all events. Returns [] if the store does not exist yet."""
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            logger.warning("Skipping malformed snapshot line: %s", line[:80])
    return events


# ── Pure queries ───────────────────────────────────────────────────────────

def has_snapshot(events: list[dict], match_id: str, etype: str, offset: str | None = None) -> bool:
    """
    True if an event of `etype` (optionally a specific `offset`) already exists
    for `match_id`. Used to avoid double-fetching the same offset window.
    """
    for e in events:
        if e.get("match_id") != match_id or e.get("type") != etype:
            continue
        if offset is None or e.get("offset") == offset:
            return True
    return False


def latest_result(events: list[dict], match_id: str) -> dict | None:
    """Most recent result event for a match, or None."""
    results = [e for e in events if e.get("type") == "result" and e.get("match_id") == match_id]
    if not results:
        return None
    return max(results, key=lambda e: e.get("captured_at", ""))


def settled_forecasts(events: list[dict]) -> list[dict]:
    """
    Join closing forecasts with realised results into scoring records:
        {"match_id", "source", "p": {home,draw,away}, "outcome"}

    For each match with a result, take the closing odds snapshot (source
    "market") and the kickoff-closest uanalyse snapshot (source "uanalyse").
    Only matches that have both a forecast and a result are returned.
    """
    # Map match_id → outcome
    outcomes: dict[str, str] = {}
    for e in events:
        if e.get("type") == "result" and e.get("outcome"):
            outcomes[e["match_id"]] = e["outcome"]

    # Best (closing) forecast per (match_id, source)
    best: dict[tuple[str, str], dict] = {}
    for e in events:
        etype = e.get("type")
        if etype == "odds":
            src = "market"
        elif etype == "uanalyse":
            src = "uanalyse"
        else:
            continue
        mid = e.get("match_id")
        p = e.get("p")
        if not mid or not p or mid not in outcomes:
            continue
        key = (mid, src)
        # Prefer the snapshot captured latest (closest to kickoff)
        prev = best.get(key)
        if prev is None or e.get("captured_at", "") > prev.get("captured_at", ""):
            best[key] = e

    records = []
    for (mid, src), e in best.items():
        records.append({
            "match_id": mid,
            "source": src,
            "p": e["p"],
            "outcome": outcomes[mid],
        })
    return records


def count_settled_matches(events: list[dict]) -> int:
    """Distinct matches that have at least one result event."""
    return len({e["match_id"] for e in events
                if e.get("type") == "result" and e.get("match_id")})
