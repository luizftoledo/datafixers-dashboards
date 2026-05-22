# Lulometro (GitHub Pages)

Dashboard para comparar discursos e entrevistas de presidentes brasileiros por palavra-chave.

Escopo atual do acervo publicado: apenas `Lula 3 (2023-)` e `Bolsonaro (2019-2022)`.

## Entregas

- `index.html`: interface principal do dashboard.
- `app.js`: busca, filtros, gráficos e tabela.
- `data/records.jsonl.gz`: base completa com textos integrais.
- `data/items.json`: tabela de metadados para consumo rápido.
- `data/super_tabela.csv`: exportação tabular (data, nome, local, link, etc.).
- `data/metadata.json`: status da atualização diária.
- `data/sources.json`: diagnóstico de fontes e rastreamento.

## Atualização diária

Workflow: `.github/workflows/update-lulometro-dashboard.yml`

Pipeline:

1. Rastreia listagens do Planalto (`entrevistas`, `discursos-e-pronunciamentos`).
2. Rastreia ex-presidentes na Biblioteca da Presidência.
3. Atualiza incrementalmente apenas URLs novas/incompletas.
4. Regrava os arquivos em `lulometro-dashboard/data`.
5. Faz commit automático somente quando há mudança.

## Build local

```bash
cd <repo>
python3 scripts/build_lulometro_data.py
```

Modo debug (limita novos fetches):

```bash
python3 scripts/build_lulometro_data.py --max-new-details 100
```

Modo em lotes (útil para recuperar base grande sem perder progresso):

```bash
python3 scripts/build_lulometro_data.py --skip-crawl --max-details 2000
```

Evitar insistir em URLs que já falharam várias vezes:

```bash
python3 scripts/build_lulometro_data.py --max-failures 3
```

Forçar refetch de detalhes:

```bash
python3 scripts/build_lulometro_data.py --force-details
```
