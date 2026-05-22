/* Monitor de emendas parlamentares — render minimalista */

const FMT_BR_DATE = new Intl.DateTimeFormat('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric' });
const FMT_BR_DAY_LONG = new Intl.DateTimeFormat('pt-BR', { weekday: 'long' });
const FMT_BR_DATE_LONG = new Intl.DateTimeFormat('pt-BR', { day: '2-digit', month: 'long', year: 'numeric' });
const NBSP = ' ';

function fmtBRLCompact(v) {
  if (v == null || isNaN(v)) return '—';
  const abs = Math.abs(v);
  if (abs >= 1e9) return `R$${NBSP}${(v / 1e9).toLocaleString('pt-BR', { maximumFractionDigits: 2 })}${NBSP}bi`;
  if (abs >= 1e6) return `R$${NBSP}${(v / 1e6).toLocaleString('pt-BR', { maximumFractionDigits: 1 })}${NBSP}mi`;
  if (abs >= 1e3) return `R$${NBSP}${(v / 1e3).toLocaleString('pt-BR', { maximumFractionDigits: 0 })}${NBSP}mil`;
  return `R$${NBSP}${v.toLocaleString('pt-BR', { maximumFractionDigits: 0 })}`;
}

function fmtBRLFull(v) {
  if (v == null || isNaN(v)) return '—';
  return `R$${NBSP}${v.toLocaleString('pt-BR', { maximumFractionDigits: 0 })}`;
}

function fmtPct(v, digits = 1) {
  if (v == null || isNaN(v)) return '—';
  return `${(v * 100).toLocaleString('pt-BR', { maximumFractionDigits: digits })}%`;
}

function parseISODate(iso) {
  if (!iso) return null;
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d);
}

function fmtDate(iso) {
  const d = parseISODate(iso);
  return d ? FMT_BR_DATE.format(d) : '—';
}

function fmtDateLong(iso) {
  const d = parseISODate(iso);
  return d ? FMT_BR_DATE_LONG.format(d) : '—';
}

