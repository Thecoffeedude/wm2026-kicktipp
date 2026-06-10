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

# Poisson matrix upper bound per team (scores 0..MAX_GOALS inclusive)
MAX_GOALS = 7

# Divergence threshold above which the "Bücher uneinig" badge is shown
DIVERGENCE_BADGE_THRESHOLD = 0.04

# ---------------------------------------------------------------------------
# Data sources
# ---------------------------------------------------------------------------

UANALYSE_CSV_URL = (
    "https://raw.githubusercontent.com/uanalyse/world-cup-2026-predictions"
    "/main/data/latest/match_predictions.csv"
)

# Canonical team names follow the uanalyse spelling (primary source).
# Map any variant (Odds API, other feeds) → canonical here.
# Add entries whenever a live API call returns an unrecognised name.
TEAM_ALIASES: dict[str, str] = {
    # Odds API variants → uanalyse canonical
    "Turkey":                          "Türkiye",
    "Bosnia and Herzegovina":          "Bosnia-Herzegovina",
    "Czech Republic":                  "Czechia",
    "DR Congo":                        "Congo DR",
    "Democratic Republic of Congo":    "Congo DR",
    "Republic of Ireland":             "Ireland",
    "Curacao":                         "Curaçao",
    "USA":                             "United States",
    "Korea Republic":                  "South Korea",
    "Republic of Korea":               "South Korea",
    "DPR Korea":                       "North Korea",
    "Ivory Coast":                     "Ivory Coast",  # same — listed for visibility
    "Cote d'Ivoire":                   "Ivory Coast",

    # Kicktipp German team names → uanalyse canonical
    "Mexiko":                          "Mexico",
    "Südkorea":                        "South Korea",
    "Tschechien":                      "Czechia",
    "Kanada":                          "Canada",
    "Bosnien-Herzegowina":             "Bosnia-Herzegovina",
    "Bosnien und Herzegowina":         "Bosnia-Herzegovina",
    "Katar":                           "Qatar",
    "Brasilien":                       "Brazil",
    "Marokko":                         "Morocco",
    "Schottland":                      "Scotland",
    "Australien":                      "Australia",
    "Türkei":                          "Türkiye",
    "Argentinien":                     "Argentina",
    "Deutschland":                     "Germany",
    "Frankreich":                      "France",
    "Spanien":                         "Spain",
    "Niederlande":                     "Netherlands",
    "Belgien":                         "Belgium",
    "Schweiz":                         "Switzerland",
    "Österreich":                      "Austria",
    "Polen":                           "Poland",
    "Kroatien":                        "Croatia",
    "Serbien":                         "Serbia",
    "Ungarn":                          "Hungary",
    "Dänemark":                        "Denmark",
    "Schweden":                        "Sweden",
    "Norwegen":                        "Norway",
    "Finnland":                        "Finland",
    "Nordmazedonien":                  "North Macedonia",
    "Saudi-Arabien":                   "Saudi Arabia",
    "Südafrika":                       "South Africa",
    "Elfenbeinküste":                  "Ivory Coast",
    "Kamerun":                         "Cameroon",
    "Ägypten":                         "Egypt",
    "Tunesien":                        "Tunisia",
    "Algerien":                        "Algeria",
    "Kolumbien":                       "Colombia",
    "Neuseeland":                      "New Zealand",
    "Jamaika":                         "Jamaica",
    "Ruanda":                          "Rwanda",
    "Demokratische Republik Kongo":    "Congo DR",
    "Kongo":                           "Congo",
    "Nordkorea":                       "North Korea",
    "Vereinigte Arabische Emirate":    "United Arab Emirates",
    "Irak":                            "Iraq",
    "Vereinigte Staaten":              "United States",
}
