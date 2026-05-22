const DATA_URL = "./data/report_data.json";

const currencyFmt = new Intl.NumberFormat("pt-BR");
const percentFmt = new Intl.NumberFormat("pt-BR", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});
const dateFmt = new Intl.DateTimeFormat("pt-BR", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});
const scheduleHelper = window.DashboardUpdateSchedule || null;

let payload = null;
let charts = {
  monthlyRate: null,
  monthlyVolume: null,
  governments: null,
  parties: null,
};

const refs = {
  updated: document.getElementById("status-updated"),
  range: document.getElementById("status-range"),
  scraperHealthBtn: document.getElementById("scraper-health-btn"),
  updateScheduleNote: document.getElementById("update-schedule-note"),
  govFilter: document.getElementById("filter-government"),
  minParty: document.getElementById("filter-min-party"),
  minDeputy: document.getElementById("filter-min-deputy"),
  exportCurrent: document.getElementById("btn-export-current"),
  filterHelp: document.getElementById("filter-help"),

  kpiAlignment: document.getElementById("kpi-alignment"),
  kpiPro: document.getElementById("kpi-pro"),
  kpiAnti: document.getElementById("kpi-anti"),
  kpiVotes: document.getElementById("kpi-votes"),
  kpiNote: document.getElementById("kpi-note"),

  methodList: document.getElementById("method-list"),
  notesList: document.getElementById("notes-list"),

  tableGovernments: document.getElementById("table-governments"),
  partyNote: document.getElementById("party-note"),
  partyContextNote: document.getElementById("party-context-note"),
  deputyNote: document.getElementById("deputy-note"),

  tablePartiesPro: document.getElementById("table-parties-pro"),
  tablePartiesAnti: document.getElementById("table-parties-anti"),
  tableDeputies: document.getElementById("table-deputies"),
  tableRecent: document.getElementById("table-recent"),
};

const PARTY_CONTEXT = {
  PSL: "Partido extinto; parte da bancada migrou para o União Brasil (fusão PSL + DEM).",
  DEM: "Partido extinto; fundiu-se com o PSL para formar o União Brasil.",
  PSC: "Partido incorporado ao Progressistas (PP) em 2023.",
  PTB: "Teve o registro partidário cancelado pelo TSE em 2022.",
  PROS: "Partido incorporado ao Solidariedade em 2023.",
};

