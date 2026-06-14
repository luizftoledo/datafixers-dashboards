(() => {
  const DATA_URL = "./data/sancoes.json";
  const MAX_RESULTS = 60;
  const charts = {};
  let payload = null;
  let registros = [];

  const refs = {
    snapshotDate: document.getElementById("snapshot-date"),
    updatedAt: document.getElementById("updated-at"),
    summaryTitle: document.getElementById("summary-title"),
    activeCount: document.getElementById("active-count"),
    summaryNote: document.getElementById("summary-note"),
    metricGrid: document.getElementById("metric-grid"),
    form: document.getElementById("search-form"),
    input: document.getElementById("search-input"),
    status: document.getElementById("search-status"),
    results: document.getElementById("results"),
    cutNote: document.getElementById("cut-note"),
  };

  if (window.Chart) {
    Chart.defaults.font.family = "Inter, system-ui, -apple-system, sans-serif";
    Chart.defaults.color = "#6b6b66";
  }

  function fmtInt(value) {
    return Number(value || 0).toLocaleString("pt-BR");
  }

  function fmtDate(value) {
    if (!value) return "não informada";
    const match = String(value).match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!match) return value;
    return `${match[3]}/${match[2]}/${match[1]}`;
  }

  function fmtDateTime(value) {
    if (!value) return "não informado";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString("pt-BR", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function normalizeSearch(value) {
    return String(value || "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .replace(/\s+/g, " ")
      .trim();
  }

  function digits(value) {
    return String(value || "").replace(/\D+/g, "");
  }

  function escapeHtml(value) {
    return String(value || "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#039;",
    }[char]));
  }

  function destroyChart(name) {
    if (charts[name]) charts[name].destroy();
    delete charts[name];
  }

  function chart(name, canvasId, config) {
    if (!window.Chart) return;
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    destroyChart(name);
    charts[name] = new Chart(canvas, config);
  }

  function barOptions({ horizontal = false } = {}) {
    return {
      indexAxis: horizontal ? "y" : "x",
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (context) => fmtInt(context.raw),
          },
        },
      },
      scales: {
        x: {
          beginAtZero: true,
          grid: { color: horizontal ? "#f0eee8" : "transparent" },
          border: { display: false },
          ticks: { callback: (value) => fmtInt(value) },
        },
        y: {
          beginAtZero: true,
          grid: { color: horizontal ? "transparent" : "#f0eee8" },
          border: { display: false },
          ticks: {
            autoSkip: false,
            callback(value) {
              const label = this.getLabelForValue(value);
              return label.length > 32 ? `${label.slice(0, 31)}...` : label;
            },
          },
        },
      },
    };
  }

  function topEntries(object, limit) {
    return Object.entries(object || {}).slice(0, limit);
  }

  function renderMetrics(ag) {
    const items = [
      ["Total", ag.total],
      ["Ativas", ag.total_ativas],
      ["Novas em 30 dias", ag.novas_30d],
      ["Novas em 90 dias", ag.novas_90d],
    ];
    refs.metricGrid.innerHTML = items.map(([label, value]) => (
      `<article class="metric"><span>${label}</span><strong>${fmtInt(value)}</strong></article>`
    )).join("");
  }

  function renderCharts(ag) {
    const split = topEntries(ag.split, 4);
    chart("split", "split-chart", {
      type: "bar",
      data: {
        labels: split.map(([label]) => label),
        datasets: [{ data: split.map(([, value]) => value), backgroundColor: ["#2b3a55", "#b45309"] }],
      },
      options: barOptions(),
    });

    const categories = topEntries(ag.categorias, 8).reverse();
    chart("categories", "category-chart", {
      type: "bar",
      data: {
        labels: categories.map(([label]) => label),
        datasets: [{ data: categories.map(([, value]) => value), backgroundColor: "#2f6f8f" }],
      },
      options: barOptions({ horizontal: true }),
    });

    const orgs = topEntries(ag.orgaos_top20, 20).reverse();
    chart("orgs", "org-chart", {
      type: "bar",
      data: {
        labels: orgs.map(([label]) => label),
        datasets: [{ data: orgs.map(([, value]) => value), backgroundColor: "#6f4e37" }],
      },
      options: barOptions({ horizontal: true }),
    });
  }

  function prepareRecords() {
    registros = (payload.registros || []).map((record) => {
      const text = normalizeSearch([record.nome, record.rs, record.nf, record.cat, record.org].filter(Boolean).join(" "));
      const docDigits = digits(record.doc);
      return { ...record, _search: text, _docDigits: docDigits };
    });
  }

  function resultScore(record, query, docQuery) {
    let score = record.at ? 100 : 0;
    if (docQuery && record._docDigits === docQuery) score += 60;
    else if (docQuery && record._docDigits.includes(docQuery)) score += 30;
    if (record._search.startsWith(query)) score += 20;
    else if (record._search.includes(query)) score += 10;
    return score;
  }

  function findMatches(rawQuery) {
    const query = normalizeSearch(rawQuery);
    const docQuery = digits(rawQuery);
    if (query.length < 3 && docQuery.length < 5) return [];
    const matches = [];
    for (const record of registros) {
      const byDoc = docQuery.length >= 5 && record._docDigits.includes(docQuery);
      const byText = query.length >= 3 && record._search.includes(query);
      if (byDoc || byText) {
        matches.push({ record, score: resultScore(record, query, docQuery) });
      }
    }
    return matches
      .sort((a, b) => b.score - a.score || String(a.record.nome || "").localeCompare(String(b.record.nome || ""), "pt-BR"))
      .slice(0, MAX_RESULTS)
      .map((item) => item.record);
  }

  function detail(label, value) {
    if (!value) return "";
    return `<div class="detail"><span>${label}</span><strong>${escapeHtml(value)}</strong></div>`;
  }

  function renderResult(record) {
    const activeClass = record.at ? "active" : "inactive";
    const activeLabel = record.at ? "ativa" : "não ativa";
    const dates = `${fmtDate(record.di)} a ${fmtDate(record.df)}`;
    return `
      <article class="result">
        <div class="result-head">
          <div>
            <h3>${escapeHtml(record.nome || record.rs || "Sancionado sem nome informado")}</h3>
            <p class="sub">${escapeHtml([record.cadastro, record.doc].filter(Boolean).join(" · "))}</p>
          </div>
          <span class="badge ${activeClass}">${activeLabel}</span>
        </div>
        <div class="details">
          ${detail("Categoria", record.cat)}
          ${detail("Vigência", dates)}
          ${detail("Órgão sancionador", record.org)}
          ${detail("Processo", record.proc)}
          ${detail("Publicação", fmtDate(record.dp))}
          ${detail("Fundamentação", record.lei)}
          ${detail("Razão social", record.rs)}
          ${detail("Nome fantasia", record.nf)}
          ${detail("Abrangência", record.abr)}
        </div>
      </article>
    `;
  }

  function runSearch() {
    const query = refs.input.value.trim();
    if (!query) {
      refs.status.textContent = "Digite um CNPJ, CPF ou nome para consultar.";
      refs.results.innerHTML = "";
      return;
    }
    const matches = findMatches(query);
    if (!matches.length) {
      refs.status.textContent = "Nenhum registro encontrado no CEIS/CNEP carregado.";
      refs.results.innerHTML = "";
      return;
    }
    refs.status.textContent = `${fmtInt(matches.length)} resultado(s) exibido(s). Registros ativos aparecem primeiro.`;
    refs.results.innerHTML = matches.map(renderResult).join("");
  }

  function render() {
    const ag = payload.agregados || {};
    refs.snapshotDate.textContent = payload.snapshot_date || "não informado";
    refs.updatedAt.textContent = fmtDateTime(payload.updated_at);
    refs.summaryTitle.textContent = `${fmtInt(ag.total)} registros no CEIS/CNEP`;
    refs.activeCount.textContent = fmtInt(ag.total_ativas);
    refs.summaryNote.textContent = `${fmtInt(ag.novas_30d)} novas sanções nos últimos 30 dias; ${fmtInt(ag.novas_90d)} nos últimos 90 dias.`;
    refs.cutNote.textContent = payload.recorte ? `Recorte aplicado no arquivo: ${payload.recorte}.` : "";
    renderMetrics(ag);
    renderCharts(ag);
  }

  function bind() {
    refs.form.addEventListener("submit", (event) => {
      event.preventDefault();
      runSearch();
    });
    refs.input.addEventListener("input", () => {
      if (refs.input.value.trim().length >= 3) runSearch();
      if (!refs.input.value.trim()) runSearch();
    });
  }

  async function init() {
    bind();
    try {
      const response = await fetch(DATA_URL);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      payload = await response.json();
      prepareRecords();
      render();
      refs.status.textContent = "Base carregada. Digite um documento ou nome para consultar.";
    } catch (error) {
      refs.summaryTitle.textContent = "Não foi possível carregar os dados";
      refs.activeCount.textContent = "!";
      refs.summaryNote.textContent = "Verifique se o arquivo sancoes/data/sancoes.json foi gerado.";
      refs.status.textContent = `Erro ao carregar dados: ${error.message}`;
    }
  }

  init();
})();
