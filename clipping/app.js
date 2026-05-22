const CATEGORY_ORDER = [
  "reportagem_autoral",
  "republicacao",
  "eco_repercussao",
  "citacao_fonte",
  "mencao",
  "entrevista",
  "palestra_curso",
  "perfil_bio",
];

const CATEGORY_LABELS = {
  reportagem_autoral: "Reportagens publicadas por mim",
  republicacao: "Republicações / syndication",
  eco_repercussao: "Repercussão / interpretação de apuração minha",
  citacao_fonte: "Citado como fonte / especialista",
  mencao: "Menções ao meu nome",
  entrevista: "Entrevistas e podcasts",
  palestra_curso: "Palestras e cursos",
  perfil_bio: "Perfis e páginas institucionais",
};

const state = {
  items: [],
  metadata: null,
  search: "",
};

const refs = {
  heroMeta: document.getElementById("heroMeta"),
  toolbarCopy: document.getElementById("toolbarCopy"),
  searchInput: document.getElementById("searchInput"),
  recentSummary: document.getElementById("recentSummary"),
  recentList: document.getElementById("recentList"),
  resultSummary: document.getElementById("resultSummary"),
  categoryStack: document.getElementById("categoryStack"),
  methodologyBox: document.getElementById("methodologyBox"),
};

const dateFormatter = new Intl.DateTimeFormat("pt-BR", {
  timeZone: "America/Sao_Paulo",
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
});

