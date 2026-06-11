// ── Config ────────────────────────────────────────────────────────────────
const DATA_URL = './data.json';
const LIVE_URL = './live.json';
const RESULTS_URL = './results.json';
const FLAG_BASE = 'https://flagcdn.com/w80/';
const DIVERGENCE_THRESHOLD = 0.04;
const XG_MAX = 4.0;
const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

// ── State ─────────────────────────────────────────────────────────────────
let allMatches = [];
let metadata = {};
let tournament = {};
let tournamentProbs = {};   // FIFA_code → {team, group, prob_win_group, ..., prob_champion}
let liveScores   = [];      // today's live/finished match scores from football-data.org
let liveByKey    = {};      // lookup: "HOME_CODE:AWAY_CODE" → live entry
let resultsByPair = {};     // lookup: "HOME_CODE:AWAY_CODE" → finished result entry
let currentTab = 'heute';
let searchQuery = '';
let filterTeam = '';
let _liveRefreshTimer = null;

// ── Live data helpers ─────────────────────────────────────────────────────
function _applyLiveData(raw) {
  liveScores = raw || [];
  liveByKey  = {};
  liveScores.forEach(e => {
    liveByKey[`${e.home_code}:${e.away_code}`] = e;
    // Finished matches flow straight into the results store, so the
    // Punkte-Bilanz and standings update without waiting for results.json.
    if (e.is_done) resultsByPair[`${e.home_code}:${e.away_code}`] = e;
  });
}

function _applyResults(raw) {
  (raw || []).forEach(e => {
    resultsByPair[`${e.home_code}:${e.away_code}`] = e;
  });
}

function _liveEntry(match) {
  const hc = match.home_code || '';
  const ac = match.away_code || '';
  return liveByKey[`${hc}:${ac}`] || null;
}

// Best known real-world state for a match: live entry first (today,
// includes running games), then the persistent results store.
function _resultEntry(match) {
  const live = _liveEntry(match);
  if (live && (live.is_live || live.is_halftime || live.is_done)) return live;
  return resultsByPair[`${match.home_code}:${match.away_code}`] || null;
}

function _allResults() {
  return Object.values(resultsByPair).filter(e => e.is_done);
}

function _hasLive() {
  return liveScores.some(e => e.is_live || e.is_halftime);
}

async function _refreshLiveData() {
  try {
    // live.json is tiny (~2 KB); fall back to data.json for old deployments
    let res = await fetch(LIVE_URL + '?_=' + Date.now());
    if (res.ok) {
      const data = await res.json();
      _applyLiveData(data.live || []);
    } else {
      res = await fetch(DATA_URL + '?_=' + Date.now());
      if (!res.ok) return;
      const data = await res.json();
      _applyLiveData(data.live || []);
    }
    // Re-render current tab to show updated scores
    if (currentTab === 'heute' || currentTab === 'verlauf') renderTab();
    renderMeta();
  } catch {}
}

function _scheduleLiveRefresh() {
  if (_liveRefreshTimer) clearInterval(_liveRefreshTimer);
  // Poll every 60 s if any live match; every 5 min otherwise
  const interval = _hasLive() ? 60_000 : 300_000;
  _liveRefreshTimer = setInterval(async () => {
    await _refreshLiveData();
    // Re-check interval after refresh (live match may have started/ended)
    _scheduleLiveRefresh();
  }, interval);
}

// ── Team → ISO 3166-1 alpha-2 map (all 48 WM 2026 teams + extras) ──────────
const TEAM_ISO = {
  'Algeria': 'dz', 'Argentina': 'ar', 'Australia': 'au', 'Austria': 'at',
  'Belgium': 'be', 'Bolivia': 'bo', 'Bosnia-Herzegovina': 'ba',
  'Bosnia & Herzegovina': 'ba', 'Brazil': 'br', 'Canada': 'ca',
  'Cape Verde': 'cv', 'Chile': 'cl', 'Colombia': 'co', 'Congo DR': 'cd',
  'Costa Rica': 'cr', 'Croatia': 'hr', 'Cuba': 'cu', 'Curaçao': 'cw',
  'Czechia': 'cz', 'Denmark': 'dk', 'Ecuador': 'ec', 'Egypt': 'eg',
  'England': 'gb-eng', 'Finland': 'fi', 'France': 'fr', 'Germany': 'de',
  'Ghana': 'gh', 'Greece': 'gr', 'Guatemala': 'gt', 'Haiti': 'ht',
  'Honduras': 'hn', 'Hungary': 'hu', 'Indonesia': 'id', 'Iran': 'ir',
  'Iraq': 'iq', 'Ireland': 'ie', 'Israel': 'il', 'Italy': 'it',
  'Ivory Coast': 'ci', 'Jamaica': 'jm', 'Japan': 'jp', 'Jordan': 'jo',
  'Kenya': 'ke', 'Mali': 'ml', 'Mexico': 'mx', 'Morocco': 'ma',
  'Netherlands': 'nl', 'New Zealand': 'nz', 'Nigeria': 'ng', 'North Korea': 'kp',
  'Norway': 'no', 'Panama': 'pa', 'Paraguay': 'py', 'Peru': 'pe',
  'Poland': 'pl', 'Portugal': 'pt', 'Qatar': 'qa', 'Romania': 'ro',
  'Saudi Arabia': 'sa', 'Scotland': 'gb-sct', 'Senegal': 'sn', 'Serbia': 'rs',
  'Slovakia': 'sk', 'Slovenia': 'si', 'South Africa': 'za', 'South Korea': 'kr',
  'Spain': 'es', 'Sweden': 'se', 'Switzerland': 'ch', 'Syria': 'sy',
  'Tanzania': 'tz', 'Trinidad and Tobago': 'tt', 'Tunisia': 'tn',
  'Türkiye': 'tr', 'Ukraine': 'ua', 'United States': 'us', 'Uruguay': 'uy',
  'Uzbekistan': 'uz', 'Venezuela': 've', 'Wales': 'gb-wls',
};

function flagImg(team, altText) {
  const iso = TEAM_ISO[team];
  if (!iso) return `<div class="flag-placeholder" aria-hidden="true">⚽</div>`;
  return `<img class="flag" src="${FLAG_BASE}${iso}.png" alt="${esc(altText || team)}" loading="lazy" width="50" height="50">`;
}

// ── Poisson helpers ───────────────────────────────────────────────────────
function poissonPMF(k, lam) {
  if (lam <= 0) return k === 0 ? 1 : 0;
  let p = Math.exp(-lam);
  for (let i = 1; i <= k; i++) p *= lam / i;
  return p;
}

function topScoredlines(lH, lA, n = 5) {
  const probs = [];
  for (let h = 0; h <= 7; h++)
    for (let a = 0; a <= 7; a++)
      probs.push({ s: `${h}:${a}`, h, a, p: poissonPMF(h, lH) * poissonPMF(a, lA) });
  probs.sort((x, y) => y.p - x.p);
  return probs.slice(0, n);
}

// Probability grid P(home=h, away=a) for h,a in 0..max (independent Poisson).
function poissonGrid(lH, lA, max = 6) {
  const homeP = [], awayP = [];
  for (let i = 0; i <= max; i++) { homeP.push(poissonPMF(i, lH)); awayP.push(poissonPMF(i, lA)); }
  const grid = [];
  for (let h = 0; h <= max; h++) {
    const row = [];
    for (let a = 0; a <= max; a++) row.push(homeP[h] * awayP[a]);
    grid.push(row);
  }
  return grid;
}

// Expected Kicktipp points for tip (h,a), integrated over real outcomes 0..realMax.
// Mirrors scoreline.ev_optimize (real range 0..7 like config.MAX_GOALS).
function evPointsGrid(lH, lA, tipMax = 6, realMax = 7) {
  const homeP = [], awayP = [];
  for (let i = 0; i <= realMax; i++) { homeP.push(poissonPMF(i, lH)); awayP.push(poissonPMF(i, lA)); }
  const grid = [];
  for (let th = 0; th <= tipMax; th++) {
    const row = [];
    for (let ta = 0; ta <= tipMax; ta++) {
      let ev = 0;
      for (let rh = 0; rh <= realMax; rh++) {
        for (let ra = 0; ra <= realMax; ra++) {
          const p = homeP[rh] * awayP[ra];
          if (p < 1e-12) continue;
          ev += p * kicktippPoints(th, ta, rh, ra);
        }
      }
      row.push(ev);
    }
    grid.push(row);
  }
  return grid;
}

// Normalized Shannon entropy of the 1X2 distribution → 0 (certain) … 1 (coin-flip).
function outcomeEntropy(p) {
  const vals = [p.home, p.draw, p.away].filter(v => v > 0);
  const H = -vals.reduce((s, v) => s + v * Math.log(v), 0);
  return H / Math.log(3);
}

// ── Team kit colors [primary, secondary] ──────────────────────────────────
// Two colors per country (≈ home vs away kit) so that when two teams share a
// similar primary (e.g. two reds), matchColors() can fall back to a secondary
// and keep home/away visually distinct in every chart.
const TEAM_COLORS = {
  'Algeria': ['#006233', '#FFFFFF'], 'Argentina': ['#6CACE4', '#0A3161'],
  'Australia': ['#00843D', '#FFCD00'], 'Austria': ['#ED2939', '#1A1A1A'],
  'Belgium': ['#E30613', '#0A0A0A'], 'Bosnia-Herzegovina': ['#002F6C', '#FFD100'],
  'Brazil': ['#009C3B', '#FFDF00'], 'Canada': ['#D52B1E', '#1A1A1A'],
  'Cape Verde': ['#003893', '#CF142B'], 'Colombia': ['#FCD116', '#003893'],
  'Congo DR': ['#007FFF', '#CE1021'], 'Croatia': ['#FF0000', '#1565C0'],
  'Curaçao': ['#002B7F', '#FFD100'], 'Czechia': ['#11457E', '#D7141A'],
  'Ecuador': ['#FFD100', '#003893'], 'Egypt': ['#CE1126', '#1A1A1A'],
  'England': ['#1D3A8A', '#CF142B'], 'France': ['#0055A4', '#EF4135'],
  'Germany': ['#1A1A1A', '#D00000'], 'Ghana': ['#006B3F', '#FCD116'],
  'Haiti': ['#00209F', '#D21034'], 'Iran': ['#239F40', '#DA0000'],
  'Iraq': ['#CE1126', '#1A7A3D'], 'Ivory Coast': ['#FF8200', '#009E60'],
  'Japan': ['#1D2C5B', '#E60012'], 'Jordan': ['#007A3D', '#CE1126'],
  'Mexico': ['#006847', '#CE1126'], 'Morocco': ['#C1272D', '#006233'],
  'Netherlands': ['#FF6200', '#1A1A1A'], 'New Zealand': ['#0A0A0A', '#C8102E'],
  'Norway': ['#BA0C2F', '#00205B'], 'Panama': ['#005293', '#D21034'],
  'Paraguay': ['#D52B1E', '#0038A8'], 'Portugal': ['#C8102E', '#046A38'],
  'Qatar': ['#8A1538', '#1A1A1A'], 'Saudi Arabia': ['#006C35', '#1A1A1A'],
  'Scotland': ['#005EB8', '#1A1A1A'], 'Senegal': ['#00853F', '#E31B23'],
  'South Africa': ['#007749', '#FFB915'], 'South Korea': ['#C8102E', '#0A3161'],
  'Spain': ['#C60B1E', '#1A3A8F'], 'Sweden': ['#006AA7', '#FECC00'],
  'Switzerland': ['#D52B1E', '#1A1A1A'], 'Tunisia': ['#E70013', '#1A1A1A'],
  'Türkiye': ['#E30A17', '#1A1A1A'], 'United States': ['#0A3161', '#B31942'],
  'Uruguay': ['#5B9DD5', '#001489'], 'Uzbekistan': ['#0099B5', '#1EB53A'],
};
const FALLBACK_PALETTE = ['#6B7180', '#B0B4BD'];

