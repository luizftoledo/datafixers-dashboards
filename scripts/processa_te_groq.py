#!/usr/bin/env python3
"""
Processa relatórios TE com Groq (Llama 3.3 70B) — gratuito.

Free tier (em 05/26): ~30 RPM, 1000 RPD pro 70B.
Pros 409 PDFs com 30 RPM → ~15 min se OCR já estiver cacheado.

Chave: ~/.groq_key (ou env GROQ_API_KEY).

Uso:
    python3 processa_te_groq.py               # roda todos
    LIMIT=3 python3 processa_te_groq.py       # teste
    FORCE=1 python3 processa_te_groq.py       # reprocessa
    MODEL=llama-3.3-70b-versatile python3 ... # troca modelo
"""
import json, urllib.request, urllib.error, subprocess, os, time, shutil, sys, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELS_FILE = ROOT / "relatorios-trabalho-escravo" / "data" / "relatorios.json"
OUT_FILE = ROOT / "relatorios-trabalho-escravo" / "data" / "relatorios_ocr.json"

MODEL = os.environ.get("MODEL", "llama-3.3-70b-versatile")
API_URL = "https://api.groq.com/openai/v1/chat/completions"
RPM_SLEEP = float(os.environ.get("RPM_SLEEP", "5.0"))  # ~12 RPM, conservador pra caber em 12k TPM
TEXT_CHARS = int(os.environ.get("TEXT_CHARS", "3000"))  # ~750 tok por req; com sys+task ~1.4k tok
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15"
TMPDIR = "/tmp/te_ocr"


def load_key():
    k = os.environ.get("GROQ_API_KEY", "").strip()
    if k:
        return k
    p = Path.home() / ".groq_key"
    if p.exists():
        return p.read_text().strip().splitlines()[0].strip()
    sys.exit("ERRO: defina GROQ_API_KEY ou crie ~/.groq_key")


API_KEY = load_key()


SYSTEM_PROMPT = """Você é um extrator de dados estruturados especializado em relatórios de fiscalização do trabalho escravo do Ministério do Trabalho brasileiro (MTE/SIT/GEFM).

Você receberá o texto OCR das primeiras páginas de um relatório (o OCR pode ter erros e ruído).

Retorne APENAS um objeto JSON válido. Sem markdown, sem ```json, sem texto antes/depois.

Campos obrigatórios (TODOS devem aparecer, usando "" ou 0 quando ausente):
- empresa (string): nome do empregador/empresa/fazenda autuada como aparece no documento
- cnpj_cpf (string): só dígitos e pontuação convencional; "" se ausente
- trabalhadores_resgatados (integer): NÚMERO de trabalhadores resgatados/encontrados em condição análoga à escravidão. Procure por "X trabalhadores resgatados", "encontrados N em condição análoga", "libertados", listas/tabelas de resgatados. Se operação foi negativa, use 0. NÃO confunda com "fiscalizados" sem resgate.
- tipo_trabalho (string): setor resumido em até 6 palavras (ex.: "rural - café", "carvoaria", "construção civil", "cultivo de fumo")
- municipio (string): cidade sem UF
- uf (string): sigla do estado em 2 letras MAIÚSCULAS, "" se ausente
- data_fatos (string): AAAA-MM-DD se houver dia; senão AAAA-MM; senão AAAA; senão ""
- resumo (string): 1-2 frases factuais em português. Se OCR for incompreensível: "OCR ilegível — dados insuficientes."

REGRAS:
- JAMAIS invente. Campo ausente = "" ou 0.
- Se o ano do título contradiz o que você inferiu, prefira o ano do título.
- Iniciais com pontos ("J. S. H") são identificadores legítimos — preserve.
- OCR sujo ("NINE crr O Susa") = NÃO use como nome; use "" e mencione no resumo."""

USER_TMPL = """Título: {titulo}
Ano do título: {ano}

Texto OCR (primeiras páginas):
\"\"\"
{text}
\"\"\""""


def reset_tmp():
    shutil.rmtree(TMPDIR, ignore_errors=True)
    os.makedirs(TMPDIR)


def download(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=90) as r:
            data = r.read()
            if data[:4] != b"%PDF":
                return None
            return data
    except Exception:
        return None


def ocr_pdf(pdf_bytes, max_pages=3):
    reset_tmp()
    pdf_path = f"{TMPDIR}/in.pdf"
    with open(pdf_path, "wb") as f:
        f.write(pdf_bytes)
    subprocess.run(
        ["pdftoppm", "-png", "-r", "180", "-f", "1", "-l", str(max_pages), pdf_path, f"{TMPDIR}/p"],
        capture_output=True, timeout=120,
    )
    pages = sorted(f for f in os.listdir(TMPDIR) if f.startswith("p-") and f.endswith(".png"))
    if not pages:
        return ""
    texts = []
    for p in pages:
        try:
            subprocess.run(
                ["tesseract", p, p[:-4] + "_out", "-l", "por", "--psm", "6"],
                capture_output=True, timeout=120, cwd=TMPDIR,
            )
            txt = Path(f"{TMPDIR}/{p[:-4]}_out.txt")
            if txt.exists():
                texts.append(txt.read_text(errors="replace"))
        except Exception:
            pass
    return "\n\n".join(texts)


