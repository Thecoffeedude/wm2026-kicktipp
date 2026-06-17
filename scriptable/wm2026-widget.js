// WM 2026 Kicktipp-Prädikator — Scriptable Widget
// ------------------------------------------------------------------
// 1. App "Scriptable" aus dem App Store laden.
// 2. Dieses Skript in Scriptable als neues Skript einfügen (Name z. B.
//    "WM 2026"). 3. Home-Screen → Widget hinzufügen → Scriptable →
//    Skript "WM 2026" wählen. Größe S oder M.
//
// Daten kommen live von der GitHub-Pages-Seite der App:
//   widget.json  – Tipps + nächste Spiele (täglich)
//   live.json    – laufende Spiele (alle 5 Min)
//   results.json – Ergebnisse → Punktestand (alle 5 Min)
// Der Punktestand wird im Widget selbst aus Tipps × Ergebnissen gerechnet,
// bleibt also so frisch wie results.json.
// ------------------------------------------------------------------

const SITE = "https://thecoffeedude.github.io/wm2026-kicktipp/";

// ── Farben ─────────────────────────────────────────────────────────
const BG_TOP = new Color("#0A0C12");
const BG_BOT = new Color("#161A24");
const INK = new Color("#FFFFFF");
const MUTED = new Color("#9AA1B0");
const ACCENT = new Color("#34C759");
const LIVE = new Color("#FF3B30");

// ── Kicktipp-Punkte (identisch zu config.kicktipp_points) ──────────
function kicktippPoints(tip, real) {
  const [ta, tb] = tip, [ra, rb] = real;
  const ts = Math.sign(ta - tb), rs = Math.sign(ra - rb);
  if (ts !== rs) return 0;
  if (rs === 0) return (ta === ra && tb === rb) ? 4 : 2;
  if (ta === ra && tb === rb) return 4;
  if ((ta - tb) === (ra - rb)) return 3;
  return 2;
}

async function getJSON(name) {
  try {
    const req = new Request(SITE + name + "?t=" + Date.now());
    req.timeoutInterval = 8;
    return await req.loadJSON();
  } catch (e) {
    return null;
  }
}

function fmtTime(iso) {
  if (!iso || !iso.includes("T")) return "–:––";
  const d = new Date(iso);
  const df = new DateFormatter();
  df.locale = "de_DE";
  df.dateFormat = "EEE HH:mm";
  return df.string(d);
}

function pointsBalance(tips, results) {
  if (!tips || !results) return null;
  let total = 0, games = 0;
  for (const r of results) {
    if (!r.is_done || r.score_home == null) continue;
    const tip = tips[`${r.home_code}:${r.away_code}`];
    if (!tip) continue;
    total += kicktippPoints(tip, [r.score_home, r.score_away]);
    games += 1;
  }
  return { total, games };
}

// ── Widget aufbauen ────────────────────────────────────────────────
async function build() {
  const [widget, liveDoc, resultsDoc] = await Promise.all([
    getJSON("widget.json"), getJSON("live.json"), getJSON("results.json"),
  ]);

  const w = new ListWidget();
  const grad = new LinearGradient();
  grad.colors = [BG_TOP, BG_BOT];
  grad.locations = [0, 1];
  w.backgroundGradient = grad;
  w.setPadding(14, 14, 14, 14);
  w.url = SITE;                       // Tap → öffnet die App
  w.refreshAfterDate = new Date(Date.now() + 10 * 60 * 1000);

  if (!widget) {
    const t = w.addText("WM 2026 — offline");
    t.textColor = MUTED; t.font = Font.mediumSystemFont(13);
    return w;
  }

  // Kopf
  const head = w.addStack();
  head.centerAlignContent();
  const title = head.addText("WM 2026");
  title.textColor = INK; title.font = Font.heavySystemFont(13);
  head.addSpacer();
  const bal = pointsBalance(widget.tips, resultsDoc && resultsDoc.results);
  if (bal && bal.games > 0) {
    const pts = head.addText(`${bal.total} Pkt · ${bal.games} Sp.`);
    pts.textColor = ACCENT; pts.font = Font.semiboldSystemFont(11);
  }
  w.addSpacer(8);

  // Laufendes Spiel hat Vorrang
  const liveGames = (liveDoc && liveDoc.live || []).filter(e => e.is_live || e.is_halftime);
  if (liveGames.length) {
    const g = liveGames[0];
    const row = w.addStack();
    row.centerAlignContent();
    const dot = row.addText("● ");
    dot.textColor = LIVE; dot.font = Font.boldSystemFont(11);
    const min = row.addText(g.is_halftime ? "HZ" : (g.minute ? g.minute + "'" : "Live"));
    min.textColor = LIVE; min.font = Font.boldSystemFont(11);
    w.addSpacer(4);
    const sc = w.addText(`${g.home_code} ${g.score_home ?? 0} : ${g.score_away ?? 0} ${g.away_code}`);
    sc.textColor = INK; sc.font = Font.heavySystemFont(20);
    const tip = widget.tips[`${g.home_code}:${g.away_code}`];
    if (tip) {
      const tt = w.addText(`Tipp ${tip[0]}:${tip[1]}`);
      tt.textColor = MUTED; tt.font = Font.mediumSystemFont(12);
    }
    if (liveGames.length > 1) {
      w.addSpacer(2);
      const more = w.addText(`+${liveGames.length - 1} weitere live`);
      more.textColor = MUTED; more.font = Font.systemFont(10);
    }
    return w;
  }

  // Sonst: nächstes Spiel + Tipp
  const next = (widget.next || [])[0];
  if (!next) {
    const t = w.addText("Keine anstehenden Spiele");
    t.textColor = MUTED; t.font = Font.mediumSystemFont(13);
    return w;
  }
  const lbl = w.addText("NÄCHSTES SPIEL");
  lbl.textColor = MUTED; lbl.font = Font.semiboldSystemFont(9);
  w.addSpacer(3);
  const teams = w.addText(`${next.hc} – ${next.ac}`);
  teams.textColor = INK; teams.font = Font.heavySystemFont(19);
  w.addSpacer(1);
  const time = w.addText(fmtTime(next.kickoff));
  time.textColor = MUTED; time.font = Font.mediumSystemFont(12);
  w.addSpacer(6);

  const info = w.addStack();
  info.centerAlignContent();
  if (next.tip) {
    const tip = info.addText(`Tipp ${next.tip[0]}:${next.tip[1]}`);
    tip.textColor = ACCENT; tip.font = Font.boldSystemFont(14);
  }
  info.addSpacer();
  if (next.fav) {
    const fav = info.addText(`${next.fav.label} ${next.fav.pct}%`);
    fav.textColor = MUTED; fav.font = Font.mediumSystemFont(11);
    fav.lineLimit = 1;
  }

  // Übernächstes Spiel als Fußzeile (nur Medium-Widget hat Platz)
  if (config.widgetFamily !== "small" && widget.next[1]) {
    w.addSpacer(6);
    const n2 = widget.next[1];
    const f = w.addText(`danach: ${n2.hc} – ${n2.ac} · ${fmtTime(n2.kickoff)}`);
    f.textColor = MUTED; f.font = Font.systemFont(10); f.lineLimit = 1;
  }
  return w;
}

const widget = await build();
if (config.runsInWidget) {
  Script.setWidget(widget);
} else {
  await widget.presentMedium();   // Vorschau beim manuellen Ausführen
}
Script.complete();
