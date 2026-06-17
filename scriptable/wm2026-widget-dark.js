// WM 2026 Kicktipp-Prädikator — Scriptable Widget (Liquid-Glass, DUNKEL)
// ------------------------------------------------------------------
// 1. App "Scriptable" aus dem App Store laden.
// 2. Dieses Skript in Scriptable als neues Skript einfügen (Name z. B.
//    "WM 2026 Dark"). 3. Home-Screen → Widget hinzufügen → Scriptable →
//    Skript wählen. Größe S oder M.
//
// Feste dunkle Optik (kein Auto-Umschalten). Die helle Variante liegt in
// wm2026-widget-light.js — beide getrennt speicherbar.
//
// Hintergrund: weicher Farbverlauf (Glas-Look). Flaggen klein & scharf als
// Akzent neben den Team-Codes (kein geblurrter Flaggen-Hintergrund mehr).
//
// Daten kommen live von der GitHub-Pages-Seite der App:
//   widget.json  – Tipps + nächste Spiele + iso-Map (täglich)
//   live.json    – laufende Spiele (alle 5 Min)
//   results.json – Ergebnisse → Punktestand (alle 5 Min)
// Punktestand wird im Widget selbst aus Tipps × Ergebnissen gerechnet.
// ------------------------------------------------------------------

const SITE = "https://thecoffeedude.github.io/wm2026-kicktipp/";

// Wohin der Tap aufs Widget führt. Eine ardmediathek.de-URL öffnet per
// Universal Link direkt die ARD-Mediathek-App (falls installiert), sonst
// Safari. Alternativen:
//   ARD Mediathek Sport: "https://www.ardmediathek.de/sport"
//   MagentaTV (App):     "magentatv://"   (öffnet die App direkt, falls inst.)
//   MagentaTV (Web):     "https://web.magentatv.de/"
//   die Prädiktor-App:   SITE
const TAP_URL = "https://www.ardmediathek.de/sport";

// ── Modus (festes Erscheinungsbild dieses Skripts) ─────────────────
const DARK = true;   // dunkle Variante