function teamPalette(team) { return TEAM_COLORS[team] || FALLBACK_PALETTE; }
function teamColor(team) { return teamPalette(team)[0]; }

function _hexRgb(hex) {
  const h = hex.replace('#', '');
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}
// Perceptual "redmean" distance — good enough to detect clashing kit colors.
function colorDistance(a, b) {
  const [r1, g1, b1] = _hexRgb(a), [r2, g2, b2] = _hexRgb(b);
  const rm = (r1 + r2) / 2, dr = r1 - r2, dg = g1 - g2, db = b1 - b2;
  return Math.sqrt((2 + rm / 256) * dr * dr + 4 * dg * dg + (2 + (255 - rm) / 256) * db * db);
}
function _luminance(hex) {
  const [r, g, b] = _hexRgb(hex);
  return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
}
// Keep near-white kit colors visible on the light theme.
function chartSafe(hex) { return _luminance(hex) > 0.82 ? '#5B6472' : hex; }

const COLOR_CLASH = 130;  // below this distance, kits are too similar

// Resolve distinct {home, away} colors for a matchup, swapping to a secondary
// kit when the two primaries clash (like a real away-kit change).
function matchColors(homeTeam, awayTeam) {
  const [hp, hs] = teamPalette(homeTeam);
  const [ap, as] = teamPalette(awayTeam);
  let home = hp, away = ap;
  if (colorDistance(home, away) < COLOR_CLASH) {
    away = as;                                   // away switches kit
    if (colorDistance(home, away) < COLOR_CLASH) home = hs;  // both switch
  }
  return { home: chartSafe(home), away: chartSafe(away) };
}

// ── Kicktipp scoring (mirrors config.kicktipp_points, rules from metadata) ─
function kicktippPoints(tipH, tipA, realH, realA) {
  const rules = metadata.kicktipp_rules
    || { win: { tendency: 2, goal_diff: 3, exact: 4 }, draw: { tendency: 2, exact: 4 } };
  const tipSign  = Math.sign(tipH - tipA);
  const realSign = Math.sign(realH - realA);
  if (tipSign !== realSign) return 0;
  if (realSign === 0) {
    return (tipH === realH && tipA === realA) ? rules.draw.exact : rules.draw.tendency;
  }
  if (tipH === realH && tipA === realA) return rules.win.exact;
  if (tipH - tipA === realH - realA) return rules.win.goal_diff;
  return rules.win.tendency;
}

// ── Date helpers ──────────────────────────────────────────────────────────
function parseKickoff(ct) {
  if (!ct) return null;
  if (ct.includes('T')) return new Date(ct);
  // date-only "2026-06-14" — treat as UTC noon to avoid timezone shifts
  return new Date(ct + 'T12:00:00Z');
}

function formatTime(ct) {
  const d = parseKickoff(ct);
  if (!d) return '–:––';
  if (!ct.includes('T')) return '–:––';
  return d.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Berlin' });
}

function formatDate(ct) {
  const d = parseKickoff(ct);
  if (!d) return ct;
  return d.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', timeZone: 'Europe/Berlin' });
}

function isToday(ct) {
  const d = parseKickoff(ct);
  if (!d) return false;
  const now = new Date();
  const tz = 'Europe/Berlin';
  const toStr = dt => dt.toLocaleDateString('de-DE', { timeZone: tz });
  return toStr(d) === toStr(now);
}

function isTomorrow(ct) {
  const d = parseKickoff(ct);
  if (!d) return false;
  const tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  const tz = 'Europe/Berlin';
  const toStr = dt => dt.toLocaleDateString('de-DE', { timeZone: tz });
  return toStr(d) === toStr(tomorrow);
}

function maxDivergence(m) {
  const div = m.divergence || {};
  const disagreement = m.agreement?.same_tendency === false ? 0.08 : 0;
  return Math.max(...Object.values(div), disagreement, 0);
}

// ── Init ──────────────────────────────────────────────────────────────────
async function init() { // returns promise
  showSkeletons(4);
  try {
    const [dataRes, liveRes, resultsRes] = await Promise.allSettled([
      fetch(DATA_URL),
      fetch(LIVE_URL),
      fetch(RESULTS_URL),
    ]);
    if (dataRes.status !== 'fulfilled' || !dataRes.value.ok) {
      throw new Error(`HTTP ${dataRes.status === 'fulfilled' ? dataRes.value.status : 'fetch failed'}`);
    }
    const data = await dataRes.value.json();
    allMatches  = data.matches;
    metadata    = data.metadata;
    tournament  = data.tournament || {};
    tournamentProbs = data.tournament_probabilities || {};

    // results.json: persistent results history (optional, may 404 on old deploys)
    if (resultsRes.status === 'fulfilled' && resultsRes.value.ok) {
      try { _applyResults((await resultsRes.value.json()).results || []); } catch {}
    }
    // live.json preferred; fall back to the live block inside data.json
    let liveApplied = false;
    if (liveRes.status === 'fulfilled' && liveRes.value.ok) {
      try {
        _applyLiveData((await liveRes.value.json()).live || []);
        liveApplied = true;
      } catch {}
    }
    if (!liveApplied) _applyLiveData(data.live || []);
    renderMeta();
    renderTab();
    _scheduleLiveRefresh();
    registerSW();
    maybeShowInstallBanner();
    // Wake Lock in standalone / matchday tab
    if (window.matchMedia('(display-mode: standalone)').matches) requestWakeLock();
    // Badge: count matches starting today
    const todayCount = allMatches.filter(m => isToday(m.commence_time)).length;
    updateBadge(todayCount);
  } catch (e) {
    document.getElementById('app').innerHTML =
      `<div class="error">Fehler beim Laden der Daten: ${esc(e.message)}
       <code>Lokal: python3 -m http.server aus dem docs/-Ordner starten</code></div>`;
  }
}

// ── Meta bar ──────────────────────────────────────────────────────────────
function renderMeta() {
  const el = document.getElementById('meta');
  if (!metadata.generated_at) return;
  const dt = new Date(metadata.generated_at);
  const time = dt.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Berlin' });
  const ageH = (Date.now() - dt.getTime()) / 36e5;
  if (ageH > 26) {
    // Data older than one daily build cycle — surface silently failing workflows
    const date = dt.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', timeZone: 'Europe/Berlin' });
    el.innerHTML = `<span class="pill pill-stale"></span>${date} · veraltet`;
    el.title = `Daten zuletzt aktualisiert: ${date} ${time} Uhr — der tägliche Workflow läuft evtl. nicht.`;
  } else {
    el.innerHTML = `<span class="pill"></span>${time} Uhr`;
    el.title = '';
  }
}

// ── Tab switching ─────────────────────────────────────────────────────────
function setTab(tab) {
  currentTab = tab;
  document.querySelectorAll('.tab').forEach(b => {
    const active = b.id === `tab-${tab}`;
    b.classList.toggle('active', active);
    b.setAttribute('aria-selected', String(active));
  });
  renderTab();
}
window.setTab = setTab;

function renderTab() {
  const app = document.getElementById('app');
  if (currentTab === 'modell')  { renderModel(app);   return; }
  if (currentTab === 'gruppen') { renderGruppen(app); return; }
  if (currentTab === 'baum')    { renderBaum(app);    return; }
  if (currentTab === 'verlauf') { renderVerlauf(app); return; }

  let matches = [...allMatches];

  if (currentTab === 'heute') {
    matches = matches.filter(m => isToday(m.commence_time) || isTomorrow(m.commence_time));
    matches.sort((a, b) => (parseKickoff(a.commence_time) || 0) - (parseKickoff(b.commence_time) || 0));
  } else if (currentTab === 'alle') {
    matches.sort((a, b) => (parseKickoff(a.commence_time) || 0) - (parseKickoff(b.commence_time) || 0));
  }

  app.innerHTML = '';

  if (currentTab === 'heute') {
    renderLiveSection(app);
    renderHeuteStats(app, matches);
    if (matches.length === 0) {
      const upcoming = [...allMatches]
        .filter(m => (parseKickoff(m.commence_time) || 0) >= Date.now())
        .sort((a, b) => parseKickoff(a.commence_time) - parseKickoff(b.commence_time))
        .slice(0, 3);
      if (upcoming.length) {
        upcoming.forEach((m, i) => app.appendChild(buildCard(m, i)));
      }
    } else {
      matches.forEach((m, i) => app.appendChild(buildCard(m, i)));
    }
    animateBars();
    return;
  }

  if (currentTab === 'alle') {
    renderSearchFilter(app);
    const calContainer = document.createElement('div');
    calContainer.id = 'cal-container';
    app.appendChild(calContainer);
    renderCalendar(calContainer);
    animateBars();
    return;
  }
}

// ── Live score section (Heute tab) ────────────────────────────────────────
function renderLiveSection(app) {
  const relevant = liveScores.filter(e => e.is_live || e.is_halftime || e.is_done);
  if (!relevant.length) return;

  const section = document.createElement('div');
  section.className = 'live-section';
  section.id = 'live-section';

  relevant.forEach(e => {
    const isLive = e.is_live;
    const isHT   = e.is_halftime;
    const isDone = e.is_done;

    const sh = e.score_home ?? '–';
    const sa = e.score_away ?? '–';
    const hth = e.halftime_home;
    const hta = e.halftime_away;

    let statusHtml;
    if (isLive) {
      statusHtml = `<span class="ls-badge ls-live"><span class="live-dot"></span>${e.minute ? e.minute + '\'' : 'Live'}</span>`;
    } else if (isHT) {
      statusHtml = `<span class="ls-badge ls-halftime">⏸ Halbzeit</span>`;
    } else {
      statusHtml = `<span class="ls-badge ls-done">✓ Beendet</span>`;
    }

    const htLine = (hth !== null && hth !== undefined && hta !== null && hta !== undefined)
      ? `<div class="ls-ht">HZ ${hth}:${hta}</div>` : '';

    const card = document.createElement('div');
    card.className = `live-card${isLive ? ' live-card--live' : isHT ? ' live-card--ht' : ' live-card--done'}`;
    card.innerHTML = `
      <div class="ls-header">
        ${statusHtml}
        ${e.stage ? `<span class="ls-stage">${esc(e.stage)}</span>` : ''}
      </div>
      <div class="ls-fixture">
        <div class="ls-team">
          ${flagImg(e.home_team, e.home_team)}
          <span class="ls-name">${esc(e.home_team)}</span>
        </div>
        <div class="ls-score">
          <div class="ls-score-line"><span class="ls-goals">${sh}</span><span class="ls-sep">:</span><span class="ls-goals">${sa}</span></div>
          ${htLine}
        </div>
        <div class="ls-team ls-team-away">
          ${flagImg(e.away_team, e.away_team)}
          <span class="ls-name">${esc(e.away_team)}</span>
        </div>
      </div>
    `;
    section.appendChild(card);
  });

  app.appendChild(section);
}

