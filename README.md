# WM 2026 · Kicktipp-Prädikator

EV-optimale Kicktipp-Tipps für die FIFA WM 2026 — vollautomatisch, täglich aktualisiert.

**[→ Live-App öffnen](https://thecoffeedude.github.io/wm2026-kicktipp/)**

---

## Was das macht

1. **Vorhersagen holen** — zieht täglich die Spielprognosen von [uanalyse](https://github.com/uanalyse/world-cup-2026-predictions) (Siegwahrscheinlichkeiten + Expected Goals) und optional Buchmacher-Quoten von [The Odds API](https://the-odds-api.com).
2. **Optimalen Tipp berechnen** — Poisson-Modell auf Basis der λ-Werte, maximiert den Expected Value im Kicktipp-Punktesystem.
3. **`docs/data.json` aktualisieren** — GitHub Actions commitet das Ergebnis täglich automatisch.
4. **Tipps eintragen** — Playwright-Skript loggt sich in Kicktipp ein und trägt alle offenen Spiele + Sonderfragen ein (optional, per Secret aktivierbar).

---

## App-Tabs

| Tab | Inhalt |
|---|---|
| **Heute** | Heutige Spiele + Stat-Widgets (Tendenz, xG-Leader, Klarster Favorit, Übereinstimmung) |
| **Spiele** | Alle Spiele mit Suche, Team-Filter und Datumsgruppen (sticky Header) |
| **Gruppen** | Champion-Ranking-Balken + Gruppenwahrscheinlichkeiten (Gruppensieg / Zweiter) |
| **Turnier** | Probabilistischer Turnierweg: Wahrscheinlichkeit je Team pro KO-Runde bis Titel |
| **Divergenz** | Abweichungen zwischen Modell (uanalyse) und Buchmachern |
| **Info** | Methodik, Datenquellen |

---

## Lokale Einrichtung

```bash
git clone https://github.com/Thecoffeedude/wm2026-kicktipp.git
cd wm2026-kicktipp
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

`.env` anlegen (`.env.example` als Vorlage):

```env
ODDS_API_KEY=dein_key          # optional — ohne Key laufen alle Funktionen außer Buchmacher-Quoten
KICKTIPP_EMAIL=deine@email.de  # nur für Auto-Submit nötig
KICKTIPP_PASSWORD=deinPasswort
KICKTIPP_COMPETITION=liga-slug
```

**Vorhersagen generieren** (zieht Live-Daten von uanalyse):

```bash
python3 src/build_data.py
```

**Mock-Betrieb** (kein Netz, kein API-Key verbraucht):

```bash
python3 src/build_data.py --mock
```

**Dry-run Kicktipp** (Login + Scraping, kein Eintragen):

```bash
python3 src/kicktipp_submit.py --dry-run
```

**App lokal öffnen:**

```bash
cd docs && python3 -m http.server 8080
# → http://localhost:8080
```

---

## Architektur

```
src/
  build_data.py        # Einstiegspunkt — aggregiert alle Quellen → docs/data.json
  fetch_uanalyse.py    # Spielprognosen + Turnier-Wahrscheinlichkeiten von uanalyse
  fetch_odds.py        # Buchmacher-Quoten via The Odds API
  fetch_live.py        # Live-Spielstände von football-data.org
  live_update.py       # Leichtgewichtiger Live-Patcher (nur "live"-Key in data.json)
  scoreline.py         # Poisson-Optimierer → recommended_tip
  tournament.py        # Gruppensieger, Halbfinalisten, Titelkandidat
  kicktipp_submit.py   # Playwright Auto-Submit
  teams.py / teams.json  # Kanonisches Team-Registry (FIFA-Codes, Aliases, Flaggen)
docs/
  index.html / app.js / style.css  # PWA-Frontend
  data.json            # Vorhersagen + Live-Ergebnisse (täglich + alle 5 min live)
  sw.js                # Service Worker (Offline-Support)
data/
  mock_uanalyse.csv    # Mock-Daten für lokale Entwicklung
  mock_response.json   # Mock-Quoten
```

---

## GitHub Actions

**`predict.yml`** — täglich 06:00 UTC:
1. Zieht Prognosen von uanalyse (kein Key nötig)
2. Holt Quoten von The Odds API (falls `ODDS_API_KEY` gesetzt)
3. Holt Live-Spielstände (falls `FOOTBALL_DATA_API_KEY` gesetzt)
4. Commitet `docs/data.json` → GitHub Pages aktualisiert sich automatisch
5. Trägt Tipps in Kicktipp ein (falls `KICKTIPP_EMAIL` gesetzt)

**`live.yml`** — alle 5 Minuten:
- Patcht nur den `"live"`-Key in `data.json` mit aktuellen Spielständen
- Commitet nur wenn Änderungen vorhanden (kein unnötiger Commit-Spam)
- Benötigt `FOOTBALL_DATA_API_KEY`

**GitHub Secrets:**

| Secret | Pflicht | Bedeutung |
|---|---|---|
| `FOOTBALL_DATA_API_KEY` | Für Live-Scores | football-data.org — kostenlos registrieren |
| `ODDS_API_KEY` | Nein | The Odds API — ohne Key keine Buchmacher-Quoten |
| `KICKTIPP_EMAIL` | Nein | Aktiviert Auto-Submit |
| `KICKTIPP_PASSWORD` | Wenn EMAIL gesetzt | Kicktipp-Passwort |
| `KICKTIPP_COMPETITION` | Wenn EMAIL gesetzt | Liga-Slug aus der Kicktipp-URL |
| `NTFY_TOPIC` | Nein | Push-Benachrichtigung nach Submit via ntfy.sh |

**`FOOTBALL_DATA_API_KEY` holen:** Kostenlos unter [football-data.org](https://www.football-data.org/client/register) registrieren → API-Key per E-Mail → als GitHub Secret hinzufügen.

Manueller Trigger: **Actions → Workflow auswählen → Run workflow**

---

## Datenquellen & Lizenz

- **Spielprognosen:** [uanalyse/world-cup-2026-predictions](https://github.com/uanalyse/world-cup-2026-predictions) — [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
- **Live-Spielstände:** [football-data.org](https://www.football-data.org) — kostenloser Tier
- **Buchmacher-Quoten:** [The Odds API](https://the-odds-api.com)
- **Flaggen:** [flagcdn.com](https://flagcdn.com)
