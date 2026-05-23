#!/usr/bin/env python3
"""SO faz OCR pra preencher text_ocr nos relatórios. Sem chamar IA."""
import json, urllib.request, subprocess, os, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
F = ROOT / "relatorios-trabalho-escravo" / "data" / "relatorios_ocr.json"
TMPDIR = "/tmp/te_ocr"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15"

def reset_tmp():
    import shutil
    shutil.rmtree(TMPDIR, ignore_errors=True)
    os.makedirs(TMPDIR)

def download(url):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': UA})
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.read()
    except: return None

def ocr_pdf(pdf_bytes, max_pages=3):
    reset_tmp()
    open(f"{TMPDIR}/in.pdf",'wb').write(pdf_bytes)
    subprocess.run(['pdftoppm','-png','-r','180','-f','1','-l',str(max_pages), f"{TMPDIR}/in.pdf", f"{TMPDIR}/p"], capture_output=True, timeout=120)
    pages = sorted([f for f in os.listdir(TMPDIR) if f.startswith('p-') and f.endswith('.png')])
    texts = []
    for p in pages:
        try:
            r = subprocess.run(['tesseract', p, p[:-4]+'_out', '-l', 'por', '--psm', '6'], capture_output=True, timeout=120, cwd=TMPDIR)
            t = Path(f"{TMPDIR}/{p[:-4]}_out.txt")
            if t.exists(): texts.append(t.read_text(errors='replace'))
        except: pass
    return "\n\n".join(texts)

data = json.loads(F.read_text())
rels = data['relatorios']
start = time.time()
ok = 0
faltam = [r for r in rels if not r.get('text_ocr')]
print(f"Vou processar {len(faltam)} relatórios sem text_ocr ainda")

for i, r in enumerate(faltam, 1):
    if i % 20 == 0:
        F.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        elapsed = time.time() - start
        eta = (len(faltam) - i) * elapsed / i / 60
        print(f"  [{i}/{len(faltam)}] checkpoint · ETA {eta:.0f}m · {ok} ok")
    pdf = download(r['url'])
    if not pdf: continue
    text = ocr_pdf(pdf)
    if len(text) > 100:
        r['text_ocr'] = text[:15000]
        ok += 1

F.write_text(json.dumps(data, ensure_ascii=False, indent=2))
elapsed = time.time() - start
print(f"\n✅ {ok}/{len(faltam)} com OCR · {elapsed/60:.1f}m")