// ── Heute: stat widgets ───────────────────────────────────────────────────
function renderHeuteStats(app, todayMatches) {
  // Use today's matches, fallback to next 3 upcoming if empty
  const src = todayMatches.length > 0 ? todayMatches
    : [...allMatches]
        .filter(m => (parseKickoff(m.commence_time) || 0) >= Date.now())
        .sort((a, b) => parseKickoff(a.commence_time) - parseKickoff(b.commence_time))
        .slice(0, 3);
  if (!src.length) return;

  // Tendency distribution
  let pH = 0, pD = 0, pA = 0;
  src.forEach(m => {
    const p = m.sources?.uanalyse?.p ?? m.sources?.odds_consensus?.p;
    if (p) { pH += p.home; pD += p.draw; pA += p.away; }
  });
  const total = pH + pD + pA || 1;
  const tH = pH / total, tD = pD / total, tA = pA / total;

  // xG leader
  let xgMax = 0, xgTeam = '', xgMatch = null;
  src.forEach(m => {
    const eg = m.expected_goals || {};
    if ((eg.home || 0) > xgMax) { xgMax = eg.home; xgTeam = m.home_team; xgMatch = m; }
    if ((eg.away || 0) > xgMax) { xgMax = eg.away; xgTeam = m.away_team; xgMatch = m; }
  });

  // Agreement rate
  const withBoth = src.filter(m => m.agreement?.same_tendency !== null && m.agreement?.same_tendency !== undefined);
  const agreeRate = withBoth.length ? withBoth.filter(m => m.agreement.same_tendency).length / withBoth.length : null;

  const row = document.createElement('div');
  row.className = 'stat-widgets';
  row.innerHTML = `
    <div class="stat-widget glass" title="Heimsieg / Unentschieden / Auswärtssieg — gemittelt über alle heutigen Spiele">
      <div class="sw-label">Tendenz heute</div>
      <div class="sw-donut" style="--h:${(tH*360).toFixed(0)}deg;--d:${((tH+tD)*360).toFixed(0)}deg"></div>
      <div class="sw-sub">${pct(tH)} / ${pct(tD)} / ${pct(tA)}</div>
    </div>
    <div class="stat-widget glass" title="Expected Goals: Erwartete Tore laut Modell">
      <div class="sw-label">Offensivstes Team</div>
      <div class="sw-main">${xgTeam ? flagImg(xgTeam, xgTeam) : '–'}</div>
      <div class="sw-sub">${xgMax > 0 ? xgMax.toFixed(1) + ' erw. Tore' : '–'}</div>
    </div>
    <div class="stat-widget glass" title="Wie oft stimmen Modell und Buchmacher in der Tendenz überein">
      <div class="sw-label">Modell-Konsens</div>
      ${agreeRate !== null
        ? `<div class="sw-pct" data-target="${Math.round(agreeRate*100)}">0%</div>
           <div class="sw-sub">Deckung</div>`
        : `<div class="sw-pct sw-pct--empty">–</div>
           <div class="sw-sub">Deckung</div>`}
    </div>
  `;
  app.appendChild(row);

  // Count-up animation for the consensus percentage
  const pctEl = row.querySelector('.sw-pct[data-target]');
  if (pctEl) animateConsensus(pctEl);

  const eyebrow = document.createElement('div');
  eyebrow.className = 'eyebrow';
  eyebrow.textContent = todayMatches.length > 0
    ? `Heute & Morgen · ${todayMatches.length} Spiel${todayMatches.length !== 1 ? 'e' : ''}`
    : `Keine Spiele heute — nächste ${src.length} Spiel${src.length !== 1 ? 'e' : ''}`;
  app.appendChild(eyebrow);
}

// ── Alle Spiele: search + filter bar ─────────────────────────────────────
function renderSearchFilter(app) {
  const teams = [...new Set(allMatches.flatMap(m => [m.home_team, m.away_team]))].sort();

  // Pin active team chip to the front; "Alle" always first
  const activeName = teams.find(t => t.toLowerCase() === filterTeam) || null;
  const otherTeams = teams.filter(t => t.toLowerCase() !== filterTeam);
  const orderedTeams = activeName ? [activeName, ...otherTeams] : teams;

  const wrap = document.createElement('div');
  wrap.className = 'search-bar';
  wrap.innerHTML = `
    <input class="search-input glass" type="search" placeholder="Team suchen…"
      value="${esc(searchQuery)}" oninput="onSearch(this.value)" aria-label="Team suchen"
      autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false">
    <div class="filter-chips" id="filter-chips">
      <button class="chip ${!filterTeam ? 'active' : ''}" onclick="setFilter('')">Alle</button>
      ${orderedTeams.map(t => `
        <button class="chip ${filterTeam === t.toLowerCase() ? 'active' : ''}"
          onclick="setFilter('${esc(t.toLowerCase())}')"
          title="${esc(t)}">
          ${flagImg(t, t)}<span>${esc(t)}</span>
        </button>
      `).join('')}
    </div>
  `;
  app.appendChild(wrap);
}

window.onSearch = function(val) {
  searchQuery = val;
  const calContainer = document.getElementById('cal-container');
  if (calContainer) {
    renderCalendar(calContainer);
    animateBars();
  } else {
    renderTab();
  }
};
window.setFilter = function(team) {
  // Toggle: tapping the active filter deselects it
  filterTeam = (filterTeam === team) ? '' : team;
  searchQuery = '';
  renderTab();
};

// ── Kalender-Ansicht (Spiele grouped by date) ─────────────────────────────
function renderCalendar(container) {
  container.innerHTML = '';
  const q = searchQuery.trim().toLowerCase();
  const ft = filterTeam.toLowerCase();
  let filtered = [...allMatches].sort((a, b) =>
    (parseKickoff(a.commence_time) || 0) - (parseKickoff(b.commence_time) || 0));

  if (q || ft) {
    filtered = filtered.filter(m => {
      const h = m.home_team.toLowerCase(), a = m.away_team.toLowerCase();
      if (ft && h !== ft && a !== ft) return false;
      if (q && !h.includes(q) && !a.includes(q)) return false;
      return true;
    });
  }

  if (!filtered.length) {
    const el = document.createElement('div');
    el.className = 'eyebrow';
    el.textContent = 'Keine Spiele gefunden.';
    container.appendChild(el);
    return;
  }
  const app = container;

  // Group by date
  const byDate = {};
  filtered.forEach(m => {
    const d = m.commence_time.slice(0, 10);
    if (!byDate[d]) byDate[d] = [];
    byDate[d].push(m);
  });

  const sortedDates = Object.keys(byDate).sort();
  const today = new Date();
  const todayStr = today.toLocaleDateString('de-DE', { timeZone: 'Europe/Berlin' });
  const tomorrowStr = new Date(today.getTime() + 86400000)
    .toLocaleDateString('de-DE', { timeZone: 'Europe/Berlin' });

  function dateLabel(date) {
    const d = parseKickoff(date);
    if (!d) return date;
    const ds = d.toLocaleDateString('de-DE', { timeZone: 'Europe/Berlin' });
    const weekday = d.toLocaleDateString('de-DE', { weekday: 'short', timeZone: 'Europe/Berlin' });
    const dm = d.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', timeZone: 'Europe/Berlin' });
    if (ds === todayStr) return `⚽ Heute · ${dm}`;
    if (ds === tomorrowStr) return `Morgen · ${dm}`;
    return `${weekday} · ${dm}`;
  }

  // Date scrubber
  const scrubber = document.createElement('div');
  scrubber.className = 'date-scrubber';
  scrubber.id = 'date-scrubber';
  sortedDates.forEach(date => {
    const pill = document.createElement('button');
    pill.className = 'date-pill';
    pill.dataset.date = date;
    pill.textContent = dateLabel(date).replace('⚽ ', '');
    pill.onclick = () => {
      const anchor = document.getElementById('cal-' + date);
      if (anchor) anchor.scrollIntoView({ behavior: 'smooth', block: 'start' });
    };
    scrubber.appendChild(pill);
  });
  app.appendChild(scrubber);

  // Date groups
  let cardIdx = 0;
  const headerEls = [];
  sortedDates.forEach(date => {
    const d = parseKickoff(date);
    const ds = d ? d.toLocaleDateString('de-DE', { timeZone: 'Europe/Berlin' }) : '';
    const isToday = ds === todayStr;

    const header = document.createElement('div');
    header.className = 'cal-header' + (isToday ? ' cal-today' : '');
    header.id = 'cal-' + date;
    header.dataset.date = date;
    const count = byDate[date].length;
    header.textContent = `${dateLabel(date)} · ${count} Spiel${count !== 1 ? 'e' : ''}`;
    app.appendChild(header);
    headerEls.push(header);

    byDate[date].forEach(m => {
      app.appendChild(buildCard(m, cardIdx++));
    });
  });

  // IntersectionObserver for sticky header elevation + scrubber sync
  requestAnimationFrame(() => {
    const io = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        // When header hits top (not intersecting = stuck)
        entry.target.classList.toggle('stuck', !entry.isIntersecting);
        // Sync scrubber pill
        if (!entry.isIntersecting) {
          const date = entry.target.dataset.date;
          document.querySelectorAll('.date-pill').forEach(p => {
            p.classList.toggle('active', p.dataset.date === date);
          });
        }
      });
    }, {
      rootMargin: `-${(parseInt(getComputedStyle(document.querySelector('header'))?.height) || 60) + 1}px 0px 0px 0px`,
      threshold: 1,
    });
    headerEls.forEach(h => io.observe(h));
  });
}

// ── Card builder ──────────────────────────────────────────────────────────
function buildCard(match, index) {
  const article = document.createElement('article');
  article.className = 'card glass';
  article.style.animationDelay = `${Math.min(index * 0.055, 0.5)}s`;
  article.dataset.id = match.id;

  const tip    = match.recommended_tip;
  const modal  = match.modal_scoreline;
  const ua     = match.sources?.uanalyse;
  const oddsC  = match.sources?.odds_consensus;
  const agree  = match.agreement || {};
  const div    = match.divergence || {};
  const maxDiv = Math.max(...Object.values(div), 0);

  // Primary probabilities: prefer uanalyse, fallback to odds_consensus
  const primaryP = ua?.p ?? oddsC?.p;

  const time = formatTime(match.commence_time);
  const date = formatDate(match.commence_time);
  const stage = match.stage || '';

  // Source tag on tip
  const srcClass = tip?.based_on === 'uanalyse' ? 'srctag' : 'srctag srctag--secondary';
  const srcLabel = tip?.based_on === 'uanalyse' ? 'uanalyse' : 'Wettbüros';

  // Modal note
  const tipMatchesModal = modal && tip && tip.home === modal.home && tip.away === modal.away;
  const modalNote = tipMatchesModal
    ? '= wahrscheinlichstes Ergebnis'
    : modal ? `Modal: ${modal.home}:${modal.away} (${pct(modal.probability)})` : '';

  // Badges
  const badges = [];
  if (agree.same_tendency === false) {
    badges.push(`<div class="badge-sources" title="${esc(agree.note || '')}">⚡ Quellen uneinig — Tendenz weicht ab</div>`);
  }
  if (maxDiv >= DIVERGENCE_THRESHOLD) {
    badges.push(`<div class="badge-warn">⚡ Bücher uneinig (max Δ ${pct(maxDiv)})</div>`);
  }

  // Real-world result line (live score or final score + earned points)
  let resultLine = '';
  const rEntry = _resultEntry(match);
  if (rEntry && rEntry.score_home != null) {
    if (rEntry.is_done) {
      const pts = tip ? kicktippPoints(tip.home, tip.away, rEntry.score_home, rEntry.score_away) : null;
      const cls = pts == null ? '' : pts >= 3 ? 'vp-high' : pts > 0 ? 'vp-mid' : 'vp-zero';
      resultLine = `<div class="result-line">
        <span class="rl-label">✓ Endstand</span>
        <span class="rl-score">${rEntry.score_home}:${rEntry.score_away}</span>
        ${pts != null ? `<span class="verlauf-pts ${cls}">+${pts} Pkt</span>` : ''}
      </div>`;
    } else if (rEntry.is_live || rEntry.is_halftime) {
      resultLine = `<div class="result-line result-line--live">
        <span class="rl-label"><span class="live-dot"></span>${rEntry.is_halftime ? 'Halbzeit' : (rEntry.minute ? rEntry.minute + '\'' : 'Live')}</span>
        <span class="rl-score">${rEntry.score_home}:${rEntry.score_away}</span>
      </div>`;
    }
  }

  // Drawer content is built lazily on first expand (heavy: heatmap + curves).

  article.innerHTML = `
    <div class="ctop">
      <span class="ko">${time !== '–:––' ? `${date} · ${time} Uhr` : date}</span>
      <span class="badge-stage">${esc(stage)}</span>
    </div>
    <div class="fixture">
      <div class="team">
        ${flagImg(match.home_team)}
        <span class="name">${esc(match.home_team)}</span>
      </div>
      <div class="score glass">
        <b>${tip ? tip.home : '–'}</b><span>:</span><b>${tip ? tip.away : '–'}</b>
      </div>
      <div class="team away">
        ${flagImg(match.away_team)}
        <span class="name">${esc(match.away_team)}</span>
      </div>
    </div>
    ${tip ? `
    <div class="tipmeta">
      empfohlener Tipp ·
      <span class="ev">+${tip.expected_points} Pkt</span>
      <span class="${srcClass}">${esc(srcLabel)}</span>
      ${modalNote ? `<span style="font-size:11px;color:var(--muted)">${esc(modalNote)}</span>` : ''}
    </div>` : ''}
    ${resultLine}

    ${primaryP ? `
    <div class="data">
      ${renderBar(primaryP)}
      <div class="key">
        <i class="kh">Heimsieg</i>
        <i class="kd">Unentschieden</i>
        <i class="ka">Auswärtssieg</i>
      </div>
      ${ua && oddsC ? renderOddsCompare(oddsC.p) : ''}
    </div>` : ''}

    ${badges.join('')}

    <div class="drawer"><div class="drawer-inner" data-built="0"></div></div>
    <button class="expand" onclick="toggleCard(this)" aria-expanded="false">
      Details <span class="chev" aria-hidden="true">⌄</span>
    </button>
  `;
  return article;
}