function safeNumber(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

function formatInt(v) {
  return currencyFmt.format(safeNumber(v));
}

function formatPct(v) {
  return `${percentFmt.format(safeNumber(v))}%`;
}

function esc(value) {
  return (value || "")
    .toString()
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function normalizePartyName(value) {
  const raw = (value || "").toString().trim();
  return raw || "Sem partido informado";
}

function normalizeUf(value) {
  const raw = (value || "").toString().trim().toUpperCase();
  return raw || "--";
}

function partyCellHtml(party) {
  const normalized = normalizePartyName(party);
  const context = PARTY_CONTEXT[normalized];
  if (!context) return esc(normalized);
  return `<span title="${esc(context)}">${esc(normalized)}*</span>`;
}

function parseDate(dateString) {
  if (!dateString) return null;
  const value = new Date(`${dateString}T12:00:00`);
  return Number.isNaN(value.getTime()) ? null : value;
}

function parseMonth(monthString) {
  if (!monthString) return null;
  const value = new Date(`${monthString}-01T12:00:00`);
  return Number.isNaN(value.getTime()) ? null : value;
}

function countMissingMonths(monthlySeries) {
  if (!Array.isArray(monthlySeries) || monthlySeries.length < 2) return 0;
  let missing = 0;
  for (let i = 1; i < monthlySeries.length; i += 1) {
    const prev = parseMonth(monthlySeries[i - 1].month);
    const curr = parseMonth(monthlySeries[i].month);
    if (!prev || !curr) continue;
    const diff = (curr.getFullYear() - prev.getFullYear()) * 12 + (curr.getMonth() - prev.getMonth());
    if (diff > 1) {
      missing += (diff - 1);
    }
  }
  return missing;
}

function latestMonthRange(monthlySeries) {
  if (!monthlySeries.length) return "--";
  const first = monthlySeries[0].month;
  const last = monthlySeries[monthlySeries.length - 1].month;
  return `${first} até ${last}`;
}

function pickGovStats(baseItem, govId) {
  if (!baseItem) return { pro_votes: 0, anti_votes: 0, total_votes: 0, alignment_pct: 0 };
  if (govId === "all") {
    return {
      pro_votes: safeNumber(baseItem.pro_votes),
      anti_votes: safeNumber(baseItem.anti_votes),
      total_votes: safeNumber(baseItem.total_votes),
      alignment_pct: safeNumber(baseItem.alignment_pct),
    };
  }
  const byGov = baseItem.by_government || {};
  const row = byGov[govId];
  if (!row) return { pro_votes: 0, anti_votes: 0, total_votes: 0, alignment_pct: 0 };
  return {
    pro_votes: safeNumber(row.pro_votes),
    anti_votes: safeNumber(row.anti_votes),
    total_votes: safeNumber(row.total_votes),
    alignment_pct: safeNumber(row.alignment_pct),
  };
}

function selectedGovernment() {
  return refs.govFilter.value || "all";
}

function filterMonthly(series, govId) {
  if (govId === "all") return series.slice();
  return series.filter((row) => row.government_id === govId);
}

function buildSummary(govId) {
  if (govId === "all") {
    return {
      pro_votes: payload.summary.pro_votes,
      anti_votes: payload.summary.anti_votes,
      total_votes: payload.summary.total_votes,
      alignment_pct: payload.summary.alignment_pct,
      votacoes: payload.summary.votacoes_validas,
      label: "Série completa",
    };
  }

  const gov = payload.government_series.find((row) => row.id === govId);
  if (!gov) {
    return {
      pro_votes: 0,
      anti_votes: 0,
      total_votes: 0,
      alignment_pct: 0,
      votacoes: 0,
      label: "Sem dados",
    };
  }

  return {
    pro_votes: gov.pro_votes,
    anti_votes: gov.anti_votes,
    total_votes: gov.total_votes,
    alignment_pct: gov.alignment_pct,
    votacoes: gov.votacoes,
    label: gov.label,
  };
}

function setMethodology() {
  refs.methodList.innerHTML = "";
  refs.notesList.innerHTML = "";

  for (const item of payload.methodology.items || []) {
    const li = document.createElement("li");
    li.textContent = item;
    refs.methodList.appendChild(li);
  }

  for (const note of payload.methodology.notes || []) {
    const li = document.createElement("li");
    li.textContent = note;
    refs.notesList.appendChild(li);
  }

  const extraNotes = [
    "Partidos extintos ou incorporados permanecem com a sigla original do momento da votação (ex.: PSL, DEM, PSC, PTB, PROS).",
    "No recorte 'todos os governos', o PL pode aparecer com alinhamento médio baixo porque foi oposição em boa parte do período 2019-2021.",
    "Esta versão cobre o período disponível no painel atual (2019 em diante), não a série histórica completa desde 2003.",
  ];
  for (const note of extraNotes) {
    const li = document.createElement("li");
    li.textContent = note;
    refs.notesList.appendChild(li);
  }
}

function setStatus() {
  const metadata = payload.metadata || {};
  const updatedAtRaw = metadata.updated_at || metadata.generated_at || "";
  const updatedAt = updatedAtRaw ? new Date(updatedAtRaw) : null;
  const updateNotice = scheduleHelper ? scheduleHelper.buildNotice("basometro", updatedAtRaw) : null;
  const updatedLabel = updatedAtRaw
    ? (scheduleHelper ? scheduleHelper.formatDateTime(updatedAtRaw) : (updatedAt ? dateFmt.format(updatedAt) : "--"))
    : "--";
  refs.updated.textContent = `Atualizado: ${updatedLabel}`;
  if (refs.updateScheduleNote) {
    refs.updateScheduleNote.textContent = updateNotice
      ? updateNotice.text
      : `Ultima atualizacao: ${updatedLabel}.`;
  }
  if (scheduleHelper && refs.scraperHealthBtn) {
    scheduleHelper.applyHealthState("basometro", updatedAtRaw, refs.scraperHealthBtn);
  }
  refs.range.textContent = `Período: ${metadata.start_year || "--"} até ${metadata.end_year || "--"}`;
}

function setGovFilterOptions() {
  refs.govFilter.innerHTML = "";
  const optAll = document.createElement("option");
  optAll.value = "all";
  optAll.textContent = "Todos os governos";
  refs.govFilter.appendChild(optAll);

  for (const gov of payload.governments || []) {
    const opt = document.createElement("option");
    opt.value = gov.id;
    opt.textContent = gov.label;
    refs.govFilter.appendChild(opt);
  }
}

function setKpis(summary, filteredMonthly) {
  refs.kpiAlignment.textContent = formatPct(summary.alignment_pct);
  refs.kpiPro.textContent = formatInt(summary.pro_votes);
  refs.kpiAnti.textContent = formatInt(summary.anti_votes);
  refs.kpiVotes.textContent = formatInt(summary.votacoes);

  const missingMonths = countMissingMonths(filteredMonthly);
  const gapNote = missingMonths > 0
    ? ` Há ${formatInt(missingMonths)} mês(es) sem votação nominal no recorte e por isso eles não aparecem na série mensal.`
    : "";
  refs.kpiNote.textContent = `${summary.label}. ${formatInt(summary.total_votes)} votos de deputados considerados, em ${filteredMonthly.length} meses com votações válidas.${gapNote}`;
}

function drawMonthlyRate(filteredMonthly) {
  const ctx = document.getElementById("chart-monthly-rate");
  if (charts.monthlyRate) charts.monthlyRate.destroy();

  const labels = filteredMonthly.map((row) => row.month);
  const data = filteredMonthly.map((row) => safeNumber(row.alignment_pct));

  charts.monthlyRate = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "% de alinhamento",
          data,
          borderColor: "#0f6aa6",
          backgroundColor: "rgba(15, 106, 166, 0.15)",
          pointBackgroundColor: "#0f6aa6",
          pointRadius: 2,
          tension: 0.22,
          fill: true,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      scales: {
        y: {
          min: 0,
          max: 100,
          ticks: {
            callback: (value) => `${value}%`,
          },
          grid: { color: "rgba(0, 0, 0, 0.08)" },
        },
        x: {
          ticks: {
            maxRotation: 0,
            autoSkip: true,
            maxTicksLimit: 12,
          },
          grid: { display: false },
        },
      },
      plugins: {
        legend: { display: false },
      },
    },
  });
}

