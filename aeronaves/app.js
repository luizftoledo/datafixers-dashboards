(() => {
  const META_URL = "./data/metadata.json";
  const DATA_URL = "./data/records.jsonl.gz";
  const RESULTS_PER_PAGE = 20;

  const queryInput = document.getElementById("query");
  const searchForm = document.getElementById("search-form");
  const btnSearch = document.getElementById("btn-search");
  const btnClear = document.getElementById("btn-clear");
  const examplesEl = document.getElementById("examples");
  const searchFeedback = document.getElementById("search-feedback");
  const searchFeedbackCount = document.getElementById("search-feedback-count");
  const searchFeedbackText = document.getElementById("search-feedback-text");

  const updatedBadge = document.getElementById("updated-badge");
  const generatedBadge = document.getElementById("generated-badge");
  const metricTotal = document.getElementById("metric-total");
  const metricActive = document.getElementById("metric-active");
  const metricCancelled = document.getElementById("metric-cancelled");
  const metricOperators = document.getElementById("metric-operators");

  const statusLine = document.getElementById("status-line");
  const resultsSummary = document.getElementById("results-summary");
  const emptyState = document.getElementById("empty-state");
  const pagination = document.getElementById("pagination");
  const paginationSummary = document.getElementById("pagination-summary");
  const paginationPrev = document.getElementById("pagination-prev");
  const paginationNext = document.getElementById("pagination-next");
  const paginationPage = document.getElementById("pagination-page");
  const resultsList = document.getElementById("results-list");

  const loadStatusLabel = document.getElementById("load-status-label");
  const loadStatusSummary = document.getElementById("load-status-summary");
  const loadStatusFill = document.getElementById("load-status-fill");

  const methodologyList = document.getElementById("methodology-list");
  const limitsList = document.getElementById("limits-list");
  const metaSource = document.getElementById("meta-source");
  const metaUpdated = document.getElementById("meta-updated");
  const metaGenerated = document.getElementById("meta-generated");
  const metaUfs = document.getElementById("meta-ufs");
  const metaYears = document.getElementById("meta-years");
  const metaLimit = document.getElementById("meta-limit");

  const numberFmt = new Intl.NumberFormat("pt-BR");

  let metadata = null;
  let records = null;
  let loadPromise = null;
  let currentMatches = [];
  let currentQuery = "";
  let currentPage = 1;
  let requestedPage = 1;

  function formatResultLabel(total) {
    return `${numberFmt.format(total)} ${total === 1 ? "resultado" : "resultados"}`;
  }

  function getPageCount(totalMatches) {
    return Math.max(1, Math.ceil(totalMatches / RESULTS_PER_PAGE));
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

  function clean(value) {
    return (value || "").toString().trim();
  }

  function normalizeText(value) {
    return clean(value)
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .replace(/\s+/g, " ")
      .trim();
  }

  function compactText(value) {
    return normalizeText(value).replace(/[^a-z0-9]+/g, "");
  }

  function formatPrefix(value) {
    const raw = clean(value).toUpperCase();
    if (/^[A-Z]{2}[A-Z0-9]{3}$/.test(raw)) {
      return `${raw.slice(0, 2)}-${raw.slice(2)}`;
    }
    return raw || "-";
  }

  function formatDate(value) {
    const raw = clean(value);
    if (!raw) return "-";

    if (/^\d{4}-\d{2}-\d{2}/.test(raw)) {
      const iso = raw.slice(0, 10);
      const parsed = new Date(`${iso}T00:00:00`);
      if (!Number.isNaN(parsed.getTime())) {
        return parsed.toLocaleDateString("pt-BR");
      }
      return iso;
    }

    if (/^\d{2}\/\d{2}\/\d{4}$/.test(raw)) {
      return raw;
    }

    if (/^\d{8}$/.test(raw)) {
      return `${raw.slice(0, 2)}/${raw.slice(2, 4)}/${raw.slice(4)}`;
    }

    return raw;
  }

  function formatDateTime(value) {
    const raw = clean(value);
    if (!raw) return "-";
    const parsed = new Date(raw);
    if (Number.isNaN(parsed.getTime())) {
      return formatDate(raw);
    }
    return parsed.toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo" });
  }

  function formatShare(value) {
    const raw = clean(value);
    if (!raw) return "";
    const normalized = raw.replace(",", ".");
    const number = Number(normalized);
    if (!Number.isFinite(number)) return raw;
    return `${number.toLocaleString("pt-BR", { maximumFractionDigits: 2 })}%`;
  }

  function parseOwners(rawValue) {
    const raw = clean(rawValue);
    if (!raw) return [];
    return raw
      .split(";")
      .map((chunk) => chunk.trim())
      .filter(Boolean)
      .map((chunk) => {
        const parts = chunk.split("|").map((part) => part.trim());
        return {
          name: parts[0] || "",
          document: parts[1] || "",
          share: parts[2] || "",
        };
      })
      .filter((owner) => owner.name || owner.document || owner.share);
  }

  function ownerToText(owner) {
    const parts = [owner.name, owner.document, formatShare(owner.share)].filter(Boolean);
    return parts.join(" • ");
  }

  function buildFieldLines(lines, mono = false) {
    const cleaned = lines.map((line) => clean(line)).filter(Boolean);
    if (!cleaned.length) {
      return `<span class="detail-value${mono ? " mono" : ""}">-</span>`;
    }
    return `<span class="detail-value${mono ? " mono" : ""}">${cleaned.map((line) => esc(line)).join("<br>")}</span>`;
  }

  function buildOwnersHtml(owners, rawFallback) {
    if (owners.length) {
      return `
        <ul class="owners-list">
          ${owners.map((owner) => `<li>${esc(ownerToText(owner))}</li>`).join("")}
        </ul>
      `;
    }
    const fallback = clean(rawFallback);
    if (!fallback) return `<span class="detail-value">-</span>`;
    return `<span class="detail-value">${esc(fallback)}</span>`;
  }

  function detailCard(label, contentHtml) {
    return `
      <div class="detail">
        <span class="detail-label">${esc(label)}</span>
        ${contentHtml}
      </div>
    `;
  }

  function parseRecord(raw) {
    const owners = parseOwners(raw.pr);
    const ownersText = owners.map(ownerToText).join(" ");
    const operator = clean(raw.n);
    const ownersRaw = clean(raw.pr);
    const otherOperators = clean(raw.o);
    const document = clean(raw.d);
    const prefix = clean(raw.p);
    const model = clean(raw.m);
    const manufacturer = clean(raw.f);
    const icaoType = clean(raw.i);

    const searchText = normalizeText([
      prefix,
      operator,
      otherOperators,
      document,
      ownersRaw,
      ownersText,
      model,
      manufacturer,
      icaoType,
      clean(raw.u),
      clean(raw.ou),
      clean(raw.ca),
      clean(raw.to),
      clean(raw.cf),
    ].join(" "));

    const searchCompact = compactText([
      prefix,
      operator,
      otherOperators,
      document,
      ownersRaw,
      ownersText,
      model,
      manufacturer,
      icaoType,
    ].join(" "));

    return {
      prefix,
      prefixDisplay: formatPrefix(prefix),
      prefixCompact: compactText(prefix),
      uf: clean(raw.u).toUpperCase(),
      operator,
      otherOperators,
      operatorUf: clean(raw.ou).toUpperCase(),
      document,
      owners,
      ownersRaw,
      ownersNorm: normalizeText(`${ownersText} ${ownersRaw}`),
      documentsCompact: compactText(`${document} ${ownersRaw}`),
      model,
      manufacturer,
      icaoType,
      year: clean(raw.y),
      registrationDate: clean(raw.r),
      cancelDate: clean(raw.c),
      cancelReason: clean(raw.cr),
      operationalRule: clean(raw.cf),
      homologationCategory: clean(raw.ca),
      operationType: clean(raw.to),
      encumbrance: clean(raw.g),
      airworthinessType: clean(raw.tc),
      engineType: clean(raw.tm),
      engineCount: clean(raw.qm),
      passengers: clean(raw.ps),
      seats: clean(raw.as),
      interdictionCode: clean(raw.it),
      operatorNorm: normalizeText(operator),
      modelNorm: normalizeText(`${model} ${manufacturer} ${icaoType}`),
      searchText,
      searchCompact,
    };
  }

  function setLoadState(label, summary, percent) {
    if (loadStatusLabel) loadStatusLabel.textContent = label;
    if (loadStatusSummary) loadStatusSummary.textContent = summary;
    if (loadStatusFill) loadStatusFill.style.width = `${Math.max(0, Math.min(100, percent || 0))}%`;
  }

  async function fetchJson(url) {
    const resp = await fetch(url, { cache: "no-store" });
    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status}`);
    }
    return resp.json();
  }

  async function fetchGzipText(url) {
    const resp = await fetch(url, { cache: "no-store" });
    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status}`);
    }
    const gzBuffer = await resp.arrayBuffer();

    if ("DecompressionStream" in window) {
      const stream = new Blob([gzBuffer]).stream().pipeThrough(new DecompressionStream("gzip"));
      return new Response(stream).text();
    }

    if (window.pako && typeof window.pako.ungzip === "function") {
      return window.pako.ungzip(new Uint8Array(gzBuffer), { to: "string" });
    }

    throw new Error("Navegador sem suporte para descompactar gzip.");
  }

  function renderTextWithLinks(text) {
    const source = clean(text);
    if (!source) return "";
    return source
      .split(/(https?:\/\/\S+)/g)
      .filter(Boolean)
      .map((part) => {
        if (/^https?:\/\//.test(part)) {
          const safeUrl = esc(part);
          return `<a href="${safeUrl}" target="_blank" rel="noopener noreferrer">${safeUrl}</a>`;
        }
        return esc(part);
      })
      .join("");
  }

  function renderList(target, items) {
    target.innerHTML = (items || []).map((item) => `<li>${renderTextWithLinks(item)}</li>`).join("");
  }

  function renderExamples(examples) {
    const chips = (examples || [])
      .map((example) => `<button class="example-chip" type="button" data-example="${esc(example)}">${esc(example)}</button>`)
      .join("");
    examplesEl.innerHTML = `<span>Exemplos:</span>${chips}`;
    examplesEl.querySelectorAll("[data-example]").forEach((button) => {
      button.addEventListener("click", async () => {
        queryInput.value = button.dataset.example || "";
        await handleSearch();
      });
    });
  }

  function setSearchFeedback(countLabel, text, tone = "ready") {
    if (searchFeedback) searchFeedback.dataset.tone = tone;
    if (searchFeedbackCount) searchFeedbackCount.textContent = countLabel;
    if (searchFeedbackText) searchFeedbackText.textContent = text;
  }

  function renderMetadata() {
    if (!metadata) return;

    metricTotal.textContent = numberFmt.format(metadata.rows || 0);
    metricActive.textContent = numberFmt.format(metadata.active_rows || 0);
    metricCancelled.textContent = numberFmt.format(metadata.canceled_rows || 0);
    metricOperators.textContent = numberFmt.format(metadata.unique_operators || 0);

    updatedBadge.textContent = `Atualizacao da base: ${formatDate(metadata.source_updated_at)}`;
    generatedBadge.textContent = `Publicada em ${formatDateTime(metadata.generated_at)}`;

    renderExamples(metadata.sample_queries || []);
    renderList(methodologyList, metadata.methodology || []);
    renderList(limitsList, metadata.limits || []);

    metaSource.textContent = `${metadata.source_file || "dados_aeronaves.csv"} (${numberFmt.format(metadata.rows || 0)} registros)`;
    metaUpdated.textContent = formatDate(metadata.source_updated_at);
    metaGenerated.textContent = formatDateTime(metadata.generated_at);
    metaUfs.textContent = (metadata.ufs || []).length ? metadata.ufs.join(", ") : "Sem UF informado";
    metaYears.textContent = metadata.year_min && metadata.year_max
      ? `${metadata.year_min} a ${metadata.year_max}`
      : "Nao informado";
    metaLimit.textContent = `${numberFmt.format(metadata.search_result_limit || RESULTS_PER_PAGE)} resultados por pagina`;
  }

  async function ensureRecordsLoaded() {
    if (records) return records;
    if (loadPromise) return loadPromise;

    loadPromise = (async () => {
      setLoadState("Baixando snapshot compactado", "4%", 4);
      const rawText = await fetchGzipText(DATA_URL);
      const lines = rawText.split("\n");
      const totalLines = Math.max(lines.length, 1);
      const parsed = [];
      const progressStep = Math.max(500, Math.floor(totalLines / 40));
      const yieldStep = progressStep * 2;

      setLoadState("Processando registros para busca", "8%", 8);

      for (let index = 0; index < lines.length; index += 1) {
        const line = lines[index].trim();
        if (!line) continue;

        try {
          parsed.push(parseRecord(JSON.parse(line)));
        } catch (_error) {
          continue;
        }

        if ((index + 1) % progressStep === 0 || index === lines.length - 1) {
          const pct = Math.max(8, Math.min(99, Math.round(((index + 1) / totalLines) * 92)));
          setLoadState("Processando registros para busca", `${pct}%`, pct);
        }

        if ((index + 1) % yieldStep === 0) {
          // Yield occasionally so the UI can repaint during parsing.
          // eslint-disable-next-line no-await-in-loop
          await new Promise((resolve) => setTimeout(resolve, 0));
        }
      }

      records = parsed;
      setLoadState("Dados prontos para pesquisar", "100%", 100);
      statusLine.textContent = "Base pronta. Pesquise por prefixo, nome, CPF ou CNPJ.";
      setSearchFeedback("Base pronta", "Digite um termo para consultar a base.", "ready");
      return records;
    })().catch((error) => {
      setLoadState("Falha ao carregar a base", "erro", 0);
      statusLine.textContent = `Erro ao preparar a base: ${error.message || "falha desconhecida"}`;
      setSearchFeedback("Erro ao carregar", "Nao foi possivel preparar a base para busca.", "empty");
      throw error;
    }).finally(() => {
      loadPromise = null;
    });

    return loadPromise;
  }

  function computeScore(record, queryNorm, queryCompact, tokens) {
    let score = 0;
    let matched = false;

    if (queryCompact) {
      if (record.prefixCompact === queryCompact) {
        score += 500;
        matched = true;
      } else if (queryCompact.length >= 2 && record.prefixCompact.startsWith(queryCompact)) {
        score += 320;
        matched = true;
      } else if (queryCompact.length >= 2 && record.prefixCompact.includes(queryCompact)) {
        score += 220;
        matched = true;
      }

      if (record.documentsCompact === queryCompact) {
        score += 460;
        matched = true;
      } else if (queryCompact.length >= 4 && record.documentsCompact.includes(queryCompact)) {
        score += 260;
        matched = true;
      }

      if (queryCompact.length >= 3 && record.searchCompact.includes(queryCompact)) {
        score += 48;
        matched = true;
      }
    }

    if (queryNorm) {
      if (record.operatorNorm.includes(queryNorm)) {
        score += 180;
        matched = true;
      }
      if (record.ownersNorm.includes(queryNorm)) {
        score += 160;
        matched = true;
      }
      if (record.modelNorm.includes(queryNorm)) {
        score += 95;
        matched = true;
      }
    }

    for (const token of tokens) {
      const tokenCompact = compactText(token);
      let tokenScore = 0;

      if (tokenCompact && record.prefixCompact.includes(tokenCompact)) tokenScore = Math.max(tokenScore, 110);
      if (tokenCompact && tokenCompact.length >= 3 && record.documentsCompact.includes(tokenCompact)) tokenScore = Math.max(tokenScore, 105);
      if (record.operatorNorm.includes(token)) tokenScore = Math.max(tokenScore, 62);
      if (record.ownersNorm.includes(token)) tokenScore = Math.max(tokenScore, 58);
      if (record.modelNorm.includes(token)) tokenScore = Math.max(tokenScore, 36);
      if (!tokenScore && record.searchText.includes(token)) tokenScore = 20;
      if (!tokenScore && tokenCompact && tokenCompact.length >= 2 && record.searchCompact.includes(tokenCompact)) tokenScore = 14;

      if (!tokenScore) return -1;
      score += tokenScore;
      matched = true;
    }

    if (!matched) return -1;
    if (!record.cancelDate) score += 6;
    return score;
  }

  function renderIdleState(message) {
    emptyState.hidden = false;
    emptyState.textContent = message;
    pagination.hidden = true;
    paginationSummary.textContent = "";
    paginationPage.textContent = "Pagina 1";
    resultsList.hidden = true;
    resultsList.innerHTML = "";
  }

  function renderPagination(totalMatches) {
    const pageCount = getPageCount(totalMatches);
    const pageStart = ((currentPage - 1) * RESULTS_PER_PAGE) + 1;
    const pageEnd = Math.min(totalMatches, currentPage * RESULTS_PER_PAGE);

    pagination.hidden = totalMatches <= 0;
    paginationSummary.textContent = `Exibindo ${numberFmt.format(pageStart)}-${numberFmt.format(pageEnd)} de ${numberFmt.format(totalMatches)} resultados.`;
    paginationPage.textContent = `Pagina ${numberFmt.format(currentPage)} de ${numberFmt.format(pageCount)}`;
    paginationPrev.disabled = currentPage <= 1;
    paginationNext.disabled = currentPage >= pageCount;
  }

  function renderResultsPage() {
    const totalMatches = currentMatches.length;
    const pageCount = getPageCount(totalMatches);
    currentPage = Math.min(Math.max(currentPage, 1), pageCount);
    const startIndex = (currentPage - 1) * RESULTS_PER_PAGE;
    const visibleMatches = currentMatches.slice(startIndex, startIndex + RESULTS_PER_PAGE);

    renderPagination(totalMatches);
    emptyState.hidden = true;
    resultsList.hidden = false;
    resultsList.innerHTML = visibleMatches.map((row) => renderRecordCard(row.record)).join("");
  }

  function renderResults(query, matches, page = 1) {
    const totalMatches = matches.length;

    if (!totalMatches) {
      currentMatches = [];
      currentQuery = query;
      currentPage = 1;
      statusLine.textContent = `Nenhum registro encontrado para "${query}".`;
      resultsSummary.innerHTML = "Tente um prefixo sem espacos, um nome parcial ou um CPF/CNPJ sem pontuacao.";
      setSearchFeedback("0 resultados", `Nada encontrado para "${query}".`, "empty");
      renderIdleState(`Nenhum resultado para "${query}". Tente combinar menos termos ou usar apenas parte do nome/documento.`);
      return;
    }

    currentMatches = matches;
    currentQuery = query;
    currentPage = Number.isFinite(Number(page)) ? Math.max(1, Number(page)) : 1;
    statusLine.textContent = `${numberFmt.format(totalMatches)} registro(s) encontrado(s) para "${query}".`;
    setSearchFeedback(
      formatResultLabel(totalMatches),
      `Busca por "${query}". ${numberFmt.format(RESULTS_PER_PAGE)} por pagina.`,
      "success",
    );
    resultsSummary.innerHTML = `<strong>${numberFmt.format(totalMatches)} resultados.</strong> Ordenados por relevancia e paginados em blocos de ${numberFmt.format(RESULTS_PER_PAGE)}.`;
    renderResultsPage();
  }

  function renderRecordCard(record) {
    const ownersPrimary = record.owners[0]?.name || record.operator || "Operador nao informado";
    const summaryParts = [
      record.model && record.manufacturer ? `${record.model} · ${record.manufacturer}` : record.model || record.manufacturer,
      record.year ? `Ano ${record.year}` : "",
      record.icaoType ? `ICAO ${record.icaoType}` : "",
    ].filter(Boolean);

    const statusBadge = record.cancelDate
      ? `<span class="pill cancelled">Cancelada em ${esc(formatDate(record.cancelDate))}</span>`
      : `<span class="pill live">Sem cancelamento</span>`;

    const metaBadges = [
      statusBadge,
      record.uf ? `<span class="pill">UF ${esc(record.uf)}</span>` : "",
      record.operationType ? `<span class="pill">${esc(record.operationType)}</span>` : "",
    ].filter(Boolean).join("");

    return `
      <article class="record-card">
        <div class="record-top">
          <div class="record-prefix">
            <strong>${esc(record.prefixDisplay)}</strong>
            <span>${esc(summaryParts.join(" · ") || "Registro de aeronave")}</span>
          </div>
          <div class="record-badges">${metaBadges}</div>
        </div>

        <div class="record-main">
          <h3>${esc(record.operator || ownersPrimary)}</h3>
          <p>${esc([
            record.operatorUf ? `UF do operador: ${record.operatorUf}` : "",
            record.otherOperators ? `Outros operadores informados: ${record.otherOperators}` : "",
          ].filter(Boolean).join(" · ") || "Operador e proprietarios conforme a base original.")}</p>
        </div>

        <div class="record-grid">
          ${detailCard("Documento para busca", buildFieldLines([record.document || record.ownersRaw], true))}
          ${detailCard("Proprietarios", buildOwnersHtml(record.owners, record.ownersRaw))}
          ${detailCard("Modelo e fabricante", buildFieldLines([
            record.model,
            record.manufacturer,
            record.icaoType ? `Tipo ICAO: ${record.icaoType}` : "",
          ]))}
          ${detailCard("Categoria operacional", buildFieldLines([
            record.operationalRule ? `Certificacao: ${record.operationalRule}` : "",
            record.homologationCategory ? `Categoria: ${record.homologationCategory}` : "",
            record.airworthinessType ? `TP CA: ${record.airworthinessType}` : "",
            record.operationType ? `Operacao: ${record.operationType}` : "",
          ]))}
          ${detailCard("Datas relevantes", buildFieldLines([
            record.registrationDate ? `Matricula: ${formatDate(record.registrationDate)}` : "",
            record.cancelDate ? `Cancelamento: ${formatDate(record.cancelDate)}` : "",
            record.cancelReason ? `Motivo: ${record.cancelReason}` : "",
          ]))}
          ${detailCard("Configuracao", buildFieldLines([
            record.engineType ? `Motor: ${record.engineType}` : "",
            record.engineCount ? `Quantidade de motores: ${record.engineCount}` : "",
            record.passengers ? `Passageiros max.: ${record.passengers}` : "",
            record.seats ? `Assentos: ${record.seats}` : "",
          ]))}
          ${detailCard("Gravame e situacao", buildFieldLines([
            record.encumbrance,
            record.interdictionCode ? `Codigo de interdicao: ${record.interdictionCode}` : "",
          ]))}
          ${detailCard("Outros operadores", buildFieldLines([
            record.otherOperators,
            record.operatorUf ? `UF do operador: ${record.operatorUf}` : "",
          ]))}
        </div>
      </article>
    `;
  }

  function syncQueryToUrl(query, page = 1) {
    const url = new URL(window.location.href);
    if (clean(query)) {
      url.searchParams.set("q", query);
    } else {
      url.searchParams.delete("q");
    }
    if (page > 1) {
      url.searchParams.set("page", String(page));
    } else {
      url.searchParams.delete("page");
    }
    window.history.replaceState({}, "", url);
  }

  async function handleSearch(page = 1) {
    const rawQuery = clean(queryInput.value);
    const safePage = Number.isFinite(Number(page)) ? Math.max(1, Number(page)) : 1;
    syncQueryToUrl(rawQuery, safePage);

    if (!rawQuery) {
      statusLine.textContent = "Base pronta. Pesquise por prefixo, nome, CPF ou CNPJ.";
      resultsSummary.textContent = "Digite um termo para consultar a base.";
      setSearchFeedback("Base pronta", "Digite um termo para consultar a base.", "ready");
      renderIdleState("Digite um prefixo, nome, CPF ou CNPJ para procurar registros.");
      return;
    }

    btnSearch.disabled = true;
    statusLine.textContent = "Pesquisando na base local...";
    setSearchFeedback("Pesquisando...", `Procurando por "${rawQuery}" na base local.`, "loading");

    try {
      const loadedRecords = await ensureRecordsLoaded();
      const queryNorm = normalizeText(rawQuery);
      const queryCompact = compactText(rawQuery);
      const tokens = queryNorm.split(" ").filter(Boolean);
      const matches = [];

      for (const record of loadedRecords) {
        const score = computeScore(record, queryNorm, queryCompact, tokens);
        if (score < 0) continue;
        matches.push({ record, score });
      }

      matches.sort((left, right) => {
        if (right.score !== left.score) return right.score - left.score;
        if (Boolean(left.record.cancelDate) !== Boolean(right.record.cancelDate)) {
          return left.record.cancelDate ? 1 : -1;
        }
        return left.record.prefixDisplay.localeCompare(right.record.prefixDisplay, "pt-BR");
      });

      renderResults(rawQuery, matches, safePage);
    } catch (error) {
      statusLine.textContent = `Erro ao pesquisar: ${error.message || "falha desconhecida"}`;
      setSearchFeedback("Erro na busca", "Nao foi possivel concluir essa pesquisa agora.", "empty");
      renderIdleState("Nao foi possivel carregar a base estatica desta dashboard.");
    } finally {
      btnSearch.disabled = false;
    }
  }

  async function bootstrap() {
    try {
      metadata = await fetchJson(META_URL);
      renderMetadata();
    } catch (error) {
      statusLine.textContent = `Erro ao ler metadados: ${error.message || "falha desconhecida"}`;
      setSearchFeedback("Erro ao iniciar", "Os metadados da dashboard nao puderam ser carregados.", "empty");
      renderIdleState("Os metadados da dashboard nao puderam ser carregados.");
      return;
    }

    try {
      await ensureRecordsLoaded();
      const searchParams = new URLSearchParams(window.location.search);
      const initialQuery = searchParams.get("q");
      const initialPage = Number(searchParams.get("page") || "1");
      requestedPage = Number.isFinite(initialPage) ? Math.max(1, initialPage) : 1;
      if (initialQuery) {
        queryInput.value = initialQuery;
        await handleSearch(requestedPage);
      } else {
        setSearchFeedback("Base pronta", "Digite um termo para consultar a base.", "ready");
        renderIdleState("Digite um prefixo, nome, CPF ou CNPJ para procurar registros.");
      }
    } catch (_error) {
      setSearchFeedback("Erro ao iniciar", "Os registros nao puderam ser preparados para pesquisa.", "empty");
      renderIdleState("Os registros nao puderam ser preparados para pesquisa.");
    }
  }

  searchForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    await handleSearch(1);
  });

  btnClear.addEventListener("click", () => {
    currentMatches = [];
    currentQuery = "";
    currentPage = 1;
    requestedPage = 1;
    queryInput.value = "";
    syncQueryToUrl("", 1);
    statusLine.textContent = "Base pronta. Pesquise por prefixo, nome, CPF ou CNPJ.";
    resultsSummary.textContent = "Digite um termo para consultar a base.";
    setSearchFeedback("Base pronta", "Digite um termo para consultar a base.", "ready");
    renderIdleState("Digite um prefixo, nome, CPF ou CNPJ para procurar registros.");
  });

  paginationPrev.addEventListener("click", () => {
    if (currentPage <= 1) return;
    currentPage -= 1;
    syncQueryToUrl(currentQuery, currentPage);
    renderResultsPage();
    setSearchFeedback(formatResultLabel(currentMatches.length), `Busca por "${currentQuery}". Página ${currentPage}.`, "success");
  });

  paginationNext.addEventListener("click", () => {
    const pageCount = getPageCount(currentMatches.length);
    if (currentPage >= pageCount) return;
    currentPage += 1;
    syncQueryToUrl(currentQuery, currentPage);
    renderResultsPage();
    setSearchFeedback(formatResultLabel(currentMatches.length), `Busca por "${currentQuery}". Página ${currentPage}.`, "success");
  });

  void bootstrap();
})();
