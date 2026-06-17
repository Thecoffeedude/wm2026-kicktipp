// WM 2026 Kicktipp-Prädikator — Scriptable Widget (Liquid-Glass-Look)
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
// Der Punktestand wird im Widget selbst aus Tipps × Ergebnissen gerechnet,
// bleibt also so frisch wie results.json.
// ------------------------------------------------------------------

const SITE = "https://thecoffeedude.github.io/wm2026-kicktipp/";

// ── Palette (App: dunkles Glas) ────────────────────────────────────
const BG_TOP = rgb(0.05, 0.06, 0.09);   // #0D0F17
const BG_BOT = rgb(0.10, 0.11, 0.15);   // #1A1C26
const INK = new Color("#FFFFFF");
const MUTED = new Color("#B6BCC9");      // etwas heller für Kontrast auf Glas
const ACCENT = new Color("#41D67A");
const LIVE = new Color("#FF5247");

function rgb(r, g, b, a) {
  const h = x => ("0" + Math.round(Math.max(0, Math.min(1, x)) * 255).toString(16)).slice(-2);
  return new Color(h(r) + h(g) + h(b), a === undefined ? 1 : a);
}
function mix(c1, c2, t) {
  return rgb(c1.red + (c2.red - c1.red) * t,
            c1.green + (c2.green - c1.green) * t,
            c1.blue + (c2.blue - c1.blue) * t);
}

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

// Glas-Hintergrund: Verlauf + Flaggen-Wash links/rechts + Scrim + Glanzkante.
function glassBackground(W, H, homeImg, awayImg) {
  const ctx = new DrawContext();
  ctx.size = new Size(W, H);
  ctx.opaque = true;
  ctx.respectScreenScale = true;

  // 1) vertikaler Verlauf
  for (let y = 0; y < H; y++) {
    ctx.setFillColor(mix(BG_TOP, BG_BOT, y / (H - 1)));
    ctx.fillRect(new Rect(0, y, W, 1));
  }
  // 2) Flaggen als weicher Wash, links/rechts über die Kante hinaus
  if (homeImg) {
    ctx.drawImageInRect(soften(homeImg, 30, 20),
      new Rect(-W * 0.14, -H * 0.18, W * 0.72, H * 1.36));
  }
  if (awayImg) {
    ctx.drawImageInRect(soften(awayImg, 30, 20),
      new Rect(W * 0.42, -H * 0.18, W * 0.72, H * 1.36));
  }
  // 3) dunkler Scrim → macht Flaggen subtil UND sichert Schrift-Kontrast
  ctx.setFillColor(rgb(0.05, 0.06, 0.09, 0.70));
  ctx.fillRect(new Rect(0, 0, W, H));
  // 4) sanfter Mitten-Tunnel: zentrale Spalte etwas dunkler für Text
  ctx.setFillColor(rgb(0.05, 0.06, 0.09, 0.18));
  ctx.fillRect(new Rect(W * 0.22, 0, W * 0.56, H));
  // 5) Glas-Glanz oben + feine Bodenlinie
  ctx.setFillColor(rgb(1, 1, 1, 0.10));
  ctx.fillRect(new Rect(0, 0, W, 2));
  ctx.setFillColor(rgb(1, 1, 1, 0.05));
  ctx.fillRect(new Rect(0, 0, W, Math.round(H * 0.4)));
  ctx.setFillColor(rgb(0, 0, 0, 0.18));
  ctx.fillRect(new Rect(0, H - 2, W, 2));
  return ctx.getImage();
}

// ── Widget aufbauen ────────────────────────────────────────────────
async function build() {
  const [widget, liveDoc, resultsDoc] = await Promise.all([
    getJSON("widget.json"), getJSON("live.json"), getJSON("results.json"),
  ]);

  const w = new ListWidget();
  w.setPadding(15, 16, 15, 16);
  w.url = SITE;
  w.refreshAfterDate = new Date(Date.now() + 10 * 60 * 1000);

  if (!widget) {
    w.backgroundColor = BG_TOP;
    const t = w.addText("WM 2026 — offline");
    t.textColor = MUTED; t.font = Font.mediumSystemFont(13);
    return w;
  }

  const iso = widget.iso || {};
  const liveGames = ((liveDoc && liveDoc.live) || []).filter(e => e.is_live || e.is_halftime);
  const live = liveGames[0];
  const next = (widget.next || [])[0];

  // Welche Flaggen in den Hintergrund? Das angezeigte Spiel.
  const feat = live
    ? { hc: live.home_code, ac: live.away_code }
    : next ? { hc: next.hc, ac: next.ac } : null;

  const fam = config.widgetFamily || "medium";
  const dims = fam === "small" ? [170, 170] : fam === "large" ? [360, 360] : [360, 170];
  if (feat) {
    const [homeImg, awayImg] = await Promise.all([
      getImage(flagURL(iso, feat.hc)), getImage(flagURL(iso, feat.ac)),
    ]);
    w.backgroundImage = glassBackground(dims[0], dims[1], homeImg, awayImg);
  } else {
    w.backgroundImage = glassBackground(dims[0], dims[1], null, null);
  }

  // Kopf: Titel + Punktestand
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
  w.addSpacer(fam === "small" ? 6 : 10);

  if (live) {
    const row = w.addStack();
    row.centerAlignContent();
    const dot = row.addText("● ");
    dot.textColor = LIVE; dot.font = Font.boldSystemFont(11);
    const min = row.addText(live.is_halftime ? "HALBZEIT" : (live.minute ? live.minute + "'" : "LIVE"));
    min.textColor = LIVE; min.font = Font.boldSystemFont(11);
    w.addSpacer(4);
    const sc = w.addText(`${live.home_code} ${live.score_home ?? 0} : ${live.score_away ?? 0} ${live.away_code}`);
    sc.textColor = INK; sc.font = Font.heavySystemFont(fam === "small" ? 19 : 22);
    sc.minimumScaleFactor = 0.6;
    const tip = widget.tips[`${live.home_code}:${live.away_code}`];
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

  if (!next) {
    const t = w.addText("Keine anstehenden Spiele");
    t.textColor = MUTED; t.font = Font.mediumSystemFont(13);
    return w;
  }
  const lbl = w.addText("NÄCHSTES SPIEL");
  lbl.textColor = MUTED; lbl.font = Font.semiboldSystemFont(9);
  w.addSpacer(3);
  const teams = w.addText(`${next.hc} – ${next.ac}`);
  teams.textColor = INK; teams.font = Font.heavySystemFont(fam === "small" ? 18 : 21);
  teams.minimumScaleFactor = 0.6;
  w.addSpacer(1);
  const time = w.addText(fmtTime(next.kickoff));
  time.textColor = MUTED; time.font = Font.mediumSystemFont(12);
  w.addSpacer(fam === "small" ? 5 : 7);

  const info = w.addStack();
  info.centerAlignContent();
  if (next.tip && next.tip[0] != null) {
    const tip = info.addText(`Tipp ${next.tip[0]}:${next.tip[1]}`);
    tip.textColor = ACCENT; tip.font = Font.boldSystemFont(14);
  }
  info.addSpacer();
  if (next.fav) {
    const fav = info.addText(`${next.fav.label} ${next.fav.pct}%`);
    fav.textColor = MUTED; fav.font = Font.mediumSystemFont(11);
    fav.lineLimit = 1;
  }

  if (fam !== "small" && widget.next[1]) {
    w.addSpacer(7);
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
  await widget.presentMedium();
}
Script.complete();
