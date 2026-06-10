// ── Config ────────────────────────────────────────────────────────────────
const DATA_URL = './data.json';
const FLAG_BASE = 'https://flagcdn.com/w80/';
const DIVERGENCE_THRESHOLD = 0.04;
const XG_MAX = 4.0;

// ── State ─────────────────────────────────────────────────────────────────
let allMatches = [];
let metadata = {};
let tournament = {};
let tournamentProbs = {};   // FIFA_code → {team, group, prob_win_group, ..., prob_champion}
let currentTab = 'heute';
let searchQuery = '';
let filterTeam = '';

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
    const res = await fetch(DATA_URL);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    allMatches = data.matches;
    metadata = data.metadata;
    tournament = data.tournament || {};
    tournamentProbs = data.tournament_probabilities || {};
    renderMeta();
    renderTab();
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
  el.innerHTML = `<span class="pill"></span>${time} Uhr`;
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

  let matches = [...allMatches];

  if (currentTab === 'heute') {
    matches = matches.filter(m => isToday(m.commence_time) || isTomorrow(m.commence_time));
    matches.sort((a, b) => (parseKickoff(a.commence_time) || 0) - (parseKickoff(b.commence_time) || 0));
  } else if (currentTab === 'alle') {
    matches.sort((a, b) => (parseKickoff(a.commence_time) || 0) - (parseKickoff(b.commence_time) || 0));
  } else if (currentTab === 'diverg') {
    matches.sort((a, b) => maxDivergence(b) - maxDivergence(a));
  }

  // Apply search/filter for 'alle' and 'diverg' tabs
  if (currentTab === 'alle' || currentTab === 'diverg') {
    const q = searchQuery.trim().toLowerCase();
    const ft = filterTeam.toLowerCase();
    if (q || ft) {
      matches = matches.filter(m => {
        const h = m.home_team.toLowerCase(), a = m.away_team.toLowerCase();
        if (ft && h !== ft && a !== ft) return false;
        if (q && !h.includes(q) && !a.includes(q)) return false;
        return true;
      });
    }
  }

  app.innerHTML = '';

  if (currentTab === 'heute') {
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
    renderCalendar(app, matches);
    animateBars();
    return;
  }

  const label = currentTab === 'diverg'
    ? `Nach Divergenz · ${matches.length} Spiele`
    : `Alle Spiele · ${matches.length} Spiele`;

  const eyebrow = document.createElement('div');
  eyebrow.className = 'eyebrow';
  eyebrow.textContent = label;
  app.appendChild(eyebrow);
  matches.forEach((m, i) => app.appendChild(buildCard(m, i)));
  animateBars();
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

  // Clearest favorite
  let maxFav = 0, favTeam = '';
  src.forEach(m => {
    const p = m.sources?.uanalyse?.p ?? m.sources?.odds_consensus?.p;
    if (!p) return;
    if (p.home > maxFav) { maxFav = p.home; favTeam = m.home_team; }
    if (p.away > maxFav) { maxFav = p.away; favTeam = m.away_team; }
  });

  const row = document.createElement('div');
  row.className = 'stat-widgets';
  row.innerHTML = `
    <div class="stat-widget glass">
      <div class="sw-label">Tendenz</div>
      <div class="sw-donut" style="--h:${(tH*360).toFixed(0)}deg;--d:${((tH+tD)*360).toFixed(0)}deg"></div>
      <div class="sw-sub">${pct(tH)} / ${pct(tD)} / ${pct(tA)}</div>
    </div>
    <div class="stat-widget glass">
      <div class="sw-label">xG-Leader</div>
      <div class="sw-main">${xgTeam ? flagImg(xgTeam, xgTeam) : '–'}</div>
      <div class="sw-sub">${xgMax > 0 ? xgMax.toFixed(1) + ' xG' : '–'}</div>
    </div>
    <div class="stat-widget glass">
      <div class="sw-label">Klarster Favorit</div>
      <div class="sw-main">${favTeam ? flagImg(favTeam, favTeam) : '–'}</div>
      <div class="sw-sub">${maxFav > 0 ? pct(maxFav) : '–'}</div>
    </div>
    ${agreeRate !== null ? `
    <div class="stat-widget glass">
      <div class="sw-label">Quellen einig</div>
      <div class="sw-gauge" style="--g:${(agreeRate*180).toFixed(0)}deg"></div>
      <div class="sw-sub">${pct(agreeRate)}</div>
    </div>` : ''}
  `;
  app.appendChild(row);

  const eyebrow = document.createElement('div');
  eyebrow.className = 'eyebrow';
  eyebrow.textContent = `Heute & Morgen · ${todayMatches.length} Spiel${todayMatches.length !== 1 ? 'e' : ''}`;
  app.appendChild(eyebrow);
}

