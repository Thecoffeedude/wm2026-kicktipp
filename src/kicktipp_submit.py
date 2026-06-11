#!/usr/bin/env python3
"""
Kicktipp Auto-Submit — reads docs/data.json and enters recommended tips.

Default mode: --dry-run (login + scrape, but NO form fill or submit).
Use explicit --submit to actually enter tips.

Env vars (via .env or CI secrets):
  KICKTIPP_EMAIL, KICKTIPP_PASSWORD, KICKTIPP_COMPETITION
  OVERWRITE=false   — skip games already tipped (default: false)
  NTFY_TOPIC        — optional ntfy.sh push on successful submit
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow `import config` from repo root — same pattern as build_data.py / scoreline.py
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from src.teams import resolve as _resolve_team, canonical_en as _canonical_en

# ─── Constants ────────────────────────────────────────────────────────────────

BASE_URL = "https://www.kicktipp.de"
LOGIN_URL = f"{BASE_URL}/info/profil/login"
DEFAULT_DEADLINE_BUFFER_HOURS = 2.0
SCREENSHOT_DIR = Path("screenshots")

# ─── Submit-window timing (minutes to the ACTUAL Kicktipp deadline) ───────────
#
#   ttd = (deadline − now) in minutes.   Two passes per game, derived from ttd:
#
#     ┌─ SAFETY pass ──────────────────────────────────────────────────────────┐
#     │  Ziel-Fenster: 6–12 h vor Deadline. Jedes OFFENE Spiel wird mit der     │
#     │  besten verfügbaren Prediction getippt (kein Überschreiben).            │
#     │  Wirkung: garantiert kein leeres Feld. Greift als Netz auch später,     │
#     │  solange das Feld leer ist und noch Marge bleibt.                       │
#     ├─ FRESHNESS pass ───────────────────────────────────────────────────────┤
#     │  Fenster: 25–75 min vor Deadline (nach den Aufstellungen). Bereits      │
#     │  getippte Spiele werden mit der frischen Schlussquoten-Blend            │
#     │  ÜBERSCHRIEBEN — aber nur, wenn sich der Tipp tatsächlich ändert.       │
#     ├─ FINISH-MARGIN ────────────────────────────────────────────────────────┤
#     │  Unter 22 min vor Deadline wird NICHT mehr getippt (Cron-/Submit-Verzug │
#     │  einkalkuliert) → ntfy-Alarm für manuellen Eingriff.                    │
#     └─────────────────────────────────────────────────────────────────────────┘
FINISH_MARGIN_MIN = 22       # nie näher als 22 min an die Deadline heran tippen
SAFETY_MIN_MIN    = 360      # 6 h  — frühestes Ziel des Absicherungs-Passes
SAFETY_MAX_MIN    = 720      # 12 h — spätestes Ziel des Absicherungs-Passes
FRESH_MIN_MIN     = 25       # frühestes Ende des Freshness-Fensters
FRESH_MAX_MIN     = 75       # spätester Beginn des Freshness-Fensters

# Kleiner Zustands-Store, damit der (teure) Playwright-Lauf im langen Safety-
# Fenster nicht bei jedem 30-Min-Tick erneut startet.
SUBMIT_STATE_PATH = Path(__file__).parent.parent / "data" / "submit_state.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)

# ─── Pure matching logic (no browser — fully unit-testable) ───────────────────


def canonicalize(name: str, aliases: dict[str, str] = {}) -> str:
    """Return canonical English team name via team registry. aliases param kept for compat."""
    return _canonical_en(_resolve_team(name))


def build_prediction_index(
    matches: list[dict], aliases: dict[str, str] = {}
) -> dict[tuple[str, str], dict]:
    """Return (home_code, away_code) → match dict. Deduplication by FIFA code."""
    result: dict[tuple[str, str], dict] = {}
    for m in matches:
        home_code = m.get("home_code") or _resolve_team(m["home_team"])
        away_code = m.get("away_code") or _resolve_team(m["away_team"])
        result[(home_code, away_code)] = m
    return result


def match_row(
    kicktipp_home: str,
    kicktipp_away: str,
    index: dict[tuple[str, str], dict],
    aliases: dict[str, str] = {},
) -> dict | None:
    """Find prediction for a kicktipp row by resolving team names to FIFA codes."""
    key = (_resolve_team(kicktipp_home), _resolve_team(kicktipp_away))
    return index.get(key)


def parse_kicktipp_deadline(text: str, now: datetime | None = None) -> datetime | None:
    """
    Parse a Kicktipp deadline cell into a UTC datetime.

    Accepts German formats commonly shown in the tippabgabe time column:
      "14.06.26 18:00", "14.06.2026 18:00", "14.06. 18:00", "18:00".
    Kicktipp times are Europe/Berlin local; converted to UTC (DST-aware).
    Returns None if no time could be extracted.
    """
    import re
    from zoneinfo import ZoneInfo

    if not text:
        return None
    berlin = ZoneInfo("Europe/Berlin")
    now = now or datetime.now(timezone.utc)
    now_local = now.astimezone(berlin)

    t = text.strip().replace("\n", " ")
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})?\s*[^\d]*?(\d{1,2}):(\d{2})", t)
    if m:
        day, month, year, hh, mm = m.groups()
        year_i = int(year) if year else now_local.year
        if year_i < 100:
            year_i += 2000
        try:
            local = datetime(year_i, int(month), int(day), int(hh), int(mm), tzinfo=berlin)
            return local.astimezone(timezone.utc)
        except ValueError:
            return None

    # Time-only ("18:00") → assume today (Berlin); roll to tomorrow if already past
    m2 = re.search(r"(\d{1,2}):(\d{2})", t)
    if m2:
        hh, mm = m2.groups()
        try:
            local = now_local.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        except ValueError:
            return None
        if local < now_local - timedelta(minutes=1):
            local += timedelta(days=1)
        return local.astimezone(timezone.utc)

    return None


def deadline_for(row: dict, prediction: dict | None, now: datetime | None = None) -> datetime | None:
    """
    Resolve the authoritative deadline for a row: the scraped Kicktipp deadline
    if available, else the prediction's kickoff time as a proxy.
    """
    scraped = row.get("deadline")
    if scraped:
        if isinstance(scraped, datetime):
            return scraped
        try:
            return datetime.fromisoformat(str(scraped).replace("Z", "+00:00"))
        except ValueError:
            pass
    raw = row.get("deadline_text", "")
    parsed = parse_kicktipp_deadline(raw, now)
    if parsed:
        return parsed
    ct = (prediction or {}).get("commence_time", "")
    if ct:
        try:
            if "T" in ct:
                return datetime.fromisoformat(ct.replace("Z", "+00:00"))
            return datetime.fromisoformat(ct).replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def uncovered_due_matches(
    matches: list[dict],
    scraped_keys: set[tuple[str, str]],
    tipped_keys: set[tuple[str, str]],
    now: datetime,
) -> list[dict]:
    """
    Data.json matches that are inside a submit window (safety/freshness) but have
    NO corresponding row on the scraped Kicktipp page and aren't already tipped.

    This is the safety net for a collapsed / not-yet-expanded matchday: instead of
    silently missing a game, the caller can fire an ntfy alert.
    """
    out = []
    for m in matches:
        ct = m.get("commence_time", "")
        if "T" not in ct:
            continue
        try:
            kickoff = datetime.fromisoformat(ct.replace("Z", "+00:00"))
        except ValueError:
            continue
        ttd = (kickoff - now).total_seconds() / 60.0
        if submit_window(ttd) not in ("safety", "freshness"):
            continue
        key = (m.get("home_code"), m.get("away_code"))
        if key not in scraped_keys and key not in tipped_keys:
            out.append(m)
    return out


def submit_window(ttd_min: float | None) -> str:
    """
    Classify a time-to-deadline (minutes) into a submit phase.
    Returns one of: "too_late", "freshness", "safety", "waiting", "closed".
    Pure helper used for both the cheap gate and per-row logging.
    """
    if ttd_min is None:
        return "safety"            # unknown deadline → treat conservatively
    if ttd_min <= 0:
        return "closed"
    if ttd_min < FINISH_MARGIN_MIN:
        return "too_late"
    if FRESH_MIN_MIN <= ttd_min <= FRESH_MAX_MIN:
        return "freshness"
    if SAFETY_MIN_MIN <= ttd_min <= SAFETY_MAX_MIN:
        return "safety"
    return "waiting"


def decide_action(
    home_value: str,
    away_value: str,
    new_tip: dict | None,
    ttd_min: float | None,
    force_overwrite: bool = False,
) -> tuple[str, str]:
    """
    Decide what to do for one game row, given minutes-to-deadline (ttd_min).

    Adaptive two-pass logic (no separate cron passes needed):
      • empty field + enough margin            → tip   (SAFETY fill, never empty)
      • already tipped, inside freshness window → tip   (FRESHNESS overwrite) —
        but only if the fresh tip actually differs (idempotent)
      • already tipped, outside that window     → skip_tipped
      • < FINISH_MARGIN before deadline         → skip_too_late  (ntfy alert)

    Returns (action, reason). action ∈ {tip, skip_no_match, skip_tipped,
    skip_unchanged, skip_too_late, skip_closed}.
    """
    if new_tip is None:
        return "skip_no_match", "no matching prediction in data.json"

    empty = not (home_value.strip() or away_value.strip())
    same = (str(new_tip["home"]), str(new_tip["away"])) == (home_value.strip(), away_value.strip())

    if ttd_min is not None:
        if ttd_min <= 0:
            return "skip_closed", "deadline already passed"
        if ttd_min < FINISH_MARGIN_MIN:
            return ("skip_too_late",
                    f"only {ttd_min:.0f} min to deadline (< {FINISH_MARGIN_MIN} min margin)")

    # Open game → always fill while there is margin (safety net)
    if empty:
        ttd_txt = f"{ttd_min:.0f} min" if ttd_min is not None else "unknown"
        return "tip", f"safety-fill (deadline in {ttd_txt})"

    # Already tipped
    if force_overwrite and not same:
        return "tip", "forced overwrite"

    in_freshness = ttd_min is None or (FRESH_MIN_MIN <= ttd_min <= FRESH_MAX_MIN)
    if in_freshness and not same:
        return "tip", f"freshness-overwrite (deadline in {ttd_min:.0f} min)" if ttd_min is not None \
            else "freshness-overwrite"
    if same:
        return "skip_unchanged", "tip unchanged — idempotent"
    return "skip_tipped", "already tipped, outside freshness window"


def plan_submissions(
    rows: list[dict],
    matches: list[dict],
    aliases: dict[str, str] = {},
    now: datetime | None = None,
    force_overwrite: bool = False,
) -> list[dict]:
    """
    Pure function: map kicktipp rows to actions using each row's actual deadline.

    Each row dict: {home, away, home_value, away_value, home_input, away_input,
                    deadline | deadline_text (optional)}.
    Returns action dicts with action, reason, tip, phase, ttd_min.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    index = build_prediction_index(matches, aliases)
    result = []

    for row in rows:
        prediction = match_row(row["home"], row["away"], index, aliases)
        deadline = deadline_for(row, prediction, now)
        ttd_min = (deadline - now).total_seconds() / 60.0 if deadline else None
        phase = submit_window(ttd_min)

        new_tip = prediction.get("recommended_tip") if prediction else None
        action, reason = decide_action(
            row.get("home_value", ""),
            row.get("away_value", ""),
            new_tip,
            ttd_min,
            force_overwrite,
        )
        result.append(
            {
                "kicktipp_home": row["home"],
                "kicktipp_away": row["away"],
                "home_input": row.get("home_input", ""),
                "away_input": row.get("away_input", ""),
                "deadline": deadline.strftime("%Y-%m-%dT%H:%M:%SZ") if deadline else None,
                "ttd_min": round(ttd_min, 1) if ttd_min is not None else None,
                "phase": phase,
                "action": action,
                "reason": reason,
                "tip": new_tip if action == "tip" else None,
            }
        )

    return result


