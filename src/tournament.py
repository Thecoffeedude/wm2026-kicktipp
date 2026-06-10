"""
Tournament predictions derived from group-stage match data.

All predictions use uanalyse win probabilities as source of truth.
No bracket structure is hard-coded — groups are inferred from the match schedule.
Teams are identified by FIFA code (home_code/away_code) throughout.
"""

from __future__ import annotations

from collections import defaultdict

from src.teams import canonical_en


# ── Group reconstruction ───────────────────────────────────────────────────────

def _build_groups(matches: list[dict]) -> dict[str, list[str]]:
    """
    Infer group membership from group-stage match schedule using FIFA codes.
    Teams that play each other are in the same group (4-clique).
    Returns {'A': ['ARG', ...], 'B': [...], ...} sorted alphabetically by group.
    """
    adj: dict[str, set[str]] = defaultdict(set)
    for m in matches:
        if m.get("stage", "").lower().startswith("group"):
            h = m.get("home_code") or m["home_team"]
            a = m.get("away_code") or m["away_team"]
            adj[h].add(a)
            adj[a].add(h)

    seen: set[str] = set()
    groups: list[list[str]] = []
    for team in sorted(adj.keys()):
        if team not in seen:
            group = sorted({team} | adj[team])
            if len(group) == 4:
                groups.append(group)
                seen.update(group)

    groups.sort(key=lambda g: g[0])
    return {chr(65 + i): g for i, g in enumerate(groups)}


# ── Per-team expected points in group stage ────────────────────────────────────

def _team_expected_points(code: str, matches: list[dict]) -> float:
    """3·p_win + 1·p_draw across all group stage games for this team (by code)."""
    pts = 0.0
    for m in matches:
        if m.get("stage", "").lower().startswith("group"):
            ua = m.get("sources", {}).get("uanalyse", {})
            p = ua.get("p", {})
            h = m.get("home_code") or m["home_team"]
            a = m.get("away_code") or m["away_team"]
            if h == code:
                pts += 3 * p.get("home", 0) + p.get("draw", 0)
            elif a == code:
                pts += 3 * p.get("away", 0) + p.get("draw", 0)
    return pts


def _team_expected_goals(code: str, matches: list[dict]) -> float:
    """Total expected goals scored across all group stage games."""
    xg = 0.0
    for m in matches:
        if m.get("stage", "").lower().startswith("group"):
            eg = m.get("expected_goals", {})
            h = m.get("home_code") or m["home_team"]
            a = m.get("away_code") or m["away_team"]
            if h == code:
                xg += eg.get("home", 0)
            elif a == code:
                xg += eg.get("away", 0)
    return xg


# ── Public prediction API ──────────────────────────────────────────────────────

def predict_group_winners(matches: list[dict]) -> dict[str, str]:
    """
    Returns {'A': 'ARG', 'B': 'BRA', ...} — one predicted winner (code) per group.
    Winner = team with highest expected points in group stage.
    """
    groups = _build_groups(matches)
    winners: dict[str, str] = {}
    for label, codes in groups.items():
        best = max(codes, key=lambda c: _team_expected_points(c, matches))
        winners[label] = best
    return winners


def predict_champion(matches: list[dict]) -> str:
    """Predicted WM champion (FIFA code) = team with highest expected points overall."""
    groups = _build_groups(matches)
    all_codes = [c for g in groups.values() for c in g]
    if not all_codes:
        return ""
    return max(all_codes, key=lambda c: _team_expected_points(c, matches))


def predict_semifinalists(matches: list[dict]) -> list[str]:
    """Top 4 teams (FIFA codes) by expected points, sorted descending."""
    groups = _build_groups(matches)
    all_codes = [c for g in groups.values() for c in g]
    ranked = sorted(all_codes, key=lambda c: _team_expected_points(c, matches), reverse=True)
    return ranked[:4]


def predict_top_scorer_team(matches: list[dict]) -> str:
    """Team (FIFA code) most likely to provide the top scorer = highest total xG."""
    groups = _build_groups(matches)
    all_codes = [c for g in groups.values() for c in g]
    if not all_codes:
        return ""
    return max(all_codes, key=lambda c: _team_expected_goals(c, matches))


def build_team_strength(matches: list[dict]) -> dict[str, float]:
    """
    Returns {canonical_en_name: expected_group_stage_points} for all 48 teams.
    Keyed by canonical_en so kicktipp_submit can look up by dropdown option text.
    """
    groups = _build_groups(matches)
    all_codes = [c for g in groups.values() for c in g]
    return {canonical_en(c): _team_expected_points(c, matches) for c in all_codes}


def build_tournament_predictions(matches: list[dict]) -> dict:
    """Builds the full tournament prediction block for data.json."""
    group_winner_codes = predict_group_winners(matches)
    champion_code      = predict_champion(matches)
    semifinalist_codes = predict_semifinalists(matches)
    top_scorer_code    = predict_top_scorer_team(matches)

    return {
        "group_winners":   {g: canonical_en(c) for g, c in group_winner_codes.items()},
        "champion":        canonical_en(champion_code) if champion_code else "",
        "semifinalists":   [canonical_en(c) for c in semifinalist_codes],
        "top_scorer_team": canonical_en(top_scorer_code) if top_scorer_code else "",
        "team_strength":   build_team_strength(matches),
    }