// ── Alle Spiele: search + filter bar ─────────────────────────────────────
function renderSearchFilter(app) {
  const teams = [...new Set(allMatches.flatMap(m => [m.home_team, m.away_team]))].sort();

  const wrap = document.createElement('div');
  wrap.className = 'search-bar';
  wrap.innerHTML = `
    <input class="search-input glass" type="search" placeholder="Team oder Datum suchen…"
      value="${esc(searchQuery)}" oninput="onSearch(this.value)" aria-label="Suchen">
    <div class="filter-chips" id="filter-chips">
      <button class="chip ${!filterTeam ? 'active' : ''}" onclick="setFilter('')">Alle</button>
      ${teams.map(t => `
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
  renderTab();
};
window.setFilter = function(team) {
  filterTeam = team;
  searchQuery = '';
  const inp = document.querySelector('.search-input');
  if (inp) inp.value = '';
  renderTab();
};

// ── Kalender-Ansicht (Spiele grouped by date) ─────────────────────────────
function renderCalendar(app, matches) {
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
    app.appendChild(el);
    return;
  }

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

  // Build drawer content
  const drawerContent = buildDrawer(match, ua, oddsC);

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

    <div class="drawer"><div>${drawerContent}</div></div>
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
  const tip = match.recommended_tip;
  let html = '<div class="data" style="margin:0 12px 12px">';

  // Source comparison (if both available)
  if (ua && oddsC) {
    html += `<div class="dt">Quellenvergleich</div>`;
    html += `<div class="srcrow"><span class="srclab">uanalyse</span>${renderMiniBar(ua.p)}</div>`;
    html += `<div class="srcrow"><span class="srclab">Wettbüros</span>${renderMiniBar(oddsC.p)}</div>`;
  }

  // xG bars
  const lambda = ua?.lambda ?? match.expected_goals;
  if (lambda?.home != null) {
    html += `<div class="dt">Erwartete Tore (xG)</div>`;
    const hW = Math.min(lambda.home / XG_MAX * 100, 100).toFixed(1);
    const aW = Math.min(lambda.away / XG_MAX * 100, 100).toFixed(1);
    const hAbbr = match.home_team.slice(0, 3).toUpperCase();
    const aAbbr = match.away_team.slice(0, 3).toUpperCase();
    html += `
      <div class="xg-row-d">
        <span class="l">${hAbbr}</span>
        <div class="xg-track"><div class="xg-fill" style="--xw:${hW}%;background:var(--home)"></div></div>
        <span class="v">${lambda.home.toFixed(1)}</span>
      </div>
      <div class="xg-row-d">
        <span class="l">${aAbbr}</span>
        <div class="xg-track"><div class="xg-fill" style="--xw:${aW}%;background:var(--away)"></div></div>
        <span class="v">${lambda.away.toFixed(1)}</span>
      </div>`;
  }

  // Top scorelines (computed from Poisson if lambda available)
  if (lambda?.home != null) {
    const top = topScoredlines(lambda.home, lambda.away, 5);
    html += `<div class="dt">Wahrscheinlichste Ergebnisse</div><div class="scl">`;
    top.forEach((sc, i) => {
      const isEVtip = tip && sc.h === tip.home && sc.a === tip.away;
      const isTop = i === 0;
      const cls = isEVtip ? 'scl-chip tip-match' : isTop ? 'scl-chip top' : 'scl-chip';
      const label = isEVtip ? `${sc.s} ★` : sc.s;
      html += `<div class="${cls}"><b>${esc(label)}</b><span class="sp">${pct(sc.p)}</span></div>`;
    });
    html += `</div>`;
    if (tip && !top.some(s => s.h === tip.home && s.a === tip.away)) {
      html += `<p style="font-size:11px;color:var(--muted);margin-top:8px">
        ★ Empfohlener Tipp ${tip.home}:${tip.away} (${pct(poissonPMF(tip.home, lambda.home) * poissonPMF(tip.away, lambda.away))})
        ist EV-optimal, aber nicht in Top-5 nach Wahrscheinlichkeit.
      </p>`;
    }
  }

  // Bookmakers
  if (match.bookmakers?.length > 0) {
    html += `<div class="dt" style="margin-top:18px">Buchmacher (${match.bookmakers.length})</div>`;
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
  }

  html += '</div>';
  return html;
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

  Object.keys(byGroup).sort().forEach(grp => {
    const teams = byGroup[grp].sort((a, b) => b.prob_win_group - a.prob_win_group);
    const card = document.createElement('div');
    card.className = 'group-card glass';
    card.innerHTML = `
      <div class="group-label">${esc(grp)}</div>
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

  const panel = document.createElement('div');
  panel.className = 'baum-panel';

  // Header row
  panel.innerHTML = `
    <div class="baum-header">
      <div class="baum-team-col"></div>
      ${stages.map(s => `<div class="baum-stage-col" title="${esc(s.label)}">${esc(s.abbr)}</div>`).join('')}
    </div>
  `;

  // Team rows
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

// ── Model tab ─────────────────────────────────────────────────────────────
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

// ── Utilities ─────────────────────────────────────────────────────────────
function pct(p) { return `${Math.round(p * 100)}%`; }
function esc(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ── PWA: iOS install banner ───────────────────────────────────────────────
function maybeShowInstallBanner() {
  const isIOS = /iP(hone|ad|od)/.test(navigator.userAgent);
  const isStandalone = window.navigator.standalone === true ||
    window.matchMedia('(display-mode: standalone)').matches;
  if (!isIOS || isStandalone) return;
  const dismissed = sessionStorage.getItem('install-dismissed');
  if (dismissed) return;
  const banner = document.getElementById('install-banner');
  if (banner) banner.hidden = false;
}

function dismissInstallBanner() {
  const banner = document.getElementById('install-banner');
  if (banner) banner.hidden = true;
  sessionStorage.setItem('install-dismissed', '1');
}
window.dismissInstallBanner = dismissInstallBanner;

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

// ── Swipe between tabs (horizontal) ──────────────────────────────────────
(function initSwipe() {
  const TAB_ORDER = ['heute', 'alle', 'gruppen', 'baum', 'diverg', 'modell'];
  let touchStartX = 0, touchStartY = 0;
  const app = document.getElementById('app');
  if (!app) return;

  document.addEventListener('touchstart', e => {
    touchStartX = e.touches[0].clientX;
    touchStartY = e.touches[0].clientY;
  }, { passive: true });

  document.addEventListener('touchend', e => {
    const dx = e.changedTouches[0].clientX - touchStartX;
    const dy = e.changedTouches[0].clientY - touchStartY;
    if (Math.abs(dx) < 50 || Math.abs(dy) > Math.abs(dx) * 0.8) return;
    // Only swipe in the content area, not on nav/header
    const target = e.changedTouches[0].target;
    if (target.closest('nav') || target.closest('header')) return;
    const idx = TAB_ORDER.indexOf(currentTab);
    if (dx < 0 && idx < TAB_ORDER.length - 1) setTab(TAB_ORDER[idx + 1]);
    else if (dx > 0 && idx > 0) setTab(TAB_ORDER[idx - 1]);
  }, { passive: true });
})();

// ── setTab with View Transitions ──────────────────────────────────────────
const _tabOrder = ['heute', 'alle', 'gruppen', 'baum', 'diverg', 'modell'];
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
