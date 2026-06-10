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

from dotenv import load_dotenv

# ─── Constants ────────────────────────────────────────────────────────────────

BASE_URL = "https://www.kicktipp.de"
LOGIN_URL = f"{BASE_URL}/info/profil/login"
DEFAULT_DEADLINE_BUFFER_HOURS = 2.0
SCREENSHOT_DIR = Path("screenshots")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)

# ─── Pure matching logic (no browser — fully unit-testable) ───────────────────


def canonicalize(name: str, aliases: dict[str, str]) -> str:
    """Apply alias map; return canonical team name (uanalyse spelling)."""
    return aliases.get(name, name)


def build_prediction_index(
    matches: list[dict], aliases: dict[str, str]
) -> dict[tuple[str, str], dict]:
    """Return (canonical_home, canonical_away) → match dict."""
    return {
        (canonicalize(m["home_team"], aliases), canonicalize(m["away_team"], aliases)): m
        for m in matches
    }


def match_row(
    kicktipp_home: str,
    kicktipp_away: str,
    index: dict[tuple[str, str], dict],
    aliases: dict[str, str],
) -> dict | None:
    """Find prediction for a kicktipp row; return match dict or None."""
    key = (canonicalize(kicktipp_home, aliases), canonicalize(kicktipp_away, aliases))
    return index.get(key)


def decide_action(
    home_value: str,
    away_value: str,
    prediction: dict | None,
    overwrite: bool,
    now: datetime,
    buffer_h: float,
) -> tuple[str, str]:
    """
    Determine what to do for one game row.

    Returns (action, reason) where action ∈ {
        "tip", "skip_no_match", "skip_tipped", "skip_deadline"
    }
    """
    if prediction is None:
        return "skip_no_match", "no matching prediction in data.json"

    already_tipped = bool(home_value.strip() or away_value.strip())
    if already_tipped and not overwrite:
        return "skip_tipped", f"already tipped {home_value}:{away_value} (OVERWRITE=false)"

    ct = prediction.get("commence_time", "")
    if ct:
        try:
            if "T" in ct:
                kickoff = datetime.fromisoformat(ct.replace("Z", "+00:00"))
            else:
                kickoff = datetime.fromisoformat(ct).replace(tzinfo=timezone.utc)
            if (kickoff - now) < timedelta(hours=buffer_h):
                return (
                    "skip_deadline",
                    f"kickoff {ct} is within {buffer_h}h deadline buffer",
                )
        except ValueError:
            pass

    return "tip", ""


def plan_submissions(
    rows: list[dict],
    matches: list[dict],
    aliases: dict[str, str],
    overwrite: bool = False,
    now: datetime | None = None,
    buffer_h: float = DEFAULT_DEADLINE_BUFFER_HOURS,
) -> list[dict]:
    """
    Pure function: map kicktipp rows to actions.

    Each row dict: {home, away, home_value, away_value, home_input, away_input}
    Returns list of action dicts (same keys + action, reason, tip).
    """
    if now is None:
        now = datetime.now(timezone.utc)

    index = build_prediction_index(matches, aliases)
    result = []

    for row in rows:
        prediction = match_row(row["home"], row["away"], index, aliases)
        action, reason = decide_action(
            row.get("home_value", ""),
            row.get("away_value", ""),
            prediction,
            overwrite,
            now,
            buffer_h,
        )
        result.append(
            {
                "kicktipp_home": row["home"],
                "kicktipp_away": row["away"],
                "home_input": row.get("home_input", ""),
                "away_input": row.get("away_input", ""),
                "action": action,
                "reason": reason,
                "tip": prediction["recommended_tip"] if prediction and action == "tip" else None,
            }
        )

    return result


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

            home_name = (await tr.locator("td").nth(1).inner_text()).strip()
            away_name = (await tr.locator("td").nth(2).inner_text()).strip()
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

    if not dry_run and tipped_actions:
        try:
            submit_btn = page.locator('[name="submitbutton"]')
            await submit_btn.evaluate("btn => btn.click()")
            await page.wait_for_load_state("networkidle")
            logging.info(f"Page submitted successfully ({len(tipped_actions)} tip(s))")
        except Exception as exc:
            logging.error(f"Submit button click failed: {exc}")
            counts["errors"] += 1

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
    overwrite = os.environ.get("OVERWRITE", "false").lower() == "true"

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

    dry_run = not args.submit
    if dry_run:
        logging.info("=== DRY-RUN mode — no tips will be entered (pass --submit to go live) ===")

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logging.error("playwright not installed — run: pip install playwright && playwright install chromium")
        return 1

    import config

    SCREENSHOT_DIR.mkdir(exist_ok=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=not args.headed)
        page = await browser.new_page()
        try:
            await login(page, email, password)
            rows = await scrape_game_rows(page, competition)

            now = datetime.now(timezone.utc)
            actions = plan_submissions(
                rows=rows,
                matches=matches,
                aliases=config.TEAM_ALIASES,
                overwrite=overwrite,
                now=now,
                buffer_h=args.deadline_buffer,
            )

            counts = await apply_tips(page, competition, actions, dry_run)

            tipped = [a for a in actions if a["action"] == "tip"]
            mode_label = "DRY-RUN" if dry_run else "SUBMITTED"
            summary = (
                f"{mode_label}: {len(tipped)} tip(s) planned, "
                f"{counts['skipped']} skipped, {counts['errors']} error(s)"
            )
            logging.info(summary)

            if ntfy_topic and not dry_run and counts["tipped"] > 0:
                lines = [
                    f"{a['kicktipp_home']} vs {a['kicktipp_away']}: "
                    f"{a['tip']['home']}:{a['tip']['away']}"
                    for a in tipped
                ]
                send_ntfy(ntfy_topic, "WM 2026 Kicktipp", summary + "\n" + "\n".join(lines))

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
        "--deadline-buffer",
        type=float,
        default=DEFAULT_DEADLINE_BUFFER_HOURS,
        metavar="HOURS",
        help=f"Skip games starting within N hours (default: {DEFAULT_DEADLINE_BUFFER_HOURS})",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