function drawMonthlyVolume(filteredMonthly) {
  const ctx = document.getElementById("chart-monthly-volume");
  if (charts.monthlyVolume) charts.monthlyVolume.destroy();

  const labels = filteredMonthly.map((row) => row.month);
  const pro = filteredMonthly.map((row) => safeNumber(row.pro_votes));
  const anti = filteredMonthly.map((row) => safeNumber(row.anti_votes));

  charts.monthlyVolume = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [
        {
          label: "Pró-governo",
          data: pro,
          backgroundColor: "rgba(47, 143, 119, 0.86)",
          borderColor: "#2f8f77",
          borderWidth: 1,
          stack: "votes",
        },
        {
          label: "Contra-governo",
          data: anti,
          backgroundColor: "rgba(196, 91, 77, 0.82)",
          borderColor: "#c45b4d",
          borderWidth: 1,
          stack: "votes",
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: {
          stacked: true,
          ticks: {
            maxRotation: 0,
            autoSkip: true,
            maxTicksLimit: 12,
          },
          grid: { display: false },
        },
        y: {
          stacked: true,
          beginAtZero: true,
          grid: { color: "rgba(0, 0, 0, 0.08)" },
        },
      },
      plugins: {
        legend: { position: "top" },
      },
    },
  });
}

function setGovernmentTable() {
  refs.tableGovernments.innerHTML = "";

  const rows = (payload.government_series || []).slice().sort((a, b) => {
    const da = parseDate(a.start);
    const db = parseDate(b.start);
    return da - db;
  });

  if (!rows.length) {
    refs.tableGovernments.innerHTML = '<tr><td colspan="6">Sem dados de governos.</td></tr>';
    return;
  }

  for (const row of rows) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${row.label}</td>
      <td><span class="chip chip-pro">${formatPct(row.alignment_pct)}</span></td>
      <td>${formatInt(row.pro_votes)}</td>
      <td>${formatInt(row.anti_votes)}</td>
      <td>${formatInt(row.total_votes)}</td>
      <td>${formatInt(row.votacoes)}</td>
    `;
    refs.tableGovernments.appendChild(tr);
  }
}

function drawGovernmentChart() {
  const ctx = document.getElementById("chart-governments");
  if (charts.governments) charts.governments.destroy();

  const rows = (payload.government_series || []).slice().sort((a, b) => {
    const da = parseDate(a.start);
    const db = parseDate(b.start);
    return da - db;
  });

  charts.governments = new Chart(ctx, {
    type: "bar",
    data: {
      labels: rows.map((row) => row.label),
      datasets: [
        {
          label: "% de alinhamento",
          data: rows.map((row) => safeNumber(row.alignment_pct)),
          backgroundColor: rows.map((row) => (row.id === "lula3" ? "rgba(47, 143, 119, 0.86)" : "rgba(15, 106, 166, 0.78)")),
          borderColor: rows.map((row) => (row.id === "lula3" ? "#2f8f77" : "#0f6aa6")),
          borderWidth: 1,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: {
          min: 0,
          max: 100,
          ticks: { callback: (value) => `${value}%` },
          grid: { color: "rgba(0, 0, 0, 0.08)" },
        },
        x: {
          grid: { display: false },
          ticks: {
            autoSkip: false,
            maxRotation: 20,
            minRotation: 0,
          },
        },
      },
      plugins: {
        legend: { display: false },
      },
    },
  });
}

function buildPartyRows(govId, minVotes) {
  const out = [];
  for (const item of payload.party_ranking || []) {
    const stats = pickGovStats(item, govId);
    if (stats.total_votes < minVotes) continue;
    out.push({
      party: normalizePartyName(item.party),
      ...stats,
    });
  }
  out.sort((a, b) => b.alignment_pct - a.alignment_pct || b.total_votes - a.total_votes);
  return out;
}

function setPartyContextNote(govId, rows) {
  if (!refs.partyContextNote) return;
  if (!rows.length) {
    refs.partyContextNote.textContent = "Sem partidos suficientes no recorte atual para mostrar contexto.";
    return;
  }

  const parties = new Set(rows.map((row) => row.party));
  const flagged = [...parties].filter((party) => PARTY_CONTEXT[party]);
  const notes = [];

  if (flagged.length) {
    const details = flagged
      .map((party) => `${party}: ${PARTY_CONTEXT[party]}`)
      .join(" | ");
    notes.push(`Siglas históricas no recorte: ${details}`);
  }

  if (govId === "all" && parties.has("PL")) {
    notes.push("Leitura de contexto: no agregado de todos os governos, o PL inclui fase de oposição entre 2019 e 2021; por isso o percentual médio pode parecer contraintuitivo quando comparado ao cenário atual.");
  }

  notes.push("Cobertura temporal desta versão: 2019 em diante.");
  refs.partyContextNote.textContent = notes.join(" ");
}

function setPartyTables(govId, minVotes) {
  const rows = buildPartyRows(govId, minVotes);
  const totalParties = (payload.party_ranking || []).length;
  const excluded = Math.max(0, totalParties - rows.length);
  refs.partyNote.textContent = `Filtro aplicado: mínimo de ${formatInt(minVotes)} votos por partido (${rows.length} partidos no recorte; ${excluded} excluídos pelo mínimo).`;

  refs.tablePartiesPro.innerHTML = "";
  refs.tablePartiesAnti.innerHTML = "";

  if (!rows.length) {
    refs.tablePartiesPro.innerHTML = '<tr><td colspan="5">Sem partidos com o mínimo de votos definido.</td></tr>';
    refs.tablePartiesAnti.innerHTML = '<tr><td colspan="5">Sem partidos com o mínimo de votos definido.</td></tr>';
    setPartyContextNote(govId, rows);
    return;
  }

  const topPro = rows.slice(0, 12);
  const topAnti = rows.slice(-12).reverse();

  for (const row of topPro) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${partyCellHtml(row.party)}</td>
      <td><span class="chip chip-pro">${formatPct(row.alignment_pct)}</span></td>
      <td>${formatInt(row.pro_votes)}</td>
      <td>${formatInt(row.anti_votes)}</td>
      <td>${formatInt(row.total_votes)}</td>
    `;
    refs.tablePartiesPro.appendChild(tr);
  }

  for (const row of topAnti) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${partyCellHtml(row.party)}</td>
      <td><span class="chip chip-anti">${formatPct(row.alignment_pct)}</span></td>
      <td>${formatInt(row.pro_votes)}</td>
      <td>${formatInt(row.anti_votes)}</td>
      <td>${formatInt(row.total_votes)}</td>
    `;
    refs.tablePartiesAnti.appendChild(tr);
  }

  drawPartyChart(rows);
  setPartyContextNote(govId, rows);
}

function drawPartyChart(rows) {
  const ctx = document.getElementById("chart-parties");
  if (charts.parties) charts.parties.destroy();

  const top = rows.slice(0, 10);

  charts.parties = new Chart(ctx, {
    type: "bar",
    data: {
      labels: top.map((row) => row.party),
      datasets: [
        {
          label: "% de alinhamento",
          data: top.map((row) => safeNumber(row.alignment_pct)),
          backgroundColor: "rgba(15, 106, 166, 0.82)",
          borderColor: "#0f6aa6",
          borderWidth: 1,
        },
      ],
    },
    options: {
      indexAxis: "y",
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: {
          min: 0,
          max: 100,
          ticks: { callback: (value) => `${value}%` },
          grid: { color: "rgba(0, 0, 0, 0.08)" },
        },
        y: {
          grid: { display: false },
        },
      },
      plugins: { legend: { display: false } },
    },
  });
}

function buildDeputyRows(govId, minVotes) {
  const rows = [];

  for (const item of payload.deputy_ranking || []) {
    const stats = pickGovStats(item, govId);
    if (stats.total_votes < minVotes) continue;
    rows.push({
      deputy_id: item.deputy_id,
      name: item.name,
      party: normalizePartyName(item.party),
      uf: normalizeUf(item.uf),
      ...stats,
    });
  }

  rows.sort((a, b) => b.total_votes - a.total_votes || b.alignment_pct - a.alignment_pct);
  return rows;
}

function setDeputyTable(govId, minVotes) {
  const rows = buildDeputyRows(govId, minVotes);
  const top = rows.slice(0, 40);
  const totalDeputies = (payload.deputy_ranking || []).length;
  const excluded = Math.max(0, totalDeputies - rows.length);

  refs.deputyNote.textContent = `Mostrando os 40 deputados com mais votos no recorte (mínimo de ${formatInt(minVotes)} votos por deputado; universo filtrado: ${rows.length}; ${excluded} excluídos pelo mínimo).`;
  refs.tableDeputies.innerHTML = "";

  if (!top.length) {
    refs.tableDeputies.innerHTML = '<tr><td colspan="7">Sem deputados com o mínimo de votos definido.</td></tr>';
    return;
  }

  for (const row of top) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${esc(row.name || "--")}</td>
      <td>${partyCellHtml(row.party)}</td>
      <td>${esc(row.uf || "--")}</td>
      <td><span class="chip ${row.alignment_pct >= 50 ? "chip-pro" : "chip-anti"}">${formatPct(row.alignment_pct)}</span></td>
      <td>${formatInt(row.pro_votes)}</td>
      <td>${formatInt(row.anti_votes)}</td>
      <td>${formatInt(row.total_votes)}</td>
    `;
    refs.tableDeputies.appendChild(tr);
  }
}