function renderBar(p) {
  const h = Math.round(p.home * 100);
  const d = Math.round(p.draw * 100);
  const a = 100 - h - d;
  return `<div class="bar">
    <div class="seg h" style="--w:${h}%">${h}%</div>
    <div class="seg d" style="--w:${d}%">${d}%</div>
    <div class="seg a" style="--w:${a}%">${a}%</div>
  </div>`;
}

function renderMiniBar(p) {
  const h = Math.round(p.home * 100);
  const d = Math.round(p.draw * 100);
  const a = 100 - h - d;
  return `<div class="mini">
    <div class="seg h" style="width:${h}%"></div>
    <div class="seg d" style="width:${d}%"></div>
    <div class="seg a" style="width:${a}%"></div>
  </div>`;
}

function renderOddsCompare(p) {
  const h = Math.round(p.home * 100);
  const d = Math.round(p.draw * 100);
  const a = 100 - h - d;
  return `<div class="srcrow" style="margin-top:10px;padding-top:10px;border-top:1px solid var(--hair)">
    <span class="srclab" style="color:var(--muted);font-size:11px">Wettbüros:</span>
    <span style="font-size:12px;font-weight:600;display:flex;gap:10px">
      <span style="color:#60a5fa"><span style="color:var(--muted);font-size:10px;margin-right:2px">H</span>${h}%</span>
      <span style="color:#9ca3af"><span style="color:var(--muted);font-size:10px;margin-right:2px">U</span>${d}%</span>
      <span style="color:#fb923c"><span style="color:var(--muted);font-size:10px;margin-right:2px">A</span>${a}%</span>
    </span>
  </div>`;
}

function buildDrawer(match, ua, oddsC) {
  const lambda = ua?.lambda ?? match.expected_goals;
  let html = '<div class="data" style="margin:0 12px 12px">';

  // 6 · Flag-tinted hero + entropy indicator
  html += buildDetailHero(match);

  // 1+2 · Result heatmap (probability ↔ expected points) and 3 · Poisson curves
  if (lambda?.home != null) {
    html += buildHeatmap(match, lambda);
    html += buildPoissonCurves(match, lambda);
  }

  // 4 · Source detail (uanalyse vs odds + divergence)
  html += buildSourceDetail(match, ua, oddsC);

  // 5 · What's at stake (tournament probabilities)
  html += buildStakes(match);

  // Compact numeric xG (anchors the curves above)
  if (lambda?.home != null) {
    const hW = Math.min(lambda.home / XG_MAX * 100, 100).toFixed(1);
    const aW = Math.min(lambda.away / XG_MAX * 100, 100).toFixed(1);
    const hAbbr = match.home_team.slice(0, 3).toUpperCase();
    const aAbbr = match.away_team.slice(0, 3).toUpperCase();
    html += `<div class="dt">Erwartete Tore (λ)</div>
      <div class="xg-row-d">
        <span class="l">${hAbbr}</span>
        <div class="xg-track"><div class="xg-fill" style="--xw:${hW}%;background:var(--home)"></div></div>
        <span class="v">${lambda.home.toFixed(2)}</span>
      </div>
      <div class="xg-row-d">
        <span class="l">${aAbbr}</span>
        <div class="xg-track"><div class="xg-fill" style="--xw:${aW}%;background:var(--away)"></div></div>
        <span class="v">${lambda.away.toFixed(2)}</span>
      </div>`;
  }

  // Bookmakers (collapsible)
  if (match.bookmakers?.length > 0) {
    const bkId = `bk-${match.id || Math.random().toString(36).slice(2)}`;
    html += `
      <button class="bk-toggle" aria-expanded="false" aria-controls="${bkId}"
        onclick="toggleBookmakers(this)">
        <span>Buchmacher-Quoten (${match.bookmakers.length})</span>
        <svg class="bk-chevron" viewBox="0 0 24 24" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg>
      </button>
      <div id="${bkId}" class="bk-list" hidden>`;
    match.bookmakers.forEach(bk => {
      const p = bk.probabilities;
      html += `<div class="book-row">
        <span class="book-name" title="${esc(bk.title)}">${esc(bk.title)}</span>
        <div class="book-bar">
          <div class="bh" style="flex:${p.home}"></div>
          <div class="bd" style="flex:${p.draw}"></div>
          <div class="ba" style="flex:${p.away}"></div>
        </div>
        <span class="book-margin">Marge ${pct(bk.overround)}</span>
      </div>`;
    });
    html += `</div>`;
  }

  html += '</div>';
  return html;
}

// ── 6 · Flag-tinted hero + entropy ("Münzwurf → klarer Favorit") ───────────
function buildDetailHero(match) {
  const p = match.sources?.uanalyse?.p ?? match.sources?.odds_consensus?.p;
  const { home: cH, away: cA } = matchColors(match.home_team, match.away_team);
  let entropyHtml = '';
  if (p) {
    const ent = outcomeEntropy(p);          // 1 = coin-flip, 0 = certain
    const markX = ((1 - ent) * 100).toFixed(1);   // left = Münzwurf, right = klarer Favorit
    const verdict = ent > 0.92 ? 'Münzwurf'
      : ent > 0.7 ? 'offenes Spiel'
      : ent > 0.45 ? 'leichter Favorit'
      : 'klarer Favorit';
    entropyHtml = `
      <div class="entro">
        <div class="entro-track"><div class="entro-marker" style="left:${markX}%"></div></div>
        <div class="entro-labels">
          <span>Münzwurf</span>
          <span class="entro-verdict">${verdict}</span>
          <span>klarer Favorit</span>
        </div>
      </div>`;
  }
  return `
    <div class="detail-hero" style="--cH:${cH};--cA:${cA}">
      <div class="dh-teams">
        <span class="dh-team"><span class="dh-dot" style="background:${cH}"></span>${esc(match.home_team)}</span>
        <span class="dh-vs">gegen</span>
        <span class="dh-team away">${esc(match.away_team)}<span class="dh-dot" style="background:${cA}"></span></span>
      </div>
      ${entropyHtml}
    </div>`;
}

// ── 1+2 · Result heatmap (probability ↔ expected Kicktipp points) ──────────
function hmCellBg(region, t, mode) {
  t = Math.max(0, Math.min(1, t));
  const a = (0.06 + 0.84 * Math.pow(t, 0.85)).toFixed(3);
  if (mode === 'pts') return `rgba(52,199,89,${a})`;                  // --good ramp
  const rgb = region === 'home' ? '0,122,255'                        // --home
    : region === 'away' ? '255,149,0'                                 // --away
    : '142,142,147';                                                  // --draw
  return `rgba(${rgb},${a})`;
}

function heatmapReadout(h, a, prob, pts, flags) {
  const tags = [];
  if (flags.ev) tags.push('<span class="hm-tag ev">★ EV-optimal</span>');
  if (flags.modal) tags.push('<span class="hm-tag modal">◆ wahrscheinlichstes</span>');
  return `
    <span class="hm-ro-score">${h}:${a}</span>
    <span class="hm-ro-stats">
      <b>${(prob * 100).toFixed(1)}%</b> Wahrscheinlichkeit ·
      <b>${pts.toFixed(2)}</b> Ø Punkte
    </span>
    ${tags.length ? `<span class="hm-ro-tags">${tags.join('')}</span>` : ''}`;
}

function buildHeatmap(match, lambda) {
  const MAXG = 6;
  const probGrid = poissonGrid(lambda.home, lambda.away, MAXG);
  const ptsGrid  = evPointsGrid(lambda.home, lambda.away, MAXG, 7);
  let maxProb = 0, maxPts = 0;
  for (let h = 0; h <= MAXG; h++) for (let a = 0; a <= MAXG; a++) {
    if (probGrid[h][a] > maxProb) maxProb = probGrid[h][a];
    if (ptsGrid[h][a] > maxPts) maxPts = ptsGrid[h][a];
  }
  const tip = match.recommended_tip, modal = match.modal_scoreline;
  const tipIsModal = tip && modal && tip.home === modal.home && tip.away === modal.away;

  let cells = `<div class="hm-corner" aria-hidden="true"><i>Heim ↓</i><i>Gast →</i></div>`;
  for (let a = 0; a <= MAXG; a++) cells += `<div class="hm-axis top">${a}</div>`;
  for (let h = 0; h <= MAXG; h++) {
    cells += `<div class="hm-axis left">${h}</div>`;
    for (let a = 0; a <= MAXG; a++) {
      const prob = probGrid[h][a], pts = ptsGrid[h][a];
      const region = h > a ? 'home' : h < a ? 'away' : 'draw';
      const isEV = tip && h === tip.home && a === tip.away;
      const isModal = modal && h === modal.home && a === modal.away;
      const cls = ['hm-cell', region];
      if (isEV) cls.push('ev', 'sel');
      if (isModal) cls.push('modal');
      const mark = isEV ? '★' : isModal ? '◆' : '';
      cells += `<button type="button" class="${cls.join(' ')}"
        style="--bgp:${hmCellBg(region, prob / maxProb, 'prob')};--bgpts:${hmCellBg(region, pts / maxPts, 'pts')}"
        data-h="${h}" data-a="${a}" data-prob="${prob.toFixed(5)}" data-pts="${pts.toFixed(3)}"
        onclick="selectHmCell(this)"
        aria-label="Tipp ${h} zu ${a}: ${(prob * 100).toFixed(1)} Prozent, ${pts.toFixed(2)} erwartete Punkte">${mark ? `<span class="hm-mk">${mark}</span>` : ''}</button>`;
    }
  }

  const evH = tip ? Math.min(tip.home, MAXG) : 0;
  const evA = tip ? Math.min(tip.away, MAXG) : 0;
  const caption = !tip ? ''
    : tipIsModal
      ? `<p class="hm-caption">Der EV-optimale Tipp <b>${tip.home}:${tip.away}</b> ist zugleich das wahrscheinlichste Ergebnis.</p>`
      : `<p class="hm-caption">★ Der EV-optimale Tipp <b>${tip.home}:${tip.away}</b> schlägt das wahrscheinlichste Ergebnis ◆ <b>${modal.home}:${modal.away}</b> — über <em>alle</em> Ausgänge gemittelt bringt er mehr Kicktipp-Punkte.</p>`;

  return `
    <div class="dt">Ergebnis-Matrix — warum dieser Tipp?</div>
    <div class="hm-modes" role="tablist">
      <button type="button" class="hm-mode active" data-mode="prob" role="tab" aria-selected="true" onclick="setHeatmapMode(this)">Wahrscheinlichkeit</button>
      <button type="button" class="hm-mode" data-mode="pts" role="tab" aria-selected="false" onclick="setHeatmapMode(this)">Ø Punkte</button>
    </div>
    <div class="heatmap mode-prob">
      <div class="hm-grid">${cells}</div>
      <div class="hm-readout">${heatmapReadout(evH, evA, probGrid[evH][evA], ptsGrid[evH][evA], { ev: true, modal: tipIsModal })}</div>
      <div class="hm-legend">
        <span><i class="hm-lg-h"></i>Heimsieg</span>
        <span><i class="hm-lg-d"></i>Remis</span>
        <span><i class="hm-lg-a"></i>Auswärtssieg</span>
      </div>
      ${caption}
    </div>`;
}

