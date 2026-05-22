#!/usr/bin/env python3
"""
Scraper dos Relatórios de Fiscalização de Trabalho Escravo do MTE.
Extrai títulos REAIS do parágrafo (não do anchor "clique aqui").
"""
import re, json, urllib.request, datetime, os
from pathlib import Path

ROOT_PAGE = "https://www.gov.br/trabalho-e-emprego/pt-br/assuntos/inspecao-do-trabalho/areas-de-atuacao/copy_of_combate-ao-trabalho-escravo-e-analogo-ao-de-escravo"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "relatorios-trabalho-escravo" / "data"
OUT.mkdir(parents=True, exist_ok=True)

def strip_tags(s):
    s = re.sub(r'<[^>]+>', '', s)
    s = re.sub(r'&nbsp;', ' ', s)
    s = re.sub(r'&amp;', '&', s)
    s = re.sub(r'&aacute;', 'á', s)
    s = re.sub(r'\s+', ' ', s)
    return s.strip()

def extract_meta(text, url):
    """Extrai ano, op_num, UFs"""
    # Operação específica: "Op. 01 de 1999"
    op_match = re.search(r'Op\.?\s*(\d+)\s+de\s+(\d{4})', text, re.IGNORECASE)
    if op_match:
        return {
            'ano': int(op_match.group(2)),
            'op_num': int(op_match.group(1)),
            'tipo': 'operacao_especifica',
            'ufs': re.findall(r'\b(AC|AL|AP|AM|BA|CE|DF|ES|GO|MA|MT|MS|MG|PA|PB|PR|PE|PI|RJ|RN|RS|RO|RR|SC|SP|SE|TO)\b', text),
        }
    # Consolidado anual: "Operações 2025"
    cons_match = re.search(r'Opera[çc][õo]es\s+(\d{4})', text, re.IGNORECASE)
    if cons_match:
        return {
            'ano': int(cons_match.group(1)),
            'op_num': None,
            'tipo': 'consolidado_anual',
            'ufs': [],
        }
    # Tenta achar ano em qualquer lugar
    ano_match = re.search(r'\b(19|20)(\d{2})\b', text)
    return {
        'ano': int(ano_match.group(0)) if ano_match else None,
        'op_num': None,
        'tipo': 'outro',
        'ufs': re.findall(r'\b(AC|AL|AP|AM|BA|CE|DF|ES|GO|MA|MT|MS|MG|PA|PB|PR|PE|PI|RJ|RN|RS|RO|RR|SC|SP|SE|TO)\b', text),
    }

def main():
    req = urllib.request.Request(ROOT_PAGE, headers={'User-Agent':'Mozilla/5.0 datafixers-bot'})
    with urllib.request.urlopen(req, timeout=60) as r:
        html = r.read().decode('utf-8', errors='replace')

    # Encontra TODOS os <p> que contém um <a> com link de relatório
    # E pareia com possível <p class="callout"> ANTERIOR (que tem o título do consolidado)
    paragraphs = re.findall(r'<p[^>]*>(.*?)</p>', html, re.DOTALL)

    relatorios = []
    pending_callout = None

    for p_inner in paragraphs:
        # É um callout (título de consolidado)?
        if 'class="callout"' in '' or 'callout' in p_inner[:50]:
            pass
        text_clean = strip_tags(p_inner)

        # Detecta se é callout só de título "Relatório YYYY"
        if re.search(r'^Relat[óo]rios?\s+\d{4}\s*$', text_clean, re.IGNORECASE):
            pending_callout = text_clean
            continue

        # Tem link de relatório?
        a_match = re.search(r'<a[^>]+href="([^"]+)"[^>]*>([^<]*?)</a>', p_inner)
        if not a_match: continue
        url = a_match.group(1)
        anchor_text = a_match.group(2)

        # Filtra só relatórios de trabalho escravo
        if not re.search(r'(operacoe?s?|relatorios?[\s_-]op|/op-?\d|areas-de-atuacao/op[-_])', url, re.IGNORECASE):
            continue
        # Skip nav/menu
        if any(s in url for s in ['#', 'mailto:', 'javascript:']): continue

        # URL absoluta
        if url.startswith('/'):
            url = 'https://www.gov.br' + url

        # Limpa o texto: remove "(clique aqui)" e similares
        clean = re.sub(r'\(?clique\s+aqui\)?\.?', '', text_clean, flags=re.IGNORECASE)
        clean = re.sub(r'^\s*[-–•]\s*', '', clean)
        clean = re.sub(r';?\s*$', '', clean)
        clean = re.sub(r'\s+', ' ', clean).strip()

        # Se for consolidado, junta com o callout pendente
        meta = extract_meta(clean, url)
        titulo_completo = clean
        if meta['tipo'] == 'consolidado_anual' and pending_callout:
            titulo_completo = f"{pending_callout} — {clean}"
            pending_callout = None

        relatorios.append({
            'titulo': titulo_completo,
            'titulo_curto': clean,
            'url': url,
            **meta,
        })

    # Dedupe
    seen = set()
    final = []
    for r in relatorios:
        if r['url'] not in seen:
            seen.add(r['url'])
            final.append(r)

    # Ordena: consolidados (mais recentes primeiro), depois operações (mais recentes primeiro)
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
    print(f"Total: {out['total']} | Consolidados: {len(out['consolidados'])} | Op. específicas: {len(out['operacoes'])} | Outros: {len(out['outros'])}")
    print()
    print("Amostra consolidados:")
    for r in out['consolidados'][:5]:
        print(f"  • {r['titulo_curto']} ({r['ano']})")
    print()
    print("Amostra operações:")
    for r in out['operacoes'][:5]:
        ufs = '/'.join(r.get('ufs',[]))
        print(f"  • {r['titulo_curto'][:80]} [{ufs}]")

if __name__ == '__main__':
    main()
