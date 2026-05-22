#!/usr/bin/env python3
"""
Baixa do PesqEle público (TSE) o arquivo "Questionário completo" de cada
pesquisa em pesquisas-eleitorais-dashboard/data/pesquisas.json e extrai o
texto com pdftotext -layout. Salva:

  data/questionarios/{id_safe}.txt   -> texto extraído
  data/questionarios.json            -> estado {id: {status, sha256, file, ts}}

Status:
  ok             -> texto extraído com sucesso
  nao_fornecido  -> empresa não anexou o questionário
  erro           -> falha de rede / extração (tentar de novo no próximo run)

Reaproveita estado entre execuções: pesquisas com status "ok" ou
"nao_fornecido" já registrado e arquivo presente são puladas.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPCookieProcessor
from http.cookiejar import CookieJar

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "pesquisas" / "data"
PESQUISAS_PATH = DATA_DIR / "pesquisas.json"
QUEST_DIR = DATA_DIR / "questionarios"
ESTADO_PATH = DATA_DIR / "questionarios.json"

BASE = "https://pesqele-divulgacao.tse.jus.br"
LISTAR_URL = f"{BASE}/app/pesquisa/listar.xhtml"
DETALHAR_URL = f"{BASE}/app/pesquisa/detalhar.xhtml"
UA = "luizftoledo-portfolio-pesqele/1.0 (jornalismo, BBC Brasil)"


def format_protocolo(raw_id: str) -> str:
    """BR067312026 -> BR-06731/2026"""
    if len(raw_id) != 11 or not raw_id[2:].isdigit():
        raise ValueError(f"ID inesperado: {raw_id}")
    return f"{raw_id[:2]}-{raw_id[2:7]}/{raw_id[7:]}"


def id_safe(raw_id: str) -> str:
    return format_protocolo(raw_id).replace("/", "-")


def fetch(opener, url: str, data: bytes | None = None, extra_headers: dict | None = None):
    headers = {"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9"}
    if extra_headers:
        headers.update(extra_headers)
    req = Request(url, data=data, headers=headers, method="POST" if data else "GET")
    with opener.open(req, timeout=60) as resp:
        body = resp.read()
        return resp.status, resp.headers, body


def find_viewstate(html: str) -> str:
    m = re.search(r'name="javax\.faces\.ViewState"[^>]*value="([^"]+)"', html)
    if not m:
        raise RuntimeError("ViewState não encontrado")
    return m.group(1)


def find_detalhar_btn_id(xml: str) -> str:
    """Extrai o ID do botão da lupa da resposta da busca (sempre :0:detalhar
    para resultado único)."""
    m = re.search(r'(formPesquisa:tabelaPesquisas:\d+:detalhar)', xml)
    if not m:
        raise RuntimeError("Botão detalhar não encontrado no resultado da busca")
    return m.group(1)


def baixar_questionario(protocolo: str, *, delay: float = 0.5) -> tuple[str, bytes | None, str | None]:
    """
    Retorna (status, pdf_bytes_or_none, mensagem).
    status ∈ {"ok", "nao_fornecido", "erro"}.
    """
    jar = CookieJar()
    opener = build_opener(HTTPCookieProcessor(jar))

    try:
        # 1. GET listar -> sessão + ViewState
        _, _, body = fetch(opener, LISTAR_URL)
        html = body.decode("iso-8859-1")
        vs = find_viewstate(html)
        time.sleep(delay)

        # 2. POST busca pelo protocolo
        busca_params = [
            ("javax.faces.partial.ajax", "true"),
            ("javax.faces.source", "formPesquisa:idBtnPesquisar"),
            ("javax.faces.partial.execute", "@all"),
            ("javax.faces.partial.render", "formPesquisa:grupoPrincipal"),
            ("formPesquisa:idBtnPesquisar", "formPesquisa:idBtnPesquisar"),
            ("formPesquisa_SUBMIT", "1"),
            ("formPesquisa:eleicoes_focus", ""),
            ("formPesquisa:empresas_focus", ""),
            ("formPesquisa:filtroUF_focus", ""),
            ("formPesquisa:selectCidades_focus", ""),
            ("formPesquisa:j_id_22", protocolo),
            ("formPesquisa:j_id_25_input", ""),
            ("formPesquisa:j_id_27_input", ""),
            ("javax.faces.ViewState", vs),
        ]
        _, _, body = fetch(
            opener, LISTAR_URL,
            data=urlencode(busca_params).encode("iso-8859-1"),
            extra_headers={"Faces-Request": "partial/ajax", "Content-Type": "application/x-www-form-urlencoded; charset=ISO-8859-1"},
        )
        busca_xml = body.decode("iso-8859-1")
        if "Total de registros: 0" in busca_xml or "tabelaPesquisas:0:detalhar" not in busca_xml:
            return "erro", None, "protocolo não encontrado na busca"
        # ViewState pode ter rotacionado na resposta partial
        m_vs = re.search(r'<update id="[^"]*ViewState:1"><!\[CDATA\[([^\]]+)\]\]>', busca_xml)
        if m_vs:
            vs = m_vs.group(1)
        time.sleep(delay)

        # 3. POST clique na lupa -> redirect
        click_params = [
            ("javax.faces.partial.ajax", "true"),
            ("javax.faces.source", "formPesquisa:tabelaPesquisas:0:detalhar"),
            ("javax.faces.partial.execute", "formPesquisa:tabelaPesquisas:0:detalhar"),
            ("formPesquisa:tabelaPesquisas:0:detalhar", "formPesquisa:tabelaPesquisas:0:detalhar"),
            ("formPesquisa_SUBMIT", "1"),
            ("formPesquisa:j_id_22", protocolo),
            ("javax.faces.ViewState", vs),
        ]
        _, _, body = fetch(
            opener, LISTAR_URL,
            data=urlencode(click_params).encode("iso-8859-1"),
            extra_headers={"Faces-Request": "partial/ajax", "Content-Type": "application/x-www-form-urlencoded; charset=ISO-8859-1"},
        )
        if b"<redirect" not in body:
            return "erro", None, "clique na lupa não devolveu redirect"
        time.sleep(delay)

        # 4. GET detalhar -> novo ViewState
        _, _, body = fetch(opener, DETALHAR_URL)
        html = body.decode("iso-8859-1")
        if protocolo not in html:
            return "erro", None, "página detalhar não carregou o protocolo esperado"
        vs = find_viewstate(html)
        if "arquivoQuestionario" not in html:
            return "nao_fornecido", None, "botão arquivoQuestionario ausente"
        time.sleep(delay)

        # 5. POST botão arquivoQuestionario
        dl_params = [
            ("j_id_11_SUBMIT", "1"),
            ("javax.faces.ViewState", vs),
            ("j_id_11:arquivoQuestionario", ""),
        ]
        status, headers, body = fetch(
            opener, DETALHAR_URL,
            data=urlencode(dl_params).encode("iso-8859-1"),
            extra_headers={"Content-Type": "application/x-www-form-urlencoded; charset=ISO-8859-1"},
        )
        ctype = (headers.get("Content-Type") or "").lower()
        if "application/pdf" in ctype or body[:5] == b"%PDF-":
            return "ok", body, None
        # HTML => não fornecido (mensagem ui-messages-warn)
        try:
            txt = body.decode("iso-8859-1", errors="ignore")
            if "não foi fornecido" in txt or "n&#227;o foi fornecido" in txt or "nao foi fornecido" in txt:
                return "nao_fornecido", None, None
        except Exception:
            pass
        return "erro", None, f"resposta inesperada (Content-Type={ctype}, size={len(body)})"

    except Exception as e:
        return "erro", None, f"{type(e).__name__}: {e}"


def extrair_texto(pdf_bytes: bytes) -> str:
    """Roda pdftotext -layout via stdin/stdout."""
    proc = subprocess.run(
        ["pdftotext", "-layout", "-enc", "UTF-8", "-", "-"],
        input=pdf_bytes,
        capture_output=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"pdftotext falhou: {proc.stderr.decode(errors='ignore')[:200]}")
    return proc.stdout.decode("utf-8", errors="replace")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="processar no máximo N pesquisas (debug)")
    ap.add_argument("--retry-erros", action="store_true", help="reprocessar IDs marcados como 'erro'")
    ap.add_argument("--delay", type=float, default=0.8, help="pausa entre pesquisas (s)")
    args = ap.parse_args()

    QUEST_DIR.mkdir(parents=True, exist_ok=True)
    pesquisas = json.loads(PESQUISAS_PATH.read_text(encoding="utf-8"))["pesquisas"]

    estado: dict = {}
    if ESTADO_PATH.exists():
        try:
            estado = json.loads(ESTADO_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            estado = {}

    skip_ok = skip_nf = skip_err = 0
    processadas = ok = nf = err = 0
    try:
        for p in pesquisas:
            raw_id = p["id"]
            try:
                protocolo = format_protocolo(raw_id)
            except ValueError as e:
                print(f"[pesqele] {raw_id}: id mal formado ({e}); pulando")
                continue

            prev = estado.get(raw_id)
            if prev:
                if prev.get("status") == "ok":
                    txt = QUEST_DIR / f"{id_safe(raw_id)}.txt"
                    if txt.exists():
                        skip_ok += 1
                        continue
                elif prev.get("status") in ("nao_fornecido", "ok_imagem"):
                    skip_nf += 1
                    continue
                elif prev.get("status") == "erro" and not args.retry_erros:
                    skip_err += 1
                    continue

            if args.limit is not None and processadas >= args.limit:
                break

            processadas += 1
            t0 = time.time()
            status, pdf_bytes, msg = baixar_questionario(protocolo, delay=0.4)
            elapsed = time.time() - t0
            ts = datetime.now(timezone.utc).isoformat()

            if status == "ok":
                txt_path = QUEST_DIR / f"{id_safe(raw_id)}.txt"
                try:
                    texto = extrair_texto(pdf_bytes)
                    sha = hashlib.sha256(pdf_bytes).hexdigest()
                    if len(texto.strip()) < 200:
                        # PDF é imagem rasterizada (Print To PDF, escaneado etc.)
                        estado[raw_id] = {
                            "status": "ok_imagem",
                            "sha256": sha,
                            "pdf_bytes": len(pdf_bytes),
                            "txt_bytes": len(texto.encode("utf-8")),
                            "ts": ts,
                        }
                        # remove txt residual de execução anterior, se houver
                        if txt_path.exists():
                            txt_path.unlink()
                        ok += 1
                        print(f"[pesqele] {protocolo}: OK_IMAGEM ({len(pdf_bytes)//1024} KB PDF, texto vazio, {elapsed:.1f}s)")
                    else:
                        txt_path.write_text(texto, encoding="utf-8")
                        estado[raw_id] = {
                            "status": "ok",
                            "file": f"questionarios/{txt_path.name}",
                            "sha256": sha,
                            "pdf_bytes": len(pdf_bytes),
                            "txt_bytes": len(texto.encode("utf-8")),
                            "ts": ts,
                        }
                        ok += 1
                        print(f"[pesqele] {protocolo}: OK ({len(pdf_bytes)//1024} KB PDF -> {len(texto)//1024} KB txt, {elapsed:.1f}s)")
                except Exception as e:
                    estado[raw_id] = {"status": "erro", "msg": f"extracao: {e}", "ts": ts}
                    err += 1
                    print(f"[pesqele] {protocolo}: ERRO extração: {e}")
            elif status == "nao_fornecido":
                estado[raw_id] = {"status": "nao_fornecido", "ts": ts}
                nf += 1
                print(f"[pesqele] {protocolo}: não fornecido")
            else:
                estado[raw_id] = {"status": "erro", "msg": msg, "ts": ts}
                err += 1
                print(f"[pesqele] {protocolo}: ERRO {msg}")

            time.sleep(args.delay)
    finally:
        ESTADO_PATH.write_text(json.dumps(estado, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    print()
    print(f"[pesqele] resumo: processadas={processadas} ok={ok} nf={nf} err={err}")
    print(f"[pesqele] puladas (cache): ok={skip_ok} nf={skip_nf} erros_pendentes={skip_err}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
