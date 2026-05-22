#!/usr/bin/env python3
import argparse
import csv
import datetime as dt
import gzip
import json
import re
import unicodedata
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DASH_DIR = ROOT_DIR / "aeronaves"
DATA_DIR = DASH_DIR / "data"
DATA_FILE = DATA_DIR / "records.jsonl.gz"
META_FILE = DATA_DIR / "metadata.json"
DEFAULT_SOURCE = Path.home() / "Desktop" / "dados_aeronaves.csv"
SEARCH_RESULT_LIMIT = 20

FIELD_MAP = {
    "MARCA": "p",
    "SG_UF": "u",
    "NM_OPERADOR": "n",
    "OUTROS_OPERADORES": "o",
    "UF_OPERADOR": "ou",
    "CPF_CNPJ": "d",
    "PROPRIETARIOS": "pr",
    "DS_MODELO": "m",
    "NM_FABRICANTE": "f",
    "CD_TIPO_ICAO": "i",
    "NR_ANO_FABRICACAO": "y",
    "DT_MATRICULA": "r",
    "DT_CANC": "c",
    "DS_MOTIVO_CANC": "cr",
    "CF_OPERACIONAL": "cf",
    "DS_CATEGORIA_HOMOLOGACAO": "ca",
    "TP_OPERACAO": "to",
    "DS_GRAVAME": "g",
    "TP_CA": "tc",
    "TP_MOTOR": "tm",
    "QT_MOTOR": "qm",
    "NR_PASSAGEIROS_MAX": "ps",
    "NR_ASSENTOS": "as",
    "CD_INTERDICAO": "it",
}


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def clean_value(value):
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.lower() == "null":
        return ""
    return text


def normalize_text(value):
    text = clean_value(value)
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text).strip().lower()


def compact_text(value):
    return re.sub(r"[^a-z0-9]+", "", normalize_text(value))


def format_prefix(prefix):
    text = clean_value(prefix).upper()
    if re.fullmatch(r"[A-Z]{2}[A-Z0-9]{3}", text):
        return f"{text[:2]}-{text[2:]}"
    return text


def parse_source_updated_at(header_line):
    match = re.search(r"(\d{4}-\d{2}-\d{2})", header_line or "")
    if not match:
        return ""
    try:
        return dt.date.fromisoformat(match.group(1)).isoformat()
    except ValueError:
        return ""


def parse_year(value):
    text = clean_value(value)
    if not re.fullmatch(r"\d{4}", text):
        return None
    year = int(text)
    if year < 1900 or year > 2100:
        return None
    return year


def first_owner_name(raw_value):
    raw = clean_value(raw_value)
    if not raw:
        return ""
    first_chunk = raw.split(";")[0].strip()
    if not first_chunk:
        return ""
    first_piece = first_chunk.split("|")[0].strip()
    return first_piece


