"""
Tournament predictions derived from group-stage match data.

All predictions use uanalyse win probabilities as source of truth.
No bracket structure is hard-coded — groups are inferred from the match schedule.
"""

from __future__ import annotations

from collections import defaultdict


# ── Group reconstruction ───────────────────────────────────────────────────────

def _build_groups(matches: list[dict]) -> dict[str, list[str]]:
    """
    Infer group membership from group-stage match schedule.
    Teams that play each other are in the same group (4-clique).
    Returns {'A': ['Argentina', ...], 'B': [...], ...} sorted alphabetically.
    """
    adj: dict[str, set[str]] = defaultdict(set)
    for m in matches:
        if m.get("stage", "").lower().startswith("group"):
            h, a = m["home_team"], m["away_team"]
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

def _team_expected_points(
    team: str, matches: list[dict]
) -> float:
    """3·p_win + 1·p_draw across all group stage games for this team."""
    pts = 0.0
    for m in matches:
        if m.get("stage", "").lower().startswith("group"):
            ua = m.get("sources", {}).get("uanalyse", {})
            p = ua.get("p", {})
            if m["home_team"] == team:
                pts += 3 * p.get("home", 0) + p.get("draw", 0)
            elif m["away_team"] == team:
                pts += 3 * p.get("away", 0) + p.get("draw", 0)
    return pts


def _team_expected_goals(team: str, matches: list[dict]) -> float:
    """Total expected goals scored across all group stage games."""
    xg = 0.0
    for m in matches:
        if m.get("stage", "").lower().startswith("group"):
            eg = m.get("expected_goals", {})
            if m["home_team"] == team:
                xg += eg.get("home", 0)
            elif m["away_team"] == team:
                xg += eg.get("away", 0)
    return xg


# ── Public prediction API ──────────────────────────────────────────────────────

def predict_group_winners(matches: list[dict]) -> dict[str, str]:
    """
    Returns {'A': 'Brazil', 'B': 'France', ...} — one predicted winner per group.
    Winner = team with highest expected points in group stage.
    """
    groups = _build_groups(matches)
    winners: dict[str, str] = {}
    for label, teams in groups.items():
        best = max(teams, key=lambda t: _team_expected_points(t, matches))
        winners[label] = best
    return winners


def predict_champion(matches: list[dict]) -> str:
    """
    Predicted WM champion = team with highest expected points overall.
    Simple proxy without full bracket simulation.
    """
    groups = _build_groups(matches)
    all_teams = [t for g in groups.values() for t in g]
    return max(all_teams, key=lambda t: _team_expected_points(t, matches))


def predict_semifinalists(matches: list[dict]) -> list[str]:
    """
    Predicted 4 semifinalists = top 4 teams by expected points, sorted descending.
    """
    groups = _build_groups(matches)
    all_teams = [t for g in groups.values() for t in g]
    ranked = sorted(all_teams, key=lambda t: _team_expected_points(t, matches), reverse=True)
    return ranked[:4]


def predict_top_scorer_team(matches: list[dict]) -> str:
    """
    Team most likely to provide the top scorer = highest total expected goals.
    """
    groups = _build_groups(matches)
    all_teams = [t for g in groups.values() for t in g]
    return max(all_teams, key=lambda t: _team_expected_goals(t, matches))


def build_team_strength(matches: list[dict]) -> dict[str, float]:
    """
    Returns {canonical_team: expected_group_stage_points} for all 48 teams.
    Used by kicktipp_submit to pick the strongest team from any dropdown.
    """
    groups = _build_groups(matches)
    all_teams = [t for g in groups.values() for t in g]
    return {t: _team_expected_points(t, matches) for t in all_teams}


def build_tournament_predictions(matches: list[dict]) -> dict:
    """
    Builds the full tournament prediction block for data.json.
    """
    return {
        "group_winners": predict_group_winners(matches),
        "champion": predict_champion(matches),
        "semifinalists": predict_semifinalists(matches),
        "top_scorer_team": predict_top_scorer_team(matches),
        "team_strength": build_team_strength(matches),
    }
