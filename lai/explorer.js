(() => {
  const byId = (id) => document.getElementById(id);
  const controls = Object.fromEntries(['reason', 'org', 'outcome', 'unit', 'bb'].map((id) => [id, byId('explorer-' + id)]));
  const fmt = new Intl.NumberFormat('pt-BR');
  const percent = new Intl.NumberFormat('pt-BR', { style: 'percent', maximumFractionDigits: 2 });
  const monthName = new Intl.DateTimeFormat('pt-BR', { month: 'long', timeZone: 'UTC' });
  const monthLabel = (month) => monthName.format(new Date(Date.UTC(2020, month - 1, 1)));
  const key = (text) => text.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  const detailLabel = (label) => label.replace(/^(Parte da informação |A informação )/i, '')
    .replace(/^está protegida por /i, '').replace(/^./, (c) => c.toUpperCase());
  let data, categories, trend, governments;

  function addOption(parent, label, value) {
    const option = document.createElement('option');
    option.textContent = label;
    option.value = value;
    parent.append(option);
  }

  function setUp() {
    const reasonKinds = new Map();
    for (const [, , kind, code] of data.rows) {
      if (!reasonKinds.has(code)) reasonKinds.set(code, new Set());
      reasonKinds.get(code).add(kind);
    }
    const basic = (text) => key(text).replace(/^parte d[oa] (informacao |pedido )?/, '')
      .replace(/^informacao (contem |e |esta relacionada a )?/, '')
      .replace(/^pedido (e )?/, '').replace(/^contem /, '');
    const grouped = new Map();
    function addCategory(id, label, kind, codes, group) {
      const entry = { id, label, kind, codes: new Set(codes), group, since: 0 };
      categories.set(id, entry);
      return entry;
    }
    categories = new Map();
    addCategory('all', 'Todos os motivos', 1, null, '');
    for (const [code, kinds] of reasonKinds) {
      if (code < 0) continue;
      const raw = data.reasons[code];
      const kind = kinds.has(5) || kinds.has(6) ? 5 : kinds.has(3) || kinds.has(4) ? 3 : 1;
      const label = kind === 5 ? raw + (raw === '1' ? ' ano informado' : ' anos informados')
        : kind === 3 ? detailLabel(raw) : raw;
      const group = kind === 5 ? 'Prazo registrado para dados pessoais' : kind === 3 ? 'Fundamentos detalhados' : 'Motivos registrados';
      const groupKey = kind === 1 ? basic(label) : key(label);
      const id = kind + ':' + groupKey;
      if (grouped.has(id)) grouped.get(id).codes.add(code);
      else grouped.set(id, addCategory(id, label, kind, [code], group));
    }
    const details = Array.from(categories.values()).filter(c => c.kind === 3);
    const sensitive = [...categories.values()].filter(c => c.kind === 1 && basic(c.label).includes('dados pessoais'));
    const lawyer = details.find(c => key(c.label).includes('sigilo profissional do advogado'));
    const hundred = [...categories.values()].find(c => c.kind === 5 && c.label.startsWith('100 '));
    const quick = [lawyer, sensitive[0], hundred].filter(Boolean);
    for (const c of quick) {
      if (c.kind === 1) c.label = 'Dados pessoais (art. 31 da LAI)';
      if (c.kind === 5) c.label = 'Dados pessoais com prazo informado de 100 anos';
    }
    const firstByKind = new Map();
    for (const [period, , kind] of data.rows) {
      if (kind < 3) continue;
      const family = kind >= 5 ? 5 : 3;
      if (!firstByKind.has(family)) firstByKind.set(family, period);
    }
    for (const c of categories.values()) c.since = firstByKind.get(c.kind) || 0;
    addOption(controls.reason, 'Todos os motivos', 'all');
    for (const c of quick) addOption(controls.reason, c.label, c.id);
    for (const group of ['Motivos registrados', 'Fundamentos detalhados', 'Prazo registrado para dados pessoais']) {
      const options = document.createElement('optgroup');
      options.label = group;
      const entries = [...categories.values()].filter(c => c.group === group && !quick.includes(c));
      entries.sort((a, b) => a.label.localeCompare(b.label, 'pt-BR', { numeric: true }));
      for (const c of entries) addOption(options, c.label, c.id);
      controls.reason.append(options);
    }
    addOption(controls.org, 'Todos os órgãos federais', 'all');
    data.orgs.forEach((org, i) => addOption(controls.org, org, String(i)));
    for (const ctl of Object.values(controls)) {
      ctl.disabled = false;
      ctl.addEventListener('change', render);
    }
    controls.org.addEventListener('change', () => {
      controls.bb.disabled = controls.org.value !== 'all';
    });
  }

  function aggregate(category, org, exclusion, outcome) {
    const bins = new Map();
    const foundTypes = outcome === 'both' ? [category.kind, category.kind + 1] : [category.kind + Number(outcome) - 1];
    for (const [period, index, kind, code, count] of data.rows) {
      if (org !== 'all' && index !== Number(org) || org === 'all' && exclusion.has(index)) continue;
      const denominator = kind === 0;
      if (!denominator && (!foundTypes.includes(kind) || category.id !== 'all' && !category.codes.has(code))) continue;
      if (!bins.has(period)) bins.set(period, [0, 0]);
      bins.get(period)[denominator ? 0 : 1] += count;
    }
    return bins;
  }

  function summarize(bins, start, end, since) {
    let n = 0, hits = 0;
    for (const [period, values] of bins) {
      if (period < Math.max(start, since) || period > end) continue;
      n += values[0];
      hits += values[1];
    }
    return { n, hits, rate: n ? hits / n : null, limited: Boolean(since && start < since) };
  }

  function render() {
    const category = categories.get(controls.reason.value);
    const exclude = new Set();
    if (controls.bb.checked) data.orgs.forEach((name, i) => { if (name.startsWith('BB –')) exclude.add(i); });
    const bins = aggregate(category, controls.org.value, exclude, controls.outcome.value);
    const lastYear = Math.floor(data.cutoff / 100);
    const lastMonth = data.cutoff % 100;
    const firstYear = Math.floor(data.rows[0][0] / 100);
    const firstDetailedYear = category.since ? Math.floor(category.since / 100) : firstYear;
    const years = Array.from({ length: lastYear - firstDetailedYear + 1 }, (_, i) => firstDetailedYear + i);
    const points = years.map(y => summarize(bins, y * 100 + 1, Math.min(y * 100 + 12, data.cutoff), category.since));
    const mode = controls.unit.value;
    const numeric = (p) => p.n ? mode === 'count' ? p.hits : p.rate * 100 : null;
    const fmtAxis = (v) => fmt.format(v) + (mode === 'rate' ? '%' : '');
    const opt = {
      responsive: true, maintainAspectRatio: false, animation: false,
      scales: { y: { beginAtZero: true, ticks: { callback: fmtAxis } } },
      plugins: { tooltip: { callbacks: { label: (ctx) => ctx.raw === null ? 'Sem cobertura' : ctx.dataset.label + ': ' + fmtAxis(ctx.raw) } } },
    };
    const formatPeriod = (y) => {
      let label = String(y);
      if (y === firstDetailedYear && category.since % 100 > 1) label += '*';
      if (y === lastYear && lastMonth < 12 && !label.endsWith('*')) label += '*';
      return label;
    };
    trend?.destroy();
    trend = new Chart(byId('explorer-trend'), {
      type: 'line',
      data: { labels: years.map(formatPeriod), datasets: [{ label: 'Série do recorte', data: points.map(numeric), borderColor: '#594174', backgroundColor: '#594174', pointRadius: 3 }] },
      options: { ...opt, plugins: { ...opt.plugins, legend: { display: false } } },
    });
    const ages = [0, 1, 2, 3];
    const mandato = (label, startYear, color) => ({
      label, backgroundColor: color, borderRadius: 3,
      data: ages.map(i => {
        const y = startYear + i, start = y * 100 + 1, end = y * 100 + lastMonth;
        if (end > data.cutoff || start < category.since) return null;
        return numeric(summarize(bins, start, end, category.since));
      }),
    });
    governments?.destroy();
    governments = new Chart(byId('explorer-gov'), {
      type: 'bar',
      data: { labels: ages.map(i => (i + 1) + 'º ano'), datasets: [
        mandato('Bolsonaro · 2019–22', 2019, '#586f81'), mandato('Lula · 2023–26', 2023, '#376d68'),
      ] },
      options: { ...opt, plugins: { ...opt.plugins, legend: { position: 'bottom' } } },
    });
    const sourceDate = data.reference.replace(/(\d{4})(\d{2})(\d{2})/, '$3/$2/$1');
    let note = 'Fala.BR, data da resposta · extração de ' + sourceDate + ' · até ' + monthLabel(lastMonth) + '/' + lastYear + '.';
    if (category.since) note += ' Campo disponível a partir de ' + monthLabel(category.since % 100) + '/' + Math.floor(category.since / 100) + '.';
    byId('explorer-status').textContent = note;
    const comparable = governments.data.datasets.every(series => series.data.some(value => value !== null));
    byId('explorer-gov-note').textContent = comparable
      ? 'Janeiro a ' + monthLabel(lastMonth) + ' de cada ano.'
      : 'Sem cobertura suficiente para comparar os dois governos neste recorte.';
    byId('explorer-method').textContent = 'Taxa sobre todas as respostas com decisão registrada. No comparativo, janeiro a ' +
      monthLabel(lastMonth) + ' de cada ano; períodos sem cobertura completa ficam em branco. * Ano ou cobertura parcial. ' +
      (category.kind === 5 ? 'O prazo é o que consta no campo estruturado. Registros sem prazo ficam fora deste motivo.' :
        category.kind === 3 ? 'Contagem baseada no detalhamento estruturado da decisão.' :
          'A categoria dados pessoais inclui restrições com diferentes prazos.');
    const table = byId('explorer-table');
    table.replaceChildren();
    [...points].reverse().forEach((p, i) => {
      const tr = table.insertRow();
      for (const value of [formatPeriod(years.at(-i - 1)), p.n ? fmt.format(p.n) : '—', p.n ? fmt.format(p.hits) : '—', p.n ? percent.format(p.rate) : '—']) {
        tr.insertCell().textContent = value;
      }
    });
  }

  fetch('./data/monitor.json').then(response => {
    if (!response.ok) throw new Error('HTTP ' + response.status);
    return response.json();
  }).then(payload => {
    data = payload;
    setUp();
    render();
  }).catch(() => { byId('explorer-status').textContent = 'Não foi possível carregar a comparação detalhada.'; });
})();
