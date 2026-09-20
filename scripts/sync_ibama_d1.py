#!/usr/bin/env python3
"""Create small, idempotent D1 SQL batches from IBAMA's official CSV export.

This runs in GitHub Actions, not in a Worker: the source ZIP is too large to
buffer or parse within Workers' memory limits.
"""
import argparse
import csv
import io
import re
import sys
import unicodedata
import zipfile
from pathlib import Path
from typing import Dict, Optional

csv.field_size_limit(sys.maxsize)

FIELDS = (
    ("seq_auto", "SEQ_AUTO_INFRACAO", "integer"),
    ("num_auto", "NUM_AUTO_INFRACAO", "text"),
    ("tipo_auto", "TIPO_AUTO", "text"),
    ("valor", "VAL_AUTO_INFRACAO", "number"),
    ("data_auto", "DAT_HORA_AUTO_INFRACAO", "text"),
    ("data_fato", "DT_FATO_INFRACIONAL", "text"),
    ("uf", "UF", "text"),
    ("municipio", "MUNICIPIO", "text"),
    ("cod_municipio", "COD_MUNICIPIO", "integer"),
    ("num_processo", "NUM_PROCESSO", "text"),
    ("cod_infracao", "COD_INFRACAO", "text"),
    ("des_infracao", "DES_INFRACAO", "text"),
    ("tipo_infracao", "TIPO_INFRACAO", "text"),
    ("tp_pessoa", "TP_PESSOA_INFRATOR", "text"),
    ("nome_infrator", "NOME_INFRATOR", "text"),
    ("cpf_cnpj", "CPF_CNPJ_INFRATOR", "text"),
    ("qt_area", "QT_AREA", "number"),
    ("infracao_area", "INFRACAO_AREA", "text"),
    ("classificacao_area", "CLASSIFICACAO_AREA", "text"),
    ("longitude", "NUM_LONGITUDE_AUTO", "number"),
    ("latitude", "NUM_LATITUDE_AUTO", "number"),
    ("des_local", "DES_LOCAL_INFRACAO", "text"),
    ("unidade_conservacao", "UNIDADE_CONSERVACAO", "text"),
    ("biomas", "DS_BIOMAS_ATINGIDOS", "text"),
    ("gravidade", "GRAVIDADE_INFRACAO", "text"),
    ("status_formulario", "DES_STATUS_FORMULARIO", "text"),
    ("sit_cancelado", "SIT_CANCELADO", "text"),
    ("ds_sit", "DS_SIT_AUTO_AIE", "text"),
    ("ultima_atualizacao", "ULTIMA_ATUALIZACAO_RELATORIO", "text"),
)


def normalized_name(value: str) -> str:
    value = unicodedata.normalize("NFD", (value or "").strip())
    return "".join(c for c in value if not unicodedata.combining(c)).lower()


def sql_value(value: str, kind: str) -> str:
    value = (value or "").strip()
    if not value:
        return "NULL"
    if kind == "text":
        return "'" + value.replace("'", "''") + "'"
    value = re.sub(r"[^0-9.-]", "", value.replace(".", "").replace(",", "."))
    if value in {"", "-", ".", "-."}:
        return "NULL"
    try:
        return str(int(float(value))) if kind == "integer" else str(float(value))
    except ValueError:
        return "NULL"


def to_sql(row: Dict[str, str]) -> Optional[str]:
    if not (row.get("SEQ_AUTO_INFRACAO") or "").strip().isdigit():
        return None
    names = [target for target, _, _ in FIELDS] + ["cpf_cnpj_norm", "nome_norm"]
    values = [sql_value(row.get(source, ""), kind) for _, source, kind in FIELDS]
    values.extend((
        sql_value(re.sub(r"\D", "", row.get("CPF_CNPJ_INFRATOR") or ""), "text"),
        sql_value(normalized_name(row.get("NOME_INFRATOR") or ""), "text"),
    ))
    update = ", ".join(f"{name}=excluded.{name}" for name in names[1:])
    return f"INSERT INTO autos_infracao ({', '.join(names)}) VALUES ({', '.join(values)}) ON CONFLICT(seq_auto) DO UPDATE SET {update};\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("zip_file", type=Path)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    pending, batches, rows, skipped = [], 0, 0, 0
    with zipfile.ZipFile(args.zip_file).open(f"auto_infracao_{args.year}.csv") as raw:
        for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline=""), delimiter=";"):
            statement = to_sql(row)
            if statement is None:
                skipped += 1
                continue
            pending.append(statement)
            rows += 1
            if len(pending) == args.batch_size:
                (args.out_dir / f"batch-{batches:04d}.sql").write_text("".join(pending), encoding="utf-8")
                batches, pending = batches + 1, []
    if pending:
        (args.out_dir / f"batch-{batches:04d}.sql").write_text("".join(pending), encoding="utf-8")
        batches += 1
    print(f"rows={rows} skipped_without_sequence={skipped} batches={batches}")


if __name__ == "__main__":
    main()
