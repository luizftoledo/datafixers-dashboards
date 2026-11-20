(() => {
  const element = (id) => document.getElementById('topic-' + id);
  const topic = document.body.dataset.topic;
  const config = {
    advogado: { kind: 3, match: /sigilo profissional do advogado/i, case: 'advogado' },
    preparatorios: { kind: 1, match: /processo decisório em curso/i, case: 'preparatorio' },
    classificada: { kind: 1, match: /classificada conforme artigos 23 e 24/i, case: 'classificada' },
    'cem-anos': { kind: 5, match: /^100$/, case: 'cem-anos' },
    negativas: { kind: 1, match: /.*/, case: 'negativas' },
  }[topic];
  const number = new Intl.NumberFormat('pt-BR', { maximumFractionDigits: 2 });
  const month = new Intl.DateTimeFormat('pt-BR', { month: 'short', timeZone: 'UTC' });
  const monthLabel = (m) => month.format(new Date(Date.UTC(2020, m - 1, 1)));
  const normalize = (s) => s.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  const blank = '—', pageSize = 10;
  let data, history, firstPeriod, lastYear, lastMonth, trend, rank, corpus = [], page = 0;

  const make = (parent, tag, text) => {
    const n = document.createElement(tag);
    if (text != null) n.textContent = text;
    parent.append(n);
    return n;
  };

  const fetchResource = async (url) => {
    const response = await fetch(url);
    if (!response.ok) throw new Error('HTTP ' + response.status);
    return response;
  };

  function setOptions(select, pairs, prompt) {
    const items = pairs.map(([value, label]) => new Option(label, value));
    select.replaceChildren(...(prompt ? [new Option(prompt, '')] : []), ...items);
    select.disabled = false;
  }

  function loadHistory() {
    const index = new Map();
    let first = Infinity;
    const base = config.kind;
    for (const [period, org, kind, code, count] of data.rows) {
      const isRelevant = kind === base || kind === base + 1;
      if ((base === 1 && kind === 0) || (base !== 1 && isRelevant)) first = Math.min(first, period);
      if (kind !== 0 && (!isRelevant || !config.match.test(data.reasons[code]))) continue;
      if (!index.has(org)) index.set(org, new Map());
      const periods = index.get(org);
      if (!periods.has(period)) periods.set(period, [0, 0, 0]);
      periods.get(period)[kind === 0 ? 0 : kind - base + 1] += count;
    }
    firstPeriod = Number.isFinite(first) ? first : data.cutoff;
    return index;
  }

  function summarize(orgs, year, firstMonth, endMonth) {
    let total = 0, hits = 0;
    const outcome = element('outcome').value;
    for (const org of orgs) {
      const values = history.get(org);
      for (let m = firstMonth; m <= endMonth; m++) {
        const period = year * 100 + m;
        if (period > data.cutoff || period < firstPeriod) continue;
        const cell = values?.get(period);
        if (!cell) continue;
        total += cell[0];
        hits += outcome === 'both' ? cell[1] + cell[2] : cell[Number(outcome)];
      }
    }
    return { total, hits };
  }

  function value(row) {
    if (!row.total) return null;
    return element('unit').value === 'rate' ? 100 * row.hits / row.total : row.hits;
  }
  function label(v) { return v === null ? blank : number.format(v) + (element('unit').value === 'rate' ? '%' : ''); }
  function cell(row, content) { row.insertCell().textContent = content; }
  function chartOptions(axis = 'x') {
    return {
      responsive: true, maintainAspectRatio: false, animation: false, indexAxis: axis,
      scales: { [axis === 'x' ? 'y' : 'x']: { beginAtZero: true, ticks: { callback: label } } },
      plugins: { legend: { position: 'bottom' }, tooltip: { callbacks: { label: ctx => ctx.dataset.label + ': ' + label(ctx.raw) } } },
    };
  }

  function renderCharts() {
    const all = [...history.keys()];
    const selected = element('org').value;
    const chosen = selected === '' ? null : Number(selected);
    const chosenYear = Number(element('year').value);
    const monthly = element('period').value === 'monthly';
    const suffix = monthly ? chosenYear === lastYear ? lastMonth : 12 : lastMonth;
    const firstYear = Math.floor(firstPeriod / 100);
    const periods = monthly
      ? Array.from({ length: suffix }, (_, i) => ({ year: chosenYear, start: i + 1, end: i + 1, label: monthLabel(i + 1) }))
      : Array.from({ length: lastYear - firstYear + 1 }, (_, i) => ({ year: firstYear + i, start: 1, end: suffix, label: String(firstYear + i) }));
    const series = [{ label: monthly ? 'Federal · ' + chosenYear : 'Total federal', orgs: all, offset: 0, color: '#165e4c' }];
    if (monthly && chosenYear > firstYear) series.push({ label: 'Federal · ' + (chosenYear - 1), orgs: all, offset: -1, color: '#576d82' });
    if (chosen !== null) series.push({ label: data.orgs[chosen].split(/ [–-] /)[0], orgs: [chosen], offset: 0, color: '#a34924' });
    const lineData = series.map(record => ({
      label: record.label, borderColor: record.color, backgroundColor: record.color,
      data: periods.map(p => value(summarize(record.orgs, p.year + record.offset, p.start, p.end))),
      pointRadius: 3,
    }));
    element('trend-title').textContent = monthly ? 'Evolução mensal · ' + chosenYear : 'Evolução anual';
    trend?.destroy();
    trend = new Chart(element('trend'), {
      type: 'line', data: { labels: periods.map(p => p.label), datasets: lineData },
      options: chartOptions(),
    });

    const end = chosenYear === lastYear ? lastMonth : 12;
    const ranked = all.map(i => ({ i, ...summarize([i], chosenYear, 1, end) }))
      .filter(r => r.total && r.hits).sort((a, b) => value(b) - value(a) || b.hits - a.hits);
    const featured = ranked.slice(0, 10);
    element('rank-title').textContent = 'Órgãos · ' + chosenYear;
    rank?.destroy();
    const options = chartOptions('y');
    rank = new Chart(element('ranking'), {
      type: 'bar',
      data: { labels: featured.map(r => data.orgs[r.i].split(/ [–-] /)[0]), datasets: [{ label: 'Pedidos no recorte', data: featured.map(value), backgroundColor: '#26745e', borderRadius: 3 }] },
      options: {
        ...options,
        plugins: { ...options.plugins, legend: { display: false }, tooltip: { callbacks: {
          title: ([item]) => data.orgs[featured[item.dataIndex].i],
          label: ({ dataIndex }) => {
            const r = featured[dataIndex];
            return label(value(r)) + ' · ' + number.format(r.hits) + ' de ' + number.format(r.total) + ' respostas';
          },
        } } },
        onClick(event, items) {
          if (items.length) {
            element('org').value = String(featured[items[0].index].i);
            renderCharts();
          }
        },
      },
    });
    element('note').textContent = (monthly ? 'Série mensal.' : 'Série anual com o mesmo corte em todos os anos, de janeiro a ' + monthLabel(suffix) + '.')
      + ' A taxa tem como base todos os pedidos respondidos com decisão registrada. Clique numa barra para incluir o órgão na série.';
    const trendTable = element('trend-values');
    const trendHead = trendTable.closest('table').tHead.rows[0];
    trendHead.replaceChildren();
    make(trendHead, 'th', 'Período');
    for (const dataset of lineData) make(trendHead, 'th', dataset.label);
    trendTable.replaceChildren();
    periods.forEach((p, i) => {
      const row = trendTable.insertRow();
      cell(row, p.label);
      for (const dataset of lineData) cell(row, label(dataset.data[i]));
    });
    const rankTable = element('rank-values');
    rankTable.replaceChildren();
    for (const r of ranked) {
      const row = rankTable.insertRow();
      for (const text of [data.orgs[r.i], number.format(r.hits), number.format(r.total), number.format(r.hits / r.total * 100) + '%']) cell(row, text);
    }
  }

  function renderCases() {
    const query = normalize(element('search').value.trim());
    const org = element('case-org').value, year = element('case-year').value, status = element('case-outcome').value;
    const rows = corpus.filter(({ record: [date, name, decision], textIndex }) =>
      (!org || org === name) && (!year || date.startsWith(year)) && (!status || status === decision) &&
      (!query || query.split(/\s+/).every(word => textIndex.includes(word))));
    const max = Math.max(1, Math.ceil(rows.length / pageSize));
    const table = element('case-rows');
    table.replaceChildren();
    for (const { record: [date, name, decision, protocol, id], excerpts } of rows.slice(page * pageSize, (page + 1) * pageSize)) {
      const row = table.insertRow();
      cell(row, date.split('-').reverse().join('/'));
      const agency = row.insertCell();
      make(agency, 'strong', name);
      make(agency, 'small', decision);
      const body = row.insertCell();
      const link = make(body, 'a', protocol + ' · BuscaLAI');
      link.href = 'https://buscalai.cgu.gov.br/busca/' + encodeURIComponent(id);
      if (excerpts.some(Boolean)) {
        const box = make(body, 'details');
        make(box, 'summary', 'Ler trechos do pedido e da resposta');
        for (const [i, label] of ['Pedido', 'Resposta'].entries()) {
          const p = make(box, 'p');
          make(p, 'strong', label + ' · ');
          p.append(document.createTextNode(excerpts[i] || 'Trecho não disponível neste catálogo.'));
        }
      }
    }
    element('case-count').textContent = number.format(rows.length) + (rows.length === 1 ? ' pedido encontrado' : ' pedidos encontrados');
    element('page').textContent = rows.length ? (page + 1) + ' de ' + max : '';
    element('prev').disabled = page === 0;
    element('next').disabled = page + 1 >= max;
  }

  async function loadCases() {
    const metadata = await (await fetchResource('../data/public-cases.json')).json();
    const response = await fetchResource('../data/case-texts.json.gz');
    const bytes = await response.arrayBuffer();
    const stream = new Blob([bytes]).stream();
    const compressed = new Uint8Array(bytes, 0, 2)[0] === 31;
    const details = await new Response(compressed ? stream.pipeThrough(new DecompressionStream('gzip')) : stream).json();
    const entries = topic === 'negativas' ? details.recent
      : metadata.rows.filter(r => r[5].includes(config.case)).map(record => [record, details.texts[record[4]] || []]);
    corpus = entries.map(([record, excerpts]) => ({
      record, excerpts, textIndex: normalize([record[1], record[2], record[3], ...excerpts].join(' ')),
    }));
    const period = topic === 'negativas' && corpus[0]
      ? 'Até 300 negativas públicas mais recentes de ' + monthLabel(Number(corpus[0].record[0].slice(5, 7))) + '/' + corpus[0].record[0].slice(0, 4) + '. '
      : 'Base pública do BuscaLAI, arquivos de ' + metadata.registration_years.join(' e ') + '. ';
    element('case-scope').textContent = period + (topic === 'cem-anos'
      ? 'Nesta página, a busca cobre órgão e protocolo. O texto fica no BuscaLAI.'
      : 'A busca inclui trechos públicos do pedido e da resposta. Abra o protocolo para consultar o texto integral.');
    const agencyNames = [...new Set(corpus.map(r => r.record[1]))].sort((a, b) => a.localeCompare(b, 'pt-BR'));
    setOptions(element('case-org'), agencyNames.map(o => [o, o]), 'Todos os órgãos');
    const years = [...new Set(corpus.map(r => r.record[0].slice(0, 4)))];
    setOptions(element('case-year'), years.map(y => [y, y]), 'Todos os anos');
    for (const id of ['search', 'case-org', 'case-year', 'case-outcome']) {
      element(id).disabled = false;
      element(id).addEventListener(id === 'search' ? 'input' : 'change', () => { page = 0; renderCases(); });
    }
    element('prev').addEventListener('click', () => { page--; renderCases(); });
    element('next').addEventListener('click', () => { page++; renderCases(); });
    renderCases();
  }

  async function startCharts() {
    data = await (await fetchResource('../data/monitor.json')).json();
    lastYear = Math.floor(data.cutoff / 100);
    lastMonth = data.cutoff % 100;
    history = loadHistory();
    const years = Array.from({ length: lastYear - Math.floor(firstPeriod / 100) + 1 }, (_, i) => lastYear - i);
    setOptions(element('year'), years.map(y => [y, y]));
    setOptions(element('org'), data.orgs.map((name, i) => [i, name]), 'Total federal');
    for (const id of ['year', 'org', 'period', 'unit', 'outcome']) {
      element(id).disabled = topic === 'negativas' && id === 'outcome';
      element(id).addEventListener('change', renderCharts);
    }
    const when = data.reference.replace(/(\d{4})(\d{2})(\d{2})/, '$3/$2/$1');
    element('source').textContent = 'Fala.BR · Respostas até ' + monthLabel(lastMonth) + '/' + lastYear +
      (when ? ' · extração de ' + when : '') +
      (config.kind > 1 ? ' · Campo disponível desde ' + monthLabel(firstPeriod % 100) + '/' + Math.floor(firstPeriod / 100) : '');
    renderCharts();
  }

  startCharts().catch(() => { element('source').textContent = 'Não foi possível carregar os gráficos.'; });
  loadCases().catch(() => { element('case-count').textContent = 'Não foi possível carregar o catálogo público.'; });
})();
