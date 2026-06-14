(() => {
  const DATA_URL = "./data/cpgf_agg.json";
  const NBSP = "\u00a0";
  const monthNames = [
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
  ];
  const shortMonths = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
  const electionYears = new Set([2014, 2018, 2022, 2026]);
  const bandColors = ["rgba(43,58,85,0.055)", "rgba(180,83,9,0.06)", "rgba(47,111,143,0.055)", "rgba(31,111,72,0.055)", "rgba(139,26,26,0.05)"];

  let payload = null;
  let state = { mode: "media_gestao", real: true };
  const charts = {};

  const refs = {
    updatedAt: document.getElementById("updated-at"),
    valueModeLabel: document.getElementById("value-mode-label"),
    summaryTitle: document.getElementById("summary-title"),
    summaryNumber: document.getElementById("summary-number"),
    summaryNote: document.getElementById("summary-note"),
    comparisonSubtitle: document.getElementById("comparison-subtitle"),
    comparisonNote: document.getElementById("comparison-note"),
    ipcaPill: document.getElementById("ipca-pill"),
    realToggle: document.getElementById("real-toggle"),
    splitSubtitle: document.getElementById("split-subtitle"),
    splitStats: document.getElementById("split-stats"),
    unitSubtitle: document.getElementById("unit-subtitle"),
    electoralNote: document.getElementById("electoral-note"),
    federalNote: document.getElementById("federal-note"),
  };

  Chart.defaults.font.family = "Inter, system-ui, -apple-system, sans-serif";
  Chart.defaults.color = "#6b6b66";

  const managementBands = {
    id: "managementBands",
    beforeDraw(chart, args, options) {
      if (!options || !options.enabled || !payload) return;
      const x = chart.scales.x;
      const area = chart.chartArea;
      if (!x || !area) return;
      const labels = chart.data.labels || [];
      const ctx = chart.ctx;
      ctx.save();
      payload.gestoes.forEach((gestao, index) => {
        const start = Math.max(0, labels.findIndex((label) => label >= gestao.inicio));
        if (start < 0) return;
        const fim = gestao.fim || payload.latest_month;
        const end = lastIndex(labels, (label) => label <= fim);
        if (end < start) return;
        const left = start === 0 ? area.left : midpoint(x.getPixelForValue(start - 1), x.getPixelForValue(start));
        const right = end === labels.length - 1 ? area.right : midpoint(x.getPixelForValue(end), x.getPixelForValue(end + 1));
        ctx.fillStyle = bandColors[index % bandColors.length];
        ctx.fillRect(left, area.top, right - left, area.bottom - area.top);
      });
      ctx.restore();
    },
  };

  const campaignBands = {
    id: "campaignBands",
    beforeDraw(chart, args, options) {
      if (!options || !options.enabled) return;
      const x = chart.scales.x;
      const area = chart.chartArea;
      if (!x || !area) return;
      const labels = chart.data.labels || [];
      const ctx = chart.ctx;
      ctx.save();
      labels.forEach((label, index) => {
        if (!isCampaignMonth(label)) return;
        const left = index === 0 ? area.left : midpoint(x.getPixelForValue(index - 1), x.getPixelForValue(index));
        const right = index === labels.length - 1 ? area.right : midpoint(x.getPixelForValue(index), x.getPixelForValue(index + 1));
        ctx.fillStyle = "rgba(180,83,9,0.12)";
        ctx.fillRect(left, area.top, right - left, area.bottom - area.top);
      });
      ctx.restore();
    },
  };
  Chart.register(managementBands, campaignBands);

  function lastIndex(items, predicate) {
    for (let i = items.length - 1; i >= 0; i -= 1) {
      if (predicate(items[i], i)) return i;
    }
    return -1;
  }

  function midpoint(a, b) {
    return a + ((b - a) / 2);
  }

  function fmtBRL(value, compact = true) {
    const abs = Math.abs(value || 0);
    if (compact && abs >= 1e9) return `R$${NBSP}${(value / 1e9).toLocaleString("pt-BR", { maximumFractionDigits: 2 })}${NBSP}bi`;
    if (compact && abs >= 1e6) return `R$${NBSP}${(value / 1e6).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}${NBSP}mi`;
    return `R$${NBSP}${(value || 0).toLocaleString("pt-BR", { maximumFractionDigits: 0 })}`;
  }

  function fmtPct(value, digits = 1) {
    return `${(value || 0).toLocaleString("pt-BR", { maximumFractionDigits: digits })}%`;
  }

  function avg(items) {
    return items.length ? items.reduce((a, b) => a + b, 0) / items.length : 0;
  }

  function monthIndex(month) {
    return Number(month.slice(5, 7));
  }

  function yearOf(month) {
    return Number(month.slice(0, 4));
  }

  function monthLabel(month) {
    return `${shortMonths[monthIndex(month) - 1]}/${month.slice(2, 4)}`;
  }

  function longPeriodLabel(monthCount, year) {
    const first = monthNames[0];
    const last = monthNames[monthCount - 1];
    if (monthCount === 1) return `${first} de ${year}`;
    return `${first} e ${last} de ${year}`;
  }

  function shortPeriodLabel(monthCount) {
    if (monthCount === 1) return "jan";
    return `jan-${shortMonths[monthCount - 1]}`;
  }

  function availableMonths(source = "presidencia") {
    const series = source === "federal" ? payload.months : payload.presidencia;
    return Object.keys(series || {}).sort();
  }

  function factor(month) {
    return state.real ? (payload.ipca_fator || {})[month] || 1 : 1;
  }

  function rowFor(month, source = "presidencia") {
    const series = source === "federal" ? payload.months : payload.presidencia;
    return (series || {})[month] || {};
  }

  function value(month, key = "total", source = "presidencia") {
    return (rowFor(month, source)[key] || 0) * factor(month);
  }

  function unitValue(month, unidade) {
    const row = rowFor(month);
    return ((row.unidades || {})[unidade] || 0) * factor(month);
  }

  function sumMonths(months, key = "total", source = "presidencia") {
    return months.reduce((total, month) => total + value(month, key, source), 0);
  }

  function monthsForYearWindow(year, monthCount) {
    const months = [];
    for (let i = 1; i <= monthCount; i += 1) {
      months.push(`${year}-${String(i).padStart(2, "0")}`);
    }
    return months;
  }

  function completeYearsForWindow(monthCount, endYear, source = "presidencia") {
    const years = [];
    for (let year = Number(payload.first_month.slice(0, 4)); year <= endYear; year += 1) {
      const months = monthsForYearWindow(year, monthCount);
      if (months.every((month) => rowFor(month, source).n)) years.push(year);
    }
    return years;
  }

  function currentWindow() {
    const latestYear = yearOf(payload.latest_month);
    const count = monthIndex(payload.latest_month);
    const currentMonths = monthsForYearWindow(latestYear, count).filter((month) => rowFor(month).n);
    const previousYears = completeYearsForWindow(currentMonths.length, latestYear - 1);
    return { latestYear, count: currentMonths.length, currentMonths, previousYears };
  }

  function isCampaignMonth(month) {
    const monthNum = monthIndex(month);
    return electionYears.has(yearOf(month)) && monthNum >= 7 && monthNum <= 10;
  }

  function destroyChart(name) {
    if (charts[name]) charts[name].destroy();
    delete charts[name];
  }

  function createChart(name, canvasId, config) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const container = canvas.parentElement;
    if (!container || container.clientHeight === 0) {
      requestAnimationFrame(() => createChart(name, canvasId, config));
      return;
    }
    destroyChart(name);
    charts[name] = new Chart(canvas, config);
  }

  function commonMoneyAxis() {
    return {
      beginAtZero: true,
      ticks: {
        callback: (v) => {
          if (Math.abs(v) >= 1e9) return `${(v / 1e9).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} bi`;
          if (Math.abs(v) >= 1e6) return `${(v / 1e6).toLocaleString("pt-BR", { maximumFractionDigits: 0 })} mi`;
          return v.toLocaleString("pt-BR");
        },
      },
      grid: { color: "#f0eee8" },
      border: { display: false },
    };
  }

  function tooltipMoney(label) {
    return (item) => `${label || item.dataset.label}: ${fmtBRL(item.raw, false)}`;
  }

  function renderHeader() {
    const updated = new Date(payload.updated_at);
    refs.updatedAt.textContent = Number.isNaN(updated.getTime())
      ? payload.updated_at
      : updated.toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
    const latestLabel = `${monthNames[monthIndex(payload.latest_month) - 1]} de ${yearOf(payload.latest_month)}`;
    refs.valueModeLabel.textContent = state.real ? `corrigidos para ${latestLabel}` : "nominais";
    refs.ipcaPill.textContent = state.real ? `valores de ${latestLabel}` : "valores nominais";
  }

  function renderSummary() {
    const win = currentWindow();
    const currentTotal = sumMonths(win.currentMonths);
    const previousTotals = win.previousYears.map((year) => sumMonths(monthsForYearWindow(year, win.count)));
    const average = avg(previousTotals);
    const diffPct = average ? ((currentTotal - average) / average) * 100 : 0;
    const direction = diffPct >= 0 ? "A MAIS" : "A MENOS";
    const periodText = longPeriodLabel(win.count, win.latestYear);
    const shortPeriod = shortPeriodLabel(win.count);
    const yearRange = win.previousYears.length ? `${win.previousYears[0]}-${win.previousYears[win.previousYears.length - 1]}` : "anos anteriores";

    refs.summaryTitle.innerHTML = `Entre ${periodText}, a Presidência da República gastou <strong>${fmtBRL(currentTotal)}</strong> no cartão corporativo — <strong>${fmtPct(Math.abs(diffPct))} ${direction}</strong> que a média do mesmo período (${shortPeriod}) de ${yearRange}.`;
    refs.summaryNumber.textContent = `${diffPct >= 0 ? "▲" : "▼"} ${fmtPct(Math.abs(diffPct), 1)}`;
    refs.summaryNumber.classList.toggle("more", diffPct >= 0);
    refs.summaryNumber.classList.toggle("less", diffPct < 0);
    refs.summaryNote.textContent = `Comparação com ${previousTotals.length} ano(s) anterior(es) com todos os meses de ${shortPeriod} disponíveis na série da Presidência.`;
  }

  function monthsInGestao(gestao) {
    const end = gestao.fim || payload.latest_month;
    return availableMonths().filter((month) => month >= gestao.inicio && month <= end);
  }

  function renderComparison() {
    if (state.mode === "media_gestao") renderManagementAverage();
    if (state.mode === "mandato_n") renderFirstMandateMonths();
    if (state.mode === "janela_calendario") renderCalendarWindow();
  }

  function renderManagementAverage() {
    const rows = payload.gestoes.map((gestao) => {
      const months = monthsInGestao(gestao);
      const total = sumMonths(months);
      return {
        gestao,
        label: `${gestao.nome}${gestao.parcial || gestao.em_curso ? "*" : ""}`,
        value: months.length ? total / months.length : 0,
        months: months.length,
      };
    });
    refs.comparisonSubtitle.textContent = "Média mensal da Presidência: total da gestão dividido pelo número de meses com dados.";
    refs.comparisonNote.textContent = "* Gestão parcial ou em curso. Dilma 1 começa em 2013 nesta base.";
    createChart("comparison", "comparison-chart", {
      type: "bar",
      data: {
        labels: rows.map((row) => row.label),
        datasets: [{
          label: "R$/mês",
          data: rows.map((row) => row.value),
          backgroundColor: rows.map((row) => row.gestao.em_curso ? "rgba(180,83,9,0.82)" : "rgba(43,58,85,0.82)"),
          borderColor: rows.map((row) => row.gestao.em_curso ? "#b45309" : "#2b3a55"),
          borderWidth: 1,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (item) => `${fmtBRL(item.raw, false)} por mês`,
              afterLabel: (item) => `${rows[item.dataIndex].months} meses com dados`,
            },
          },
        },
        scales: { x: { grid: { display: false } }, y: commonMoneyAxis() },
      },
    });
  }

  function renderFirstMandateMonths() {
    const series = payload.gestoes.map((gestao) => {
      const months = monthsInGestao(gestao).sort();
      return { gestao, months };
    }).filter((row) => row.months.length);
    const n = Math.min(...series.map((row) => row.months.length));
    const labels = Array.from({ length: n }, (_, i) => `mês ${i + 1}`);
    refs.comparisonSubtitle.textContent = `Comparação acumulada dos primeiros ${n} meses com dados de cada mandato, na série da Presidência.`;
    refs.comparisonNote.textContent = "N é calculado dinamicamente pelo menor mandato com dados na base.";
    createChart("comparison", "comparison-chart", {
      type: "line",
      data: {
        labels,
        datasets: series.map((row, index) => {
          let acc = 0;
          return {
            label: `${row.gestao.nome}${row.gestao.parcial || row.gestao.em_curso ? "*" : ""}`,
            data: row.months.slice(0, n).map((month) => {
              acc += value(month);
              return acc;
            }),
            borderColor: ["#2b3a55", "#2f6f8f", "#b45309", "#1f6f48", "#8b1a1a"][index % 5],
            backgroundColor: "transparent",
            borderWidth: 2,
            pointRadius: 1.5,
            tension: 0.18,
          };
        }),
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: { tooltip: { callbacks: { label: tooltipMoney() } } },
        scales: { x: { grid: { display: false } }, y: commonMoneyAxis() },
      },
    });
  }

  function renderCalendarWindow() {
    const win = currentWindow();
    const years = completeYearsForWindow(win.count, win.latestYear);
    refs.comparisonSubtitle.textContent = `Gasto da Presidência no intervalo ${shortPeriodLabel(win.count)} de cada ano.`;
    refs.comparisonNote.textContent = "O intervalo de meses é sempre o mesmo do último período fechado do ano corrente.";
    createChart("comparison", "comparison-chart", {
      type: "bar",
      data: {
        labels: years.map(String),
        datasets: [{
          label: `Total ${shortPeriodLabel(win.count)}`,
          data: years.map((year) => sumMonths(monthsForYearWindow(year, win.count))),
          backgroundColor: years.map((year) => year === win.latestYear ? "rgba(180,83,9,0.82)" : "rgba(43,58,85,0.78)"),
          borderColor: years.map((year) => year === win.latestYear ? "#b45309" : "#2b3a55"),
          borderWidth: 1,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: tooltipMoney("Total") } } },
        scales: { x: { grid: { display: false } }, y: commonMoneyAxis() },
      },
    });
  }

  function renderSplit() {
    const win = currentWindow();
    const compra = sumMonths(win.currentMonths, "compra");
    const saque = sumMonths(win.currentMonths, "saque");
    const total = compra + saque;
    refs.splitSubtitle.textContent = `Participação em ${shortPeriodLabel(win.count)}/${win.latestYear}, na Presidência.`;
    refs.splitStats.innerHTML = `
      <div class="mini-stat"><span>Compra</span><strong>${fmtPct(total ? (compra / total) * 100 : 0)} · ${fmtBRL(compra)}</strong></div>
      <div class="mini-stat"><span>Saque</span><strong>${fmtPct(total ? (saque / total) * 100 : 0)} · ${fmtBRL(saque)}</strong></div>
    `;
    createChart("split", "split-chart", {
      type: "doughnut",
      data: {
        labels: ["Compra", "Saque"],
        datasets: [{ data: [compra, saque], backgroundColor: ["#2f6f8f", "#b45309"], borderWidth: 0 }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "62%",
        plugins: { legend: { position: "bottom" }, tooltip: { callbacks: { label: tooltipMoney() } } },
      },
    });
  }

  function renderTypeEvolution() {
    const labels = availableMonths();
    createChart("type", "type-chart", {
      type: "bar",
      data: {
        labels,
        datasets: [
          { label: "Compra", data: labels.map((month) => value(month, "compra")), backgroundColor: "rgba(47,111,143,0.82)", stack: "tipo" },
          { label: "Saque", data: labels.map((month) => value(month, "saque")), backgroundColor: "rgba(180,83,9,0.82)", stack: "tipo" },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          tooltip: { callbacks: { title: (items) => monthLabel(items[0].label), label: tooltipMoney() } },
        },
        scales: {
          x: { stacked: true, ticks: { callback: function(v) { return monthLabel(this.getLabelForValue(v)); }, maxTicksLimit: 8 }, grid: { display: false } },
          y: { ...commonMoneyAxis(), stacked: true },
        },
      },
    });
  }

  function renderUnitChart() {
    const win = currentWindow();
    const recent = new Map();
    win.currentMonths.forEach((month) => {
      Object.entries(rowFor(month).unidades || {}).forEach(([unidade]) => {
        recent.set(unidade, (recent.get(unidade) || 0) + unitValue(month, unidade));
      });
    });
    const top = [...recent.entries()].sort((a, b) => b[1] - a[1]).slice(0, 10);
    const historical = new Map();
    win.previousYears.forEach((year) => {
      const months = monthsForYearWindow(year, win.count);
      top.forEach(([unidade]) => {
        const total = months.reduce((acc, month) => acc + unitValue(month, unidade), 0);
        historical.set(unidade, (historical.get(unidade) || 0) + total);
      });
    });
    const histLabel = win.previousYears.length
      ? `média ${win.previousYears[0]}-${win.previousYears[win.previousYears.length - 1]}`
      : "média histórica";
    refs.unitSubtitle.textContent = `Maiores unidades no período ${shortPeriodLabel(win.count)}/${win.latestYear}, comparadas à média do mesmo intervalo nos anos anteriores.`;
    createChart("unit", "unit-chart", {
      type: "bar",
      data: {
        labels: top.map(([unidade]) => unidade),
        datasets: [
          { label: `${shortPeriodLabel(win.count)}/${win.latestYear}`, data: top.map(([, total]) => total), backgroundColor: "rgba(43,58,85,0.82)" },
          { label: histLabel, data: top.map(([unidade]) => (historical.get(unidade) || 0) / Math.max(1, win.previousYears.length)), backgroundColor: "rgba(180,83,9,0.7)" },
        ],
      },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        plugins: { tooltip: { callbacks: { label: tooltipMoney() } } },
        scales: {
          x: commonMoneyAxis(),
          y: { grid: { display: false }, ticks: { autoSkip: false, font: { size: 11 } } },
        },
      },
    });
  }

  function renderElectoral() {
    const labels = availableMonths();
    const campaignMonths = labels.filter(isCampaignMonth);
    const otherMonths = labels.filter((month) => !isCampaignMonth(month));
    const campaignAvg = avg(campaignMonths.map((month) => value(month)));
    const otherAvg = avg(otherMonths.map((month) => value(month)));
    const diff = otherAvg ? ((campaignAvg - otherAvg) / otherAvg) * 100 : 0;
    const years = [...new Set(campaignMonths.map(yearOf))].join(", ");
    refs.electoralNote.textContent = `Nos meses de campanha presidencial disponíveis (julho a outubro de ${years || "anos eleitorais"}), a média mensal foi ${fmtBRL(campaignAvg)}, contra ${fmtBRL(otherAvg)} nos demais meses da série. Diferença: ${fmtPct(Math.abs(diff))} ${diff >= 0 ? "a mais" : "a menos"}. A comparação descreve o padrão observado, sem inferir causa.`;
    createChart("electoral", "electoral-chart", {
      type: "line",
      data: {
        labels,
        datasets: [{
          label: "Total mensal da Presidência",
          data: labels.map((month) => value(month)),
          borderColor: "#2b3a55",
          backgroundColor: "rgba(43,58,85,0.1)",
          fill: true,
          tension: 0.18,
          borderWidth: 1.8,
          pointRadius: labels.map((month) => isCampaignMonth(month) ? 2.2 : 0),
          pointBackgroundColor: labels.map((month) => isCampaignMonth(month) ? "#b45309" : "#2b3a55"),
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          campaignBands: { enabled: true },
          tooltip: { callbacks: { title: (items) => monthLabel(items[0].label), label: tooltipMoney("Total") } },
        },
        scales: {
          x: { ticks: { callback: function(v) { return monthLabel(this.getLabelForValue(v)); }, maxTicksLimit: 12 }, grid: { display: false } },
          y: commonMoneyAxis(),
        },
      },
    });
  }

  function renderMonthlySeries() {
    const labels = availableMonths();
    createChart("monthly", "monthly-chart", {
      type: "line",
      data: {
        labels,
        datasets: [{
          label: "Total mensal",
          data: labels.map((month) => value(month)),
          borderColor: "#2b3a55",
          backgroundColor: "rgba(43,58,85,0.12)",
          fill: true,
          tension: 0.18,
          borderWidth: 1.8,
          pointRadius: 0,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          managementBands: { enabled: true },
          tooltip: { callbacks: { title: (items) => monthLabel(items[0].label), label: tooltipMoney("Total") } },
        },
        scales: {
          x: { ticks: { callback: function(v) { return monthLabel(this.getLabelForValue(v)); }, maxTicksLimit: 12 }, grid: { display: false } },
          y: commonMoneyAxis(),
        },
      },
    });
  }

  function renderFederalContext() {
    const labels = availableMonths("federal");
    const latestTotal = value(payload.latest_month, "total", "federal");
    const latestPresidencia = value(payload.latest_month);
    refs.federalNote.textContent = `No último mês fechado, a Presidência respondeu por ${fmtPct(latestTotal ? (latestPresidencia / latestTotal) * 100 : 0)} do gasto federal no CPGF. Esta seção mantém a série federal apenas como contexto.`;
    createChart("federal", "federal-chart", {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: "Governo federal",
            data: labels.map((month) => value(month, "total", "federal")),
            borderColor: "#6b6b66",
            backgroundColor: "transparent",
            borderWidth: 1.6,
            pointRadius: 0,
          },
          {
            label: "Presidência",
            data: labels.map((month) => value(month)),
            borderColor: "#b45309",
            backgroundColor: "transparent",
            borderWidth: 1.9,
            pointRadius: 0,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          tooltip: { callbacks: { title: (items) => monthLabel(items[0].label), label: tooltipMoney() } },
        },
        scales: {
          x: { ticks: { callback: function(v) { return monthLabel(this.getLabelForValue(v)); }, maxTicksLimit: 12 }, grid: { display: false } },
          y: commonMoneyAxis(),
        },
      },
    });
  }

  function renderAll() {
    renderHeader();
    renderSummary();
    renderComparison();
    renderUnitChart();
    renderElectoral();
    renderSplit();
    renderTypeEvolution();
    renderMonthlySeries();
    renderFederalContext();
  }

  function scheduleRenderAll() {
    requestAnimationFrame(renderAll);
  }

  function bindControls() {
    document.querySelectorAll(".tab").forEach((button) => {
      button.addEventListener("click", () => {
        state.mode = button.dataset.mode;
        document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("is-active", tab === button));
        requestAnimationFrame(renderComparison);
      });
    });
    refs.realToggle.addEventListener("change", () => {
      state.real = refs.realToggle.checked;
      scheduleRenderAll();
    });
  }

  async function bootstrap() {
    try {
      const response = await fetch(`${DATA_URL}?v=${Date.now()}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      payload = await response.json();
      bindControls();
      scheduleRenderAll();
    } catch (err) {
      console.error("[cartao] falha ao carregar dados:", err);
      refs.summaryTitle.textContent = "Falha ao carregar dados.";
      refs.summaryNote.textContent = err && err.message ? err.message : String(err);
    }
  }

  bootstrap();
})();
