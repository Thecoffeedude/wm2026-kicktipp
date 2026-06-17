// WM 2026 Kicktipp-Prädikator — Scriptable Widget (Liquid-Glass, auto Light/Dark)
// ------------------------------------------------------------------
// 1. App "Scriptable" aus dem App Store laden.
// 2. Dieses Skript in Scriptable als neues Skript einfügen (Name z. B.
//    "WM 2026"). 3. Home-Screen → Widget hinzufügen → Scriptable →
//    Skript "WM 2026" wählen. Größe S oder M.
//
// Daten kommen live von der GitHub-Pages-Seite der App:
//   widget.json  – Tipps + nächste Spiele + iso-Map (täglich)
//   live.json    – laufende Spiele (alle 5 Min)
//   results.json – Ergebnisse → Punktestand (alle 5 Min)
// Punktestand wird im Widget selbst aus Tipps × Ergebnissen gerechnet.
// Optik folgt automatisch dem System-Erscheinungsbild (hell/dunkel).
// ------------------------------------------------------------------

const SITE = "https://thecoffeedude.github.io/wm2026-kicktipp/";

function rgb(r, g, b, a) {
  const h = x => ("0" + Math.round(Math.max(0, Math.min(1, x)) * 255).toString(16)).slice(-2);
  return new Color(h(r) + h(g) + h(b), a === undefined ? 1 : a);
}
function mix(c1, c2, t) {
  return rgb(c1.red + (c2.red - c1.red) * t,
            c1.green + (c2.green - c1.green) * t,
            c1.blue + (c2.blue - c1.blue) * t);
}

// ── Palette (auto hell/dunkel) ─────────────────────────────────────
const INK = Color.dynamic(new Color("#0A0C12"), new Color("#FFFFFF"));
const MUTED = Color.dynamic(new Color("#52607A"), new Color("#B8BECB"));
const ACCENT = Color.dynamic(new Color("#138A48"), new Color("#46DC80"));
const LIVE = Color.dynamic(new Color("#D8362C"), new Color("#FF5247"));

// Hintergrund-Verläufe je Modus
const BG = {
  dark:  { top: rgb(0.05, 0.06, 0.09), bot: rgb(0.10, 0.11, 0.15) },
  light: { top: rgb(0.93, 0.95, 0.98), bot: rgb(0.82, 0.88, 0.95) },
};

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
  return i ? `https://flagcdn.com/w320/${i}.png` : null;
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

// Weich heruntergerechnete Flagge (Fake-Blur durch Downsampling).
function soften(img, w, h) {
  const c = new DrawContext();
  c.size = new Size(w, h);
  c.opaque = false;
  c.respectScreenScale = false;
  c.drawImageInRect(img, new Rect(0, 0, w, h));
  return c.getImage();
}

// Glas-Hintergrund: Verlauf + Flaggen-Wash links/rechts + Scrim + Glanz.
function glassBackground(W, H, homeImg, awayImg, dark) {
  const pal = dark ? BG.dark : BG.light;
  const ctx = new DrawContext();
  ctx.size = new Size(W, H);
  ctx.opaque = true;
  ctx.respectScreenScale = true;

  // 1) vertikaler Verlauf
  for (let y = 0; y < H; y++) {
    ctx.setFillColor(mix(pal.top, pal.bot, y / (H - 1)));
    ctx.fillRect(new Rect(0, y, W, 1));
  }
  // 2) Flaggen als weicher Wash, links/rechts über die Kante hinaus
  if (homeImg) ctx.drawImageInRect(soften(homeImg, 30, 20),
    new Rect(-W * 0.14, -H * 0.18, W * 0.72, H * 1.36));
  if (awayImg) ctx.drawImageInRect(soften(awayImg, 30, 20),
    new Rect(W * 0.42, -H * 0.18, W * 0.72, H * 1.36));
  // 3) Scrim → Flaggen subtil + Schrift-Kontrast (hell: weiß, dunkel: schwarz)
  ctx.setFillColor(dark ? rgb(0.05, 0.06, 0.09, 0.70) : rgb(0.96, 0.97, 0.99, 0.66));
  ctx.fillRect(new Rect(0, 0, W, H));
  // 4) zentraler Tunnel hebt die Textspalte ab
  ctx.setFillColor(dark ? rgb(0.05, 0.06, 0.09, 0.18) : rgb(1, 1, 1, 0.22));
  ctx.fillRect(new Rect(W * 0.20, 0, W * 0.60, H));
  // 5) Glas-Glanz oben + feine Bodenlinie
  ctx.setFillColor(rgb(1, 1, 1, dark ? 0.10 : 0.30));
  ctx.fillRect(new Rect(0, 0, W, 2));
  ctx.setFillColor(rgb(1, 1, 1, dark ? 0.05 : 0.12));
  ctx.fillRect(new Rect(0, 0, W, Math.round(H * 0.4)));
  ctx.setFillColor(rgb(0, 0, 0, dark ? 0.18 : 0.08));
  ctx.fillRect(new Rect(0, H - 2, W, 2));
  return ctx.getImage();
}

