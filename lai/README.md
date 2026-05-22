# LAI Dashboard (GitHub Pages)

Painel estático com série histórica de pedidos LAI no Executivo Federal, com foco em negativas, motivos, órgãos e informação pessoal.

## Arquivos principais

- `lai-dashboard/index.html`: interface do painel e chat IA no rodapé.
- `lai-dashboard/app.js`: gráficos, tabelas, narrativa e integração Gemini.
- `lai-dashboard/data/report_data.json`: base agregada para visualização.
- `lai-dashboard/data/metadata.json`: carimbo de atualização e cobertura.
- `lai-dashboard/data/cache/yearly/*.json`: cache anual incremental.

## Build local

```bash
cd <seu-repositorio>
python3 scripts/build_lai_dashboard_data.py
```

Forçar reprocessamento completo:

```bash
python3 scripts/build_lai_dashboard_data.py --force
```

## Automação mensal (GitHub Actions)

Workflow: `.github/workflows/update-lai-dashboard.yml`

- Agenda mensal: dia 1 às 10:40 (horario de Brasilia), equivalente a `13:40 UTC`.
- Atualização incremental:
  - anos antigos usam cache anual já salvo em `lai-dashboard/data/cache/yearly/`;
  - ano atual é baixado e reprocessado a cada execução.
- Commit automático apenas quando houver mudança em `lai-dashboard/data`.

## Gemini no rodapé

- O campo de API key salva apenas no `localStorage` do navegador.
- O chat usa o contexto dos dados da página para explicar negativas e sugerir melhoria de pedidos.
- Para precedentes oficiais de recurso (CGU/CMRI):
  `https://www.gov.br/cgu/pt-br/acesso-a-informacao/dados-abertos/arquivos/busca-de-precedentes`