# ─── Submit-state (avoids redundant Playwright spins in the safety window) ───


def load_submit_state(path: Path = SUBMIT_STATE_PATH) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_submit_state(state: dict, path: Path = SUBMIT_STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _state_key(home: str, away: str) -> str:
    return f"{_resolve_team(home)}:{_resolve_team(away)}"


# ─── Browser helpers (Playwright) ─────────────────────────────────────────────


async def _dismiss_gdpr(page) -> None:
    try:
        await page.locator('button:has-text("Alle akzeptieren")').click(timeout=3_000)
        logging.debug("GDPR banner dismissed")
    except Exception:
        pass  # banner absent or already dismissed


async def login(page, email: str, password: str) -> None:
    """Log in; raise RuntimeError with plain-text message on failure."""
    logging.info("Logging in to kicktipp.de…")
    await page.goto(LOGIN_URL)
    await _dismiss_gdpr(page)
    await page.locator("#kennung").fill(email)
    await page.locator("#passwort").fill(password)
    await page.locator('[name="submitbutton"]').click()
    await page.wait_for_load_state("networkidle")
    if "login" in page.url.lower():
        raise RuntimeError(
            "Login failed — check KICKTIPP_EMAIL / KICKTIPP_PASSWORD "
            "or kicktipp.de changed its login page structure"
        )
    logging.info("Login successful")


async def scrape_game_rows(page, competition: str) -> list[dict]:
    """
    Navigate to tippabgabe and return one dict per game row.

    DOM assumptions (from antonengelhardt/kicktipp-bot + schwalle/kicktipp-betbot):
      #tippabgabeSpiele tbody tr.datarow
        td[0] — match time / date
        td[1] — home team name
        td[2] — away team name
        td[3+] — inputs: input[name*=heimTipp], input[name*=gastTipp]
    """
    url = f"{BASE_URL}/{competition}/tippabgabe"
    logging.info(f"Fetching {url}")
    await page.goto(url)
    try:
        await page.wait_for_selector("#tippabgabeSpiele", timeout=10_000)
    except Exception:
        raise RuntimeError(
            f"Table #tippabgabeSpiele not found on {url}. "
            "Wrong competition name or page structure changed."
        )

    rows: list[dict] = []
    tr_locators = page.locator("#tippabgabeSpiele tbody tr.datarow")
    count = await tr_locators.count()
    logging.info(f"Found {count} game row(s)")

    for i in range(count):
        tr = tr_locators.nth(i)
        try:
            home_input = tr.locator('input[name*="heimTipp"]')
            away_input = tr.locator('input[name*="gastTipp"]')

            # td[0] carries the match date/time — Kicktipp's tip deadline.
            # text_content (not inner_text) so collapsed/hidden matchday rows
            # still yield their text instead of an empty string.
            deadline_text = ((await tr.locator("td").nth(0).text_content()) or "").strip()
            home_name = ((await tr.locator("td").nth(1).text_content()) or "").strip()
            away_name = ((await tr.locator("td").nth(2).text_content()) or "").strip()
            home_val = await home_input.input_value()
            away_val = await away_input.input_value()
            home_inp_name = (await home_input.get_attribute("name")) or ""
            away_inp_name = (await away_input.get_attribute("name")) or ""

            rows.append(
                {
                    "home": home_name,
                    "away": away_name,
                    "home_value": home_val,
                    "away_value": away_val,
                    "home_input": home_inp_name,
                    "away_input": away_inp_name,
                    "deadline_text": deadline_text,
                }
            )
        except Exception as exc:
            logging.warning(f"Row {i}: could not parse — {exc}")

    return rows


async def apply_tips(
    page,
    competition: str,
    actions: list[dict],
    dry_run: bool,
) -> dict[str, int]:
    """Fill inputs and submit. Returns counts: tipped/skipped/errors."""
    counts = {"tipped": 0, "skipped": 0, "errors": 0}
    tipped_actions = []

    for act in actions:
        label = f"{act['kicktipp_home']} vs {act['kicktipp_away']}"
        if act["action"] != "tip":
            logging.info(f"SKIP  {label} — {act['action']}: {act['reason']}")
            counts["skipped"] += 1
            continue

        tip = act["tip"]
        tag = "DRY " if dry_run else "TIP "
        logging.info(
            f"{tag}  {label} → {tip['home']}:{tip['away']}"
            f"  (EV {tip['expected_points']} Pkt., src={tip['based_on']})"
        )

        if not dry_run:
            try:
                await page.locator(f'input[name="{act["home_input"]}"]').fill(
                    str(tip["home"])
                )
                await page.locator(f'input[name="{act["away_input"]}"]').fill(
                    str(tip["away"])
                )
                tipped_actions.append(act)
                counts["tipped"] += 1
            except Exception as exc:
                logging.error(f"Error filling {label}: {exc}")
                counts["errors"] += 1
        else:
            counts["tipped"] += 1  # count as "would tip" in dry-run

    return counts


async def submit_tippabgabe(page) -> bool:
    """Click 'Tipps speichern'. Returns True on success."""
    try:
        submit_btn = page.locator('[name="submitbutton"]').last
        await submit_btn.evaluate("btn => btn.click()")
        await page.wait_for_load_state("networkidle")
        logging.info("Tipps gespeichert (form submitted)")
        return True
    except Exception as exc:
        logging.error(f"Submit button click failed: {exc}")
        return False


# ─── Sonderfragen (special questions) ────────────────────────────────────────


async def scrape_sonderfragen(page) -> list[dict]:
    """
    Find all <select> elements on the current page (Sonderfragen dropdowns).
    Returns list of {name, question, options, current_value}.
    """
    result = await page.evaluate("""
        () => Array.from(document.querySelectorAll('select')).map(s => {
            let tr = s.closest('tr');
            let question = '';
            if (tr) {
                let clone = tr.cloneNode(true);
                clone.querySelectorAll('select,input,button').forEach(el => el.remove());
                question = clone.textContent.trim().replace(/\\s+/g, ' ');
            }
            return {
                name: s.name || '',
                currentValue: s.value || '',
                question: question,
                options: Array.from(s.options).map(o => ({
                    value: o.value,
                    text: o.text.trim()
                }))
            };
        })
    """)
    logging.info(f"Found {len(result)} Sonderfragen select(s)")
    return result


def _classify_question(question: str) -> tuple[str, str]:
    """
    Returns (question_type, group_letter).
    question_type ∈ {champion, semifinalist, top_scorer_team, group_winner, unknown}
    """
    q = question.lower()
    if "weltmeister" in q:
        return "champion", ""
    if "halbfinale" in q:
        return "semifinalist", ""
    if "meisten toren" in q or "torschütze" in q or "torjäger" in q:
        return "top_scorer_team", ""
    for letter in "ABCDEFGHIJKL":
        if f"gruppe {letter.lower()}" in q:
            return "group_winner", letter
    return "unknown", ""


def _find_option(
    canonical_name: str, options: list[dict], aliases: dict[str, str] = {}
) -> dict | None:
    """
    Find the <option> whose text resolves (via teams registry) to canonical_name.
    Falls back to case-insensitive substring match.
    """
    if not canonical_name:
        return None
    target = canonical_name.lower()
    for opt in options:
        text = opt["text"].strip()
        if not text or opt["value"] == "":
            continue
        canon = _canonical_en(_resolve_team(text)).lower()
        if canon == target:
            return opt
    # fallback: case-insensitive text match
    for opt in options:
        text = opt["text"].strip()
        if not text or opt["value"] == "":
            continue
        if text.lower() == target:
            return opt
    # fallback: substring match
    for opt in options:
        text = opt["text"].strip()
        if not text or opt["value"] == "":
            continue
        if target in text.lower() or text.lower() in target:
            return opt
    return None


def plan_sonderfragen(
    sf_rows: list[dict],
    tournament: dict,
    aliases: dict[str, str] = {},
    overwrite: bool = False,
) -> list[dict]:
    """
    Map Sonderfragen selects to answers.
    Returns list of action dicts with {name, question, canonical, option, action, reason}.
    Uses a counter per type to assign the 4 Halbfinale slots in order.
    """
    group_winners = tournament.get("group_winners", {})
    champion = tournament.get("champion", "")
    semifinalists = tournament.get("semifinalists", [])
    top_scorer_team = tournament.get("top_scorer_team", "")

    semifinalist_idx = 0
    actions = []

    for row in sf_rows:
        current_val = row.get("currentValue", "").strip()
        # Treat as unanswered if value maps to the "not tipped" placeholder option
        placeholder_values: set[str] = {
            opt["value"]
            for opt in row.get("options", [])
            if not opt["value"] or "nicht getippt" in opt["text"].lower() or opt["text"].strip() == ""
        }
        already = bool(current_val) and current_val not in placeholder_values
        if already and not overwrite:
            actions.append({
                **row,
                "canonical": "",
                "option": None,
                "action": "skip_tipped",
                "reason": f"already answered (OVERWRITE=false)",
            })
            continue

        qtype, letter = _classify_question(row["question"])

        if qtype == "champion":
            canonical = champion
        elif qtype == "top_scorer_team":
            canonical = top_scorer_team
        elif qtype == "semifinalist":
            if semifinalist_idx < len(semifinalists):
                canonical = semifinalists[semifinalist_idx]
                semifinalist_idx += 1
            else:
                canonical = ""
        elif qtype == "group_winner":
            # Pick the strongest team among the actual dropdown options
            # (avoids depending on FIFA vs. algorithmic group label order)
            team_strength = tournament.get("team_strength", {})
            best_opt = None
            best_xp = -1.0
            for opt in row["options"]:
                text = opt["text"].strip()
                if not text or "nicht getippt" in text.lower():
                    continue
                canon = _canonical_en(_resolve_team(text))
                xp = team_strength.get(canon, 0.0)
                if xp > best_xp:
                    best_xp = xp
                    best_opt = opt
                    canonical = canon
            if best_opt:
                actions.append({
                    **row,
                    "canonical": canonical,
                    "option": best_opt,
                    "action": "answer",
                    "reason": f"strongest in group (xP={best_xp:.3f})",
                })
                continue
            canonical = ""
        else:
            canonical = ""

        if not canonical:
            actions.append({
                **row,
                "canonical": "",
                "option": None,
                "action": "skip_no_prediction",
                "reason": f"no prediction for question type '{qtype}'",
            })
            continue

        option = _find_option(canonical, row["options"], aliases)
        if option:
            actions.append({
                **row,
                "canonical": canonical,
                "option": option,
                "action": "answer",
                "reason": "",
            })
        else:
            actions.append({
                **row,
                "canonical": canonical,
                "option": None,
                "action": "skip_no_option",
                "reason": f"no matching option for '{canonical}' in dropdown",
            })

    return actions


async def apply_sonderfragen(
    page, sf_actions: list[dict], dry_run: bool
) -> dict[str, int]:
    """Fill Sonderfragen selects. Returns counts: answered/skipped/errors."""
    counts = {"answered": 0, "skipped": 0, "errors": 0}

    for act in sf_actions:
        q_short = act["question"][:55]
        if act["action"] != "answer":
            logging.info(
                f"SF-SKIP  {q_short!r} — {act['action']}: {act['reason']}"
            )
            counts["skipped"] += 1
            continue

        tag = "SF-DRY" if dry_run else "SF-TIP"
        logging.info(
            f"{tag}  {q_short!r} → {act['option']['text']}"
            f"  (canonical: {act['canonical']})"
        )
        if not dry_run:
            try:
                await page.locator(
                    f'select[name="{act["name"]}"]'
                ).select_option(value=act["option"]["value"])
                counts["answered"] += 1
            except Exception as exc:
                logging.error(f"Error filling Sonderfrage {q_short!r}: {exc}")
                counts["errors"] += 1
        else:
            counts["answered"] += 1

    return counts


# ─── Optional ntfy push ───────────────────────────────────────────────────────


def send_ntfy(topic: str, title: str, message: str) -> None:
    try:
        import requests as _req

        _req.post(
            f"https://ntfy.sh/{topic}",
            data=message.encode(),
            headers={"Title": title, "Priority": "default"},
            timeout=5,
        )
        logging.info(f"ntfy push sent to topic '{topic}'")
    except Exception as exc:
        logging.warning(f"ntfy push failed: {exc}")


# ─── Main ─────────────────────────────────────────────────────────────────────


async def run(args) -> int:
    load_dotenv()

    email = os.environ.get("KICKTIPP_EMAIL", "")
    password = os.environ.get("KICKTIPP_PASSWORD", "")
    competition = args.competition or os.environ.get("KICKTIPP_COMPETITION", "")
    ntfy_topic = os.environ.get("NTFY_TOPIC", "")
    # OVERWRITE=true forces re-tipping changed games regardless of window (manual runs).
    force_overwrite = args.force_overwrite or os.environ.get("OVERWRITE", "false").lower() == "true"

    if not email or not password:
        logging.error("KICKTIPP_EMAIL and KICKTIPP_PASSWORD must be set (env or .env)")
        return 1
    if not competition:
        logging.error(
            "Competition name required: set KICKTIPP_COMPETITION or pass --competition"
        )
        return 1

    data_path = Path(args.data)
    if not data_path.exists():
        logging.error(f"data.json not found: {data_path}")
        return 1

    with data_path.open() as fh:
        data = json.load(fh)
    matches = data["matches"]
    tournament = data.get("tournament", {})

    dry_run = not args.submit
    if dry_run:
        logging.info("=== DRY-RUN mode — no tips will be entered (pass --submit to go live) ===")

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logging.error("playwright not installed — run: pip install playwright && playwright install chromium")
        return 1

    SCREENSHOT_DIR.mkdir(exist_ok=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=not args.headed)
        page = await browser.new_page()
        try:
            await login(page, email, password)
            rows = await scrape_game_rows(page, competition)
            sf_rows = await scrape_sonderfragen(page)

            now = datetime.now(timezone.utc)
            actions = plan_submissions(
                rows=rows,
                matches=matches,
                now=now,
                force_overwrite=force_overwrite,
            )
            sf_actions = plan_sonderfragen(
                sf_rows=sf_rows,
                tournament=tournament,
                overwrite=force_overwrite,
            )

            tip_counts = await apply_tips(page, competition, actions, dry_run)
            sf_counts = await apply_sonderfragen(page, sf_actions, dry_run)

            tipped = [a for a in actions if a["action"] == "tip"]
            sf_answered = [a for a in sf_actions if a["action"] == "answer"]
            missed = [a for a in actions if a["action"] in ("skip_too_late", "skip_closed")]

            submitted = False
            if not dry_run and (tipped or sf_answered):
                submitted = await submit_tippabgabe(page)
                if not submitted:
                    tip_counts["errors"] += 1

            # Persist which games are now tipped (gate uses this to avoid re-spinning)
            if not dry_run:
                state = load_submit_state()
                for a in actions:
                    if a["action"] in ("tip", "skip_tipped", "skip_unchanged"):
                        state[_state_key(a["kicktipp_home"], a["kicktipp_away"])] = {
                            "tipped": True,
                            "last_tip": f"{a['tip']['home']}:{a['tip']['away']}" if a.get("tip") else None,
                            "updated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        }
                save_submit_state(state)

            mode_label = "DRY-RUN" if dry_run else "SUBMITTED"
            summary = (
                f"{mode_label}: {len(tipped)} tip(s) "
                f"({sum(1 for a in tipped if 'freshness' in a['reason'])} freshness-overwrite), "
                f"{len(sf_answered)} Sonderfrage(n), "
                f"{tip_counts['skipped']} skips, "
                f"{len(missed)} too-late, "
                f"{tip_counts['errors'] + sf_counts['errors']} error(s)"
            )
            logging.info(summary)

            # ── ntfy: tips entered ───────────────────────────────────────────
            if ntfy_topic and not dry_run and (tip_counts["tipped"] > 0 or sf_counts["answered"] > 0):
                lines = [
                    f"{a['kicktipp_home']} {a['tip']['home']}:{a['tip']['away']} {a['kicktipp_away']}"
                    f"{'  ⟳' if 'freshness' in a['reason'] else ''}"
                    for a in tipped
                ]
                sf_lines = [
                    f"Sonderfrage: {a['question'][:40]} → {a['option']['text']}"
                    for a in sf_answered
                ]
                send_ntfy(
                    ntfy_topic, "WM 2026 Kicktipp — Tipps gesetzt",
                    summary + "\n" + "\n".join(lines + sf_lines)
                )

            # ── Coverage check: due games with NO Kicktipp row (collapsed/other
            #    matchday view) — would otherwise be missed silently ──────────
            scraped_keys = {
                (_resolve_team(r["home"]), _resolve_team(r["away"])) for r in rows
            }
            state_now = load_submit_state()
            tipped_keys = {
                tuple(k.split(":")) for k, v in state_now.items() if v.get("tipped")
            }
            uncovered = uncovered_due_matches(matches, scraped_keys, tipped_keys, now)
            for m in uncovered:
                logging.warning(
                    "UNCOVERED: %s vs %s is due but no Kicktipp row found "
                    "(matchday collapsed / different view?)",
                    m["home_team"], m["away_team"],
                )

            # ── ntfy ALERT: game not tipped in time OR not found → manual action ─
            if ntfy_topic and (missed or uncovered or tip_counts["errors"] > 0):
                alert_lines = [
                    f"⚠️ {a['kicktipp_home']} vs {a['kicktipp_away']} — {a['reason']}"
                    for a in missed
                ]
                alert_lines += [
                    f"❓ {m['home_team']} vs {m['away_team']} — fällig, aber keine "
                    f"Kicktipp-Zeile gefunden (Spieltag eingeklappt?)"
                    for m in uncovered
                ]
                if tip_counts["errors"] > 0:
                    alert_lines.append(f"⚠️ {tip_counts['errors']} Eintrag-Fehler beim Absenden")
                send_ntfy(
                    ntfy_topic, "⚠️ WM 2026 Kicktipp — manuell eingreifen!",
                    "Diese Spiele konnten NICHT (rechtzeitig) getippt werden:\n"
                    + "\n".join(alert_lines),
                )

        except Exception as exc:
            logging.error(f"Fatal: {exc}")
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            sc = SCREENSHOT_DIR / f"error_{ts}.png"
            await page.screenshot(path=str(sc))
            logging.info(f"Screenshot saved: {sc}")
            await browser.close()
            return 1

        await browser.close()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Submit Kicktipp tips from docs/data.json. Default: --dry-run."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        dest="submit",
        action="store_false",
        default=False,
        help="Log planned tips only, no browser interaction beyond scraping (default)",
    )
    mode.add_argument(
        "--submit",
        dest="submit",
        action="store_true",
        help="Actually fill and submit tips",
    )
    parser.add_argument("--headed", action="store_true", help="Show browser window (debugging)")
    parser.add_argument(
        "--competition",
        default="",
        metavar="NAME",
        help="Kicktipp competition slug (overrides KICKTIPP_COMPETITION env)",
    )
    parser.add_argument(
        "--data",
        default="docs/data.json",
        metavar="PATH",
        help="Path to data.json (default: docs/data.json)",
    )
    parser.add_argument(
        "--force-overwrite",
        action="store_true",
        help="Overwrite already-tipped games with the current tip regardless of the "
             "freshness window (manual runs). Unchanged tips are still skipped.",
    )
    parser.add_argument(
        "--deadline-buffer",
        type=float,
        default=DEFAULT_DEADLINE_BUFFER_HOURS,
        metavar="HOURS",
        help="(Deprecated — timing now derives from each game's actual deadline.)",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
