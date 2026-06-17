"""
Central configuration: API settings, bookmaker weights, Kicktipp scoring rules.
Edit KICKTIPP_RULES to match your league's actual point system.
"""

SPORT_KEY = "soccer_fifa_world_cup"
ODDS_API_BASE_URL = "https://api.the-odds-api.com/v4"
ODDS_API_REGIONS = "eu"
ODDS_API_MARKETS = "h2h,totals"
ODDS_API_FORMAT = "decimal"

# Sharp/trusted books get higher weight; unknown books default to 1.0.
# If Pinnacle/Betfair are not in the actual API response, all books get equal weight.
BOOKMAKER_WEIGHTS: dict[str, float] = {
    "pinnacle": 3.0,
    "betfair_ex_eu": 2.5,
    "betfair_ex_uk": 2.5,
    "sport888": 1.5,
    "unibet_eu": 1.2,
}
DEFAULT_BOOKMAKER_WEIGHT = 1.0

# --- Kicktipp scoring rules ---
# Win and draw are scored differently: draws have no Tordifferenz tier.
# *** HIER DEINE LIGAREGELN EINTRAGEN falls sie abweichen ***
KICKTIPP_POINTS = {
    "win":  {"tendency": 2, "goal_diff": 3, "exact": 4},
    "draw": {"tendency": 2, "exact": 4},  # keine goal_diff-Stufe bei Remis
}


def kicktipp_points(tip: tuple[int, int], real: tuple[int, int],
                    rules: dict = KICKTIPP_POINTS) -> int:
    """tip, real je (heim, auswaerts). Gibt erzielte Punkte zurueck."""
    ta, tb = tip
    ra, rb = real
    tip_sign  = (ta > tb) - (ta < tb)
    real_sign = (ra > rb) - (ra < rb)
    if tip_sign != real_sign:
        return 0                      # falsche Tendenz
    if real_sign == 0:                # reales Remis
        return rules["draw"]["exact"] if (ta, tb) == (ra, rb) else rules["draw"]["tendency"]
    # realer Sieg, Tendenz stimmt
    if (ta, tb) == (ra, rb):
        return rules["win"]["exact"]
    if (ta - tb) == (ra - rb):
        return rules["win"]["goal_diff"]
    return rules["win"]["tendency"]

# Blend market (bookmaker consensus) + uanalyse into the recommended tip via
# logit pooling + λ-calibration (src/weighting.py). False = uanalyse-only tips.
ENABLE_BLEND = True

# Poisson matrix upper bound per team (scores 0..MAX_GOALS inclusive)
MAX_GOALS = 7

# ---------------------------------------------------------------------------
# Scoreline calibration (Phase B — post-mortem of match days 1–3)
# ---------------------------------------------------------------------------
# Empirical finding over the first 20 games: the model's λ_total was already
# well calibrated (Ø 3.19 predicted vs 3.00 realised), but the EV-optimiser
# shrank the *tipped* scoreline toward 1:0/0:0 (Ø 1.45 goals) and so missed the
# goal-difference tier on clear favourite wins. κ and the variance dial below
# are two handles on that decision-rule conservatism — not a fix of λ.

# κ — goal-level scaling applied to λ_total before the Poisson matrix is built.
# Static base de-shrinks the tip; the adaptive term nudges it from the running
# realised/predicted goal ratio in the snapshot store (heavily shrunk, bounded).
GOAL_SCALE_KAPPA = 1.15            # static base scaling (1.0 = off)
ENABLE_ADAPTIVE_KAPPA = True       # blend base with realised/predicted ratio
KAPPA_BOUNDS = (1.0, 1.5)          # never shrink goals; cap the stretch
KAPPA_MIN_SETTLED = 6              # settled matches before adaptive κ engages
KAPPA_SHRINK = 0.4                 # weight of the empirical ratio vs the base

# ρ — Dixon & Coles (1997) low-score dependence. ρ<0 lifts the 0:0/1:1 cells
# (draw mass). In-sample effect on MD1–3 ≈ 0 (the draws were market upsets) but
# it is the literature-standard guard against long-run draw under-dispersion.
ENABLE_DIXON_COLES = True
DIXON_COLES_RHO = -0.10

# Risk dial ("Rang statt EV") — upside-weighted tip selection.
# objective = E[pts] + γ · P(exact hit) · exact_points. γ=0 → pure EV-optimal.
# γ>0 pulls the tip toward the most probable scoreline, trading a little
# tendency-safety for exact-hit upside (4 pts). Backtest over the first 20
# games: the safe plateau is γ≈0.5–1.0 (+2 pts, +1 exact hit, no forced draws);
# γ≥1.5 starts forcing draw/exact gambles and degrades. 0.75 sits mid-plateau.
# Pool literature: Kaplan & Garstka (2001), Clair & Letscher (2007).
VARIANCE_AGGRESSION = 0.75

# Divergence threshold above which the "Bücher uneinig" badge is shown
DIVERGENCE_BADGE_THRESHOLD = 0.04

# ---------------------------------------------------------------------------
# Data sources
# ---------------------------------------------------------------------------

UANALYSE_CSV_URL = (
    "https://raw.githubusercontent.com/uanalyse/world-cup-2026-predictions"
    "/main/data/latest/match_predictions.csv"
)

UANALYSE_TOURNAMENT_URL = (
    "https://raw.githubusercontent.com/uanalyse/world-cup-2026-predictions"
    "/main/data/latest/tournament_probabilities.csv"
)

# Team name resolution is handled by src/teams.py (canonical registry).
# Use teams.resolve(name) → FIFA code, teams.canonical_en(code) → English name.
# TEAM_ALIASES is kept as empty dict for any remaining backward-compat references.
TEAM_ALIASES: dict[str, str] = {}
