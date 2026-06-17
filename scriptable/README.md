# iOS-Widget (Scriptable)

Home-Screen-/Lock-Screen-Widget für den WM-2026-Kicktipp-Prädiktor. iOS lässt
PWAs keine nativen Widgets ausliefern, daher läuft das Widget über die
kostenlose App **Scriptable**, die das öffentliche JSON der GitHub-Pages-Seite
zieht.

## Zwei feste Varianten (dunkel / hell)

Kein Auto-Umschalten (bei einem gebackenen Hintergrundbild unzuverlässig) —
stattdessen zwei getrennt speicherbare Skripte. Nimm das, das zu dir passt:

- [`wm2026-widget-dark.js`](wm2026-widget-dark.js) — dunkle Optik
- [`wm2026-widget-light.js`](wm2026-widget-light.js) — helle Optik

## Einrichtung

1. **Scriptable** aus dem App Store installieren.
2. In Scriptable ein neues Skript anlegen und den Inhalt der gewünschten
   Variante einfügen (Name z. B. „WM 2026 Dark" / „WM 2026 Light").
3. Home-Screen lange drücken → **+** → **Scriptable** → Widget-Größe **S** oder
   **M** wählen → platzieren.
4. Widget antippen und gedrückt halten → **Widget bearbeiten** → Skript wählen.

Optik: führt den **Liquid-Glass-Look** der App fort — Verlauf, die beiden
Länderflaggen subtil im Hintergrund, Scrim für sicheren Schrift-Kontrast, feine
Glanzkante oben, große/wuchtige Schriften.

> Nach Skript-Updates den Inhalt erneut in Scriptable einfügen (das Widget lädt
> nur Daten, nicht den Skript-Code, aus dem Netz).

Die beiden Flaggen liegen je auf einer Hälfte des Hintergrunds (keine
Überlappung) und werden **global** mit einer **echten Gauß'schen Unschärfe**
(CSS `filter: blur` via WebView) weichgezeichnet — eine Anwendung über den
ganzen Hintergrund, daher eine weiche Mittel-Naht statt scharfer Kante.

## Tap-Ziel

`TAP_URL` (oben im Skript) legt fest, wohin der Tap führt. Standard ist die
**ARD Mediathek**: die `ardmediathek.de`-URL öffnet per **Universal Link**
direkt die **ARD-Mediathek-App** (falls installiert), sonst Safari. Alternativen
liegen als Kommentar bei:

- ARD Mediathek Sport: `https://www.ardmediathek.de/sport` (öffnet die App)
- MagentaTV (App): `magentatv://` (öffnet die App direkt, falls installiert)
- MagentaTV (Web): `https://web.magentatv.de/`
- die Prädiktor-App: `SITE`

## Was es zeigt

- **Laufendes Spiel** (falls eines live ist): Live-Score + Minute + eigener Tipp.
- **Sonst**: nächstes Spiel, Anstoßzeit, empfohlener Tipp, Favorit.
- **Kopfzeile**: aktueller Punktestand (`Pkt · Spiele`), im Widget selbst aus
  Tipps × Ergebnissen gerechnet — so frisch wie `results.json` (alle 5 Min).
- **Tap** öffnet das gewählte `TAP_URL`-Ziel (Standard: ARD Mediathek).

## Datenquellen (öffentlich)

- `…/widget.json` — Tipps + nächste Spiele (täglicher Build)
- `…/live.json` — laufende Spiele (alle 5 Min)
- `…/results.json` — Ergebnisse für den Punktestand (alle 5 Min)
