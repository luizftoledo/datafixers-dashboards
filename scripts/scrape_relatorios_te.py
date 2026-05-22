#!/usr/bin/env python3
"""
Scraper dos Relatórios de Fiscalização de Trabalho Escravo do MTE.
Página: gov.br/trabalho-e-emprego/.../combate-ao-trabalho-escravo-e-analogo-ao-de-escravo

Extrai metadata de cada link (ano, fazenda, UF) e salva em JSON.
Roda mensalmente via GitHub Action.
"""
import re, json, urllib.request, datetime, os
from html.parser import HTMLParser
from pathlib import Path

ROOT_PAGE = "https://www.gov.br/trabalho-e-emprego/pt-br/assuntos/inspecao-do-trabalho/areas-de-atuacao/copy_of_combate-ao-trabalho-escravo-e-analogo-ao-de-escravo"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "relatorios-trabalho-escravo" / "data"
OUT.mkdir(parents=True, exist_ok=True)

class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.current_href = None
        self.current_text = []
    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            href = dict(attrs).get('href', '')
            self.current_href = href
            self.current_text = []
    def handle_data(self, data):
        if self.current_href is not None:
            self.current_text.append(data)
    def handle_endtag(self, tag):
        if tag == 'a' and self.current_href:
            text = ' '.join(self.current_text).strip()
            text = re.sub(r'\s+', ' ', text)
            if text and self.current_href:
                self.links.append({'url': self.current_href, 'text': text})
            self.current_href = None
            self.current_text = []

def extract_meta(url, text):
    """Extrai ano, UF e nome da operação do título"""
    # operação ano (op-NN-de-YYYY)
    op_match = re.search(r'op[\s\-_]*(\d+)[\s\-_]+de[\s\-_]+(\d{4})', url + ' ' + text, re.IGNORECASE)
    relatorio_match = re.search(r'opera[cç][ãa]o(?:es)?[\s\-_]+(\d{4})|relatorios?[\s\-_]+op[\s\-_]+(\d{4})', url, re.IGNORECASE)
    # UF (sigla de 2 letras no fim/título)
    ufs_match = re.findall(r'\b(AC|AL|AP|AM|BA|CE|DF|ES|GO|MA|MT|MS|MG|PA|PB|PR|PE|PI|RJ|RN|RS|RO|RR|SC|SP|SE|TO)\b', text)
    # tipo
    if relatorio_match:
        ano = int(relatorio_match.group(1) or relatorio_match.group(2))
        tipo = 'consolidado_anual'
        op_num = None
    elif op_match:
        ano = int(op_match.group(2))
        op_num = int(op_match.group(1))
        tipo = 'operacao_especifica'
    else:
        ano = None
        op_num = None
        tipo = 'outro'
    return {
        'ano': ano,
        'op_num': op_num,
        'ufs': list(set(ufs_match)) if ufs_match else [],
        'tipo': tipo,
    }

def main():
    req = urllib.request.Request(ROOT_PAGE, headers={'User-Agent':'Mozilla/5.0 datafixers-bot'})
    with urllib.request.urlopen(req, timeout=60) as r:
        html = r.read().decode('utf-8', errors='replace')

    parser = LinkParser()
    parser.feed(html)

    relatorios = []
    for link in parser.links:
        url = link['url']
        text = link['text']
        # filtra só relatórios de trabalho escravo
        if not re.search(r'(operacoes?|operacoe|relatorios?[\s_-]op|op[\s_-]\d|areas-de-atuacao/op-)', url, re.IGNORECASE):
            continue
        if any(s in url for s in ['combate-ao-trabalho-escravo', '#', 'ainda-nao-vinculado']):
            continue
        # URL absoluta
        if url.startswith('/'):
            url = 'https://www.gov.br' + url

        meta = extract_meta(url, text)
        # Extrai "nome principal" do título — remove "Op. NN de YYYY -"
        clean = re.sub(r'^op[\s\.\-]*\d+[\s\-]+de[\s\-]+\d{4}[\s\-]+', '', text, flags=re.IGNORECASE).strip()
        clean = re.sub(r'^Relatório\s+\d{4}\s*\-\s*', '', clean).strip()

        relatorios.append({
            'titulo': text,
            'titulo_curto': clean,
            'url': url,
            **meta,
        })

    # Dedupe por URL
    seen = set()
    final = []
    for r in relatorios:
        if r['url'] not in seen:
            seen.add(r['url'])
            final.append(r)

    # Ordena: consolidados primeiro (mais recentes primeiro), depois operações específicas
    final.sort(key=lambda r: (
        0 if r['tipo']=='consolidado_anual' else 1,
        -(r['ano'] or 0),
        r['op_num'] or 0,
    ))

    out = {
        'extracted_at': datetime.datetime.utcnow().isoformat(),
        'source_url': ROOT_PAGE,
        'total': len(final),
        'consolidados': [r for r in final if r['tipo']=='consolidado_anual'],
        'operacoes': [r for r in final if r['tipo']=='operacao_especifica'],
        'outros': [r for r in final if r['tipo']=='outro'],
    }

    (OUT / 'relatorios.json').write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"Total: {out['total']} | Consolidados: {len(out['consolidados'])} | Op. específicas: {len(out['operacoes'])}")

if __name__ == '__main__':
    main()
