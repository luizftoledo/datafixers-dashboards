#!/usr/bin/env python3
"""
Upserts records.jsonl.gz pro Cloudflare D1 (database "ibama").
Roda DEPOIS de build_lulometro_data.py. Usa wrangler CLI com --remote.

Schema esperado no D1:
    lulometro_records (id PK, url, source, type, president_slug, president,
                       mandate, date, title, location, description, text,
                       word_count, updated_at)
    lulometro_fts (FTS5 virtual table com title, description, text)

Estratégia:
1. Lê records.jsonl.gz local.
2. Consulta D1 pra saber quais IDs já existem com mesmo updated_at (evita
   reescrever os 1700 todo dia — só os novos/alterados).
3. Para os que mudaram: gera SQL chunked (250 records por arquivo) e roda
   wrangler d1 execute --remote --file=.
4. Atualiza FTS5 (rebuild da rowid se necessário) ao final.
"""
from __future__ import annotations
import argparse
import gzip
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
RECORDS_GZ = ROOT / "lulometro" / "data" / "records.jsonl.gz"

# Onde está o wrangler.toml. Em prioridade:
# 1. cwd tem wrangler.toml
# 2. <cwd>/worker/wrangler.toml (CI clona worker repo lá)
# 3. ~/Desktop/Code_folder/cursor_testes/datafixers-ibama-worker (uso local)
def _find_worker_dir() -> Path:
    cwd = Path.cwd()
    for cand in [cwd, cwd / "worker",
                 Path.home() / "Desktop/Code_folder/cursor_testes/datafixers-ibama-worker"]:
        if (cand / "wrangler.toml").exists():
            return cand
    raise FileNotFoundError("wrangler.toml não achado em nenhum lugar conhecido")

WORKER_DIR = _find_worker_dir()
DB_NAME = "ibama"
CHUNK_SIZE = 100


def esc(s: str) -> str:
    """Escape SQL string."""
    return "'" + (s or "").replace("'", "''") + "'"


BAD_TITLES = {"request rejected", "the requested url was rejected", ""}


def is_bad_record(rec: dict) -> bool:
    """Record com title WAF, text curto demais ou só com erro."""
    title = (rec.get("title") or "").strip().lower()
    text = (rec.get("text") or "").strip()
    if title in BAD_TITLES:
        return True
    # Para registros do Planalto/Biblioteca, exigir text mínimo. Bluesky pode ter posts curtos.
    if rec.get("source") in ("planalto", "biblioteca") and len(text) < 200:
        return True
    return False


