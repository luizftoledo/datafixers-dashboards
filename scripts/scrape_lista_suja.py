#!/usr/bin/env python3
"""
Lista Suja MTE — scraper.
Baixa PDF oficial, parseia em JSON e compara com snapshot anterior.
Manda alerta Telegram em novas inclusões.
"""
import re, json, sys, subprocess, urllib.request, os, hashlib, datetime
from pathlib import Path
from urllib.parse import urlencode

URL = "https://www.gov.br/trabalho-e-emprego/pt-br/assuntos/inspecao-do-trabalho/areas-de-atuacao/cadastro_de_empregadores.pdf"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "lista-suja" / "data"
OUT.mkdir(parents=True, exist_ok=True)

def download():
    req = urllib.request.Request(URL, headers={'User-Agent': 'Mozilla/5.0 datafixers-bot'})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()

def norm_cnpj(s):
    return re.sub(r'\D', '', s or '')

def parse(pdf_bytes):
    with open('/tmp/_ls.pdf', 'wb') as f: f.write(pdf_bytes)
    txt = subprocess.run(['pdftotext', '-layout', '/tmp/_ls.pdf', '-'],
                        capture_output=True, text=True).stdout
    os.remove('/tmp/_ls.pdf')

    # Junta linhas que terminam sem dados completos
    lines = txt.split('\n')
    rows = []
    rgx = re.compile(
        r'^\s*(\d+)\s+(\d{4})\s+([A-Z]{2})\s+'
        r'(.+?)\s+'
        r'(\d{2,3}\.\d{3}\.\d{3}/\d{4}-\d{2}|\d{3}\.\d{3}\.\d{3}-\d{2})\s+'
        r'(.+?)\s+'
        r'(\d+)\s+'
        r'(\d{4}-\d/\d{2})\s+'
        r'(\d{2}/\d{2}/\d{4})\s+'
        r'(\d{2}/\d{2}/\d{4})\s*$'
    )

    # Junta linha atual + próxima quando empregador é multiline
    for i, line in enumerate(lines):
        m = rgx.match(line)
        if m:
            empregador = re.sub(r'\s+', ' ', m.group(4)).strip()
            # se vazio (caso "41.297.068 GILBERTO ..."), tenta linha de cima
            if i > 0 and (not empregador or len(empregador) < 5):
                prev = lines[i-1].strip()
                if prev and not rgx.match(prev) and len(prev) > 5 and 'Cadastro' not in prev:
                    empregador = re.sub(r'\s+', ' ', prev).strip()
            rows.append({
                'id': int(m.group(1)),
                'ano': int(m.group(2)),
                'uf': m.group(3),
                'empregador': empregador,
                'cpf_cnpj_raw': m.group(5),
                'cpf_cnpj': norm_cnpj(m.group(5)),
                'estabelecimento': re.sub(r'\s+', ' ', m.group(6)).strip(),
                'trabalhadores': int(m.group(7)),
                'cnae': m.group(8),
                'decisao_administrativa': m.group(9),
                'incluido_em': m.group(10),
            })
    return rows

def send_telegram(text):
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat = os.environ.get('TELEGRAM_CHAT_ID')
    if not token or not chat:
        print('[skip] TELEGRAM secrets ausentes')
        return
    body = urlencode({
        'chat_id': chat,
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': 'true',
    }).encode()
    req = urllib.request.Request(
        f'https://api.telegram.org/bot{token}/sendMessage',
        data=body, method='POST'
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        print('Telegram:', r.status)

def main():
    pdf = download()
    sha = hashlib.sha256(pdf).hexdigest()
    rows = parse(pdf)
    print(f"PDF: {len(pdf)} bytes  | parsed: {len(rows)} empregadores")

    # Snapshot anterior
    snap_file = OUT / "empregadores.json"
    prev_cnpjs = set()
    if snap_file.exists():
        prev = json.loads(snap_file.read_text())
        prev_cnpjs = set(e['cpf_cnpj'] for e in prev.get('empregadores', []))
        print(f"Anterior: {len(prev_cnpjs)} empregadores")

    # Inclusões novas
    cur_cnpjs = set(e['cpf_cnpj'] for e in rows)
    novos = [e for e in rows if e['cpf_cnpj'] and e['cpf_cnpj'] not in prev_cnpjs]
    print(f"Inclusões novas: {len(novos)}")

    # Salva snapshot atualizado
    snap = {
        'extracted_at': datetime.datetime.utcnow().isoformat(),
        'pdf_sha256': sha,
        'pdf_url': URL,
        'count': len(rows),
        'empregadores': rows,
    }
    snap_file.write_text(json.dumps(snap, ensure_ascii=False, indent=2))

    # Salva diff se houver novos
    if novos:
        diff_file = OUT / f"diff_{datetime.date.today().isoformat()}.json"
        diff_file.write_text(json.dumps(novos, ensure_ascii=False, indent=2))

    # Telegram
    if novos and prev_cnpjs:  # Só notifica se já tinha snapshot anterior
        lines = [f'<b>🚨 Lista Suja MTE · {len(novos)} novo(s) empregador(es)</b>', '']
        for e in novos[:20]:
            lines.append(f"<b>{e['empregador'][:80]}</b>")
            lines.append(f"  {e['uf']} · {e['cpf_cnpj_raw']} · {e['trabalhadores']} trab.")
            lines.append(f"  {e['estabelecimento'][:80]}")
            lines.append(f"  Incluído em {e['incluido_em']}")
            lines.append('')
        if len(novos) > 20:
            lines.append(f'... +{len(novos)-20} mais')
        lines.append(f'→ <a href="https://dashboards.datafixers.org/lista-suja/">Ver lista completa</a>')
        send_telegram('\n'.join(lines))

if __name__ == '__main__':
    main()
