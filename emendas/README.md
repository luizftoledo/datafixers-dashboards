# Emendas Dashboard (GitHub Pages)

Painel diário para monitorar variações de **valor empenhado** em emendas parlamentares.

Agora com duas leituras:

- `Monitor UNICO (snapshot)`: compara snapshots consecutivos da base consolidada.
- `Aba paralela (documentos + apoiamento)`: série diária desde 1º de janeiro do ano corrente, com `emendas-parlamentares-documentos` e `apoiamento-emendas-parlamentares-documentos`.

## Arquivos principais

- `index.html`: interface com gráficos e tabelas.
- `app.js`: renderização no cliente.
- `data/report_data.json`: resumo consolidado para a página.
- `data/metadata.json`: metadados de atualização.
- `data/daily_history.json`: série histórica do monitoramento diário.
- `data/state/latest_aggregates.json.gz`: estado agregado para cálculo do delta diário.
- `scripts/build_emendas_dashboard_data.py`: integra as fontes `UNICO`, `documentos` e `apoiamento`.

## Atualização diária automática

Workflow: `.github/workflows/update-emendas-dashboard.yml`

- Agenda: 10:10 (horario de Brasilia), equivalente a `13:10 UTC`.
- Baixa a base oficial de emendas.
- Baixa também a base anual de documentos e a base de apoiamento.
- Compara com o último snapshot.
- Recalcula deltas por autor e destino.
- Atualiza os arquivos em `emendas-dashboard/data`.
- Faz commit automático apenas quando houver mudança.

## Build manual local

```bash
cd <seu-repositorio>
python3 scripts/build_emendas_dashboard_data.py
```

Forçar rebuild mesmo sem mudança de `etag/last-modified`:

```bash
python3 scripts/build_emendas_dashboard_data.py --force
```