// ── 3 · Two overlaid Poisson curves (home-λ vs away-λ) ─────────────────────
function buildPoissonCurves(match, lambda) {
  const N = 7, W = 300, H = 116, padL = 8, padR = 8, padT = 8, padB = 18;
  const hP = [], aP = [];
  for (let k = 0; k <= N; k++) { hP.push(poissonPMF(k, lambda.home)); aP.push(poissonPMF(k, lambda.away)); }
  const yMax = Math.max(...hP, ...aP) * 1.12 || 1;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const X = k => padL + (k / N) * plotW;
  const Y = v => padT + plotH - (v / yMax) * plotH;
  const line = P => P.map((v, k) => `${k === 0 ? 'M' : 'L'}${X(k).toFixed(1)},${Y(v).toFixed(1)}`).join(' ');
  const area = P => `${line(P)} L${X(N).toFixed(1)},${(padT + plotH).toFixed(1)} L${X(0).toFixed(1)},${(padT + plotH).toFixed(1)} Z`;
  const { home: cH, away: cA } = matchColors(match.home_team, match.away_team);
  let axis = '';
  for (let k = 0; k <= N; k++) axis += `<text class="pc-axt" x="${X(k).toFixed(1)}" y="${H - 5}" text-anchor="middle">${k}</text>`;

  return `
    <div class="dt">Torverteilung (Poisson)</div>
    <svg class="pcurve" viewBox="0 0 ${W} ${H}" role="img" aria-label="Poisson-Torverteilung ${esc(match.home_team)} gegen ${esc(match.away_team)}">
      <path d="${area(hP)}" fill="${cH}" opacity=".13"/>
      <path d="${area(aP)}" fill="${cA}" opacity=".13"/>
      <path d="${line(hP)}" fill="none" stroke="${cH}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
      <path d="${line(aP)}" fill="none" stroke="${cA}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
      ${hP.map((v, k) => `<circle cx="${X(k).toFixed(1)}" cy="${Y(v).toFixed(1)}" r="2.1" fill="${cH}"/>`).join('')}
      ${aP.map((v, k) => `<circle cx="${X(k).toFixed(1)}" cy="${Y(v).toFixed(1)}" r="2.1" fill="${cA}"/>`).join('')}
      ${axis}
    </svg>
    <div class="pc-legend">
      <span><i class="pc-sw" style="background:${cH}"></i>${esc(match.home_team)} · λ ${lambda.home.toFixed(2)}</span>
      <span><i class="pc-sw" style="background:${cA}"></i>${esc(match.away_team)} · λ ${lambda.away.toFixed(2)}</span>
    </div>`;
}

// ── 4 · Source detail (uanalyse vs odds consensus + divergence) ────────────
function buildSourceDetail(match, ua, oddsC) {
  if (!ua && !oddsC) return '';
  const row = (lab, p) => p
    ? `<div class="srcrow"><span class="srclab">${lab}</span>${renderMiniBar(p)}<span class="src-nums">${pct(p.home)} / ${pct(p.draw)} / ${pct(p.away)}</span></div>`
    : '';
  let html = `<div class="dt">Quellen im Detail</div>`;
  if (ua) html += row('uanalyse', ua.p);
  if (oddsC) html += row('Wettbüros', oddsC.p);

  const div = match.divergence;
  if (ua && oddsC && div && div.home != null) {
    const maxD = Math.max(div.home, div.draw, div.away);
    const agree = match.agreement?.same_tendency;
    html += `
      <div class="src-div">
        <span class="src-div-lab">Divergenz |uanalyse − Wettbüros|</span>
        <div class="src-div-grid">
          <div><i>Heim</i><b>${pct(div.home)}</b></div>
          <div><i>Remis</i><b>${pct(div.draw)}</b></div>
          <div><i>Gast</i><b>${pct(div.away)}</b></div>
        </div>
        <div class="src-verdict ${agree === false ? 'disagree' : 'agree'}">
          ${agree === false ? '⚡ Tendenz weicht ab' : '✓ Tendenz übereinstimmend'} · max Δ ${pct(maxD)}
        </div>
      </div>`;
  }
  return html;
}

// ── 5 · What's at stake (tournament probabilities per team) ────────────────
function buildStakes(match) {
  const tH = tournamentProbs[match.home_code], tA = tournamentProbs[match.away_code];
  if (!tH && !tA) return '';
  const { home: cH, away: cA } = matchColors(match.home_team, match.away_team);
  const miles = [
    ['Gruppensieg', 'prob_win_group'],
    ['Achtelfinale', 'prob_reach_round_of_32'],
    ['Viertelfinale', 'prob_reach_quarterfinals'],
    ['Halbfinale', 'prob_reach_semifinals'],
    ['Finale', 'prob_reach_final'],
    ['Titel', 'prob_champion'],
  ];
  let rows = '';
  miles.forEach(([lab, key]) => {
    const vH = tH?.[key], vA = tA?.[key];
    if (vH == null && vA == null) return;
    rows += `
      <div class="stake-row">
        <div class="stake-side h">
          <span class="stake-val">${vH != null ? pct(vH) : '–'}</span>
          <div class="stake-bar mir"><div class="stake-fill" style="width:${vH != null ? (vH * 100).toFixed(1) : 0}%;background:${cH}"></div></div>
        </div>
        <span class="stake-lab">${lab}</span>
        <div class="stake-side a">
          <div class="stake-bar"><div class="stake-fill" style="width:${vA != null ? (vA * 100).toFixed(1) : 0}%;background:${cA}"></div></div>
          <span class="stake-val">${vA != null ? pct(vA) : '–'}</span>
        </div>
      </div>`;
  });
  if (!rows) return '';
  return `
    <div class="dt">Was steht auf dem Spiel</div>
    <div class="stake-head">
      <span style="color:${cH}">${esc(match.home_team)}</span>
      <span style="color:${cA}">${esc(match.away_team)}</span>
    </div>
    <div class="stakes">${rows}</div>`;
}

// ── Group standings from real results ─────────────────────────────────────
function computeGroupStandings() {
  const table = {};  // code → standings row
  const ensureRow = (code, team, group) => {
    if (!table[code]) table[code] = { code, team, group, games: 0, w: 0, d: 0, l: 0, gf: 0, ga: 0, pts: 0 };
    return table[code];
  };

  // Every team starts at zero so the table is complete from matchday 1
  Object.values(tournamentProbs).forEach(t => {
    if (t.group) ensureRow(t.code, t.team, t.group);
  });

  _allResults().forEach(r => {
    if (r.score_home == null || r.score_away == null) return;
    if (!(r.stage || '').toLowerCase().includes('group')) return;
    const gH = tournamentProbs[r.home_code]?.group;
    const gA = tournamentProbs[r.away_code]?.group;
    if (!gH || gH !== gA) return;
    [
      [r.home_code, r.home_team, r.score_home, r.score_away],
      [r.away_code, r.away_team, r.score_away, r.score_home],
    ].forEach(([code, team, gf, ga]) => {
      const t = ensureRow(code, team, gH);
      t.games++; t.gf += gf; t.ga += ga;
      if (gf > ga)      { t.w++; t.pts += 3; }
      else if (gf === ga) { t.d++; t.pts += 1; }
      else t.l++;
    });
  });

  const byGroup = {};
  Object.values(table).forEach(t => (byGroup[t.group] ||= []).push(t));
  Object.values(byGroup).forEach(rows =>
    rows.sort((a, b) => b.pts - a.pts || (b.gf - b.ga) - (a.gf - a.ga) || b.gf - a.gf
      || a.team.localeCompare(b.team)));
  return byGroup;
}

function standingsTable(rows) {
  return `
    <div class="dt standings-title">Tabelle</div>
    <table class="standings">
      <thead><tr><th></th><th class="st-team"></th><th>Sp</th><th>Tore</th><th>Pkt</th></tr></thead>
      <tbody>
        ${rows.map((t, i) => `<tr class="${i < 2 ? 'st-qualify' : ''}">
          <td class="st-pos">${i + 1}</td>
          <td class="st-team">${flagImg(t.team, t.team)}<span>${esc(t.team)}</span></td>
          <td>${t.games}</td>
          <td>${t.gf}:${t.ga}</td>
          <td class="st-pts">${t.pts}</td>
        </tr>`).join('')}
      </tbody>
    </table>`;
}

