# iOS-Widget (Scriptable)

Home-Screen-/Lock-Screen-Widget für den WM-2026-Kicktipp-Prädiktor. iOS lässt
PWAs keine nativen Widgets ausliefern, daher läuft das Widget über die
kostenlose App **Scriptable**, die das öffentliche JSON der GitHub-Pages-Seite
zieht.

## Einrichtung

1. **Scriptable** aus dem App Store installieren.
2. In Scriptable ein neues Skript anlegen und den Inhalt von
   [`wm2026-widget.js`](wm2026-widget.js) einfügen (Name z. B. „WM 2026").
3. Home-Screen lange drücken → **+** → **Scriptable** → Widget-Größe **S** oder
   **M** wählen → platzieren.
4. Widget antippen und gedrückt halten → **Widget bearbeiten** → Skript
   „WM 2026" auswählen.

Optik: führt den **Liquid-Glass-Look** der App fort — dunkler Verlauf, die
beiden Länderflaggen weich/subtil links und rechts im Hintergrund, dunkler
Scrim für sicheren Schrift-Kontrast, feine Glanzkante oben.

## Was es zeigt

- **Laufendes Spiel** (falls eines live ist): Live-Score + Minute + eigener Tipp.
- **Sonst**: nächstes Spiel, Anstoßzeit, empfohlener Tipp, Favorit.
- **Kopfzeile**: aktueller Punktestand (`Pkt · Spiele`), im Widget selbst aus
  Tipps × Ergebnissen gerechnet — so frisch wie `results.json` (alle 5 Min).
- **Tap** öffnet die App.

## Datenquellen (öffentlich)

- `…/widget.json` — Tipps + nächste Spiele (täglicher Build)
- `…/live.json` — laufende Spiele (alle 5 Min)
- `…/results.json` — Ergebnisse für den Punktestand (alle 5 Min)
