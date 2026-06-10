const DATA_URL = './data.json';
const DIVERGENCE_THRESHOLD = 0.04;

let allMatches = [];
let sortMode = 'time';

async function init() {
  try {
    const res = await fetch(DATA_URL);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    allMatches = data.matches;
    renderMeta(data.metadata);
    renderMatches();
  } catch (e) {
    document.getElementById('app').innerHTML =
      `<div class="error">Fehler beim Laden der Daten: ${e.message}<br>
       Starte den Server mit <code>python3 -m http.server</code> aus dem docs/-Ordner.</div>`;
  }
}

function renderMeta(meta) {
  const dt = new Date(meta.generated_at);
  const formatted = dt.toLocaleString('de-DE', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit', timeZoneName: 'short',
  });
  document.getElementById('meta').textContent =
    `${meta.match_count} Spiele · Stand: ${formatted}${meta.mock ? ' · Mock-Daten' : ''}`;
}

function setSortMode(mode) {
  sortMode = mode;
  document.getElementById('sort-time').classList.toggle('sort-btn--active', mode === 'time');
  document.getElementById('sort-div').classList.toggle('sort-btn--active', mode === 'divergence');
  renderMatches();
}
window.setSortMode = setSortMode;

function renderMatches() {
  const sorted = [...allMatches].sort((a, b) => {
    if (sortMode === 'divergence') {
      const maxDiv = m => Math.max(...Object.values(m.divergence || {}), 0);
      return maxDiv(b) - maxDiv(a);
    }
    return a.commence_time.localeCompare(b.commence_time);
  });
  const app = document.getElementById('app');
  app.innerHTML = '';
  sorted.forEach(match => app.appendChild(buildCard(match)));
}

// ── Card builder ─────────────────────────────────────────────────────────

function buildCard(match) {
  const card = document.createElement('div');
  card.className = 'card';

  const dt = new Date(match.commence_time);
  const hasTime = match.commence_time.length > 10;
  const dateStr = dt.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit' });
  const timeStr = hasTime
    ? dt.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' })
    : '–:––';

  const tip      = match.recommended_tip;
  const modal    = match.modal_scoreline;
  const sources  = match.sources || {};
  const ua       = sources.uanalyse;
  const oddsC    = sources.odds_consensus;
  const agree    = match.agreement || {};
  const div      = match.divergence || {};
  const maxDiv   = Math.max(...Object.values(div), 0);

  const primaryP = ua ? ua.p : (oddsC ? oddsC.p : null);

  card.innerHTML = `
    ${buildHeader(match.home_team, match.away_team, dateStr, timeStr, match.stage)}
    ${buildTipRow(tip, modal)}
    ${ua ? buildXgRow(ua.lambda) : ''}
    ${primaryP ? buildConsensusSection(primaryP, ua, oddsC, agree, maxDiv) : ''}
    ${match.bookmakers && match.bookmakers.length > 0 ? `
      <button class="card__books-toggle" aria-expanded="false" onclick="toggleBooks(this)">
        <span class="toggle-chevron">▼</span>
        ${match.bookmakers.length} Buchmacher
      </button>
      <div class="card__books">
        ${match.bookmakers.map(buildBookRow).join('')}
      </div>` : ''}
  `;
  return card;
}

function buildHeader(home, away, date, time, stage) {
  const stageLabel = stage ? `<div class="card__stage">${esc(stage)}</div>` : '';
  return `
    <div class="card__header">
      <div class="card__team card__team--home">${esc(home)}</div>
      <div class="card__kickoff">
        ${stageLabel}
        <div class="card__kickoff-vs">vs</div>
        <div>${date} · ${time}</div>
      </div>
      <div class="card__team card__team--away">${esc(away)}</div>
    </div>`;
}

function buildTipRow(tip, modal) {
  if (!tip) {
    return `<div class="card__tip"><span style="color:var(--c-text-muted);font-size:0.85rem">Keine Vorhersage verfügbar</span></div>`;
  }
  const tipIsSameAsModal = modal && tip.home === modal.home && tip.away === modal.away;
  const modalNote = tipIsSameAsModal
    ? '= wahrscheinlichstes Ergebnis'
    : (modal ? `Modal: ${modal.home}:${modal.away} (${pct(modal.probability)})` : '');

  const sourceLabel = tip.based_on === 'uanalyse'
    ? '<span class="source-chip source-chip--primary">uanalyse</span>'
    : '<span class="source-chip source-chip--secondary">Wettbüros</span>';

  return `
    <div class="card__tip">
      <div class="tip-score">
        <span class="tip-score__num tip-score__num--home">${tip.home}</span>
        <span class="tip-score__sep">:</span>
        <span class="tip-score__num tip-score__num--away">${tip.away}</span>
      </div>
      <div>
        <div class="tip-meta__label">Empfohlener Tipp ${sourceLabel}</div>
        <div class="tip-ev">⌀ ${tip.expected_points} Pkt.</div>
        <div class="tip-modal">${modalNote}</div>
      </div>
    </div>`;
}