// ── Gruppen tab ───────────────────────────────────────────────────────────
function renderGruppen(app) {
  app.innerHTML = '';
  const probs = Object.values(tournamentProbs);
  if (!probs.length) {
    app.innerHTML = '<div class="eyebrow">Keine Turnierdaten geladen.</div>';
    return;
  }

  // Champion ranking (top 8)
  const ranked = [...probs].sort((a, b) => b.prob_champion - a.prob_champion).slice(0, 8);
  const maxChamp = ranked[0]?.prob_champion || 1;

  const champPanel = document.createElement('div');
  champPanel.className = 'model-panel';
  champPanel.innerHTML = `
    <div class="eyebrow" style="margin:0 0 12px">Weltmeister-Favoriten</div>
    ${ranked.map((t, i) => `
      <div class="champ-row">
        <span class="champ-rank">${i + 1}</span>
        ${flagImg(t.team, t.team)}
        <span class="champ-name">${esc(t.team)}</span>
        <div class="champ-bar-wrap">
          <div class="champ-bar" style="--bw:${(t.prob_champion / maxChamp * 100).toFixed(1)}%"></div>
        </div>
        <span class="champ-pct">${pct(t.prob_champion)}</span>
      </div>
    `).join('')}
  `;
  app.appendChild(champPanel);

  // Group cards
  const byGroup = {};
  probs.forEach(t => {
    const g = t.group || 'Unknown';
    if (!byGroup[g]) byGroup[g] = [];
    byGroup[g].push(t);
  });

  const eyebrow = document.createElement('div');
  eyebrow.className = 'eyebrow';
  eyebrow.textContent = `12 Gruppen`;
  app.appendChild(eyebrow);

  const standingsByGroup = computeGroupStandings();

  Object.keys(byGroup).sort().forEach(grp => {
    const teams = byGroup[grp].sort((a, b) => b.prob_win_group - a.prob_win_group);
    const standing = standingsByGroup[grp];
    const hasResults = standing && standing.some(t => t.games > 0);
    const card = document.createElement('div');
    card.className = 'group-card glass';
    card.innerHTML = `
      <div class="group-label">${esc(grp)}</div>
      ${hasResults ? standingsTable(standing) : ''}
      <div class="group-teams">
        ${teams.map(t => `
          <div class="group-team-row">
            ${flagImg(t.team, t.team)}
            <span class="group-team-name">${esc(t.team)}</span>
            <div class="group-bars">
              <div class="group-bar-row" title="Gruppensieger">
                <span class="gb-label">1.</span>
                <div class="gb-track"><div class="gb-fill win" style="width:${(t.prob_win_group*100).toFixed(1)}%"></div></div>
                <span class="gb-val">${pct(t.prob_win_group)}</span>
              </div>
              <div class="group-bar-row" title="Gruppenzeiter">
                <span class="gb-label">2.</span>
                <div class="gb-track"><div class="gb-fill run" style="width:${(t.prob_runner_up*100).toFixed(1)}%"></div></div>
                <span class="gb-val">${pct(t.prob_runner_up)}</span>
              </div>
            </div>
          </div>
        `).join('')}
      </div>
    `;
    app.appendChild(card);
  });

  requestAnimationFrame(() => requestAnimationFrame(() => {
    document.querySelectorAll('.gb-fill, .champ-bar').forEach(el => el.classList.add('revealed'));
  }));
}

// ── Baum tab (Tournament path) ────────────────────────────────────────────
function renderBaum(app) {
  app.innerHTML = '';
  const probs = Object.values(tournamentProbs);
  if (!probs.length) {
    app.innerHTML = '<div class="eyebrow">Keine Turnierdaten geladen.</div>';
    return;
  }

  const sorted = [...probs].sort((a, b) => b.prob_champion - a.prob_champion);

  const stages = [
    { key: 'prob_reach_round_of_32', label: 'R32',     abbr: 'Rd32' },
    { key: 'prob_reach_quarterfinals', label: 'Viertelfinale', abbr: 'VF' },
    { key: 'prob_reach_semifinals',  label: 'Halbfinale',   abbr: 'HF' },
    { key: 'prob_reach_final',       label: 'Finale',       abbr: 'F' },
    { key: 'prob_champion',          label: 'Weltmeister',  abbr: '🏆' },
  ];

  // Sticky header lives OUTSIDE the panel so overflow:hidden on the panel
  // can clip scrolling rows at the rounded top corners without breaking sticky.
  const stickyHdr = document.createElement('div');
  stickyHdr.className = 'baum-header';
  stickyHdr.innerHTML = `
    <div class="baum-team-col"></div>
    ${stages.map(s => `<div class="baum-stage-col" title="${esc(s.label)}">${esc(s.abbr)}</div>`).join('')}
  `;
  app.appendChild(stickyHdr);

  // Panel contains only rows — overflow:hidden now clips at border-radius correctly.
  const panel = document.createElement('div');
  panel.className = 'baum-panel';

  sorted.forEach((t, i) => {
    const row = document.createElement('div');
    row.className = 'baum-row' + (i % 2 === 0 ? '' : ' baum-alt');
    const hue = Math.round((1 - t.prob_champion) * 200); // green→blue gradient
    row.innerHTML = `
      <div class="baum-team-col">
        ${flagImg(t.team, t.team)}
        <span class="baum-name">${esc(t.team)}</span>
      </div>
      ${stages.map(s => {
        const v = t[s.key] || 0;
        const w = (v * 100).toFixed(1);
        return `
          <div class="baum-stage-col">
            <div class="baum-bar-wrap">
              <div class="baum-bar" style="width:${w}%;background:hsl(${hue},70%,52%)"></div>
            </div>
            <span class="baum-pct">${pct(v)}</span>
          </div>`;
      }).join('')}
    `;
    panel.appendChild(row);
  });

  app.appendChild(panel);
  requestAnimationFrame(() => requestAnimationFrame(() => {
    document.querySelectorAll('.baum-bar').forEach(el => el.classList.add('revealed'));
  }));
}

// ── Punkte-Bilanz (model scoreboard from real results) ────────────────────
function renderBilanz(app) {
  let total = 0, modalTotal = 0, n = 0;
  const tiers = { exact: 0, diff: 0, tendency: 0, miss: 0 };

  allMatches.forEach(m => {
    const r = _resultEntry(m);
    if (!r || !r.is_done || r.score_home == null || r.score_away == null) return;
    const tip = m.recommended_tip;
    if (!tip) return;
    n++;
    const pts = kicktippPoints(tip.home, tip.away, r.score_home, r.score_away);
    total += pts;
    const rules = metadata.kicktipp_rules
      || { win: { tendency: 2, goal_diff: 3, exact: 4 }, draw: { tendency: 2, exact: 4 } };
    if (pts === 0) tiers.miss++;
    else if (pts === rules.win.exact) tiers.exact++;
    else if (pts === rules.win.goal_diff) tiers.diff++;
    else tiers.tendency++;
    const modal = m.modal_scoreline;
    if (modal) modalTotal += kicktippPoints(modal.home, modal.away, r.score_home, r.score_away);
  });

  if (!n) return;

  const avg = (total / n).toFixed(2);
  const evDelta = total - modalTotal;
  const card = document.createElement('div');
  card.className = 'bilanz-card glass';
  card.innerHTML = `
    <div class="bilanz-head">
      <span class="bilanz-title">Punkte-Bilanz</span>
      <span class="bilanz-total">${total} Pkt</span>
    </div>
    <div class="bilanz-sub">${n} gewertete${n === 1 ? 's' : ''} Spiel${n === 1 ? '' : 'e'} · Ø ${avg} Pkt/Spiel</div>
    <div class="bilanz-tiers">
      <span class="bt bt-exact" title="Exaktes Ergebnis">${tiers.exact}× exakt</span>
      <span class="bt bt-diff" title="Richtige Tordifferenz">${tiers.diff}× Differenz</span>
      <span class="bt bt-tend" title="Richtige Tendenz">${tiers.tendency}× Tendenz</span>
      <span class="bt bt-miss" title="Falsche Tendenz">${tiers.miss}× daneben</span>
    </div>
    <div class="bilanz-ev" title="Vergleich: EV-optimaler Tipp vs. wahrscheinlichstes Ergebnis (Modal)">
      EV-Tipp vs. Modal-Tipp: <b>${evDelta > 0 ? '+' : ''}${evDelta} Pkt</b>
      ${evDelta > 0 ? '— der Optimierer lohnt sich ✓' : evDelta < 0 ? '— Modal wäre besser gewesen' : '— gleichauf'}
    </div>
  `;
  app.appendChild(card);
}

// ── Verlauf tab (match timeline with live status) ─────────────────────────
function renderVerlauf(app) {
  app.innerHTML = '';
  const now = Date.now();

  function matchStatus(ct) {
    const ko = parseKickoff(ct);
    if (!ko) return 'upcoming';
    const ms = now - ko.getTime();
    if (ms < 0) return 'upcoming';
    if (ms < 115 * 60 * 1000) return 'live';
    return 'done';
  }

  const sorted = [...allMatches].sort((a, b) =>
    (parseKickoff(a.commence_time) || 0) - (parseKickoff(b.commence_time) || 0));

  // Group by date
  const byDate = {};
  sorted.forEach(m => {
    const d = m.commence_time.slice(0, 10);
    if (!byDate[d]) byDate[d] = [];
    byDate[d].push(m);
  });

  const todayStr = new Date().toLocaleDateString('de-DE', { timeZone: 'Europe/Berlin' });
  const tomorrowStr = new Date(Date.now() + 86400000).toLocaleDateString('de-DE', { timeZone: 'Europe/Berlin' });

  function dayLabel(dateStr) {
    const d = parseKickoff(dateStr);
    if (!d) return dateStr;
    const ds = d.toLocaleDateString('de-DE', { timeZone: 'Europe/Berlin' });
    const dm = d.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', timeZone: 'Europe/Berlin' });
    if (ds === todayStr) return `⚽ Heute · ${dm}`;
    if (ds === tomorrowStr) return `Morgen · ${dm}`;
    return `${d.toLocaleDateString('de-DE', { weekday: 'long', timeZone: 'Europe/Berlin' })} · ${dm}`;
  }

  renderBilanz(app);

  const liveCount = liveScores.filter(e => e.is_live || e.is_halftime).length;
  if (liveCount) {
    const liveSection = document.createElement('div');
    liveSection.className = 'verlauf-live-section';
    liveSection.innerHTML = `<div class="verlauf-live-badge"><span class="live-dot"></span>Live – ${liveCount} Spiel${liveCount !== 1 ? 'e' : ''} läuft gerade</div>`;
    app.appendChild(liveSection);
  }

  Object.keys(byDate).sort().forEach(dateStr => {
    const matches = byDate[dateStr];
    const d = parseKickoff(dateStr);
    const isToday = d && d.toLocaleDateString('de-DE', { timeZone: 'Europe/Berlin' }) === todayStr;

    const header = document.createElement('div');
    header.className = 'verlauf-day-header' + (isToday ? ' verlauf-today' : '');
    header.textContent = dayLabel(dateStr);
    app.appendChild(header);

    matches.forEach(m => {
      const status = matchStatus(m.commence_time);
      const tip = m.recommended_tip;
      const ko = parseKickoff(m.commence_time);
      const timeStr = m.commence_time.includes('T')
        ? ko.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Berlin' })
        : '–:––';
      const p = m.sources?.uanalyse?.p ?? m.sources?.odds_consensus?.p;
      const favPct = p ? Math.round(Math.max(p.home, p.draw, p.away) * 100) : null;
      const favLabel = p
        ? (p.home >= p.draw && p.home >= p.away ? m.home_team
          : p.away > p.home && p.away >= p.draw ? m.away_team : 'Unentschieden')
        : null;

      // Real score if available from live data or the results history
      const live = _resultEntry(m);
      const hasScore = live && (live.score_home !== null && live.score_home !== undefined);
      const sh = hasScore ? live.score_home : null;
      const sa = hasScore ? live.score_away : null;

      let statusBadge;
      if (live?.is_live) {
        statusBadge = `<span class="vstatus vstatus-live"><span class="live-dot"></span>${live.minute ? live.minute + '\'' : 'Live'}</span>`;
      } else if (live?.is_halftime) {
        statusBadge = `<span class="vstatus vstatus-live">⏸ HZ</span>`;
      } else if (live?.is_done) {
        statusBadge = `<span class="vstatus vstatus-done">✓</span>`;
      } else if (status === 'done') {
        statusBadge = `<span class="vstatus vstatus-done">Beendet</span>`;
      } else {
        statusBadge = `<span class="vstatus vstatus-upcoming">${timeStr}</span>`;
      }

      const scoreDisplay = hasScore
        ? `<div class="verlauf-score verlauf-score-real">${sh}:${sa}</div>`
        : tip
          ? `<div class="verlauf-score">${tip.home}:${tip.away}</div>`
          : `<div class="verlauf-score">–:–</div>`;

      const htLine = (live?.halftime_home !== null && live?.halftime_home !== undefined && live?.is_done)
        ? `<div class="verlauf-ht">HZ ${live.halftime_home}:${live.halftime_away}</div>` : '';

      // Earned Kicktipp points: finished match with real score + a tip
      let ptsHtml = '';
      let tipResHtml = '';
      if (live?.is_done && hasScore && tip) {
        const pts = kicktippPoints(tip.home, tip.away, sh, sa);
        const cls = pts >= 3 ? 'vp-high' : pts > 0 ? 'vp-mid' : 'vp-zero';
        ptsHtml = `<span class="verlauf-pts ${cls}">+${pts} Pkt</span>`;
        tipResHtml = `<span class="verlauf-tipres">Tipp ${tip.home}:${tip.away}</span>`;
      }

      const card = document.createElement('div');
      card.className = `verlauf-card${(live?.is_live || live?.is_halftime) ? ' verlauf-live' : live?.is_done ? ' verlauf-done' : status === 'done' ? ' verlauf-done' : ''}`;
      card.innerHTML = `
        <div class="verlauf-status">
          ${statusBadge}
        </div>
        <div class="verlauf-teams">
          <div class="verlauf-team">${flagImg(m.home_team, m.home_team)}<span>${esc(m.home_team)}</span></div>
          ${scoreDisplay}
          <div class="verlauf-team verlauf-team-away">${flagImg(m.away_team, m.away_team)}<span>${esc(m.away_team)}</span></div>
        </div>
        <div class="verlauf-meta">
          ${hasScore ? '' : `<span class="verlauf-tip-label">Tipp</span>`}
          ${tipResHtml}
          ${ptsHtml}
          ${htLine}
          ${!hasScore && favLabel && favPct !== null ? `<span class="verlauf-fav">${esc(favLabel)} ${favPct}%</span>` : ''}
          ${m.stage ? `<span class="verlauf-stage">${esc(m.stage)}</span>` : ''}
        </div>
      `;
      app.appendChild(card);
    });
  });
}

