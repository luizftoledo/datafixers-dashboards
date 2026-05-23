#!/usr/bin/env python3
"""
Processa PDFs de relatórios TE: download → OCR → Llama → JSON.
Roda em LOTES com checkpoint a cada 10. Cache via OUT_FILE.
"""
import json, urllib.request, subprocess, os, time, hashlib, shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELS_FILE = ROOT / "relatorios-trabalho-escravo" / "data" / "relatorios.json"
OUT_FILE = ROOT / "relatorios-trabalho-escravo" / "data" / "relatorios_ocr.json"
WORKER = "https://ibama-worker.datafixers.org/api/te/resumir"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15"
TMPDIR = "/tmp/te_ocr"

def reset_tmp():
    shutil.rmtree(TMPDIR, ignore_errors=True)
    os.makedirs(TMPDIR)

def download(url):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': UA})
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.read()
    except Exception:
        return None

def ocr_pdf(pdf_bytes, max_pages=3):
    reset_tmp()
    pdf_path = f"{TMPDIR}/in.pdf"
    with open(pdf_path,'wb') as f: f.write(pdf_bytes)
    subprocess.run(['pdftoppm','-png','-r','180','-f','1','-l',str(max_pages), pdf_path, f"{TMPDIR}/p"], capture_output=True, timeout=120)
    pages = sorted([f for f in os.listdir(TMPDIR) if f.startswith('p-') and f.endswith('.png')])
    if not pages: return ""
    texts = []
    for p in pages:
        try:
            # Lê bytes e regrava sem xattr de quarantine
            src = f"{TMPDIR}/{p}"
            clean = f"{TMPDIR}/clean_{p}"
            with open(src,'rb') as f: data = f.read()
            with open(clean,'wb') as f: f.write(data)
            base = f"{TMPDIR}/out_{p[:-4]}"
            subprocess.run(['tesseract', p, p[:-4]+'_out', '-l', 'por', '--psm', '6'], capture_output=True, timeout=120, cwd=TMPDIR)
            txt_path = Path(f"{TMPDIR}/{p[:-4]}_out.txt")
            if txt_path.exists():
                texts.append(txt_path.read_text(errors='replace'))
        except Exception:
            pass
    return "\n\n".join(texts)

def call_worker(url, titulo, ano, text):
    body = json.dumps({'url': url, 'titulo': titulo, 'ano': ano, 'text': text[:8000]}).encode()
    req = urllib.request.Request(WORKER, data=body, method='POST', headers={
        'Content-Type': 'application/json',
        'User-Agent': UA,
    })
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read())
    except Exception as e:
        return {'erro': str(e)}

def main():
    data = json.loads(RELS_FILE.read_text())
    rels = data['relatorios']
    cache = {}
    if OUT_FILE.exists():
        try:
            prev = json.loads(OUT_FILE.read_text())
            for r in prev.get('relatorios', []):
                cache[r['url']] = r
        except: pass

    LIMIT = int(os.environ.get('LIMIT', '5'))
    todos = rels[:LIMIT] if LIMIT > 0 else rels

    results = []
    ok = falha = pulado = 0
    start = time.time()

    for i, r in enumerate(todos, 1):
        url = r['url']
        titulo = r['titulo']
        ano = r.get('ano')

        if url in cache and cache[url].get('dados'):
            results.append(cache[url])
            pulado += 1
            continue

        elapsed = time.time() - start
        print(f"[{i}/{len(todos)}] ({elapsed/60:.1f}m) {ano} · {titulo[:55]}", flush=True)

        pdf = download(url)
        if not pdf:
            falha += 1
            print(f"   ❌ download falhou")
            results.append({**r, 'erro': 'download_falhou'})
            continue

        text = ocr_pdf(pdf, max_pages=3)
        if len(text) < 100:
            falha += 1
            print(f"   ❌ OCR retornou só {len(text)} chars")
            results.append({**r, 'erro': 'ocr_vazio', 'text_chars': len(text)})
            continue

        resp = call_worker(url, titulo, ano, text)
        if resp.get('erro'):
            falha += 1
            print(f"   ❌ worker: {resp['erro'][:60]}")
            results.append({**r, 'erro': 'worker:' + str(resp.get('erro', ''))[:50]})
            continue

        dados = resp.get('dados') or {}
        ok += 1
        emp = (dados.get('empresa') or '?')[:35]
        trab = dados.get('trabalhadores_resgatados', 0)
        print(f"   ✅ {emp} | {trab} trab.")
        results.append({**r, 'dados': dados, 'resumo': dados.get('resumo',''), 'text_ocr': text[:15000]})

        if i % 10 == 0:
            OUT_FILE.write_text(json.dumps({'processed_at': time.strftime('%Y-%m-%dT%H:%M:%SZ'), 'total': len(results), 'relatorios': results}, ensure_ascii=False, indent=2))
            print(f"   💾 checkpoint {len(results)}")
        time.sleep(0.3)

    OUT_FILE.write_text(json.dumps({'processed_at': time.strftime('%Y-%m-%dT%H:%M:%SZ'), 'total': len(results), 'relatorios': results}, ensure_ascii=False, indent=2))
    print(f"\n✅ {ok} ok | 📋 {pulado} cache | ❌ {falha} falhas | tempo: {(time.time()-start)/60:.1f}m")

if __name__ == '__main__':
    main()
