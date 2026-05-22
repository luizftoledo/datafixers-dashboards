#!/usr/bin/env python3
"""
Scraper Relatórios Trabalho Escravo MTE — v2.
Entra em cada página índice (operacoes-2025, etc) e extrai PDFs individuais.
"""
import re, json, urllib.request, datetime
from pathlib import Path

ROOT_PAGE = "https://www.gov.br/trabalho-e-emprego/pt-br/assuntos/inspecao-do-trabalho/areas-de-atuacao/copy_of_combate-ao-trabalho-escravo-e-analogo-ao-de-escravo"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "relatorios-trabalho-escravo" / "data"
OUT.mkdir(parents=True, exist_ok=True)
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15'

def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode('utf-8', errors='replace')

def strip_tags(s):
    s = re.sub(r'<[^>]+>', '', s)
    s = re.sub(r'&nbsp;', ' ', s)
    s = re.sub(r'&aacute;', 'á', s).replace('&amp;','&')
    return re.sub(r'\s+',' ', s).strip()

def parse_meta(text, url):
    text_full = text + ' ' + url
    op_match = re.search(r'Op[\s\.\-]*(\d+)[\s\.\-]+de[\s\-]+(\d{4})', text_full, re.IGNORECASE)
    ufs = list(set(re.findall(r'\b(AC|AL|AP|AM|BA|CE|DF|ES|GO|MA|MT|MS|MG|PA|PB|PR|PE|PI|RJ|RN|RS|RO|RR|SC|SP|SE|TO)\b', text)))
    if op_match:
        return {'ano': int(op_match.group(2)), 'op_num': int(op_match.group(1)),
                'tipo': 'operacao_especifica', 'ufs': ufs}
    # Fallback ano no URL
    ano_match = re.search(r'(?:operacoe?s?|relatorios?[\s\-_]op)[\s\-_]+?(\d{4})', url, re.IGNORECASE)
    if ano_match:
        return {'ano': int(ano_match.group(1)), 'op_num': None,
                'tipo': 'consolidado_anual', 'ufs': ufs}
    return {'ano': None, 'op_num': None, 'tipo': 'outro', 'ufs': ufs}

def main():
    main_html = fetch(ROOT_PAGE)
    # Pega links da página principal (que apontam pra páginas índice ou PDFs diretos)
    main_links = re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(?:[^<]|<br/?>)*</a>', main_html)

    # 1) Encontra páginas índice e PDFs diretos
    indices = []  # URLs de páginas operacoes-YYYY
    pdfs_diretos = []
    paragraphs = re.findall(r'<p[^>]*>(.*?)</p>', main_html, re.DOTALL)

    for p in paragraphs:
        for m in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', p):
            url = m.group(1)
            if '/operacoe' in url or '/relatorios-op-' in url:
                # Página índice
                if url.endswith('.pdf'):
                    pdfs_diretos.append({'url': url, 'page_text': strip_tags(p)})
                else:
                    indices.append(url)
            elif '/op-' in url and url.endswith('.pdf'):
                pdfs_diretos.append({'url': url, 'page_text': strip_tags(p)})

    indices = list(set(indices))
    print(f"Páginas índice encontradas: {len(indices)}")
    print(f"PDFs diretos na página principal: {len(pdfs_diretos)}")

    # 2) Entra em cada índice e extrai PDFs individuais
    all_pdfs = []

    # PDFs diretos da página principal (relatórios antigos 1995-1999)
    for p in pdfs_diretos:
        url = p['url']
        meta = parse_meta(p['page_text'], url)
        # Limpa texto
        clean = re.sub(r'\(?clique\s+aqui\)?\.?', '', p['page_text'], flags=re.IGNORECASE)
        clean = re.sub(r'^\s*[-–•]\s*', '', clean).strip()
        clean = re.sub(r';?\s*$', '', clean).strip()
        all_pdfs.append({'titulo': clean, 'url': url, 'origem': 'pagina_principal', **meta})

    # PDFs dentro de cada índice
    for idx_url in indices:
        try:
            idx_html = fetch(idx_url)
        except Exception as e:
            print(f"  ❌ {idx_url}: {e}")
            continue

        # Extrai links pra PDFs com seus títulos
        pdf_links = re.findall(r'<a[^>]+href="([^"]+\.pdf[^"]*)"[^>]*title="File"[^>]*>([^<]+)</a>', idx_html)
        # ou class="...url..."
        if not pdf_links:
            pdf_links = re.findall(r'<a[^>]+href="([^"]+\.pdf[^"]*)"[^>]*>([^<]+)</a>', idx_html)

        for pdf_url, titulo in pdf_links:
            # remove /view do fim se tiver
            pdf_url = re.sub(r'/view$', '', pdf_url)
            if not pdf_url.startswith('http'):
                pdf_url = 'https://www.gov.br' + pdf_url
            titulo = titulo.replace('.pdf', '').strip()
            meta = parse_meta(titulo, pdf_url)
            all_pdfs.append({'titulo': titulo, 'url': pdf_url, 'origem': idx_url, **meta})

    # Dedupe por URL
    seen = set()
    final = []
    for r in all_pdfs:
        if r['url'] not in seen:
            seen.add(r['url'])
            final.append(r)

    # Ordena: ano DESC, op_num ASC
    final.sort(key=lambda r: (-(r.get('ano') or 0), r.get('op_num') or 999))

    out = {
        'extracted_at': datetime.datetime.utcnow().isoformat(),
        'source_url': ROOT_PAGE,
        'total': len(final),
        'relatorios': final,
    }
    (OUT / 'relatorios.json').write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"\nTotal PDFs únicos extraídos: {len(final)}")
    print()
    print("=== Por ano (top 10 mais recentes) ===")
    anos_count = {}
    for r in final:
        anos_count[r['ano']] = anos_count.get(r['ano'], 0) + 1
    for ano in sorted(anos_count.keys(), reverse=True)[:10]:
        print(f"  {ano}: {anos_count[ano]} relatórios")
    print()
    print("=== Amostra ===")
    for r in final[:8]:
        ufs = '/'.join(r.get('ufs',[]))
        print(f"  [{r.get('ano','?')}] {r['titulo'][:70]} [{ufs}]")

if __name__ == '__main__':
    main()