// ── Model tab ─────────────────────────────────────────────────────────────
function weightingPanel(meta) {
  const wt = meta.weighting;
  if (!wt) return '';
  const wm = Math.round((wt.weights?.market ?? 0.5) * 100);
  const wu = 100 - wm;
  const regimeLabel = wt.regime === 'performance' ? 'Performance' : 'Prior';
  const sharpTxt = wt.sharp_books
    ? `Scharfe Bücher erkannt (${(wt.book_keys || []).filter(k => /pinnacle|betfair|matchbook/.test(k)).join(', ') || 'ja'}) → Markt bekommt den Prior-Vorteil.`
    : 'Keine scharfen Bücher (Pinnacle/Betfair) in der Antwort → Gewichte auf Parität.';

  const perf = wt.performance || {};
  const hasPerf = perf.market && perf.uanalyse;
  let perfHtml;
  if (hasPerf) {
    const row = (lab, src) => `
      <div class="perf-row">
        <span class="perf-src">${lab}</span>
        <span class="perf-metric"><i>Treffer</i><b>${pct(perf[src].hit_rate)}</b></span>
        <span class="perf-metric"><i>Brier</i><b>${(perf[src].brier).toFixed(3)}</b></span>
        <span class="perf-metric"><i>LogLoss</i><b>${(perf[src].log_loss).toFixed(3)}</b></span>
        <span class="perf-metric"><i>n</i><b>${perf[src].n}</b></span>
      </div>`;
    perfHtml = `
      <div class="dt" style="margin-top:14px">Rollierende Treffsicherheit · ${wt.n_settled} Spiele</div>
      ${row('Wettbüros', 'market')}
      ${row('uanalyse', 'uanalyse')}
      <p class="model-note">Niedrigerer Brier/Log-Loss = treffsicherer. Die Gewichte ziehen invers zum Fehler nach${wt.reweighting_enabled ? '' : ' (Reweighting noch inaktiv — schaltet automatisch frei, sobald genug Ergebnisse vorliegen)'}.</p>`;
  } else {
    perfHtml = `<p class="model-note">Noch keine ausgewerteten Ergebnisse — die Gewichte stammen aus dem Prior. Die performancebasierte Nachgewichtung (Brier &amp; Log-Loss je Quelle) schaltet sich frei, sobald der Ergebnis-Feed läuft.</p>`;
  }

  return `
    <div class="dt" style="margin-top:16px">Quellen-Gewichtung
      <span class="regime-badge regime-${wt.regime}">${regimeLabel}</span></div>
    <div class="weight-split" role="img" aria-label="Markt ${wm} Prozent, uanalyse ${wu} Prozent">
      <div class="ws-seg ws-market" style="width:${wm}%">Markt ${wm}%</div>
      <div class="ws-seg ws-ua" style="width:${wu}%">uanalyse ${wu}%</div>
    </div>
    <p class="model-note">${sharpTxt}</p>
    ${perfHtml}`;
}

function renderModel(app) {
  const meta = metadata;
  const generated = meta.generated_at
    ? new Date(meta.generated_at).toLocaleString('de-DE', {
        timeZone: 'Europe/Berlin', day: '2-digit', month: '2-digit',
        year: 'numeric', hour: '2-digit', minute: '2-digit'
      })
    : '–';

  app.innerHTML = `
    <div class="eyebrow">Modell-Info</div>
    <div class="model-panel">
      <h2>Wie funktioniert der Prädikator?</h2>
      <p>Der EV-Optimierer berechnet für jeden möglichen Tipp (0–7:0–7) den
      erwarteten Punktwert gemäß den Kicktipp-Regeln und wählt das Argmax.</p>
      <p>Der wahrscheinlichste Tipp (Modal) weicht oft vom EV-optimalen ab —
      z.B. ist 1:0 oft besser als 1:1, weil die Tordifferenz-Stufe (+1 Pkt)
      auch Ergebnisse wie 2:1, 3:2 usw. einfängt.</p>
      <p>Keine Tordifferenz-Stufe bei Unentschieden (Kicktipp-Regelwerk):
      2 Pkt Tendenz · 4 Pkt exakt.</p>
      <p>Der Tipp basiert auf einem gewichteten <b>Blend</b> aus Markt-Schlussquoten
      und uanalyse (Logit-Pooling), das auf Poisson-λ kalibriert wird — keine Quelle
      wird ignoriert.</p>

      ${weightingPanel(meta)}

      <div class="dt" style="margin-top:16px">Quellen</div>
      <div class="stat-row">
        <span>uanalyse λ-Prognosen</span>
        <span class="stat-val">${meta.uanalyse_count ?? '–'} Spiele</span>
      </div>
      <div class="stat-row">
        <span>The Odds API Quoten</span>
        <span class="stat-val">${meta.odds_count ?? '–'} Spiele</span>
      </div>
      <div class="stat-row">
        <span>Gesamt</span>
        <span class="stat-val">${meta.match_count ?? allMatches.length} Spiele</span>
      </div>
      <div class="stat-row">
        <span>Zuletzt aktualisiert</span>
        <span class="stat-val">${generated}</span>
      </div>
      <div class="stat-row">
        <span>Normalisierung</span>
        <span class="stat-val">${meta.normalization_method ?? 'multiplicative'}</span>
      </div>

      <div class="dt" style="margin-top:16px">Ressourcen</div>
      <div class="stat-row">
        <span><a href="https://github.com/uanalyse/world-cup-2026-predictions" target="_blank" rel="noopener">uanalyse/world-cup-2026-predictions</a></span>
        <span style="font-size:11px;color:var(--muted)">CC BY 4.0</span>
      </div>
      <div class="stat-row">
        <span><a href="https://the-odds-api.com" target="_blank" rel="noopener">The Odds API</a></span>
        <span style="font-size:11px;color:var(--muted)">Quoten</span>
      </div>
      <div class="stat-row">
        <span><a href="https://github.com/Thecoffeedude/wm2026-kicktipp" target="_blank" rel="noopener">GitHub-Repo</a></span>
        <span style="font-size:11px;color:var(--muted)">Quellcode</span>
      </div>
    </div>`;
}

// ── Interactions ──────────────────────────────────────────────────────────
function toggleCard(btn) {
  const card = btn.closest('.card');

  // Lazy-build the (heavy) drawer content on first open
  const inner = card.querySelector('.drawer-inner');
  if (inner && inner.dataset.built === '0') {
    const match = allMatches.find(m => m.id === card.dataset.id);
    if (match) {
      inner.innerHTML = buildDrawer(match, match.sources?.uanalyse, match.sources?.odds_consensus);
      inner.dataset.built = '1';
    }
  }

  const open = card.classList.toggle('open');
  btn.setAttribute('aria-expanded', String(open));
  // Animate xG fills on open (scaleX)
  if (open) {
    requestAnimationFrame(() => {
      card.querySelectorAll('.xg-fill').forEach(el => el.classList.add('revealed'));
    });
  } else {
    card.querySelectorAll('.xg-fill').forEach(el => el.classList.remove('revealed'));
  }
}
window.toggleCard = toggleCard;

function toggleBookmakers(btn) {
  const listId = btn.getAttribute('aria-controls');
  const list = document.getElementById(listId);
  if (!list) return;
  const open = list.hidden;
  list.hidden = !open;
  btn.setAttribute('aria-expanded', String(open));
  btn.querySelector('.bk-chevron')?.classList.toggle('bk-chevron-open', open);
}
window.toggleBookmakers = toggleBookmakers;

// ── Heatmap: probability ↔ expected-points mode + cell selection ───────────
function setHeatmapMode(btn) {
  const mode = btn.dataset.mode;                       // 'prob' | 'pts'
  const modes = btn.parentElement;                     // .hm-modes
  const hm = modes.nextElementSibling;                 // .heatmap
  if (!hm || !hm.classList.contains('heatmap')) return;
  hm.classList.toggle('mode-prob', mode === 'prob');
  hm.classList.toggle('mode-pts', mode === 'pts');
  modes.querySelectorAll('.hm-mode').forEach(b => {
    const active = b === btn;
    b.classList.toggle('active', active);
    b.setAttribute('aria-selected', String(active));
  });
}
window.setHeatmapMode = setHeatmapMode;

function selectHmCell(cell) {
  const hm = cell.closest('.heatmap');
  if (!hm) return;
  hm.querySelectorAll('.hm-cell.sel').forEach(c => c.classList.remove('sel'));
  cell.classList.add('sel');
  const h = +cell.dataset.h, a = +cell.dataset.a;
  const prob = parseFloat(cell.dataset.prob), pts = parseFloat(cell.dataset.pts);
  const ro = hm.querySelector('.hm-readout');
  if (ro) ro.innerHTML = heatmapReadout(h, a, prob, pts, {
    ev: cell.classList.contains('ev'),
    modal: cell.classList.contains('modal'),
  });
}
window.selectHmCell = selectHmCell;

// ── Bar animation trigger ─────────────────────────────────────────────────
function animateBars() {
  requestAnimationFrame(() => requestAnimationFrame(() => {
    // Segmented bars: width-based
    document.querySelectorAll('.bar .seg').forEach(seg => {
      const w = getComputedStyle(seg).getPropertyValue('--w').trim();
      if (w) seg.style.width = w;
    });
    // Single-fill bars: scaleX-based
    document.querySelectorAll('.champ-bar, .gb-fill, .baum-bar').forEach(el => {
      el.classList.add('revealed');
    });
  }));
}

// Map a 0–100 consensus value to a hue-based color (red → amber → green).
function consensusColor(value) {
  const hue = Math.round((value / 100) * 130); // 0=red, 130=green
  return `hsl(${hue} 72% 45%)`;
}

