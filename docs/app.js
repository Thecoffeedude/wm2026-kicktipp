// ── Config ────────────────────────────────────────────────────────────────
const DATA_URL = './data.json';
const FLAG_BASE = 'https://flagcdn.com/w80/';
const DIVERGENCE_THRESHOLD = 0.04;
const XG_MAX = 4.0;

// ── State ─────────────────────────────────────────────────────────────────
let allMatches = [];
let metadata = {};
let currentTab = 'heute';

// ── Team → ISO 3166-1 alpha-2 map ─────────────────────────────────────────
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
  try {
    const res = await fetch(DATA_URL);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    allMatches = data.matches;
    metadata = data.metadata;
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
  if (currentTab === 'modell') { renderModel(app); return; }

  let matches = [...allMatches];

  if (currentTab === 'heute') {
    matches = matches.filter(m => isToday(m.commence_time) || isTomorrow(m.commence_time));
    matches.sort((a, b) => (parseKickoff(a.commence_time) || 0) - (parseKickoff(b.commence_time) || 0));
  } else if (currentTab === 'alle') {
    matches.sort((a, b) => (parseKickoff(a.commence_time) || 0) - (parseKickoff(b.commence_time) || 0));
  } else if (currentTab === 'diverg') {
    matches.sort((a, b) => maxDivergence(b) - maxDivergence(a));
  }

  app.innerHTML = '';

  if (currentTab === 'heute' && matches.length === 0) {
    // Try showing the next 3 upcoming matches instead
    const upcoming = [...allMatches]
      .filter(m => (parseKickoff(m.commence_time) || 0) >= Date.now())
      .sort((a, b) => parseKickoff(a.commence_time) - parseKickoff(b.commence_time))
      .slice(0, 3);
    if (upcoming.length) {
      app.innerHTML = `<div class="eyebrow">Nächste Spiele</div>`;
      upcoming.forEach((m, i) => app.appendChild(buildCard(m, i)));
    } else {
      app.innerHTML = `<div class="eyebrow">Keine Spiele für heute geplant.</div>`;
    }
    animateBars();
    return;
  }

  const label = currentTab === 'heute'
    ? `Heute & Morgen · ${matches.length} Spiel${matches.length !== 1 ? 'e' : ''}`
    : currentTab === 'diverg'
    ? `Nach Divergenz · ${matches.length} Spiele`
    : `Alle Spiele · ${matches.length} Spiele`;

  app.innerHTML = `<div class="eyebrow">${esc(label)}</div>`;
  matches.forEach((m, i) => app.appendChild(buildCard(m, i)));
  animateBars();
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
        <span class="v">${lambda.home.toFixed(2)}</span>
      </div>
      <div class="xg-row-d">
        <span class="l">${aAbbr}</span>
        <div class="xg-track"><div class="xg-fill" style="--xw:${aW}%;background:var(--away)"></div></div>
        <span class="v">${lambda.away.toFixed(2)}</span>
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
  // Animate xG fills on open
  if (open) {
    requestAnimationFrame(() => {
      card.querySelectorAll('.xg-fill').forEach(el => {
        const w = getComputedStyle(el).getPropertyValue('--xw').trim();
        if (w) el.style.width = w;
      });
    });
  }
}
window.toggleCard = toggleCard;

// ── Bar animation trigger ─────────────────────────────────────────────────
function animateBars() {
  requestAnimationFrame(() => requestAnimationFrame(() => {
    document.querySelectorAll('.bar .seg').forEach(seg => {
      const w = getComputedStyle(seg).getPropertyValue('--w').trim();
      if (w) seg.style.width = w;
    });
  }));
}

// ── Utilities ─────────────────────────────────────────────────────────────
function pct(p) { return `${(p * 100).toFixed(1)}%`; }
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

// Local testing helpers via URL params (?dark, ?tab=alle, ?open=0)
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
      // Trigger xG fill animation
      cards[idx].querySelectorAll('.xg-fill').forEach(el => {
        const w = getComputedStyle(el).getPropertyValue('--xw').trim();
        if (w) el.style.width = w;
      });
    }
  }
}

init().then(applyOpenParam).catch(() => {});