function fmtDayName(iso) {
  const d = parseISODate(iso);
  if (!d) return '—';
  const name = FMT_BR_DAY_LONG.format(d);
  return name.charAt(0).toUpperCase() + name.slice(1);
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function setHTML(id, html) {
  const el = document.getElementById(id);
  if (el) el.innerHTML = html;
}

function escapeHTML(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function parseHttpDate(headerLastModified) {
  if (!headerLastModified) return null;
  const d = new Date(headerLastModified);
  return isNaN(d.getTime()) ? null : d;
}

/* === Renders === */

function renderHeader(report, meta) {
  const updated = meta.updated_at ? new Date(meta.updated_at) : null;
  const upStr = updated
    ? `${updated.toLocaleDateString('pt-BR')} ${updated.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}`
    : '—';
  setText('dateline-updated', upStr);

  const ptDate = parseHttpDate(meta.documents_last_modified);
  setText('dateline-source-pt', ptDate ? ptDate.toLocaleDateString('pt-BR') : '—');

  const siopDate = meta.siop_base_siafi_date || '—';
  setText('dateline-source-siop', siopDate);
  setText('compare-siop-date', siopDate);
  setText('chart-source', 'CGU/Portal da Transparência (por data do documento de empenho)');
}

function renderKPI(report) {
  const docs = (report.parallel_monitor || {}).documents || {};
  const totals = docs.totals || {};
  const empenhado = totals.total_empenhado_year || 0;
  const siopTotals = (((report.parallel_monitor || {}).siop_snapshot) || {}).totals || {};
  const autorizado = siopTotals.dotacao_atual_emenda || 0;
  const exec = autorizado > 0 ? empenhado / autorizado : 0;

  setText('kpi-empenhado', fmtBRLCompact(empenhado));
  setText('kpi-empenhado-note', `Até ${fmtDate(docs.date_max)} · por data do empenho · CGU`);

  setText('kpi-autorizado', fmtBRLCompact(autorizado));
  setText(
    'kpi-autorizado-note',
    `Teto orçamentário 2026 · SIOP base SIAFI ${(((report.parallel_monitor || {}).siop_snapshot) || {}).base_siafi_date || '—'}`
  );

  setText('kpi-execucao', fmtPct(exec));
  setText('kpi-execucao-note', `${fmtBRLCompact(empenhado)} de ${fmtBRLCompact(autorizado)} autorizados`);
}

function renderCompare(report) {
  const docs = (report.parallel_monitor || {}).documents || {};
  const ptYear = (docs.totals || {}).total_empenhado_year || 0;
  const siopTotals = (((report.parallel_monitor || {}).siop_snapshot) || {}).totals || {};
  const siopYear = siopTotals.empenhado || 0;

  setText('compare-pt', fmtBRLFull(ptYear));
  setText('compare-siop', fmtBRLFull(siopYear));

  const diff = ptYear - siopYear;
  const denom = Math.max(ptYear, siopYear);
  const pct = denom > 0 ? Math.abs(diff) / denom : 0;
  setText('compare-diff', (diff >= 0 ? '+' : '') + fmtBRLFull(diff).replace('R$', 'R$'));
  setText('compare-diff-pct', fmtPct(pct, 2));

  const badge = document.getElementById('compare-badge');
  const badgeText = document.getElementById('compare-badge-text');
  if (badge) {
    badge.classList.remove('good', 'bad', 'warn');
    if (ptYear === 0 || siopYear === 0) {
      badge.classList.add('warn');
      if (badgeText) badgeText.textContent = 'Uma fonte indisponível';
    } else if (pct <= 0.02) {
      badge.classList.add('good');
      if (badgeText) badgeText.textContent = `Fontes batem (±${(pct * 100).toLocaleString('pt-BR', { maximumFractionDigits: 2 })}%)`;
    } else if (pct <= 0.05) {
      badge.classList.add('warn');
      if (badgeText) badgeText.textContent = `Diferença moderada (${(pct * 100).toLocaleString('pt-BR', { maximumFractionDigits: 1 })}%)`;
    } else {
      badge.classList.add('bad');
      if (badgeText) badgeText.textContent = `Divergem (${(pct * 100).toLocaleString('pt-BR', { maximumFractionDigits: 1 })}%)`;
    }
  }

  const ptDateStr = docs.date_max ? fmtDate(docs.date_max) : '—';
  const siopDate = ((report.parallel_monitor || {}).siop_snapshot || {}).base_siafi_date || '—';
  setText('compare-diff-note', `Diferença absoluta · PT até ${ptDateStr}, SIOP base SIAFI ${siopDate}`);
}

function isoWeekKey(date) {
  // Returns 'YYYY-Www' (ISO week). Week starts on Monday; week 1 contains the year's first Thursday.
  const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
  const dayNum = d.getUTCDay() || 7;
  d.setUTCDate(d.getUTCDate() + 4 - dayNum);
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  const weekNo = Math.ceil(((d - yearStart) / 86400000 + 1) / 7);
  return `${d.getUTCFullYear()}-W${String(weekNo).padStart(2, '0')}`;
}

function isoWeekRange(date) {
  const d = parseISODate(date) || date;
  const dow = d.getDay() || 7; // Mon=1..Sun=7
  const monday = new Date(d);
  monday.setDate(d.getDate() - (dow - 1));
  const sunday = new Date(monday);
  sunday.setDate(monday.getDate() + 6);
  const toISO = (x) => `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, '0')}-${String(x.getDate()).padStart(2, '0')}`;
  return { start: toISO(monday), end: toISO(sunday) };
}

function renderSpotlight(report) {
  const docs = (report.parallel_monitor || {}).documents || {};
  const series = docs.daily_series || [];
  if (!series.length) {
    setText('spotlight-title', 'Sem dados disponíveis');
    setText('spotlight-body-1', 'A série diária da CGU ainda não foi carregada.');
    return;
  }

  const peak = [...series].sort((a, b) => b.empenhado - a.empenhado)[0];
  if (!peak || peak.empenhado <= 0) {
    setText('spotlight-title', 'Sem picos relevantes');
    setText('spotlight-body-1', 'Não há valores de empenho no período coberto pela série.');
    return;
  }

  const totalYear = (docs.totals || {}).total_empenhado_year || 0;
  const peakShare = totalYear > 0 ? peak.empenhado / totalYear : 0;

  // Agrupa por semana ISO e acha a semana de pico
  const weekTotals = new Map();
  for (const d of series) {
    if (!d.empenhado) continue;
    const dt = parseISODate(d.date);
    if (!dt) continue;
    const key = isoWeekKey(dt);
    weekTotals.set(key, (weekTotals.get(key) || 0) + d.empenhado);
  }
  let topWeekKey = null;
  let topWeekTotal = 0;
  for (const [k, v] of weekTotals.entries()) {
    if (v > topWeekTotal) {
      topWeekTotal = v;
      topWeekKey = k;
    }
  }
  const otherWeeksTotals = [...weekTotals.entries()].filter(([k]) => k !== topWeekKey).map(([, v]) => v);
  const otherWeeksAvg = otherWeeksTotals.length ? otherWeeksTotals.reduce((s, v) => s + v, 0) / otherWeeksTotals.length : 0;
  const ratio = otherWeeksAvg > 0 ? topWeekTotal / otherWeeksAvg : 0;

  // Range da semana de pico (a partir do dia do pico, calcula segunda-domingo)
  const range = isoWeekRange(peak.date);

  const peakDate = fmtDateLong(peak.date);
  const peakDay = fmtDayName(peak.date);

  setText('spotlight-title', `Pico de ${fmtBRLCompact(peak.empenhado)} em um único dia — ${peakDay}, ${peakDate}`);

  const body1 = `O recorde de empenho diário em 2026 é de <strong>${fmtBRLFull(peak.empenhado)}</strong>, registrado em <strong>${peakDate}</strong>. Esse dia, sozinho, representa <strong>${fmtPct(peakShare, 1)}</strong> de tudo que foi empenhado no ano até o momento.`;

  let body2 = '';
  if (topWeekKey && ratio > 0 && otherWeeksTotals.length > 0) {
    body2 = `A semana de <strong>${fmtDate(range.start)} a ${fmtDate(range.end)}</strong> concentrou <strong>${fmtBRLCompact(topWeekTotal)}</strong> em empenhos — cerca de <strong>${ratio.toLocaleString('pt-BR', { maximumFractionDigits: 0 })}×</strong> a média semanal das demais ${otherWeeksTotals.length} semanas do ano, que foi de ${fmtBRLCompact(otherWeeksAvg)} por semana.`;
  }

  setHTML('spotlight-body-1', body1);
  setHTML('spotlight-body-2', body2);
}

let dailyChart = null;
function renderDailyChart(report) {
  const docs = (report.parallel_monitor || {}).documents || {};
  const series = docs.daily_series || [];
  if (!series.length) return;

  const labels = series.map((d) => d.date);
  const dailyValues = series.map((d) => d.empenhado || 0);
  const accumValues = series.map((d) => d.acumulado_empenhado || 0);
  const peakIdx = dailyValues.indexOf(Math.max(...dailyValues));
  const barColors = dailyValues.map((_, i) => (i === peakIdx ? '#b45309' : '#2b3a55'));

  const ctx = document.getElementById('chart-daily');
  if (!ctx) return;
  if (dailyChart) dailyChart.destroy();

  dailyChart = new Chart(ctx, {
    data: {
      labels,
      datasets: [
        {
          type: 'bar',
          label: 'Empenhado no dia',
          data: dailyValues,
          backgroundColor: barColors,
          borderWidth: 0,
          yAxisID: 'y',
          order: 2,
          categoryPercentage: 0.9,
          barPercentage: 0.95,
        },
        {
          type: 'line',
          label: 'Acumulado do ano',
          data: accumValues,
          borderColor: '#b45309',
          backgroundColor: 'rgba(180,83,9,0.12)',
          borderWidth: 1.5,
          fill: false,
          tension: 0.1,
          pointRadius: 0,
          yAxisID: 'y1',
          order: 1,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#131311',
          titleColor: '#fff',
          bodyColor: '#fff',
          padding: 10,
          cornerRadius: 4,
          displayColors: false,
          callbacks: {
            title: (items) => fmtDateLong(items[0].label),
            label: (item) => {
              if (item.dataset.label.includes('Acumulado')) {
                return `Acumulado: ${fmtBRLFull(item.raw)}`;
              }
              return `Empenhado no dia: ${fmtBRLFull(item.raw)}`;
            },
          },
        },
      },
      scales: {
        x: {
          ticks: {
            color: '#686864',
            font: { size: 10, family: 'Inter' },
            maxTicksLimit: 12,
            callback: function (value) {
              const lab = this.getLabelForValue(value);
              if (!lab) return '';
              const parts = lab.split('-');
              return `${parts[2]}/${parts[1]}`;
            },
          },
          grid: { display: false },
          border: { color: '#e6e3dc' },
        },
        y: {
          beginAtZero: true,
          position: 'left',
          ticks: {
            color: '#686864',
            font: { size: 10, family: 'IBM Plex Mono' },
            padding: 4,
            callback: (v) => {
              if (v >= 1e9) return (v / 1e9).toLocaleString('pt-BR', { maximumFractionDigits: 1 }) + 'b';
              if (v >= 1e6) return (v / 1e6).toLocaleString('pt-BR', { maximumFractionDigits: 0 }) + 'mi';
              return v.toLocaleString('pt-BR');
            },
          },
          grid: { color: '#f1efe8' },
          border: { display: false },
        },
        y1: {
          beginAtZero: true,
          position: 'right',
          ticks: {
            color: '#b45309',
            font: { size: 10, family: 'IBM Plex Mono' },
            padding: 4,
            callback: (v) => {
              if (v >= 1e9) return (v / 1e9).toLocaleString('pt-BR', { maximumFractionDigits: 0 }) + 'b';
              if (v >= 1e6) return (v / 1e6).toLocaleString('pt-BR', { maximumFractionDigits: 0 }) + 'mi';
              return v.toLocaleString('pt-BR');
            },
          },
          grid: { display: false },
          border: { display: false },
        },
      },
    },
  });

  const peak = series[peakIdx];
  const note = peak
    ? `Pico do ano em ${fmtDateLong(peak.date)} (${fmtDayName(peak.date)}): ${fmtBRLCompact(peak.empenhado)} em um único dia, destacado em laranja.`
    : '';
  setText('chart-daily-note', note);
}

function renderTopDays(report) {
  const docs = (report.parallel_monitor || {}).documents || {};
  const series = docs.daily_series || [];
  const totalYear = (docs.totals || {}).total_empenhado_year || 0;
  const top = [...series]
    .filter((d) => d.empenhado > 0)
    .sort((a, b) => b.empenhado - a.empenhado)
    .slice(0, 10);
  const tbody = document.getElementById('table-top-days');
  if (!tbody) return;
  if (!top.length) {
    tbody.innerHTML = '<tr><td colspan="4" class="quiet">Sem dados.</td></tr>';
    return;
  }
  tbody.innerHTML = top
    .map(
      (d, i) => `
    <tr class="${i === 0 ? 'row-peak' : ''}">
      <td class="date-cell">${fmtDate(d.date)}</td>
      <td class="quiet">${fmtDayName(d.date)}</td>
      <td class="num">${fmtBRLFull(d.empenhado)}</td>
      <td class="num">${totalYear > 0 ? fmtPct(d.empenhado / totalYear, 1) : '—'}</td>
    </tr>
  `
    )
    .join('');
}

function renderTopAuthorsAndOrgaos(report) {
  const docs = (report.parallel_monitor || {}).documents || {};
  const authors = docs.top_authors_year || [];
  const tbodyA = document.getElementById('table-top-authors-pt');
  if (tbodyA) {
    if (!authors.length) {
      tbodyA.innerHTML = '<tr><td colspan="3" class="quiet">Sem dados.</td></tr>';
    } else {
      tbodyA.innerHTML = authors
        .slice(0, 12)
        .map(
          (a) => `
        <tr>
          <td>${escapeHTML(a.author)}</td>
          <td class="quiet">${escapeHTML(a.party || '—')}</td>
          <td class="num">${fmtBRLFull(a.empenhado)}</td>
        </tr>
      `
        )
        .join('');
    }
  }

  const siopDetails = ((report.parallel_monitor || {}).siop_details) || {};
  const orgaos = siopDetails.top_orgaos || [];
  const tbodyO = document.getElementById('table-top-orgaos-siop');
  if (tbodyO) {
    if (!orgaos.length) {
      tbodyO.innerHTML = '<tr><td colspan="2" class="quiet">SIOP indisponível.</td></tr>';
    } else {
      tbodyO.innerHTML = orgaos
        .slice(0, 12)
        .map(
          (o) => `
        <tr>
          <td>${escapeHTML(o.orgao)}</td>
          <td class="num">${fmtBRLFull(o.empenhado)}</td>
        </tr>
      `
        )
        .join('');
    }
  }
}

function renderTopDestinations(report) {
  const docs = (report.parallel_monitor || {}).documents || {};
  const dests = docs.top_destinations_last_day || [];
  const tbody = document.getElementById('table-top-destinations');
  if (!tbody) return;
  if (!dests.length) {
    tbody.innerHTML = '<tr><td colspan="2" class="quiet">Sem dados.</td></tr>';
    return;
  }
  tbody.innerHTML = dests
    .slice(0, 12)
    .map(
      (d) => `
    <tr>
      <td>${escapeHTML(d.destination)}</td>
      <td class="num">${fmtBRLFull(d.empenhado)}</td>
    </tr>
  `
    )
    .join('');
}

function renderHealth(report, meta) {
  // PT
  const ptStat = document.getElementById('health-pt-stat');
  const ptMeta = document.getElementById('health-pt-meta');
  if (ptStat && ptMeta) {
    const lm = parseHttpDate(meta.documents_last_modified);
    const today = new Date();
    const ageDays = lm ? Math.round((today - lm) / 86400000) : null;
    if (lm) {
      const cls = ageDays <= 2 ? 'good' : ageDays <= 5 ? 'warn' : 'bad';
      const label = ageDays === 0 ? 'atualizado hoje' : `${ageDays} ${ageDays === 1 ? 'dia' : 'dias'} atrás`;
      ptStat.innerHTML = `<span class="badge ${cls}"><span class="dot"></span>${label}</span>`;
      ptMeta.innerHTML = `Servidor da CGU publicou em ${lm.toLocaleString('pt-BR')}.<br>Histórico do pipeline: <strong>${(report.daily_history || []).length}</strong> snapshots.`;
    } else {
      ptStat.textContent = 'sem timestamp';
      ptMeta.textContent = 'O cabeçalho Last-Modified do servidor não foi capturado.';
    }
  }

  // SIOP
  const siopStat = document.getElementById('health-siop-stat');
  const siopMeta = document.getElementById('health-siop-meta');
  if (siopStat && siopMeta) {
    const broken = meta.siop_broken_days || 0;
    const days = meta.siop_history_days || 0;
    const fallback = !!meta.siop_snapshot_fallback_from_previous;
    const avail = !!meta.siop_snapshot_available;
    let cls = 'good';
    let label = 'OK · 0 dia zerado';
    if (!avail) {
      cls = 'bad';
      label = 'indisponível agora';
    } else if (broken > 0) {
      cls = 'bad';
      label = `${broken} dia(s) zerado(s)`;
    } else if (fallback) {
      cls = 'warn';
      label = 'usando fallback do snapshot anterior';
    }
    siopStat.innerHTML = `<span class="badge ${cls}"><span class="dot"></span>${label}</span>`;
    const errorMsg = meta.siop_snapshot_error
      ? `<br>Último erro: <code>${escapeHTML(meta.siop_snapshot_error)}</code>`
      : '';
    siopMeta.innerHTML = `${days} dias no histórico SIOP. Base SIAFI: ${meta.siop_base_siafi_date || '—'}.${errorMsg}`;
  }
}

/* === Boot === */

(async function bootstrap() {
  try {
    const cacheBuster = `?v=${Date.now()}`;
    const [report, meta] = await Promise.all([
      fetch(`./data/report_data.json${cacheBuster}`).then((r) => r.json()),
      fetch(`./data/metadata.json${cacheBuster}`).then((r) => r.json()),
    ]);

    renderHeader(report, meta);
    renderSpotlight(report);
    renderKPI(report);
    renderCompare(report);
    renderDailyChart(report);
    renderTopDays(report);
    renderTopAuthorsAndOrgaos(report);
    renderTopDestinations(report);
    renderHealth(report, meta);
  } catch (err) {
    console.error('[dashboard] falha ao carregar dados:', err);
    setText('spotlight-title', 'Falha ao carregar dados');
    setText('spotlight-body-1', `Erro: ${err && err.message ? err.message : err}`);
  }
})();