const dateTimeFormatter = new Intl.DateTimeFormat("pt-BR", {
  timeZone: "America/Sao_Paulo",
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

function foldText(value) {
  return (value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
}

function cleanText(value) {
  return (value || "").replace(/\s+/g, " ").trim();
}

function formatDate(value) {
  if (!value) return "Sem data verificável";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "Sem data verificável";
  return dateFormatter.format(parsed);
}

function formatDateTime(value) {
  if (!value) return "--";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "--";
  return dateTimeFormatter.format(parsed);
}

function hasReliablePublishedDate(item) {
  return Boolean(item.published_at) && item.category !== "perfil_bio" && item.relation !== "pagina de autor";
}

function publishedTimestamp(item) {
  if (!hasReliablePublishedDate(item)) return 0;
  const parsed = new Date(item.published_at).getTime();
  return Number.isNaN(parsed) ? 0 : parsed;
}

function daysAgo(item) {
  const stamp = publishedTimestamp(item);
  if (!stamp) return null;
  const diffMs = Date.now() - stamp;
  if (diffMs < 0) return null;
  return Math.floor(diffMs / (1000 * 60 * 60 * 24));
}

function excerptFor(item) {
  const value = cleanText(item.text_excerpt || item.description || "");
  if (!value) return "Sem trecho disponível.";
  return value.length > 340 ? `${value.slice(0, 337)}...` : value;
}

function compareArchive(a, b) {
  const aPublished = publishedTimestamp(a);
  const bPublished = publishedTimestamp(b);
  if (aPublished !== bPublished) return bPublished - aPublished;
  const aCategory = CATEGORY_ORDER.indexOf(a.category);
  const bCategory = CATEGORY_ORDER.indexOf(b.category);
  if (aCategory !== bCategory) return aCategory - bCategory;
  return (a.title || "").localeCompare(b.title || "", "pt-BR");
}

function matchesSearch(item, search) {
  if (!search) return true;
  const haystack = foldText([
    item.title,
    item.domain,
    item.site_name,
    item.category_label,
    item.relation,
    item.text_excerpt,
    item.description,
  ].join(" "));
  return haystack.includes(foldText(search));
}

function getFilteredItems() {
  return state.items.filter((item) => matchesSearch(item, state.search)).sort(compareArchive);
}

function getRecentStats(items) {
  const stats = { day: 0, week: 0, month: 0 };
  items.forEach((item) => {
    const age = daysAgo(item);
    if (age === null) return;
    if (age <= 1) stats.day += 1;
    if (age <= 7) stats.week += 1;
    if (age <= 30) stats.month += 1;
  });
  return stats;
}

function renderHero(items) {
  refs.heroMeta.innerHTML = `
    <span>Atualizado: ${formatDateTime(state.metadata?.generated_at)}</span>
    <span>Base organizada por tipo de menção</span>
  `;

  refs.toolbarCopy.textContent = items.length
    ? "A busca abaixo apenas filtra títulos, fontes e trechos já coletados. O seu portfólio não é usado como fonte de descoberta."
    : "Nenhum item corresponde à busca atual.";
}

function buildEntry(item, open = false) {
  const dateLabel = hasReliablePublishedDate(item) ? formatDate(item.published_at) : "Sem data verificável";
  return `
    <details class="entry" ${open ? "open" : ""}>
      <summary>
        <div class="entry-head">
          <div class="entry-meta">
            <span class="tag">${CATEGORY_LABELS[item.category] || item.category_label || item.category}</span>
            <span class="tag">${item.domain || item.site_name || "fonte"}</span>
            <span>${dateLabel}</span>
          </div>
          <strong>${item.title || "Sem título"}</strong>
        </div>
      </summary>
      <div class="entry-body">
        <p><strong>Contexto:</strong> ${item.relation || "não classificado"}</p>
        <p>${excerptFor(item)}</p>
        <a class="entry-link" href="${item.url}" target="_blank" rel="noopener noreferrer">Abrir original</a>
      </div>
    </details>
  `;
}

function renderRecent(items) {
  const recentItems = items.filter((item) => {
    const age = daysAgo(item);
    return age !== null && age <= 30;
  }).slice(0, 12);
  refs.recentSummary.textContent = recentItems.length
    ? `${recentItems.length} item(ns) com data publicada verificável nos últimos 30 dias.`
    : "Nenhum item recente com data publicada verificável.";

  if (!recentItems.length) {
    refs.recentList.innerHTML = '<div class="empty-state">Nenhum item com data publicada verificável nos últimos 30 dias.</div>';
    return;
  }

  refs.recentList.innerHTML = recentItems.map((item, index) => buildEntry(item, index < 3)).join("");
}

function renderArchive(items) {
  refs.resultSummary.textContent = state.search
    ? `${items.length} item(ns) encontrados para "${state.search}".`
    : `${items.length} item(ns) no arquivo atual.`;
  const sections = CATEGORY_ORDER.map((category) => {
    const categoryItems = items.filter((item) => item.category === category);
    if (!categoryItems.length) return "";
    const recentCount = categoryItems.filter((item) => {
      const age = daysAgo(item);
      return age !== null && age <= 30;
    }).length;
    const entries = categoryItems.map((item) => buildEntry(item)).join("");
    const countLabel = recentCount
      ? `${categoryItems.length} item(ns) · ${recentCount} no último mês`
      : `${categoryItems.length} item(ns)`;
    return `
      <details class="category-block" open>
        <summary>
          <strong>${CATEGORY_LABELS[category] || category}</strong>
          <span class="category-count">${countLabel}</span>
        </summary>
        <div class="entry-list">${entries}</div>
      </details>
    `;
  }).filter(Boolean);

  refs.categoryStack.innerHTML = sections.join("") || '<div class="empty-state">Nenhum item encontrado com esse termo.</div>';
}

function renderMethodology() {
  const meta = state.metadata || {};
  const searches = meta.sources?.discovery?.searches || {};
  const searchLines = Object.entries(searches)
    .map(([label, count]) => `<li><strong>${label.replace(/"/g, "")}</strong>: ${count} link(s) brutos nesta execução.</li>`)
    .join("");

  refs.methodologyBox.innerHTML = `
    <p>
      <strong>Nomes monitorados:</strong> ${(meta.methodology?.names || []).join("; ") || "--"}.
      O acervo é montado a partir de páginas públicas de autor, perfis institucionais e buscas nominais externas. O seu portfólio não entra como fonte de descoberta.
    </p>
    <p>
      <strong>Recência:</strong> os números de último dia, 7 dias e 30 dias contam apenas itens com data publicada verificável. Perfis e páginas de autor podem permanecer no arquivo, mas não entram nesses blocos de recente.
    </p>
    <p>
      <strong>Classificação:</strong> a separação entre reportagem autoral, menção, entrevista, eco, palestra e perfil é feita por regras de byline, tipo de página, palavras-chave e contexto institucional. Isso reduz ruído, mas não elimina a necessidade de revisão manual em casos ambíguos.
    </p>
    <ul>${searchLines || "<li>Sem buscas externas registradas nesta execução.</li>"}</ul>
    <p>
      <strong>Limite metodológico:</strong> a cobertura é ampla, mas não exaustiva. Buscadores podem perder páginas, alguns sites bloqueiam raspagem e homônimos podem exigir curadoria manual.
    </p>
  `;
}

function render() {
  const filteredItems = getFilteredItems();
  renderHero(filteredItems);
  renderRecent(filteredItems);
  renderArchive(filteredItems);
  renderMethodology();
}

async function loadData() {
  const [itemsResponse, metadataResponse] = await Promise.all([
    fetch("./data/items.json", { cache: "no-store" }),
    fetch("./data/metadata.json", { cache: "no-store" }),
  ]);
  if (!itemsResponse.ok || !metadataResponse.ok) {
    throw new Error("Falha ao carregar os dados do clipping.");
  }
  state.items = await itemsResponse.json();
  state.metadata = await metadataResponse.json();
}

function bindControls() {
  refs.searchInput.addEventListener("input", (event) => {
    state.search = event.target.value || "";
    render();
  });
}

async function init() {
  bindControls();
  try {
    await loadData();
    render();
  } catch (error) {
    refs.toolbarCopy.textContent = "Não foi possível carregar a base agora.";
    refs.recentList.innerHTML = `<div class="empty-state">${error.message}</div>`;
    refs.categoryStack.innerHTML = '<div class="empty-state">Sem dados para exibir.</div>';
  }
}

init();