def call_groq(titulo, ano, text, retries=5):
    user = USER_TMPL.format(titulo=titulo or "(sem título)", ano=ano or "?", text=text[:TEXT_CHARS])
    body = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
        "max_completion_tokens": 800,
    }).encode()

    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                API_URL, data=body, method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {API_KEY}",
                    "User-Agent": "curl/8.4.0",
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=180) as r:
                resp = json.loads(r.read())
            txt = resp["choices"][0]["message"]["content"].strip()
            usage = resp.get("usage", {})
            try:
                return {"dados": json.loads(txt), "usage": usage}
            except Exception:
                m = re.search(r"\{.*\}", txt, re.DOTALL)
                if m:
                    try:
                        return {"dados": json.loads(m.group(0)), "usage": usage}
                    except Exception:
                        pass
                return {"erro": "json_invalido", "raw": txt[:500], "usage": usage}
        except urllib.error.HTTPError as e:
            body_err = e.read().decode("utf-8", errors="replace")[:400]
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                wait = min(60, (2 ** attempt) * 5)
                print(f"      ⏳ HTTP {e.code}, retry em {wait}s ({body_err[:80]})", flush=True)
                time.sleep(wait)
                continue
            return {"erro": f"http_{e.code}", "body": body_err}
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            return {"erro": f"exc:{e}"[:200]}
    return {"erro": "esgotou_retries"}


def sanitize(d):
    if not isinstance(d, dict):
        return {}
    try:
        trab = int(d.get("trabalhadores_resgatados") or 0)
    except Exception:
        trab = 0
    return {
        "empresa": (d.get("empresa") or "").strip(),
        "cnpj_cpf": (d.get("cnpj_cpf") or "").strip(),
        "trabalhadores_resgatados": trab,
        "tipo_trabalho": (d.get("tipo_trabalho") or "").strip(),
        "municipio": (d.get("municipio") or "").strip(),
        "uf": (d.get("uf") or "").strip().upper()[:2],
        "data_fatos": (d.get("data_fatos") or "").strip(),
        "resumo": (d.get("resumo") or "").strip(),
    }


def main():
    rels = json.loads(RELS_FILE.read_text())["relatorios"]
    cache = {}
    if OUT_FILE.exists():
        try:
            prev = json.loads(OUT_FILE.read_text())
            for r in prev.get("relatorios", []):
                cache[r["url"]] = r
        except Exception:
            pass

    LIMIT = int(os.environ.get("LIMIT", "0"))
    FORCE = os.environ.get("FORCE", "0") == "1"

    targets = []
    for r in rels:
        cached = cache.get(r["url"], {})
        if not FORCE and cached.get("dados") and not cached.get("erro"):
            continue
        targets.append({**r, "_text_ocr": cached.get("text_ocr", "")})

    if LIMIT > 0:
        targets = targets[:LIMIT]

    print(f"Modelo: {MODEL}  |  RPM sleep: {RPM_SLEEP}s")
    print(f"A processar: {len(targets)} / {len(rels)}")
    print()

    results_map = {u: r for u, r in cache.items()}
    ok = fail = 0
    total_in = total_out = 0
    start = time.time()
    last_call = 0.0

    for i, t in enumerate(targets, 1):
        try:
            url = t["url"]
            titulo = t.get("titulo", "")
            ano = t.get("ano")
            text = t.pop("_text_ocr", "") or ""

            elapsed = time.time() - start
            eta = (len(targets) - i) * (elapsed / max(i, 1)) / 60
            print(f"[{i}/{len(targets)}] ({elapsed/60:.1f}m, ETA {eta:.0f}m) {ano} · {titulo[:55]}", flush=True)

            if len(text) < 100:
                pdf = download(url)
                if not pdf:
                    fail += 1
                    print("   ❌ download falhou")
                    results_map[url] = {**t, "erro": "download_falhou"}
                    continue
                text = ocr_pdf(pdf, max_pages=3)
                if len(text) < 100:
                    fail += 1
                    print(f"   ❌ OCR só {len(text)} chars")
                    results_map[url] = {**t, "erro": "ocr_vazio", "text_chars": len(text)}
                    continue

            # Rate limit
            dt = time.time() - last_call
            if dt < RPM_SLEEP:
                time.sleep(RPM_SLEEP - dt)
            last_call = time.time()

            resp = call_groq(titulo, ano, text)
            if resp.get("erro"):
                fail += 1
                print(f"   ❌ {resp['erro'][:80]}")
                results_map[url] = {**t, "erro": resp["erro"][:120], "text_ocr": text[:15000]}
                continue

            u = resp.get("usage", {})
            total_in += u.get("prompt_tokens", 0)
            total_out += u.get("completion_tokens", 0)

            dados = sanitize(resp["dados"])
            ok += 1
            emp = (dados["empresa"] or "?")[:40]
            trab = dados["trabalhadores_resgatados"]
            print(f"   ✅ {emp} | {trab} trab. | tok in={u.get('prompt_tokens',0)} out={u.get('completion_tokens',0)}")
            results_map[url] = {**t, "dados": dados, "resumo": dados["resumo"], "text_ocr": text[:15000]}
        finally:
            if i % 10 == 0:
                _save(results_map)
                print(f"   💾 checkpoint | total={len(results_map)} | ok={ok} fail={fail}")

    _save(results_map)
    print(f"\n✅ {ok} ok | ❌ {fail} falhas | {(time.time()-start)/60:.1f}m")
    print(f"   tokens in={total_in:,} out={total_out:,}")


def _save(results_map):
    results = list(results_map.values())
    OUT_FILE.write_text(json.dumps({
        "processed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total": len(results),
        "relatorios": results,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
