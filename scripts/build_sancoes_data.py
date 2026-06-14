#!/usr/bin/env python3
import csv
import datetime as dt
import json
import re
import shutil
import unicodedata
import zipfile
from collections import Counter
from pathlib import Path

import requests


ROOT_DIR = Path(__file__).resolve().parents[1]
DASH_DIR = ROOT_DIR / "sancoes"
DATA_DIR = DASH_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
OUTPUT_FILE = DATA_DIR / "sancoes.json"

SOURCES = {
    "CEIS": "https://portaldatransparencia.gov.br/download-de-dados/ceis/{date}",
    "CNEP": "https://portaldatransparencia.gov.br/download-de-dados/cnep/{date}",
}
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)
MAX_LOOKBACK_DAYS = 15
SOFT_SIZE_LIMIT = 8 * 1024 * 1024


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)


def cleanup_raw():
    if RAW_DIR.exists():
        shutil.rmtree(RAW_DIR)
    RAW_DIR.mkdir(parents=True, exist_ok=True)


def now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def normalize_text(value):
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\ufeff", "").strip())


def normalize_key(value):
    text = unicodedata.normalize("NFKD", normalize_text(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.upper()
    return re.sub(r"[^A-Z0-9]+", " ", text).strip()


def normalize_doc(value):
    text = normalize_text(value)
    if "*" in text:
        return text
    return re.sub(r"\D+", "", text)


def parse_date(value):
    text = normalize_text(value)
    if not text:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y"):
        try:
            return dt.datetime.strptime(text[:10], fmt).date()
        except ValueError:
            pass
    return None


def date_value(value):
    parsed = parse_date(value)
    if parsed:
        return parsed.isoformat()
    return normalize_text(value)


def years_ago(today, years):
    try:
        return today.replace(year=today.year - years)
    except ValueError:
        return today.replace(year=today.year - years, day=28)


def latest_available_zip(session, source, today):
    headers = {"User-Agent": USER_AGENT, "Accept": "application/zip,*/*"}
    template = SOURCES[source]
    for delta in range(MAX_LOOKBACK_DAYS + 1):
        snapshot = today - dt.timedelta(days=delta)
        date_key = snapshot.strftime("%Y%m%d")
        url = template.format(date=date_key)
        target = RAW_DIR / f"{source.lower()}_{date_key}.zip"
        try:
            response = session.get(url, headers=headers, timeout=240)
        except requests.RequestException as exc:
            print(f"{source}: erro ao baixar {date_key}: {exc}; tentando data anterior.")
            continue
        if response.status_code != 200:
            print(f"{source}: {date_key} retornou HTTP {response.status_code}; tentando data anterior.")
            continue
        target.write_bytes(response.content)
        if zipfile.is_zipfile(target):
            print(f"{source}: usando snapshot {snapshot.isoformat()}.")
            return target, snapshot.isoformat()
        print(f"{source}: {date_key} retornou conteúdo não-zip; tentando data anterior.")
        target.unlink(missing_ok=True)
    raise RuntimeError(f"Não encontrei snapshot disponível para {source} nos últimos {MAX_LOOKBACK_DAYS} dias.")


def choose_column(headers, exact=(), contains=()):
    normalized = {normalize_key(header): header for header in headers}
    for candidate in exact:
        found = normalized.get(normalize_key(candidate))
        if found:
            return found
    scored = []
    for header in headers:
        key = normalize_key(header)
        for tokens in contains:
            if all(token in key for token in tokens):
                scored.append((len(key), header))
                break
    if scored:
        return sorted(scored)[0][1]
    return None


def build_column_map(headers):
    return {
        "tp": choose_column(headers, exact=("TIPO DE PESSOA",), contains=(("TIPO", "PESSOA"),)),
        "doc": choose_column(headers, exact=("CPF OU CNPJ DO SANCIONADO",), contains=(("CPF", "CNPJ", "SANCIONADO"),)),
        "nome": choose_column(headers, exact=("NOME DO SANCIONADO",), contains=(("NOME", "SANCIONADO"),)),
        "rs": choose_column(headers, exact=("RAZÃO SOCIAL - CADASTRO RECEITA",), contains=(("RAZAO", "SOCIAL"),)),
        "nf": choose_column(headers, exact=("NOME FANTASIA - CADASTRO RECEITA",), contains=(("NOME", "FANTASIA"),)),
        "cat": choose_column(headers, exact=("CATEGORIA DA SANÇÃO",), contains=(("CATEGORIA", "SANCAO"),)),
        "org": choose_column(headers, contains=(("ORGAO", "SANCIONADOR"), ("NOME", "ORGAO", "SANCIONADOR"))),
        "proc": choose_column(headers, exact=("NÚMERO DO PROCESSO",), contains=(("NUMERO", "PROCESSO"),)),
        "di": choose_column(headers, exact=("DATA INÍCIO SANÇÃO",), contains=(("DATA", "INICIO", "SANCAO"),)),
        "df": choose_column(headers, exact=("DATA FINAL SANÇÃO",), contains=(("DATA", "FINAL", "SANCAO"),)),
        "dp": choose_column(headers, exact=("DATA PUBLICAÇÃO",), contains=(("DATA", "PUBLICACAO"),)),
        "abr": choose_column(headers, contains=(("ABRANGENCIA",),)),
        "lei": choose_column(headers, contains=(("FUNDAMENTACAO",), ("FUNDAMENTO", "LEGAL"), ("DISPOSITIVO", "LEGAL"))),
    }


def is_active(start_value, end_value, today):
    start = parse_date(start_value)
    end = parse_date(end_value)
    if not start or start > today:
        return False
    return end is None or end >= today


def row_to_record(row, source, columns, today):
    record = {"cadastro": source}
    # "lei" (fundamentação legal) removido de propósito: era texto jurídico longo
    # e repetitivo que sozinho respondia por ~79% do tamanho do JSON. Não é essencial
    # para a busca por CNPJ nem para o panorama; quem quiser consulta pelo nº do processo.
    for key in ("tp", "doc", "nome", "rs", "nf", "cat", "org", "proc", "di", "df", "dp", "abr"):
        column = columns.get(key)
        if not column:
            continue
        value = normalize_doc(row.get(column)) if key == "doc" else normalize_text(row.get(column))
        if key in {"di", "df", "dp"}:
            value = date_value(value)
        if value:
            record[key] = value
    record["at"] = is_active(row.get(columns.get("di", "")), row.get(columns.get("df", "")), today)
    return record


def iter_csv_rows(zip_path):
    with zipfile.ZipFile(zip_path) as archive:
        csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if not csv_names:
            raise RuntimeError(f"Zip sem CSV: {zip_path}")
        with archive.open(csv_names[0]) as raw_file:
            text_file = (line.decode("latin-1") for line in raw_file)
            reader = csv.DictReader(text_file, delimiter=";", quotechar='"')
            columns = build_column_map(reader.fieldnames or [])
            missing = [key for key in ("doc", "nome", "cat", "di", "df", "dp") if not columns.get(key)]
            if missing:
                print(f"Aviso: colunas não mapeadas em {zip_path.name}: {', '.join(missing)}")
            for row in reader:
                yield row, columns


def process_source(source, zip_path, today):
    records = []
    for row, columns in iter_csv_rows(zip_path):
        records.append(row_to_record(row, source, columns, today))
    return records


def parse_record_date(record, key):
    return parse_date(record.get(key))


def should_keep_recent_or_active(record, cutoff):
    if record.get("at"):
        return True
    for key in ("dp", "di", "df"):
        value = parse_record_date(record, key)
        if value and value >= cutoff:
            return True
    return False


def aggregate(records, source_counts, today):
    categories = Counter()
    orgs = Counter()
    split = Counter()
    active = 0
    recent_30 = 0
    recent_90 = 0
    for record in records:
        split[record.get("cadastro") or ""] += 1
        if record.get("at"):
            active += 1
        category = record.get("cat") or "Sem categoria informada"
        org = record.get("org") or "Sem órgão informado"
        categories[category] += 1
        orgs[org] += 1
        published = parse_record_date(record, "dp")
        if published:
            days = (today - published).days
            if 0 <= days <= 30:
                recent_30 += 1
            if 0 <= days <= 90:
                recent_90 += 1
    return {
        "total": len(records),
        "total_ativas": active,
        "novas_30d": recent_30,
        "novas_90d": recent_90,
        "split": dict(sorted(split.items())),
        "categorias": dict(categories.most_common(20)),
        "orgaos_top20": dict(orgs.most_common(20)),
        "processados": source_counts,
    }


def snapshot_label(snapshots):
    unique = sorted(set(snapshots.values()))
    if len(unique) == 1:
        return unique[0]
    return "; ".join(f"{source} {snapshots[source]}" for source in sorted(snapshots))


def serialize(payload):
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def build_payload(records, source_counts, snapshots, today, recorte=None):
    payload = {
        "updated_at": now_iso(),
        "snapshot_date": snapshot_label(snapshots),
        "agregados": aggregate(records, source_counts, today),
        "registros": records,
    }
    if recorte:
        payload["recorte"] = recorte
    return payload


def main():
    ensure_dirs()
    today = dt.date.today()
    session = requests.Session()
    all_records = []
    source_counts = {}
    snapshots = {}
    recorte = None

    try:
        for source in ("CEIS", "CNEP"):
            zip_path, snapshot = latest_available_zip(session, source, today)
            records = process_source(source, zip_path, today)
            all_records.extend(records)
            source_counts[source] = len(records)
            snapshots[source] = snapshot
            print(f"{source}: {len(records):,} registros processados.")

        payload = build_payload(all_records, source_counts, snapshots, today)
        encoded = serialize(payload)

        # Se o JSON minificado ultrapassar o limite prático de deploy, o recorte
        # preserva sanções ativas e registros publicados/iniciados/finalizados nos
        # últimos 6 anos. O total bruto por fonte fica em agregados.processados.
        if len(encoded) > SOFT_SIZE_LIMIT:
            cutoff = years_ago(today, 6)
            filtered = [record for record in all_records if should_keep_recent_or_active(record, cutoff)]
            recorte = f"ativas ou com data de publicação/início/fim a partir de {cutoff.isoformat()}"
            payload = build_payload(filtered, source_counts, snapshots, today, recorte=recorte)
            encoded = serialize(payload)
            print(f"Recorte aplicado: {recorte}. Registros no JSON: {len(filtered):,}.")

        OUTPUT_FILE.write_bytes(encoded)
        size_mb = OUTPUT_FILE.stat().st_size / (1024 * 1024)
        print(f"Arquivo gerado: {OUTPUT_FILE} ({size_mb:.2f} MB).")
        print(f"Sanções ativas: {payload['agregados']['total_ativas']:,}.")
    finally:
        cleanup_raw()


if __name__ == "__main__":
    main()