function getRecentRows(govId) {
  let rows = (payload.recent_votes || []).slice();
  if (govId !== "all") {
    rows = rows.filter((row) => row.government_id === govId);
  }
  return rows;
}

function getVoteLink(row) {
  const direct = (row.vote_uri || "").toString().trim();
  if (direct) return direct;
  const id = (row.id || "").toString().trim();
  if (!id) return "";
  return `https://dadosabertos.camara.leg.br/api/v2/votacoes/${encodeURIComponent(id)}`;
}

function setRecentVotes(govId) {
  refs.tableRecent.innerHTML = "";
  const rows = getRecentRows(govId).slice(0, 30);

  if (!rows.length) {
    refs.tableRecent.innerHTML = '<tr><td colspan="7">Sem votações recentes no recorte selecionado.</td></tr>';
    return;
  }

  for (const row of rows) {
    const dtObj = parseDate(row.date);
    const summary = (row.description || "").slice(0, 180);
    const link = getVoteLink(row);
    const summarySuffix = (row.description || "").length > summary.length ? "..." : "";
    const safeSummary = `${summary}${summarySuffix}`.trim() || "Abrir votação";
    const summaryHtml = link
      ? `<a class="vote-link" href="${esc(link)}" target="_blank" rel="noopener noreferrer" title="${esc(row.description || "")}">${esc(safeSummary)}</a>`
      : `<span title="${esc(row.description || "")}">${esc(safeSummary)}</span>`;
    const defeatChip = safeNumber(row.alignment_pct) < 50
      ? '<span class="chip chip-anti">governo derrotado</span> '
      : "";

    const tr = document.createElement("tr");
    if (safeNumber(row.alignment_pct) < 50) {
      tr.classList.add("row-defeat");
    }
    tr.innerHTML = `
      <td>${dtObj ? dateFmt.format(dtObj) : esc(row.date || "--")}</td>
      <td><span class="chip">${esc(row.government_label || "--")}</span></td>
      <td>${esc(row.gov_orientation || "--")}</td>
      <td><span class="chip ${row.alignment_pct >= 50 ? "chip-pro" : "chip-anti"}">${formatPct(row.alignment_pct)}</span></td>
      <td>${formatInt(row.pro_votes)}</td>
      <td>${formatInt(row.anti_votes)}</td>
      <td>${defeatChip}${summaryHtml}</td>
    `;
    refs.tableRecent.appendChild(tr);
  }
}