function buildXgRow(lambda) {
  if (!lambda) return '';
  return `
    <div class="xg-row">
      <span class="xg-chip" style="color:#60a5fa">xG Heim: ${lambda.home}</span>
      <span class="xg-chip" style="color:#fb923c">xG Auswärts: ${lambda.away}</span>
    </div>`;
}

function buildConsensusSection(primaryP, ua, oddsC, agree, maxDiv) {
  const hP = Math.round(primaryP.home * 100);
  const dP = Math.round(primaryP.draw * 100);
  const aP = 100 - hP - dP;

  const sourceBadge = ua
    ? `<span class="source-label">Wahrsch. (uanalyse)</span>`
    : `<span class="source-label">Wahrsch. (Wettbüros)</span>`;

  // Badges
  const badges = [];
  if (agree.same_tendency === false) {
    badges.push(`<span class="badge-sources" title="${esc(agree.note)}">⚡ Quellen uneinig</span>`);
  }
  if (maxDiv >= DIVERGENCE_THRESHOLD) {
    badges.push(`<span class="badge-warn">⚡ Bücher uneinig</span>`);
  }

  // If both sources present, show odds comparison line
  let oddsLine = '';
  if (ua && oddsC) {
    const oH = Math.round(oddsC.p.home * 100);
    const oD = Math.round(oddsC.p.draw * 100);
    const oA = 100 - oH - oD;
    oddsLine = `
      <div class="odds-compare">
        <span class="odds-compare__label">Wettbüros:</span>
        <span class="pct--home"><span class="pct-label">H</span>${oH}%</span>
        <span class="pct--draw"><span class="pct-label">U</span>${oD}%</span>
        <span class="pct--away"><span class="pct-label">A</span>${oA}%</span>
      </div>`;
  }

  return `
    <div class="card__consensus">
      <div class="consensus-labels">
        <span>Heimsieg</span><span>Unentschieden</span><span>Auswärtssieg</span>
      </div>
      <div class="consensus-bar">
        <div class="consensus-bar__seg consensus-bar__seg--home" style="flex:${primaryP.home}">${hP}%</div>
        <div class="consensus-bar__seg consensus-bar__seg--draw" style="flex:${primaryP.draw}">${dP}%</div>
        <div class="consensus-bar__seg consensus-bar__seg--away" style="flex:${primaryP.away}">${aP}%</div>
      </div>
      <div class="consensus-footer">
        <div class="consensus-pcts">
          ${sourceBadge}
          <span class="pct--home"><span class="pct-label">H</span>${pct(primaryP.home)}</span>
          <span class="pct--draw"><span class="pct-label">U</span>${pct(primaryP.draw)}</span>
          <span class="pct--away"><span class="pct-label">A</span>${pct(primaryP.away)}</span>
        </div>
        <div class="badges">${badges.join('')}</div>
      </div>
      ${oddsLine}
    </div>`;
}

function buildBookRow(book) {
  const p = book.probabilities;
  return `
    <div class="book-row">
      <span class="book-name" title="${esc(book.title)}">${esc(book.title)}</span>
      <div class="book-bar">
        <div class="book-bar__seg book-bar__seg--home" style="flex:${p.home}"></div>
        <div class="book-bar__seg book-bar__seg--draw" style="flex:${p.draw}"></div>
        <div class="book-bar__seg book-bar__seg--away" style="flex:${p.away}"></div>
      </div>
      <span class="book-margin">Marge ${pct(book.overround)}</span>
    </div>`;
}

function toggleBooks(btn) {
  const expanded = btn.getAttribute('aria-expanded') === 'true';
  btn.setAttribute('aria-expanded', String(!expanded));
  btn.nextElementSibling.classList.toggle('open', !expanded);
}
window.toggleBooks = toggleBooks;

function pct(p) { return `${(p * 100).toFixed(1)}%`; }
function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

init();
