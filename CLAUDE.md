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

### Tertiärquelle: football-data.org (Anstoßzeiten, Live, Ergebnisse)

- **Endpoint:** `/v4/competitions/2000/matches` — Secret: `FOOTBALL_DATA_API_KEY`.
- `src/fetch_live.py`:
  - `fetch_schedule()` — kompletter Spielplan inkl. exakter Anstoßzeiten (`utcDate`)
    und Endergebnissen. `build_data.py` reichert damit date-only `commence_time`
    aus uanalyse an (`enrich_kickoff_times`, ±1 Tag Toleranz) — wichtig für UI-Zeiten
    **und** den Deadline-Buffer in `kicktipp_submit.py`.
  - `fetch_live_scores()` — nur heutige Spiele (Live-Polling).
- **Dateisplit (kein Workflow-Race):** `predict.yml` schreibt `docs/data.json` (+ initiale
  `live.json`/`results.json`); `live.yml` schreibt **nur** `docs/live.json` (~2 KB)
  und `docs/results.json` (kumulativer Ergebnis-Speicher via `merge_results` —
  Ergebnisse verschwinden nie). `live_update.py` fasst `data.json` bewusst nicht an.
- `results.json` ist Basis für Punkte-Bilanz (Verlauf-Tab) und Gruppentabellen im Frontend.
- Odds-Fetch ist fehlertolerant: ohne `ODDS_API_KEY` oder bei leerer Antwort läuft der
  Build ohne Quoten weiter; bei 0 Treffern wird der Sport-Key via `/sports/` verifiziert.

### Mock-Betrieb (Entwicklung)

- `--mock`-Flag nutzt `data/mock_uanalyse.csv` und `data/mock_response.json`.
- **Nie echte API-Calls beim Entwickeln** (Quota schonen).
- ⚠️ `build_data.py --mock` überschreibt `docs/data.json` mit Mock-Daten —
  danach `git checkout docs/data.json`.

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
| `NTFY_TOPIC` | Nein | ntfy.sh-Topic — Push nach Submit + tägliche Tipp-Übersicht (`src/notify_tips.py`) |
| `FOOTBALL_DATA_API_KEY` | Nein | football-data.org — Anstoßzeiten, Live-Scores, Ergebnisse |

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
- **Aktuell: `--submit`** (echter Eintrag, seit Turnierstart aktiv).
- Screenshots bei Fehler werden als GitHub-Artifact hochgeladen.

### GitHub-Actions-Versionen (verifiziert 2026-06-10)

| Action | Version |
|---|---|
| `actions/checkout` | `v6` |
| `actions/setup-python` | `v6` |
| `actions/upload-artifact` | `v7` |