def write_json(path, payload):
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_dataset(source_path):
    ensure_dirs()
    now_iso = dt.datetime.now().astimezone().isoformat(timespec="seconds")

    row_count = 0
    canceled_rows = 0
    rows_with_document = 0
    masked_document_rows = 0
    null_uf_rows = 0
    ufs = set()
    operators = set()
    year_min = None
    year_max = None
    sample_queries = []

    with source_path.open("r", encoding="utf-8-sig", newline="") as fp:
        source_header = fp.readline().strip()
        source_updated_at = parse_source_updated_at(source_header)
        reader = csv.DictReader(fp, delimiter=";")

        with gzip.open(DATA_FILE, "wt", encoding="utf-8", compresslevel=9) as out_fp:
            for row in reader:
                record = {}
                for source_key, compact_key in FIELD_MAP.items():
                    value = clean_value(row.get(source_key))
                    if value:
                        record[compact_key] = value

                if not record:
                    continue

                row_count += 1
                if record.get("c"):
                    canceled_rows += 1

                document_value = record.get("d", "")
                if document_value:
                    rows_with_document += 1
                    if "x" in document_value.lower():
                        masked_document_rows += 1

                uf_value = record.get("u", "")
                if uf_value:
                    ufs.add(uf_value.upper())
                else:
                    null_uf_rows += 1

                operator_value = record.get("n", "")
                if operator_value:
                    operators.add(normalize_text(operator_value))

                year_value = parse_year(record.get("y"))
                if year_value is not None:
                    year_min = year_value if year_min is None else min(year_min, year_value)
                    year_max = year_value if year_max is None else max(year_max, year_value)

                if len(sample_queries) < 4:
                    for candidate in (
                        format_prefix(record.get("p")),
                        record.get("n", ""),
                        record.get("d", ""),
                        first_owner_name(record.get("pr", "")),
                    ):
                        cleaned = clean_value(candidate)
                        if cleaned and cleaned not in sample_queries:
                            sample_queries.append(cleaned)
                        if len(sample_queries) >= 4:
                            break

                out_fp.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
                out_fp.write("\n")

    active_rows = max(row_count - canceled_rows, 0)
    metadata = {
        "generated_at": now_iso,
        "source_updated_at": source_updated_at,
        "source_header": source_header,
        "source_file": source_path.name,
        "source_size_bytes": source_path.stat().st_size,
        "data_file": DATA_FILE.name,
        "rows": row_count,
        "active_rows": active_rows,
        "canceled_rows": canceled_rows,
        "rows_with_document": rows_with_document,
        "rows_with_masked_document": masked_document_rows,
        "unique_operators": len(operators),
        "null_uf_rows": null_uf_rows,
        "ufs": sorted(ufs),
        "year_min": year_min or "",
        "year_max": year_max or "",
        "search_result_limit": SEARCH_RESULT_LIMIT,
        "sample_queries": sample_queries,
        "methodology": [
            "Esta página foi gerada a partir do CSV original entregue para o site, em vez de consultar o portal da ANAC em tempo real.",
            "Página da ANAC com todos os arquivos: https://www.gov.br/anac/pt-br/sistemas/rab/dados-abertos-rab",
            "Original da ANAC: https://www.gov.br/anac/pt-br/sistemas/rab/dados-abertos-rab#:~:text=Arquivo%20(CSV)%20%2D%20Aeronaves%20%2D%20Registro%20Aeron%C3%A1utico%20Brasileiro%20%2D%20RAB",
            "A busca normaliza maiúsculas, minúsculas e acentos. Para prefixos, CPF e CNPJ, a comparação também remove pontos, barras, espaços e hífens.",
            "Os campos de operador, outros operadores e proprietários foram preservados da base original para permitir buscas por nome e documento em um único campo.",
        ],
        "limits": [
            "CPF e CNPJ podem aparecer mascarados ou truncados na base original. A dashboard não recupera números ocultos.",
            "Alguns campos vêm vazios, com 'null' ou com formatação inconsistente no CSV de origem.",
            f"A interface exibe {SEARCH_RESULT_LIMIT} resultados por página, com navegação entre páginas, para manter desempenho e legibilidade.",
        ],
    }
    write_json(META_FILE, metadata)
    return metadata


def main():
    parser = argparse.ArgumentParser(
        description="Gera a base estática da dashboard de aeronaves para GitHub Pages."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_SOURCE,
        help=f"Caminho do CSV original (padrao: {DEFAULT_SOURCE})",
    )
    args = parser.parse_args()

    source_path = args.input.expanduser().resolve()
    if not source_path.exists():
        raise SystemExit(f"Arquivo nao encontrado: {source_path}")

    metadata = build_dataset(source_path)
    print(
        "[ok] aeronaves-dashboard atualizado:",
        f"rows={metadata['rows']}",
        f"ativas={metadata['active_rows']}",
        f"canceladas={metadata['canceled_rows']}",
        f"arquivo={DATA_FILE.name}",
    )


if __name__ == "__main__":
    main()
