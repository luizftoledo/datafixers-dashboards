const profiles = {
  lula: { vote: 'lula', denominator: 'president_valid', author: null, uf: '', label: 'Lula', caveat: 'Lula disputou a Presidência em 2022. O valor mostrado soma emendas de todos os autores com município de aplicação identificado; não atribui a ele a autoria.' },
  flavio: { vote: 'jair', denominator: 'president_valid', author: 'flavio', uf: 'RJ', label: 'Jair Bolsonaro (contexto)', caveat: 'Flávio Bolsonaro não concorreu em 2022. A votação exibida é de Jair Bolsonaro para presidente, no segundo turno, e não pode ser tratada como voto de Flávio.' },
  hugo: { vote: 'hugo', denominator: 'deputy_valid', author: 'hugo', uf: 'PB', label: 'Hugo Motta', caveat: 'Votos de Hugo Motta para deputado federal no 1º turno de 2022 versus documentos de 2026 com autoria atribuída a ele pela CGU.' },
  davi: { vote: 'davi', denominator: 'senate_valid', author: 'davi', uf: 'AP', label: 'Davi Alcolumbre', caveat: 'Votos de Davi Alcolumbre para senador no 1º turno de 2022 versus documentos de 2026 com autoria atribuída a ele pela CGU.' }
};
const $ = id => document.getElementById(id);
const real = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL', maximumFractionDigits: 0 });
const integer = new Intl.NumberFormat('pt-BR');
const decimal = new Intl.NumberFormat('pt-BR', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
let data, chart;

function amount(city, profile, metric) {
  return profile.author ? (city.authors[profile.author]?.[metric] || 0) : city[metric];
}
function voteShare(city, profile) {
  const denominator = city.votes[profile.denominator] || 0;
  return denominator ? 100 * (city.votes[profile.vote] || 0) / denominator : null;
}
function render() {
  if (!data) return;
  const choice = $('profile').value, profile = profiles[choice], metric = $('metric').value;
  const uf = $('uf').value, search = $('search').value.trim().toLocaleUpperCase('pt-BR');
  const cities = data.cities.filter(city => (!uf || city.uf === uf) && (!search || city.name.toLocaleUpperCase('pt-BR').includes(search)));
  const ranked = cities.map(city => ({ city, value: amount(city, profile, metric), share: voteShare(city, profile) })).filter(item => item.value > 0).sort((a, b) => b.value - a.value);
  const total = ranked.reduce((sum, item) => sum + item.value, 0);
  const votes = cities.reduce((sum, city) => sum + (city.votes[profile.vote] || 0), 0);
  $('caveat').textContent = profile.caveat;
  $('kpi-money').textContent = real.format(total);
  $('kpi-money-label').textContent = `Emendas ${metric === 'paid' ? 'pagas' : 'empenhadas'} nas cidades filtradas`;
  $('kpi-cities').textContent = integer.format(ranked.length);
  $('kpi-vote').textContent = integer.format(votes);
  $('kpi-vote-label').textContent = `Votos de ${profile.label} nas cidades filtradas`;
  $('table-sub').textContent = `Maiores valores ${metric === 'paid' ? 'pagos' : 'empenhados'} em 2026 no recorte selecionado. A porcentagem eleitoral usa todos os votos nominais válidos para o cargo no município.`;

  const tbody = $('rows'); tbody.replaceChildren();
  for (const { city, value, share } of ranked.slice(0, 25)) {
    const row = document.createElement('tr');
    for (const content of [`${city.name} · ${city.uf}`, real.format(value), integer.format(city.votes[profile.vote] || 0), share === null ? '—' : `${decimal.format(share)}%`]) {
      const cell = document.createElement('td'); cell.textContent = content; row.append(cell);
    }
    tbody.append(row);
  }
  if (!ranked.length) {
    const row = document.createElement('tr'), cell = document.createElement('td');
    cell.colSpan = 4; cell.textContent = 'Nenhum município com valor positivo para este filtro.'; row.append(cell); tbody.append(row);
  }
  const top = ranked[0];
  $('insight').replaceChildren();
  if (top) {
    const heading = document.createElement('h3'); heading.textContent = `${top.city.name} (${top.city.uf})`;
    const paragraph = document.createElement('p'); paragraph.textContent = `Lidera o recorte com ${real.format(top.value)} em emendas ${metric === 'paid' ? 'pagas' : 'empenhadas'} em 2026. ${profile.label} recebeu ${integer.format(top.city.votes[profile.vote] || 0)} votos ali em 2022${top.share === null ? '.' : ` (${decimal.format(top.share)}% dos válidos para o cargo).`}`;
    $('insight').append(heading, paragraph);
  }
  const points = ranked.filter(item => item.share !== null).map(item => ({ x: item.share, y: item.value / 1e6, city: `${item.city.name} (${item.city.uf})`, value: item.value, votes: item.city.votes[profile.vote] || 0 }));
  if (chart) chart.destroy();
  chart = new Chart($('chart'), {
    type: 'scatter', data: { datasets: [{ data: points, backgroundColor: 'rgba(23,76,91,.38)', borderColor: '#174c5b', pointRadius: 3, pointHoverRadius: 6 }] },
    options: { maintainAspectRatio: false, animation: false, plugins: { legend: { display: false }, tooltip: { callbacks: { title: items => items[0]?.raw.city || '', label: item => `${decimal.format(item.raw.x)}% dos votos · ${real.format(item.raw.value)} em emendas` } } }, scales: { x: { min: 0, max: 100, title: { display: true, text: '% dos votos válidos em 2022' } }, y: { type: 'logarithmic', title: { display: true, text: 'R$ milhões em 2026 (escala log)' }, ticks: { callback: value => value >= .01 ? value : '' } } } }
  });
}

fetch('/emendas/data/election_2022.json').then(response => {
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}).then(result => {
  data = result;
  const states = [...new Set(data.cities.map(city => city.uf))].filter(uf => /^[A-Z]{2}$/.test(uf)).sort();
  for (const uf of states) { const option = document.createElement('option'); option.value = option.textContent = uf; $('uf').append(option); }
  $('update').textContent = `Documentos da CGU até ${data.document_date_max}; agregação gerada em ${new Date(data.generated_at).toLocaleDateString('pt-BR', { timeZone: 'UTC' })}.`;
  $('source-votes').href = data.sources.votes; $('source-map').href = data.sources.municipality_mapping; $('source-cgu').href = data.sources.emendas;
  const cover = data.coverage;
  $('coverage').textContent = `Cobertura municipal: ${real.format(cover.localized_paid)} pagos com código IBGE, de ${real.format(cover.paid)} pagos no arquivo de documentos (${decimal.format(100 * cover.localized_paid / cover.paid)}%). Os demais valores não entram no ranking de cidades. Há ${integer.format(cover.localized_rows)} documentos com município identificado, de ${integer.format(cover.rows)} documentos de 2026.`;
  $('profile').addEventListener('change', () => { $('uf').value = profiles[$('profile').value].uf; render(); });
  for (const id of ['metric', 'uf', 'search']) $(id).addEventListener(id === 'search' ? 'input' : 'change', render);
  render();
}).catch(error => { $('update').textContent = `Falha ao carregar os dados: ${error.message}`; });
