# Lulômetro

Busca em registros de Lula (2023 em diante) e Bolsonaro (2019–2022), publicados no Planalto, na Biblioteca da Presidência e no Bluesky. O acervo é parcial: a data mais recente de cada fonte aparece na interface, mas não comprova cobertura completa.

## Arquitetura

- `index.html`: interface estática publicada no Cloudflare Pages.
- `worker/src/index.ts`: endpoints `/api/lul/stats` e `/api/lul/busca`, consultando D1.
- `scripts/build_lulometro_data.py`: coleta e normaliza os registros.
- `scripts/upsert_lulometro_to_d1.py`: envia os registros ao D1.
- `.github/workflows/lulometro.yml`: rotina diária.

O workflow gera os arquivos em `lulometro/data/` somente durante a execução. Eles não são versionados; as consultas públicas usam o D1. Se Planalto ou Biblioteca impedirem o acesso automatizado, os registros existentes permanecem no banco, mas novas publicações dessas fontes não entram no acervo.

## Verificação

Confira as datas por fonte em `/api/lul/stats` e o log do workflow. Um workflow concluído com sucesso pode ter encontrado zero URLs novas nas fontes oficiais; esse resultado exige conferência antes de afirmar que o acervo está atualizado.
