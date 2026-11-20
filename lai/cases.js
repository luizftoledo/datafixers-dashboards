(() => {
  const topics = {
    advogado: ['Sigilo profissional do advogado', 'Pedidos em que o órgão registrou esse fundamento na resposta.', /sigilo profissional do advogado/i],
    preparatorio: ['Documentos preparatórios', 'O recorte usa o motivo “processo decisório em curso” registrado pelo órgão. Cada pedido permite conferir se a restrição envolvia um documento preparatório.', /processo decisório em curso/i],
    classificada: ['Informação classificada', 'Pedidos com restrição registrada com base nos artigos 23 e 24 da LAI.', /classificada conforme artigos 23 e 24/i],
    'cem-anos': ['Prazo registrado de 100 anos', 'Pedidos envolvendo informações pessoais em que o órgão registrou expressamente o prazo de 100 anos.', /prazo informado de 100 anos/i],
  };
  const node = (id) => document.getElementById(id);
  const [org, year, decision] = ['case-org', 'case-year', 'case-decision'].map(node);
  const previous = node('case-prev'), next = node('case-next');
  const pageSize = 12, format = new Intl.NumberFormat('pt-BR');
  let data, current = 'advogado', page = 0;
  function dateLabel(iso) { return iso.split('-').reverse().join('/'); }
  function options(select, values, defaultLabel) {
    select.replaceChildren(new Option(defaultLabel, ''), ...values.map(value => new Option(value, value)));
  }
  function showCases() {
    const [title, desc] = topics[current];
    node('case-title').textContent = title;
    node('case-note').textContent = desc;
    if (!data) return;
    const rows = data.rows.filter(([date, name, status, , , kinds]) => kinds.includes(current) &&
      (!org.value || org.value === name) && (!year.value || year.value === date.slice(0, 4)) &&
      (!decision.value || decision.value === status));
    const pages = Math.max(1, Math.ceil(rows.length / pageSize));
    const table = node('case-rows');
    table.replaceChildren();
    for (const [date, name, status, protocol, id] of rows.slice(page * pageSize, (page + 1) * pageSize)) {
      const row = table.insertRow();
      for (const value of [dateLabel(date), name, status]) row.insertCell().textContent = value;
      const link = document.createElement('a');
      link.href = 'https://buscalai.cgu.gov.br/busca/' + encodeURIComponent(id);
      link.textContent = protocol;
      link.setAttribute('aria-label', 'Abrir pedido ' + protocol + ' no BuscaLAI');
      row.insertCell().append(link);
    }
    node('case-status').textContent = (rows.length ? format.format(rows.length) + (rows.length === 1 ? ' pedido' : ' pedidos') : 'Nenhum pedido') +
      ' na base pública do BuscaLAI · arquivos de ' + data.registration_years.join(' e ') + ' · extração mais recente em ' + dateLabel(data.reference) + '.';
    node('case-page').textContent = rows.length ? format.format(page + 1) + ' de ' + format.format(pages) : '';
    previous.disabled = page === 0;
    next.disabled = page + 1 >= pages;
  }
  function chooseTopic() {
    page = 0;
    if (data) {
      const relevant = data.rows.filter(row => row[5].includes(current));
      options(org, [...new Set(relevant.map(row => row[1]))].sort((a, b) => a.localeCompare(b, 'pt-BR')), 'Todos os órgãos');
      options(year, [...new Set(relevant.map(row => row[0].slice(0, 4)))], 'Todos os anos');
      decision.value = '';
    }
    showCases();
  }
  const topicButtons = [...document.querySelectorAll('[data-case]')];
  for (const button of topicButtons) button.addEventListener('click', () => {
    current = button.dataset.case;
    for (const candidate of topicButtons) candidate.setAttribute('aria-pressed', String(candidate === button));
    chooseTopic();
  });
  node('case-chart-link').addEventListener('click', () => {
    const reason = node('explorer-reason');
    const option = [...reason.options].find(item => topics[current][2].test(item.textContent));
    if (!option) return;
    reason.value = option.value;
    reason.dispatchEvent(new Event('change'));
  });
  for (const filter of [org, year, decision]) filter.addEventListener('change', () => { page = 0; showCases(); });
  previous.addEventListener('click', () => { page--; showCases(); });
  next.addEventListener('click', () => { page++; showCases(); });
  chooseTopic();
  fetch('./data/public-cases.json').then(response => {
    if (!response.ok) throw new Error('HTTP ' + response.status);
    return response.json();
  }).then(payload => {
    data = payload;
    for (const filter of [org, year, decision]) filter.disabled = false;
    chooseTopic();
  }).catch(() => { node('case-status').textContent = 'Não foi possível carregar os pedidos públicos.'; });
})();
