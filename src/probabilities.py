"""
Margin removal, weighted consensus, and divergence calculation.
All functions are pure and individually testable.
"""

import math
from typing import TypedDict

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import config


class BookmakerProbs(TypedDict):
    key: str
    title: str
    last_update: str
    weight: float
    raw_odds: dict[str, float]
    overround: float
    probabilities: dict[str, float]


class ConsensusResult(TypedDict):
    bookmakers: list[BookmakerProbs]
    consensus: dict[str, float]
    divergence: dict[str, float]
    totals_line: float | None
    totals_over_prob: float | None


def remove_margin_multiplicative(odds: dict[str, float]) -> tuple[dict[str, float], float]:
    """
    Apply multiplicative margin removal to a set of decimal odds.
    Returns (normalised_probabilities, overround).
    """
    implied = {outcome: 1.0 / price for outcome, price in odds.items()}
    booksum = sum(implied.values())
    overround = booksum - 1.0
    probs = {outcome: q / booksum for outcome, q in implied.items()}
    return probs, overround


def _bookmaker_weight(key: str, overround: float, use_margin_weight: bool = False) -> float:
    """
    Resolve weight for a bookmaker key.
    If use_margin_weight is True, weight = 1/overround (tighter margin = higher weight),
    combined multiplicatively with any manual weight from config.
    """
    manual = config.BOOKMAKER_WEIGHTS.get(key, config.DEFAULT_BOOKMAKER_WEIGHT)
    if use_margin_weight and overround > 0:
        return manual * (1.0 / overround)
    return manual


def weighted_consensus(
    bookmaker_probs: list[BookmakerProbs],
) -> dict[str, float]:
    """
    Weighted average of normalised probabilities across bookmakers.
    Outcomes: 'home', 'draw', 'away'.
    Result already sums to 1 — no re-normalisation needed.
    """
    outcomes = ("home", "draw", "away")
    weighted_sum = {o: 0.0 for o in outcomes}
    total_weight = 0.0

    for book in bookmaker_probs:
        w = book["weight"]
        for o in outcomes:
            weighted_sum[o] += w * book["probabilities"].get(o, 0.0)
        total_weight += w

    if total_weight == 0:
        return {"home": 1 / 3, "draw": 1 / 3, "away": 1 / 3}

    return {o: round(weighted_sum[o] / total_weight, 4) for o in outcomes}


def divergence(bookmaker_probs: list[BookmakerProbs]) -> dict[str, float]:
    """
    Standard deviation of bookmaker probabilities per outcome.
    High value → books disagree → potential value / uncertainty.
    """
    if len(bookmaker_probs) < 2:
        return {"home": 0.0, "draw": 0.0, "away": 0.0}

    outcomes = ("home", "draw", "away")
    result = {}
    for o in outcomes:
        values = [b["probabilities"].get(o, 0.0) for b in bookmaker_probs]
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        result[o] = round(math.sqrt(variance), 4)
    return result


def _parse_h2h_market(market: dict, home_team: str, away_team: str) -> dict[str, float] | None:
    """Extract decimal odds keyed as home/draw/away from a h2h market dict."""
    raw: dict[str, float] = {}
    for outcome in market.get("outcomes", []):
        name = outcome.get("name", "")
        price = outcome.get("price")
        if price is None:
            continue
        if name == home_team:
            raw["home"] = price
        elif name == away_team:
            raw["away"] = price
        elif name == "Draw":
            raw["draw"] = price

    if len(raw) != 3:
        return None
    return raw


def _parse_totals_market(market: dict) -> tuple[float | None, float | None]:
    """
    Return (line, over_probability) from a totals market.
    Uses the first Over outcome found.
    """
    for outcome in market.get("outcomes", []):
        if outcome.get("name") == "Over":
            line = outcome.get("point")
            price = outcome.get("price")
            if line is not None and price is not None:
                over_prob = round(1.0 / price, 4)
                return float(line), over_prob
    return None, None


def process_match(match: dict) -> ConsensusResult:
    """
    Full pipeline for one match dict from the API response.
    Returns structured probabilities ready for data.json.
    """
    home_team = match["home_team"]
    away_team = match["away_team"]

    bookmaker_results: list[BookmakerProbs] = []
    totals_line: float | None = None
    totals_over_probs: list[float] = []

    for book in match.get("bookmakers", []):
        book_key = book.get("key", "")
        book_title = book.get("title", "")
        last_update = book.get("last_update", "")

        h2h_odds: dict[str, float] | None = None
        for market in book.get("markets", []):
            if market["key"] == "h2h":
                h2h_odds = _parse_h2h_market(market, home_team, away_team)
            elif market["key"] == "totals":
                line, over_prob = _parse_totals_market(market)
                if line is not None:
                    totals_line = line
                if over_prob is not None:
                    totals_over_probs.append(over_prob)

        if h2h_odds is None:
            continue

        probs, overround = remove_margin_multiplicative(h2h_odds)
        weight = _bookmaker_weight(book_key, overround)

        bookmaker_results.append(BookmakerProbs(
            key=book_key,
            title=book_title,
            last_update=last_update,
            weight=weight,
            raw_odds=h2h_odds,
            overround=round(overround, 4),
            probabilities={k: round(v, 4) for k, v in probs.items()},
        ))

    consensus = weighted_consensus(bookmaker_results)
    div = divergence(bookmaker_results)

    avg_over_prob = (
        round(sum(totals_over_probs) / len(totals_over_probs), 4)
        if totals_over_probs
        else None
    )

    return ConsensusResult(
        bookmakers=bookmaker_results,
        consensus=consensus,
        divergence=div,
        totals_line=totals_line,
        totals_over_prob=avg_over_prob,
    )
