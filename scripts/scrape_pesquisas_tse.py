#!/usr/bin/env python3
"""
Baixa o CSV de pesquisas eleitorais 2026 do portal de dados abertos do TSE,
filtra abrangência nacional (SG_UF=BR, cargo Presidente) e produz:

  pesquisas-eleitorais-dashboard/data/pesquisas.json  -> dataset consumido pela página
  pesquisas-eleitorais-dashboard/data/novas.json      -> diff desta execução (alerta)
  pesquisas-eleitorais-dashboard/data/seen.json       -> protocolos já vistos (estado)

Na primeira execução (sem seen.json), o scraper apenas inicializa o estado e
não considera nenhuma pesquisa como "nova" — para evitar disparar 200+ alertas.
"""
from __future__ import annotations

import csv
import io
import json
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

CSV_ZIP_URL = (
    "https://cdn.tse.jus.br/estatistica/sead/odsele/"
    "pesquisa_eleitoral/pesquisa_eleitoral_2026.zip"
)

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "pesquisas" / "data"


def parse_dt(raw: str) -> str | None:
    if not raw:
        return None
    raw = raw.strip()
    if raw in ("#NULO#", "#NE#"):
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).isoformat()
        except ValueError:
            continue
    return None


def to_int(raw: str) -> int | None:
    raw = (raw or "").strip()
    return int(raw) if raw.isdigit() else None


def clean(raw: str) -> str:
    """Normaliza marcadores nulos do CSV do TSE (#NULO#, #NE#) em string vazia."""
    raw = (raw or "").strip()
    return "" if raw in ("#NULO#", "#NE#") else raw


def fetch_zip(tentativas: int = 3) -> bytes:
    """Baixa o zip do TSE com retry e backoff exponencial.

    O CDN do TSE costuma dar timeout pontual; uma única tentativa quebra a
    run agendada. Tenta `tentativas` vezes (espera 10s, 20s, 40s...) antes de
    desistir, para que falhas transitórias se auto-resolvam sem alerta falso.
    """
    req = Request(CSV_ZIP_URL, headers={"User-Agent": "luizftoledo-portfolio/1.0"})
    ultimo_erro: Exception | None = None
    for tentativa in range(1, tentativas + 1):
        try:
            with urlopen(req, timeout=180) as resp:
                return resp.read()
        except (URLError, TimeoutError, OSError) as exc:
            ultimo_erro = exc
            if tentativa < tentativas:
                espera = 10 * (2 ** (tentativa - 1))
                print(
                    f"[scraper] tentativa {tentativa}/{tentativas} falhou ({exc}); "
                    f"nova tentativa em {espera}s",
                    file=sys.stderr,
                )
                time.sleep(espera)
    raise SystemExit(f"falha ao baixar zip do TSE após {tentativas} tentativas: {ultimo_erro}")


def extract_brasil_csv(zip_bytes: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        candidates = [n for n in zf.namelist() if n.endswith("BRASIL.csv")]
        if not candidates:
            raise SystemExit("pesquisa_eleitoral_2026_BRASIL.csv não encontrado no zip do TSE")
        with zf.open(candidates[0]) as f:
            return f.read().decode("iso-8859-1")


def normalize(row: dict) -> dict:
    return {
        "id": clean(row["NR_PROTOCOLO_REGISTRO"]),
        "uf": clean(row["SG_UF"]),
        "unidade_eleitoral": clean(row["NM_UE"]),
        "cargo": clean(row["DS_CARGO"]),
        "registro": parse_dt(row["DT_REGISTRO"]),
        "inicio": parse_dt(row["DT_INICIO_PESQUISA"]),
        "fim": parse_dt(row["DT_FIM_PESQUISA"]),
        "divulgacao": parse_dt(row["DT_DIVULGACAO"]),
        "empresa": clean(row["NM_EMPRESA"]),
        "fantasia": clean(row["NM_EMPRESA_FANTASIA"]),
        "cnpj": clean(row["NR_CNPJ_EMPRESA"]),
        "entrevistados": to_int(row["QT_ENTREVISTADO"]),
        "valor": clean(row["VR_PESQUISA"]),
        "estatistico": clean(row["NM_ESTATISTICO_RESP"]),
        "propria": row["ST_PESQUISA_PROPRIA"] == "S",
        "metodologia": clean(row["DS_METODOLOGIA_PESQUISA"]),
        "plano_amostral": clean(row["DS_PLANO_AMOSTRAL"]),
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[scraper] baixando {CSV_ZIP_URL}")
    zip_bytes = fetch_zip()
    print(f"[scraper] {len(zip_bytes):,} bytes")
    text = extract_brasil_csv(zip_bytes)

    pesquisas: list[dict] = []
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    for row in reader:
        if row.get("SG_UF") != "BR":
            continue
        pesquisas.append(normalize(row))

    pesquisas.sort(key=lambda p: p["divulgacao"] or "9999")
    print(f"[scraper] {len(pesquisas)} pesquisas nacionais (SG_UF=BR)")

    seen_path = OUT_DIR / "seen.json"
    is_first_run = not seen_path.exists()
    seen: set[str] = set()
    if not is_first_run:
        try:
            seen = set(json.loads(seen_path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            print("[scraper] seen.json corrompido, tratando como primeira execução")
            is_first_run = True

    if is_first_run:
        novas: list[dict] = []
        print("[scraper] primeira execução: nenhum alerta será disparado")
    else:
        novas = [p for p in pesquisas if p["id"] not in seen]
    print(f"[scraper] {len(novas)} pesquisas novas")

    pesquisas_path = OUT_DIR / "pesquisas.json"
    previous = None
    if pesquisas_path.exists():
        try:
            previous = json.loads(pesquisas_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            previous = None

    now = datetime.now(timezone.utc).isoformat()
    if previous and previous.get("pesquisas") == pesquisas:
        print("[scraper] dataset inalterado; mantendo pesquisas.json")
        last_change = previous.get("atualizado_em") or now
    else:
        payload = {
            "atualizado_em": now,
            "fonte": CSV_ZIP_URL,
            "criterio": "SG_UF=BR (abrangência nacional, cargo Presidente)",
            "total": len(pesquisas),
            "pesquisas": pesquisas,
        }
        pesquisas_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        last_change = now

    (OUT_DIR / "status.json").write_text(
        json.dumps(
            {
                "ok": True,
                "last_check": now,
                "last_change": last_change,
                "total": len(pesquisas),
                "novas": len(novas),
                "fonte": CSV_ZIP_URL,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (OUT_DIR / "novas.json").write_text(
        json.dumps(novas, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    seen_path.write_text(
        json.dumps(sorted({p["id"] for p in pesquisas})), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