// ── Palette ────────────────────────────────────────────────────────
const INK = DARK ? new Color("#FFFFFF") : new Color("#0A0C12");
const MUTED = DARK ? new Color("#B8BECB") : new Color("#52607A");
const ACCENT = DARK ? new Color("#46DC80") : new Color("#138A48");
const LIVE = DARK ? new Color("#FF5247") : new Color("#D8362C");
// Hintergrund-Verlauf (oben → unten)
const G_TOP = DARK ? new Color("#161A26") : new Color("#F4F7FB");
const G_BOT = DARK ? new Color("#0B0D14") : new Color("#DCE6F3");
const HAIR = DARK ? new Color("#FFFFFF", 0.10) : new Color("#0A0C12", 0.10);

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
  } catch (e) { return null; }
}
async function getImage(url) {
  try { const r = new Request(url); r.timeoutInterval = 8; return await r.loadImage(); }
  catch (e) { return null; }
}
function flagURL(iso, code) {
  const i = iso && iso[code];
  return i ? `https://flagcdn.com/w160/${i}.png` : null;
}
function fmtTime(iso) {
  if (!iso || !iso.includes("T")) return "–:––";
  const df = new DateFormatter();
  df.locale = "de_DE";
  df.dateFormat = "EEE HH:mm";
  return df.string(new Date(iso));
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

// Kleine, scharfe Flagge in einen Stack legen.
function addFlag(stack, img, h) {
  if (!img) return;
  const wi = stack.addImage(img);
  const ar = img.size.width / img.size.height;
  wi.imageSize = new Size(Math.round(h * ar), h);
  wi.cornerRadius = 2;
}

// Schriftgrößen je Widget-Größe (größer = mehr Wucht)
const FONTS = {
  small:  { title: 15, pts: 13, label: 10, teams: 24, score: 28, tip: 16, time: 12, fav: 11, foot: 10 },
  medium: { title: 17, pts: 15, label: 11, teams: 30, score: 36, tip: 19, time: 14, fav: 13, foot: 11 },
  large:  { title: 20, pts: 17, label: 13, teams: 40, score: 48, tip: 22, time: 16, fav: 15, foot: 13 },
};

// ── Widget aufbauen ────────────────────────────────────────────────
async function build() {
  const [widget, liveDoc, resultsDoc] = await Promise.all([
    getJSON("widget.json"), getJSON("live.json"), getJSON("results.json"),
  ]);

  const fam = config.widgetFamily || "medium";
  const f = FONTS[fam] || FONTS.medium;

  const w = new ListWidget();
  w.setPadding(16, 17, 16, 17);
  w.url = TAP_URL;
  w.refreshAfterDate = new Date(Date.now() + 10 * 60 * 1000);

  // Hintergrund: weicher Verlauf (Glas-Look)
  const grad = new LinearGradient();
  grad.colors = [G_TOP, G_BOT];
  grad.locations = [0, 1];
  w.backgroundGradient = grad;

  if (!widget) {
    const t = w.addText("WM 2026 — offline");
    t.textColor = MUTED; t.font = Font.mediumSystemFont(13);
    return w;
  }

  const iso = widget.iso || {};
  const liveGames = ((liveDoc && liveDoc.live) || []).filter(e => e.is_live || e.is_halftime);
  const live = liveGames[0];
  const next = (widget.next || [])[0];

  const feat = live ? { hc: live.home_code, ac: live.away_code }
    : next ? { hc: next.hc, ac: next.ac } : null;
  let homeImg = null, awayImg = null;
  if (feat) {
    [homeImg, awayImg] = await Promise.all([
      getImage(flagURL(iso, feat.hc)), getImage(flagURL(iso, feat.ac)),
    ]);
  }

  // Kopf: Titel + Punktestand
  const head = w.addStack();
  head.centerAlignContent();
  const title = head.addText("WM 2026");
  title.textColor = INK; title.font = Font.heavySystemFont(f.title);
  head.addSpacer();
  const bal = pointsBalance(widget.tips, resultsDoc && resultsDoc.results);
  if (bal && bal.games > 0) {
    const pts = head.addText(`${bal.total} Pkt · ${bal.games}`);
    pts.textColor = ACCENT; pts.font = Font.boldSystemFont(f.pts);
  }
  w.addSpacer();

  if (live) {
    const row = w.addStack();
    row.centerAlignContent();
    const dot = row.addText("● ");
    dot.textColor = LIVE; dot.font = Font.boldSystemFont(f.label + 1);
    const min = row.addText(live.is_halftime ? "HALBZEIT" : (live.minute ? live.minute + "'" : "LIVE"));
    min.textColor = LIVE; min.font = Font.heavySystemFont(f.label + 1);
    w.addSpacer(6);

    const sc = w.addStack();
    sc.centerAlignContent();
    addFlag(sc, homeImg, Math.round(f.score * 0.55)); sc.addSpacer(8);
    const txt = sc.addText(`${live.home_code}  ${live.score_home ?? 0}:${live.score_away ?? 0}  ${live.away_code}`);
    txt.textColor = INK; txt.font = Font.heavySystemFont(f.score);
    txt.minimumScaleFactor = 0.5; txt.lineLimit = 1;
    sc.addSpacer(8); addFlag(sc, awayImg, Math.round(f.score * 0.55));

    const tip = widget.tips[`${live.home_code}:${live.away_code}`];
    if (tip) {
      w.addSpacer(3);
      const tt = w.addText(`Tipp ${tip[0]}:${tip[1]}`);
      tt.textColor = MUTED; tt.font = Font.semiboldSystemFont(f.fav);
    }
    if (liveGames.length > 1) {
      w.addSpacer(1);
      const more = w.addText(`+${liveGames.length - 1} weitere live`);
      more.textColor = MUTED; more.font = Font.systemFont(f.foot);
    }
    w.addSpacer();
    return w;
  }

  if (!next) {
    const t = w.addText("Keine anstehenden Spiele");
    t.textColor = MUTED; t.font = Font.mediumSystemFont(13);
    return w;
  }
  const lbl = w.addText("NÄCHSTES SPIEL");
  lbl.textColor = MUTED; lbl.font = Font.heavySystemFont(f.label);
  w.addSpacer(5);

  const row = w.addStack();
  row.centerAlignContent();
  addFlag(row, homeImg, Math.round(f.teams * 0.6)); row.addSpacer(8);
  const teams = row.addText(`${next.hc} – ${next.ac}`);
  teams.textColor = INK; teams.font = Font.heavySystemFont(f.teams);
  teams.minimumScaleFactor = 0.5; teams.lineLimit = 1;
  row.addSpacer(8); addFlag(row, awayImg, Math.round(f.teams * 0.6));

  w.addSpacer(3);
  const time = w.addText(fmtTime(next.kickoff));
  time.textColor = MUTED; time.font = Font.semiboldSystemFont(f.time);
  w.addSpacer();

  const info = w.addStack();
  info.centerAlignContent();
  if (next.tip && next.tip[0] != null) {
    const tip = info.addText(`Tipp ${next.tip[0]}:${next.tip[1]}`);
    tip.textColor = ACCENT; tip.font = Font.heavySystemFont(f.tip);
  }
  info.addSpacer();
  if (next.fav) {
    const fav = info.addText(`${next.fav.label} ${next.fav.pct}%`);
    fav.textColor = MUTED; fav.font = Font.semiboldSystemFont(f.fav);
    fav.lineLimit = 1; fav.minimumScaleFactor = 0.7;
  }

  if (fam !== "small" && widget.next[1]) {
    w.addSpacer(6);
    const n2 = widget.next[1];
    const ft = w.addText(`danach: ${n2.hc} – ${n2.ac} · ${fmtTime(n2.kickoff)}`);
    ft.textColor = MUTED; ft.font = Font.systemFont(f.foot); ft.lineLimit = 1;
  }
  return w;
}

const widget = await build();
if (config.runsInWidget) {
  Script.setWidget(widget);
} else {
  await widget.presentMedium();
}
Script.complete();
