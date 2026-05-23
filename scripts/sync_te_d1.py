#!/usr/bin/env python3
"""
Sincroniza relatorios_ocr.json → tabela te_docs no D1.
Re-roda quando processa_te_pdfs.py terminar pra adicionar text_ocr também.
"""
import json, subprocess, os
from pathlib import Path

F = Path("/Users/luizfernandotoledo/Desktop/Code_folder/cursor_testes/datafixers-dashboards/relatorios-trabalho-escravo/data/relatorios_ocr.json")
if not F.exists():
    print("relatorios_ocr.json não existe ainda — espere o batch terminar")
    exit(1)

data = json.loads(F.read_text())
rows = data['relatorios']
print(f"Total: {len(rows)} rows pra sincronizar")

# Gera batches SQL (1 row por arquivo, com texto truncado)
OUT_DIR = "/tmp/te_d1"
os.makedirs(OUT_DIR, exist_ok=True)
for f in os.listdir(OUT_DIR):
    os.remove(os.path.join(OUT_DIR, f))

def esc(v):
    if v is None: return 'NULL'
    return "'" + str(v).replace("'", "''") + "'"

def to_int(v):
    if v is None or v == '': return 'NULL'
    try: return str(int(v))
    except: return 'NULL'

n = 0
for r in rows:
    dados = r.get('dados') or {}
    url = r.get('url', '')
    if not url: continue
    titulo = r.get('titulo', '')
    ano = r.get('ano')
    op_num = r.get('op_num')
    ufs = ','.join(r.get('ufs', []) or [])
    empresa = dados.get('empresa', '')
    cnpj = dados.get('cnpj_cpf', '')
    trab = dados.get('trabalhadores_resgatados', 0)
    tipo = dados.get('tipo_trabalho', '')
    data_fatos = dados.get('data_fatos', '')
    municipio = dados.get('municipio', '')
    resumo = r.get('resumo') or dados.get('resumo', '')
    text_ocr = (r.get('text_ocr') or '')[:14000]  # limita

    # Skip rows que têm placeholder de prompt (Llama copiou prompt)
    if empresa and 'nome da empresa' in empresa.lower(): empresa = ''
    if isinstance(cnpj, str) and 'cnpj' in cnpj.lower() and 'aparec' in cnpj.lower(): cnpj = None

    sql = f"""INSERT OR REPLACE INTO te_docs (url, titulo, ano, op_num, ufs, empresa, cnpj_cpf, trabalhadores_resgatados, tipo_trabalho, data_fatos, municipio, resumo, text_ocr, updated_at) VALUES ({esc(url)}, {esc(titulo)}, {to_int(ano)}, {to_int(op_num)}, {esc(ufs)}, {esc(empresa)}, {esc(cnpj)}, {to_int(trab)}, {esc(tipo)}, {esc(data_fatos)}, {esc(municipio)}, {esc(resumo)}, {esc(text_ocr)}, {esc(__import__('datetime').datetime.utcnow().isoformat())});
"""
    with open(f"{OUT_DIR}/r_{n:05d}.sql", 'w') as f:
        f.write(sql)
    n += 1

print(f"Gerados {n} arquivos SQL em {OUT_DIR}")
print()
print("Pra importar, rode:")
print(f"  ls {OUT_DIR}/r_*.sql | xargs -P 4 -n 1 -I FILE wrangler d1 execute ibama --remote --file=FILE")
print()
print("Depois reconstrói FTS:")
print("  wrangler d1 execute ibama --remote --command=\"DELETE FROM te_fts; INSERT INTO te_fts(rowid, url, titulo, empresa, resumo, text_ocr) SELECT rowid, url, titulo, empresa, resumo, text_ocr FROM te_docs\"")