function setFilterHelp(govId, minPartyVotes, minDeputyVotes) {
  if (!refs.filterHelp) return;
  const partyRows = buildPartyRows(govId, minPartyVotes);
  const deputyRows = buildDeputyRows(govId, minDeputyVotes);
  const recentRows = getRecentRows(govId);
  refs.filterHelp.textContent = `Recorte atual pronto para exportação: ${partyRows.length} partidos, ${deputyRows.length} deputados e ${recentRows.length} votações recentes. O CSV baixa essas três tabelas com os filtros aplicados.`;
}

function csvEscape(value) {
  const raw = (value ?? "").toString();
  if (raw.includes('"') || raw.includes("\n") || raw.includes("\r") || raw.includes(";")) {
    return `"${raw.replace(/"/g, '""')}"`;
  }
  return raw;
}

function downloadCsv(filename, headers, rows) {
  const lines = [headers.join(";")];
  for (const row of rows) {
    lines.push(headers.map((key) => csvEscape(row[key])).join(";"));
  }
  const blob = new Blob([`\uFEFF${lines.join("\n")}`], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function exportCurrentCsv() {
  if (!payload) return;
  const govId = selectedGovernment();
  const minPartyVotes = Math.max(1, safeNumber(refs.minParty.value));
  const minDeputyVotes = Math.max(1, safeNumber(refs.minDeputy.value));

  const exportRows = [];
  for (const row of buildPartyRows(govId, minPartyVotes)) {
    exportRows.push({
      tipo: "partido",
      governo: govId,
      data: "",
      nome: row.party,
      partido: row.party,
      uf: "",
      orientacao: "",
      alinhamento_pct: safeNumber(row.alignment_pct).toFixed(2),
      votos_pro: row.pro_votes,
      votos_contra: row.anti_votes,
      votos_total: row.total_votes,
      resumo: "",
      link_original: "",
    });
  }

  for (const row of buildDeputyRows(govId, minDeputyVotes)) {
    exportRows.push({
      tipo: "deputado",
      governo: govId,
      data: "",
      nome: row.name || "",
      partido: row.party,
      uf: row.uf,
      orientacao: "",
      alinhamento_pct: safeNumber(row.alignment_pct).toFixed(2),
      votos_pro: row.pro_votes,
      votos_contra: row.anti_votes,
      votos_total: row.total_votes,
      resumo: "",
      link_original: "",
    });
  }

  for (const row of getRecentRows(govId)) {
    exportRows.push({
      tipo: "votacao_recente",
      governo: row.government_label || govId,
      data: row.date || "",
      nome: row.id || "",
      partido: "",
      uf: "",
      orientacao: row.gov_orientation || "",
      alinhamento_pct: safeNumber(row.alignment_pct).toFixed(2),
      votos_pro: row.pro_votes,
      votos_contra: row.anti_votes,
      votos_total: row.total_votes,
      resumo: row.description || "",
      link_original: getVoteLink(row),
    });
  }

  if (!exportRows.length) {
    refs.kpiNote.textContent = "Sem dados no recorte atual para exportar CSV.";
    return;
  }

  const headers = [
    "tipo",
    "governo",
    "data",
    "nome",
    "partido",
    "uf",
    "orientacao",
    "alinhamento_pct",
    "votos_pro",
    "votos_contra",
    "votos_total",
    "resumo",
    "link_original",
  ];
  const stamp = new Date().toISOString().slice(0, 10);
  const govLabel = govId === "all" ? "todos_governos" : govId;
  downloadCsv(`basometro_recorte_${govLabel}_${stamp}.csv`, headers, exportRows);
}

function refresh() {
  const govId = selectedGovernment();
  const minPartyVotes = Math.max(1, safeNumber(refs.minParty.value));
  const minDeputyVotes = Math.max(1, safeNumber(refs.minDeputy.value));

  const summary = buildSummary(govId);
  const monthly = filterMonthly(payload.monthly_series || [], govId);

  setKpis(summary, monthly);
  drawMonthlyRate(monthly);
  drawMonthlyVolume(monthly);
  setPartyTables(govId, minPartyVotes);
  setDeputyTable(govId, minDeputyVotes);
  setRecentVotes(govId);
  setFilterHelp(govId, minPartyVotes, minDeputyVotes);
}

async function bootstrap() {
  try {
    const response = await fetch(DATA_URL, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`Falha ao carregar dados (${response.status})`);
    }

    payload = await response.json();

    setStatus();
    setMethodology();
    setGovFilterOptions();
    setGovernmentTable();
    drawGovernmentChart();

    refs.kpiNote.textContent = `Série carregada: ${latestMonthRange(payload.monthly_series || [])}.`;

    refs.govFilter.addEventListener("change", refresh);
    refs.minParty.addEventListener("change", refresh);
    refs.minDeputy.addEventListener("change", refresh);
    if (refs.exportCurrent) {
      refs.exportCurrent.addEventListener("click", exportCurrentCsv);
    }

    refresh();
  } catch (error) {
    console.error(error);
    refs.kpiNote.textContent = `Erro ao carregar dados: ${error.message}`;
    refs.tableGovernments.innerHTML = '<tr><td colspan="6">Erro ao carregar dados.</td></tr>';
    refs.tablePartiesPro.innerHTML = '<tr><td colspan="5">Erro ao carregar dados.</td></tr>';
    refs.tablePartiesAnti.innerHTML = '<tr><td colspan="5">Erro ao carregar dados.</td></tr>';
    refs.tableDeputies.innerHTML = '<tr><td colspan="7">Erro ao carregar dados.</td></tr>';
    refs.tableRecent.innerHTML = '<tr><td colspan="7">Erro ao carregar dados.</td></tr>';
  }
}

bootstrap();