def read_records() -> list[dict]:
    if not RECORDS_GZ.exists():
        print(f"❌ Não achei {RECORDS_GZ}. Rode build_lulometro_data.py primeiro.")
        sys.exit(1)
    out = []
    skipped = 0
    with gzip.open(RECORDS_GZ, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if is_bad_record(rec):
                skipped += 1
                continue
            out.append(rec)
    if skipped:
        print(f"   ⏭️  {skipped} records ignorados (WAF block / texto vazio)", flush=True)
    return out


class AuthError(RuntimeError):
    """Token Cloudflare inválido / sem escopo D1 (API code 10000)."""


def _looks_like_auth_error(text: str) -> bool:
    t = (text or "").lower()
    return (
        "code: 10000" in t
        or '"code": 10000' in t
        or "authentication error" in t
        or "you may have incorrect permissions" in t
    )


def wrangler_exec(sql_or_file: str, is_file: bool = False, retries: int = 3) -> dict:
    cmd = [
        "npx", "wrangler", "d1", "execute", DB_NAME, "--remote", "--json",
    ]
    if is_file:
        cmd.extend(["--file", sql_or_file])
    else:
        cmd.extend(["--command", sql_or_file])

    last_err = ""
    for attempt in range(1, retries + 1):
        res = subprocess.run(cmd, cwd=str(WORKER_DIR), capture_output=True, text=True)
        if res.returncode == 0:
            try:
                return json.loads(res.stdout)
            except json.JSONDecodeError as e:
                print(f"   ⚠ JSON parse falhou: {e}", flush=True)
                print(f"   STDOUT preview: {res.stdout[:500]}", flush=True)
                # Considera sucesso mesmo se JSON não parseou (output OK normalmente)
                return {"ok": True}

        combined = f"{res.stderr}\n{res.stdout}"
        # Erro de autenticação não se resolve com retry: aborta imediatamente
        # com diagnóstico claro em vez de queimar minutos tentando 26 chunks.
        if _looks_like_auth_error(combined):
            print(f"   ✘ Cloudflare API code 10000 (auth).", flush=True)
            print(f"   STDERR: {res.stderr[:800]}", flush=True)
            raise AuthError(
                "Token Cloudflare sem permissão D1. O CLOUDFLARE_API_TOKEN faz "
                "deploy no Pages mas não no D1 — recrie-o incluindo o escopo "
                "'Account → D1 → Edit' e atualize o secret CLOUDFLARE_API_TOKEN."
            )

        last_err = combined
        print(f"   ⚠ wrangler exit {res.returncode} (tentativa {attempt}/{retries})", flush=True)
        print(f"   STDERR: {res.stderr[:1000]}", flush=True)
        if attempt < retries:
            # backoff fixo e curto; falhas D1 transitórias costumam ceder rápido
            subprocess.run(["sleep", str(3 * attempt)])

    print(f"   STDOUT: {last_err[-1000:]}", flush=True)
    return {}


def fetch_existing_signatures() -> dict[str, str]:
    """Retorna {id: updated_at} de tudo já no D1, pra detectar o que mudou."""
    print("→ Consultando D1 pra saber o que já existe...", flush=True)
    res = wrangler_exec("SELECT id, updated_at FROM lulometro_records")
    if not res:
        # Não confunda "consulta falhou" com "D1 vazio": se assumíssemos vazio,
        # o script reescreveria os ~2600 records todo dia. Aborta explícito.
        raise RuntimeError(
            "Consulta inicial ao D1 falhou (sem erro de auth). Abortando para não "
            "reescrever a base inteira por engano — verifique conectividade/D1."
        )
    rows = []
    for item in res:
        rows.extend(item.get("results", []))
    return {row["id"]: row.get("updated_at", "") for row in rows}


def build_upsert_sql(records: list[dict]) -> str:
    """Gera um BEGIN/COMMIT com INSERT OR REPLACE pros records dados."""
    cols = [
        "id", "url", "source", "type", "president_slug", "president",
        "mandate", "date", "title", "location", "description", "text",
        "word_count", "updated_at",
    ]
    lines = []
    for rec in records:
        values = [
            esc(rec.get("id", "")),
            esc(rec.get("url", "")),
            esc(rec.get("source", "")),
            esc(rec.get("type", "")),
            esc(rec.get("president_slug", "")),
            esc(rec.get("president", "")),
            esc(rec.get("mandate", "")),
            esc(rec.get("date", "")),
            esc(rec.get("title", "")),
            esc(rec.get("location", "")),
            esc(rec.get("description", "")),
            esc(rec.get("text", "")),
            str(int(rec.get("word_count") or 0)),
            esc(rec.get("updated_at", "")),
        ]
        lines.append(
            f"INSERT OR REPLACE INTO lulometro_records ({', '.join(cols)}) VALUES ({', '.join(values)});"
        )
    return "\n".join(lines)


def chunked(items: list, n: int) -> Iterable[list]:
    for i in range(0, len(items), n):
        yield items[i : i + n]


def upsert_chunk(records: list[dict], chunk_idx: int, total_chunks: int) -> bool:
    sql = build_upsert_sql(records)
    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False, encoding="utf-8") as tmp:
        tmp.write(sql)
        tmp_path = tmp.name
    try:
        print(f"   chunk {chunk_idx + 1}/{total_chunks} ({len(records)} records)", flush=True)
        res = wrangler_exec(tmp_path, is_file=True)
        if not res:
            return False
        return True
    finally:
        os.unlink(tmp_path)


def rebuild_fts() -> None:
    """Reconstrói FTS5 usando o comando especial 'rebuild' do FTS5 external-content."""
    print("→ Rebuild FTS5 index...", flush=True)
    # FTS5 external-content suporta esse comando especial pra re-sincronizar com a tabela base.
    sql = "INSERT INTO lulometro_fts(lulometro_fts) VALUES('rebuild');"
    wrangler_exec(sql, is_file=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true",
                    help="Re-upserta TODOS os records (default: só os com updated_at diferente)")
    ap.add_argument("--skip-fts", action="store_true",
                    help="Pula rebuild do FTS5 (mais rápido, mas busca pode ficar stale)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Mostra o que seria feito sem executar")
    args = ap.parse_args()

    local_records = read_records()
    print(f"📂 {len(local_records)} records em {RECORDS_GZ.name}", flush=True)

    if args.full:
        to_upsert = local_records
    else:
        existing = fetch_existing_signatures()
        print(f"📊 D1 tem {len(existing)} records hoje", flush=True)
        to_upsert = [
            r for r in local_records
            if existing.get(r.get("id", "")) != r.get("updated_at", "")
        ]
    print(f"🔄 Vão pro upsert: {len(to_upsert)} records", flush=True)

    if args.dry_run:
        for r in to_upsert[:5]:
            print(f"   - {r.get('id','?')} {r.get('date','?')} {r.get('title','')[:60]}")
        if len(to_upsert) > 5:
            print(f"   ... e mais {len(to_upsert) - 5}")
        return 0

    if not to_upsert:
        print("✅ Nada novo pra upsertar. D1 já está sincronizado.")
        return 0

    chunks = list(chunked(to_upsert, CHUNK_SIZE))
    failed = 0
    for i, chunk in enumerate(chunks):
        ok = upsert_chunk(chunk, i, len(chunks))
        if not ok:
            failed += 1
            if failed > 3:
                print(f"❌ Muitas falhas ({failed}). Abortando.")
                return 1

    if not args.skip_fts:
        rebuild_fts()

    print(f"✅ Upsert completo: {len(to_upsert) - failed * CHUNK_SIZE} records sincronizados.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
