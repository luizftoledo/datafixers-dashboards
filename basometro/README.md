# Basometro Dashboard

Painel de apoio ao governo na Câmara, inspirado no Basômetro do Estadão.

## Arquivos

- `index.html`: página do painel.
- `app.js`: renderização dos gráficos e tabelas.
- `data/report_data.json`: dados processados para a visualização.
- `data/metadata.json`: metadados de atualização.

## Como atualizar localmente

```bash
python3 scripts/build_basometro_dashboard_data.py
```

Opções úteis:

```bash
python3 scripts/build_basometro_dashboard_data.py --start-year 2019 --end-year 2026
```

## Fontes

- [Arquivos anuais da Câmara dos Deputados](https://dadosabertos.camara.leg.br/arquivos/)
  - `votacoes`
  - `votacoesVotos`
  - `votacoesOrientacoes`
- [Basômetro original (Estadão)](https://github.com/estadao/basometro)
- [Referência visual do Basômetro](https://arte.estadao.com.br/politica/basometro/)
