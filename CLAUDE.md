# CLAUDE.md — WM 2026 Kicktipp-Prädikator

> Lies diese Datei vollständig bevor du Code schreibst.
> Code-Sprache: Englisch (Bezeichner, Kommentare). UI-Texte: Deutsch.

Das vollständige Projekt-Briefing (Phasenplan, Mathematik, Tech-Stack, Frontend-Spec)
befindet sich im ursprünglichen CLAUDE.md, das beim Projekt-Kickoff als Kontext übergeben
wurde. Dieser Abschnitt dokumentiert die aktuell implementierten Datenquellen.

---

## Datenquellen

### Primärquelle: uanalyse/world-cup-2026-predictions

- **Repo:** https://github.com/uanalyse/world-cup-2026-predictions
- **Lizenz:** CC BY 4.0 — Attribution in Footer des Frontends erforderlich.
- **Datei:** `data/latest/match_predictions.csv` (täglich aktualisiert)
- **Abruf:** `src/fetch_uanalyse.py` per HTTP GET auf die GitHub Raw-URL —
  kein API-Key, kein Quota.
- **Schema (CSV-Felder):**
  ```
  snapshot_date, kickoff_date, stage, fixture_type,
  home_team, away_team, prob_meeting,
  prob_home_win, prob_draw, prob_away_win,
  exp_home_goals, exp_away_goals
  ```
  - `kickoff_date` ist **nur ein Datum** (kein Uhrzeit), z. B. `2026-06-14`.
  - `prob_meeting` ist immer `1.0` → ignorieren.
  - `exp_home_goals` / `exp_away_goals` = λ-Werte direkt für den Poisson-Optimierer.
- **Verwendung:** λ-Werte → `scoreline.py` → `recommended_tip`.
  Wahrscheinlichkeiten → Konsens-Balken im Frontend.

### Sekundärquelle: The Odds API v4

- **Basis-URL:** `https://api.the-odds-api.com/v4`
- **Sport-Key:** `soccer_fifa_world_cup`
- **Secret:** `ODDS_API_KEY` (GitHub Secret, nie in Code oder Logs)
- **Abruf:** `src/fetch_odds.py` — 2 Credits pro Call (h2h + totals, Region eu).
  Free Tier: 500 Credits/Monat. Täglich 1× abrufen reicht.
- **Verwendung:** Per-Buchmacher-Quoten, margenbereinigter Konsens,
  Divergenz-Badge. **Nicht** als Basis für `recommended_tip`.

### Zusammenführung

- Match-Matching via `(canonical_home, canonical_away, kickoff_date)`.
- Team-Alias-Map in `config.TEAM_ALIASES` (uanalyse-Schreibweise = kanonisch).
- Pro Match: `sources.uanalyse` und/oder `sources.odds_consensus` in `data.json`.
- `agreement.same_tendency` = `false` → violettes Badge "⚡ Quellen uneinig" im Frontend.
- Matches nur in Wettbüros (kein uanalyse-Eintrag) → `based_on: "odds_derived"`.

### Mock-Betrieb (Entwicklung)

- `--mock`-Flag nutzt `data/mock_uanalyse.csv` und `data/mock_response.json`.
- **Nie echte API-Calls beim Entwickeln** (Quota schonen).

---

## Phase 3 — Kicktipp Auto-Submit

### Modul: `src/kicktipp_submit.py`

- **Liest** `docs/data.json` (bereits vorhanden), berechnet **nichts neu**.
- **Standard-Modus: `--dry-run`** — Login + Scraping, kein Eintragen, kein Absenden.
- **Echter Eintrag: `--submit`** — erst nach ausdrücklichem OK aktivieren.
- `--headed` für Debug-Session mit sichtbarem Browser.
- `--deadline-buffer HOURS` (default: 2h) — Spiele innerhalb des Puffers werden übersprungen.

### Env-Variablen (secrets)

| Variable | Pflicht | Bedeutung |
|---|---|---|
| `KICKTIPP_EMAIL` | Ja | Login-E-Mail |
| `KICKTIPP_PASSWORD` | Ja | Passwort (nie geloggt) |
| `KICKTIPP_COMPETITION` | Ja | Wettbewerbs-Slug (URL-Segment) |
| `OVERWRITE` | Nein | `true` = bereits getippte Spiele überschreiben (default: false) |
| `NTFY_TOPIC` | Nein | ntfy.sh-Topic für Push-Benachrichtigung nach Submit |

### Login-/Submit-Flow (aus antonengelhardt/kicktipp-bot + schwalle/kicktipp-betbot abgeleitet)

| Schritt | Selektor |
|---|---|
| Login-URL | `https://www.kicktipp.de/info/profil/login` |
| E-Mail | `#kennung` |
| Passwort | `#passwort` |
| Submit | `[name="submitbutton"]` |
| Tippseite | `/{competition}/tippabgabe` |
| Spieltabelle | `#tippabgabeSpiele tbody tr.datarow` |
| Heim-Input | `input[name*="heimTipp"]` |
| Gast-Input | `input[name*="gastTipp"]` |
| Bereits getippt | Input-Value nicht leer → überspringen |

### Reine Matching-Logik (kein Browser, unit-testbar)

`src/kicktipp_submit.py` exportiert browserfreie Funktionen:
- `canonicalize(name, aliases)` — Team-Alias-Auflösung
- `build_prediction_index(matches, aliases)` — Lookup-Dict
- `match_row(home, away, index, aliases)` — Tipp zu Zeile suchen
- `decide_action(home_val, away_val, prediction, overwrite, now, buffer_h)` → `(action, reason)`
- `plan_submissions(rows, matches, aliases, ...)` → Liste von Action-Dicts

Tests: `tests/test_kicktipp_matching.py` (24 Tests, kein Browser).

### Workflow-Schritt (predict.yml)

- Läuft **nach** `build_data.py`, nur wenn `KICKTIPP_EMAIL` gesetzt ist.
- **Aktuell: `--dry-run`** (kein `--submit`-Flag).
- ⚠️ **Vor Aktivierung von `--submit`**: dry-run-Log prüfen, dann explizites OK geben.
- Screenshots bei Fehler werden als GitHub-Artifact hochgeladen.

### GitHub-Actions-Versionen (verifiziert 2026-06-10)

| Action | Version |
|---|---|
| `actions/checkout` | `v6` |
| `actions/setup-python` | `v6` |
| `actions/upload-artifact` | `v7` |