// Schriftgrößen je Widget-Größe (größer = mehr Wucht)
const FONTS = {
  small:  { title: 15, pts: 13, label: 10, teams: 27, score: 30, tip: 17, time: 12, fav: 11, foot: 10 },
  medium: { title: 17, pts: 15, label: 11, teams: 34, score: 40, tip: 20, time: 14, fav: 13, foot: 11 },
  large:  { title: 20, pts: 17, label: 13, teams: 46, score: 52, tip: 24, time: 16, fav: 15, foot: 13 },
};

// ── Widget aufbauen ────────────────────────────────────────────────
async function build() {
  const [widget, liveDoc, resultsDoc] = await Promise.all([
    getJSON("widget.json"), getJSON("live.json"), getJSON("results.json"),
  ]);

  const dark = Device.isUsingDarkAppearance();
  const fam = config.widgetFamily || "medium";
  const f = FONTS[fam] || FONTS.medium;

  const w = new ListWidget();
  w.setPadding(16, 17, 16, 17);
  w.url = SITE;
  w.refreshAfterDate = new Date(Date.now() + 10 * 60 * 1000);

  if (!widget) {
    w.backgroundColor = dark ? BG.dark.top : BG.light.top;
    const t = w.addText("WM 2026 — offline");
    t.textColor = MUTED; t.font = Font.mediumSystemFont(13);
    return w;
  }

  const iso = widget.iso || {};
  const liveGames = ((liveDoc && liveDoc.live) || []).filter(e => e.is_live || e.is_halftime);
  const live = liveGames[0];
  const next = (widget.next || [])[0];

  const feat = live
    ? { hc: live.home_code, ac: live.away_code }
    : next ? { hc: next.hc, ac: next.ac } : null;

  const dims = fam === "small" ? [170, 170] : fam === "large" ? [360, 360] : [360, 170];
  let homeImg = null, awayImg = null;
  if (feat) {
    [homeImg, awayImg] = await Promise.all([
      getImage(flagURL(iso, feat.hc)), getImage(flagURL(iso, feat.ac)),
    ]);
  }
  w.backgroundImage = glassBackground(dims[0], dims[1], homeImg, awayImg, dark);

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
    w.addSpacer(5);
    const sc = w.addText(`${live.home_code}  ${live.score_home ?? 0}:${live.score_away ?? 0}  ${live.away_code}`);
    sc.textColor = INK; sc.font = Font.heavySystemFont(f.score);
    sc.minimumScaleFactor = 0.5; sc.lineLimit = 1;
    const tip = widget.tips[`${live.home_code}:${live.away_code}`];
    if (tip) {
      w.addSpacer(2);
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
  w.addSpacer(4);
  const teams = w.addText(`${next.hc} – ${next.ac}`);
  teams.textColor = INK; teams.font = Font.heavySystemFont(f.teams);
  teams.minimumScaleFactor = 0.5; teams.lineLimit = 1;
  w.addSpacer(2);
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