// Count up from 0 to data-target, recoloring the text as it climbs.
function animateConsensus(el) {
  const target = parseInt(el.dataset.target, 10) || 0;
  if (prefersReducedMotion) {
    el.textContent = `${target}%`;
    el.style.color = consensusColor(target);
    return;
  }
  const duration = 700; // ms — fast
  const start = performance.now();
  function frame(now) {
    const t = Math.min(1, (now - start) / duration);
    const eased = 1 - Math.pow(1 - t, 3); // easeOutCubic
    const val = Math.round(eased * target);
    el.textContent = `${val}%`;
    el.style.color = consensusColor(val);
    if (t < 1) requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
}

// ── Utilities ─────────────────────────────────────────────────────────────
function pct(p) { return `${Math.round(p * 100)}%`; }
function esc(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ── PWA: platform-aware install prompt ───────────────────────────────────
let _deferredInstallPrompt = null;

// Capture Chrome/Android install prompt before it auto-fires
window.addEventListener('beforeinstallprompt', e => {
  e.preventDefault();
  _deferredInstallPrompt = e;
  _showInstallBanner('chromium');
});

function maybeShowInstallBanner() {
  // Already running as installed PWA → nothing to do
  const isInstalled = window.matchMedia('(display-mode: standalone)').matches
    || navigator.standalone === true;
  if (isInstalled) return;

  if (sessionStorage.getItem('install-dismissed')) return;

  // Chrome/Android: deferred prompt may already be captured
  if (_deferredInstallPrompt) { _showInstallBanner('chromium'); return; }

  // iOS: feature-detect via navigator.standalone (only exists on iOS WebKit)
  if (typeof navigator.standalone !== 'boolean') return;

  const ua = navigator.userAgent;
  const isSafari = /Safari/.test(ua) && !/CriOS|FxiOS|EdgiOS|OPiOS/.test(ua);
  _showInstallBanner(isSafari ? 'ios-safari' : 'ios-other');
}

function _showInstallBanner(mode) {
  const banner = document.getElementById('install-banner');
  const label  = document.getElementById('install-banner-text');
  if (!banner) return;

  if (mode === 'chromium') {
    if (label) label.textContent = 'App installieren';
    banner.onclick = async () => {
      if (!_deferredInstallPrompt) return;
      _deferredInstallPrompt.prompt();
      await _deferredInstallPrompt.userChoice;
      _deferredInstallPrompt = null;
      banner.hidden = true;
    };
    banner.hidden = false;
  } else if (mode === 'ios-safari') {
    if (label) label.textContent = 'App installieren';
    banner.onclick = (e) => {
      if (e.target.closest('.install-banner__close')) return;
      openIOSSheet();
    };
    banner.hidden = false;
  } else if (mode === 'ios-other') {
    if (label) label.textContent = 'In Safari öffnen, um zu installieren';
    banner.hidden = false;
  }
}

function dismissInstallBanner() {
  const banner = document.getElementById('install-banner');
  if (banner) banner.hidden = true;
  sessionStorage.setItem('install-dismissed', '1');
}
window.dismissInstallBanner = dismissInstallBanner;

function openIOSSheet() {
  const sheet = document.getElementById('ios-install-sheet');
  if (sheet) { sheet.hidden = false; requestAnimationFrame(() => sheet.classList.add('open')); }
}
function closeIOSSheet() {
  const sheet = document.getElementById('ios-install-sheet');
  if (!sheet) return;
  sheet.classList.remove('open');
  sheet.addEventListener('transitionend', () => { sheet.hidden = true; }, { once: true });
}
window.openIOSSheet = openIOSSheet;
window.closeIOSSheet = closeIOSSheet;

// ── PWA: Service Worker + "New version" signal ────────────────────────────
function registerSW() {
  if (!('serviceWorker' in navigator)) return;
  navigator.serviceWorker.register('./sw.js').catch(() => {});
  navigator.serviceWorker.addEventListener('message', e => {
    if (e.data?.type === 'SW_UPDATED') showUpdateBanner();
  });
}

function showUpdateBanner() {
  let b = document.getElementById('update-banner');
  if (b) return;
  b = document.createElement('div');
  b.id = 'update-banner';
  b.className = 'update-banner';
  b.setAttribute('role', 'alert');
  b.innerHTML = `<span>Neue Version verfügbar</span>
    <button class="update-banner__btn" onclick="window.location.reload()">Neu laden</button>`;
  document.body.appendChild(b);
}

// ── PWA: Wake Lock (Safari 18.4+ / iOS 18.4+) ────────────────────────────
let wakeLock = null;
async function requestWakeLock() {
  if (!('wakeLock' in navigator)) return;
  try {
    wakeLock = await navigator.wakeLock.request('screen');
    document.addEventListener('visibilitychange', async () => {
      if (document.visibilityState === 'visible' && wakeLock?.released) {
        try { wakeLock = await navigator.wakeLock.request('screen'); } catch {}
      }
    });
  } catch {}
}

// ── PWA: Badge API (open tips count) ─────────────────────────────────────
function updateBadge(count) {
  if (!('setAppBadge' in navigator)) return;
  if (count > 0) navigator.setAppBadge(count).catch(() => {});
  else navigator.clearAppBadge().catch(() => {});
}

// ── Card: click on header / fixture to expand ────────────────────────────
document.addEventListener('click', e => {
  const card = e.target.closest('.card');
  if (!card) return;
  // Don't trigger if clicking the expand button itself or inside the drawer
  if (e.target.closest('.expand') || e.target.closest('.drawer a') || e.target.closest('.drawer button')) return;
  // Don't trigger if clicking a link
  if (e.target.tagName === 'A') return;
  const btn = card.querySelector('.expand');
  if (btn) toggleCard(btn);
});

// ── Adaptive chrome: hide tab bar on scroll down, show on scroll up ───────
(function initAdaptiveChrome() {
  let lastY = 0;
  let ticking = false;
  const nav = document.querySelector('nav');
  if (!nav) return;

  window.addEventListener('scroll', () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(() => {
      const y = window.scrollY;
      if (y > lastY + 4 && y > 80) {
        nav.classList.add('nav-hidden');
      } else if (y < lastY - 4) {
        nav.classList.remove('nav-hidden');
      }
      lastY = y;
      ticking = false;
    });
  }, { passive: true });
})();

// ── setTab with View Transitions ──────────────────────────────────────────
const _tabOrder = ['heute', 'alle', 'gruppen', 'baum', 'verlauf', 'modell'];
const _origSetTab = setTab;
window.setTab = function(tab) {
  const prevIdx = _tabOrder.indexOf(currentTab);
  const nextIdx = _tabOrder.indexOf(tab);
  const dir = nextIdx > prevIdx ? 1 : -1;

  if (document.startViewTransition && prevIdx !== nextIdx) {
    document.documentElement.style.setProperty('--slide-dir', dir > 0 ? '1' : '-1');
    document.startViewTransition(() => _origSetTab(tab));
  } else {
    _origSetTab(tab);
  }
};
// Make view-transition-name available to CSS
const appEl = document.getElementById('app');
if (appEl) appEl.style.viewTransitionName = 'app-content';

// ── Pull-to-refresh ───────────────────────────────────────────────────────
(function initPullToRefresh() {
  let startY = 0;
  let pulling = false;
  let indicator = null;

  function getIndicator() {
    if (!indicator) {
      indicator = document.createElement('div');
      indicator.className = 'ptr-indicator';
      indicator.innerHTML = '<div class="ptr-spinner"></div><span>Aktualisieren…</span>';
      const main = document.getElementById('app');
      main?.parentNode?.insertBefore(indicator, main);
    }
    return indicator;
  }

  document.addEventListener('touchstart', e => {
    if (window.scrollY > 0) return;
    startY = e.touches[0].clientY;
    pulling = true;
  }, { passive: true });

  document.addEventListener('touchmove', e => {
    if (!pulling) return;
    const dy = e.touches[0].clientY - startY;
    if (dy > 40) getIndicator().classList.add('visible');
  }, { passive: true });

  document.addEventListener('touchend', async e => {
    if (!pulling) return;
    pulling = false;
    const ind = indicator;
    if (ind?.classList.contains('visible')) {
      try {
        const res = await fetch(DATA_URL + '?_=' + Date.now());
        if (res.ok) {
          const data = await res.json();
          allMatches = data.matches;
          metadata = data.metadata;
          tournament = data.tournament || {};
          tournamentProbs = data.tournament_probabilities || {};
          renderMeta();
          renderTab();
        }
      } catch {}
      ind.classList.remove('visible');
    }
  }, { passive: true });
})();

// ── Skeleton screen on init ───────────────────────────────────────────────
function showSkeletons(count = 3) {
  const app = document.getElementById('app');
  if (!app) return;
  app.innerHTML = '';
  for (let i = 0; i < count; i++) {
    const sk = document.createElement('div');
    sk.className = 'skeleton-card';
    sk.style.animationDelay = `${i * 0.15}s`;
    app.appendChild(sk);
  }
}

// ── Theme toggle: auto → dark → light → auto ─────────────────────────────
const THEME_ICONS = {
  auto:  '<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor" stroke="none"/>',
  dark:  '<path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z"/>',
  light: '<circle cx="12" cy="12" r="5"/><path d="M12 1v3M12 20v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M1 12h3M20 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/>',
};
const THEME_LABELS = { auto: 'automatisch', dark: 'dunkel', light: 'hell' };

function applyTheme(mode) {
  const html = document.documentElement;
  html.classList.toggle('force-dark', mode === 'dark');
  html.classList.toggle('force-light', mode === 'light');
  const btn = document.getElementById('theme-toggle');
  if (btn) {
    btn.querySelector('svg').innerHTML = THEME_ICONS[mode] || THEME_ICONS.auto;
    btn.title = `Design: ${THEME_LABELS[mode] || mode}`;
  }
}

(function initThemeToggle() {
  let mode = localStorage.getItem('theme') || 'auto';
  applyTheme(mode);
  document.getElementById('theme-toggle')?.addEventListener('click', () => {
    mode = mode === 'auto' ? 'dark' : mode === 'dark' ? 'light' : 'auto';
    localStorage.setItem('theme', mode);
    applyTheme(mode);
  });
})();

// ── Tab bar: arrow-key navigation ─────────────────────────────────────────
document.querySelector('.tabs')?.addEventListener('keydown', e => {
  if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
  e.preventDefault();
  const idx = _tabOrder.indexOf(currentTab);
  const step = e.key === 'ArrowRight' ? 1 : -1;
  const next = _tabOrder[(idx + step + _tabOrder.length) % _tabOrder.length];
  window.setTab(next);
  document.getElementById(`tab-${next}`)?.focus();
});

// ── Local testing helpers via URL params (?dark, ?tab=alle, ?open=0)
(function applyURLParams() {
  const p = new URLSearchParams(location.search);
  if (p.has('dark')) document.documentElement.classList.add('force-dark');
  if (p.has('tab')) currentTab = p.get('tab');
})();

function applyOpenParam() {
  const idx = parseInt(new URLSearchParams(location.search).get('open') ?? '-1', 10);
  if (idx >= 0) {
    const cards = document.querySelectorAll('.card');
    if (cards[idx]) {
      cards[idx].classList.add('open');
      const btn = cards[idx].querySelector('.expand');
      if (btn) btn.setAttribute('aria-expanded', 'true');
      cards[idx].querySelectorAll('.xg-fill').forEach(el => el.classList.add('revealed'));
    }
  }
}

init().then(applyOpenParam).catch(() => {});
