"""
The Odds API v4 client.
Use --mock to load data/mock_response.json instead of making a real API call.
"""

import argparse
import json
import logging
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
import config

load_dotenv()
logger = logging.getLogger(__name__)

MOCK_PATH = Path(__file__).parent.parent / "data" / "mock_response.json"


def _log_quota(response: requests.Response) -> None:
    remaining = response.headers.get("x-requests-remaining", "?")
    used = response.headers.get("x-requests-used", "?")
    logger.info("Odds API quota — used: %s, remaining: %s", used, remaining)


def fetch_odds(mock: bool = False) -> list[dict]:
    if mock:
        logger.info("Mock mode: loading %s", MOCK_PATH)
        with open(MOCK_PATH, encoding="utf-8") as f:
            return json.load(f)

    api_key = os.getenv("ODDS_API_KEY")
    if not api_key:
        raise RuntimeError("ODDS_API_KEY not set in environment / .env")

    url = f"{config.ODDS_API_BASE_URL}/sports/{config.SPORT_KEY}/odds"
    params = {
        "apiKey": api_key,
        "regions": config.ODDS_API_REGIONS,
        "markets": config.ODDS_API_MARKETS,
        "oddsFormat": config.ODDS_API_FORMAT,
    }

    for attempt in range(1, 4):
        try:
            response = requests.get(url, params=params, timeout=15)
            if response.status_code == 429:
                wait = 2 ** attempt
                logger.warning("Rate-limited (429). Waiting %ds before retry %d/3…", wait, attempt)
                time.sleep(wait)
                continue
            response.raise_for_status()
            _log_quota(response)
            return response.json()
        except requests.exceptions.Timeout:
            logger.error("Request timed out (attempt %d/3)", attempt)
            if attempt == 3:
                raise
            time.sleep(2)
        except requests.exceptions.HTTPError as exc:
            logger.error("HTTP error: %s", exc)
            raise

    raise RuntimeError("Failed to fetch odds after 3 attempts")


def verify_sport_key(mock: bool = False) -> str | None:
    """
    Check /v4/sports for a World Cup entry and return its key.
    Use this to confirm the sport_key at runtime.
    """
    if mock:
        logger.info("Mock mode: skipping sport key verification, using config value.")
        return config.SPORT_KEY

    api_key = os.getenv("ODDS_API_KEY")
    if not api_key:
        raise RuntimeError("ODDS_API_KEY not set")

    url = f"{config.ODDS_API_BASE_URL}/sports/"
    response = requests.get(url, params={"apiKey": api_key, "all": "true"}, timeout=10)
    response.raise_for_status()

    sports = response.json()
    matches = [s for s in sports if "world cup" in s.get("title", "").lower()]
    if matches:
        key = matches[0]["key"]
        logger.info("Found World Cup sport key: %s", key)
        return key

    logger.warning("No World Cup sport found in /sports/ response.")
    return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Fetch WM 2026 odds")
    parser.add_argument("--mock", action="store_true", help="Use local mock response")
    args = parser.parse_args()

    data = fetch_odds(mock=args.mock)
    print(json.dumps(data, indent=2))
    print(f"\n→ {len(data)} match(es) fetched.")
