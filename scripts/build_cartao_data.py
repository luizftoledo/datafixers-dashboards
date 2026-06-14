#!/usr/bin/env python3
import csv
import datetime as dt
import io
import json
import re
import unicodedata
import zipfile
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests


ROOT_DIR = Path(__file__).resolve().parents[1]
DASH_DIR = ROOT_DIR / "cartao-corporativo"
DATA_DIR = DASH_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
DATA_FILE = DATA_DIR / "cpgf_agg.json"

START_MONTH = "2013-01"
DOWNLOAD_URL_TEMPLATE = "https://portaldatransparencia.gov.br/download-de-dados/cpgf/{yyyymm}"
IPCA_URL = (
    "https://api.bcb.gov.br/dados/serie/bcdata.sgs.433/dados"
    "?formato=json&dataInicial=01/01/2013"
)
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)
GESTOES = [
    {"nome": "Dilma 1", "inicio": "2013-01", "fim": "2014-12", "parcial": True},
    {"nome": "Dilma 2", "inicio": "2015-01", "fim": "2016-08"},
    {"nome": "Temer", "inicio": "2016-09", "fim": "2018-12"},
    {"nome": "Bolsonaro", "inicio": "2019-01", "fim": "2022-12"},
    {"nome": "Lula 3", "inicio": "2023-01", "fim": None, "em_curso": True},
]


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)


def now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def month_to_date(month):
    year, month_num = month.split("-")
    return dt.date(int(year), int(month_num), 1)


def date_to_month(value):
    return f"{value.year:04d}-{value.month:02d}"


def next_month(month):
    value = month_to_date(month)
    if value.month == 12:
        return f"{value.year + 1:04d}-01"
    return f"{value.year:04d}-{value.month + 1:02d}"


def previous_closed_month(today=None):
    today = today or dt.date.today()
    first_day = today.replace(day=1)
    previous = first_day - dt.timedelta(days=1)
    return date_to_month(previous)


def month_range(start, end):
    current = start
    while current <= end:
        yield current
        current = next_month(current)


def load_existing():
    if not DATA_FILE.exists():
        return {}
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def normalize_text(value):
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\ufeff", "").strip())


def normalize_key(value):
    text = unicodedata.normalize("NFKD", normalize_text(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.upper()
    return re.sub(r"[^A-Z0-9]+", " ", text).strip()


def parse_decimal(value):
    text = normalize_text(value)
    if not text:
        return Decimal("0")
    text = text.replace("R$", "").replace(" ", "").replace(".", "").replace(",", ".")
    text = re.sub(r"[^0-9.\-]", "", text)
    if text in {"", "-", ".", "-."}:
        return Decimal("0")
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return Decimal("0")


def to_float(value):
    return float(value.quantize(Decimal("0.01")))


def blank_month():
    return {
        "total": Decimal("0"),
        "saque": Decimal("0"),
        "compra": Decimal("0"),
        "n": 0,
        "orgaos": defaultdict(Decimal),
    }


def blank_presidencia_month():
    return {
        "total": Decimal("0"),
        "saque": Decimal("0"),
        "compra": Decimal("0"),
        "n": 0,
        "unidades": defaultdict(Decimal),
    }


def clean_existing_month(payload):
    orgaos = defaultdict(Decimal)
    for orgao, value in (payload.get("orgaos") or {}).items():
        orgaos[normalize_text(orgao) or "Sem órgão informado"] += Decimal(str(value or 0))
    return {
        "total": Decimal(str(payload.get("total") or 0)),
        "saque": Decimal(str(payload.get("saque") or 0)),
        "compra": Decimal(str(payload.get("compra") or 0)),
        "n": int(payload.get("n") or 0),
        "orgaos": orgaos,
    }


def clean_existing_presidencia_month(payload):
    unidades = defaultdict(Decimal)
    for unidade, value in (payload.get("unidades") or {}).items():
        unidades[normalize_text(unidade) or "Sem unidade informada"] += Decimal(str(value or 0))
    return {
        "total": Decimal(str(payload.get("total") or 0)),
        "saque": Decimal(str(payload.get("saque") or 0)),
        "compra": Decimal(str(payload.get("compra") or 0)),
        "n": int(payload.get("n") or 0),
        "unidades": unidades,
    }


def serializable_month(payload):
    orgaos = {
        key: to_float(value)
        for key, value in sorted(payload["orgaos"].items(), key=lambda item: (-item[1], item[0]))
        if value
    }
    return {
        "total": to_float(payload["total"]),
        "saque": to_float(payload["saque"]),
        "compra": to_float(payload["compra"]),
        "n": int(payload["n"]),
        "orgaos": orgaos,
    }


def serializable_presidencia_month(payload):
    unidades = {
        key: to_float(value)
        for key, value in sorted(payload["unidades"].items(), key=lambda item: (-item[1], item[0]))
        if value
    }
    return {
        "total": to_float(payload["total"]),
        "saque": to_float(payload["saque"]),
        "compra": to_float(payload["compra"]),
        "n": int(payload["n"]),
        "unidades": unidades,
    }


def map_presidencia_unidade(value):
    text = normalize_text(value) or "Sem unidade informada"
    key = normalize_key(text)
    mapping = {
        "GABINETE DE SEGURANCA INSTITUCIONAL PR": "GSI",
        "AGENCIA BRASILEIRA DE INTELIGENCIA": "ABIN",
        "SECRETARIA DE ADMINISTRACAO PR": "Secretaria de Administração",
        "EMPRESA BRASIL DE COMUNICACAO S A": "EBC",
    }
    return mapping.get(key, text.title())


def local_raw_zips():
    return sorted(
        path for path in RAW_DIR.glob("*_CPGF.zip")
        if re.fullmatch(r"\d{6}_CPGF\.zip", path.name)
    )


def raw_zip_month(path):
    yyyymm = path.name[:6]
    return f"{yyyymm[:4]}-{yyyymm[4:6]}"


def session_get(session, url, timeout=300):
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return response


def download_month_zip(session, month):
    yyyymm = month.replace("-", "")
    url = DOWNLOAD_URL_TEMPLATE.format(yyyymm=yyyymm)
    target = RAW_DIR / f"{yyyymm}_CPGF.zip"
    response = session_get(session, url)
    target.write_bytes(response.content)
    if target.stat().st_size == 0:
        raise RuntimeError(f"Download vazio para {month}")
    return target


def row_month(row, fallback_month):
    year = normalize_text(row.get("ANO EXTRATO"))
    month = normalize_text(row.get("MÊS EXTRATO") or row.get("MES EXTRATO"))
    if re.fullmatch(r"\d{4}", year) and re.fullmatch(r"\d{1,2}", month):
        month_num = int(month)
        if 1 <= month_num <= 12:
            return f"{int(year):04d}-{month_num:02d}"
    return fallback_month


def open_csv_text_from_zip(zf, csv_name):
    data = zf.read(csv_name)
    last_error = None
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = data.decode(encoding)
            return io.StringIO(text, newline="")
        except UnicodeDecodeError as exc:
            last_error = exc
    raise RuntimeError(f"Não foi possível decodificar {csv_name}") from last_error


def parse_csv_from_zip(path, fallback_month):
    aggregates = defaultdict(blank_month)
    presidencia = defaultdict(blank_presidencia_month)
    with zipfile.ZipFile(path) as zf:
        csv_names = [name for name in zf.namelist() if name.lower().endswith(".csv")]
        if not csv_names:
            raise RuntimeError(f"ZIP sem CSV: {path.name}")
        text_fp = open_csv_text_from_zip(zf, csv_names[0])
        reader = csv.DictReader(text_fp, delimiter=";", quotechar='"')
        reader.fieldnames = [normalize_text(name) for name in (reader.fieldnames or [])]
        for row in reader:
            normalized_row = {normalize_text(key): value for key, value in row.items()}
            month = row_month(normalized_row, fallback_month)
            value = parse_decimal(normalized_row.get("VALOR TRANSAÇÃO") or normalized_row.get("VALOR TRANSACAO"))
            transaction = normalize_key(normalized_row.get("TRANSAÇÃO") or normalized_row.get("TRANSACAO"))
            orgao = normalize_text(
                normalized_row.get("NOME ÓRGÃO SUPERIOR")
                or normalized_row.get("NOME ORGAO SUPERIOR")
                or "Sem órgão informado"
            )
            bucket = aggregates[month]
            bucket["total"] += value
            if "SAQUE" in transaction:
                bucket["saque"] += value
            else:
                bucket["compra"] += value
            bucket["n"] += 1
            bucket["orgaos"][orgao] += value

            if "PRESID" in normalize_key(orgao):
                unidade = map_presidencia_unidade(
                    normalized_row.get("NOME UNIDADE GESTORA")
                    or normalized_row.get("NOME UNIDADE GESTORA".replace("Ó", "O"))
                    or "Sem unidade informada"
                )
                pres_bucket = presidencia[month]
                pres_bucket["total"] += value
                if "SAQUE" in transaction:
                    pres_bucket["saque"] += value
                else:
                    pres_bucket["compra"] += value
                pres_bucket["n"] += 1
                pres_bucket["unidades"][unidade] += value
    return aggregates, presidencia


def fetch_ipca_factors(session, latest_month):
    response = session_get(session, IPCA_URL, timeout=180)
    rows = response.json()
    monthly_rates = {}
    for row in rows:
        date_text = row.get("data", "")
        value_text = row.get("valor", "")
        try:
            parsed = dt.datetime.strptime(date_text, "%d/%m/%Y").date()
            monthly_rates[date_to_month(parsed)] = Decimal(str(value_text).replace(",", "."))
        except (ValueError, InvalidOperation):
            continue

    months = list(month_range(START_MONTH, latest_month))
    factors = {}
    cumulative = Decimal("1")
    for month in reversed(months):
        factors[month] = cumulative
        cumulative *= Decimal("1") + (monthly_rates.get(month, Decimal("0")) / Decimal("100"))
    return {month: float(factors[month].quantize(Decimal("0.000001"))) for month in months}


def build():
    ensure_dirs()
    existing = load_existing()
    raw_zips = [(raw_zip_month(path), path) for path in local_raw_zips()]
    raw_zips = [(month, path) for month, path in raw_zips if START_MONTH <= month <= previous_closed_month()]
    if not raw_zips:
        raise RuntimeError(f"Nenhum ZIP local encontrado em {RAW_DIR.relative_to(ROOT_DIR)}")

    latest_month = max(month for month, _path in raw_zips)
    target_months = list(month_range(START_MONTH, latest_month))
    raw_by_month = {month: path for month, path in raw_zips}
    missing = [month for month in target_months if month not in raw_by_month]
    if missing:
        raise RuntimeError(f"ZIPs locais ausentes para: {', '.join(missing[:12])}")

    months = {}
    presidencia = {}

    for index, month in enumerate(target_months, start=1):
        print(f"[{index}/{len(target_months)}] processando CPGF {month}")
        parsed, parsed_presidencia = parse_csv_from_zip(raw_by_month[month], month)
        for parsed_month, aggregate in parsed.items():
            if START_MONTH <= parsed_month <= latest_month:
                months[parsed_month] = aggregate
        for parsed_month, aggregate in parsed_presidencia.items():
            if START_MONTH <= parsed_month <= latest_month:
                presidencia[parsed_month] = aggregate

    ipca_fator = {
        month: (existing.get("ipca_fator") or {}).get(month)
        for month in target_months
    }
    missing_ipca = [month for month, value in ipca_fator.items() if value is None]
    if missing_ipca:
        raise RuntimeError(
            "ipca_fator ausente no JSON existente para: "
            + ", ".join(missing_ipca[:12])
        )
    ordered_months = {
        month: serializable_month(months.get(month, blank_month()))
        for month in target_months
        if month in months
    }
    ordered_presidencia = {
        month: serializable_presidencia_month(presidencia.get(month, blank_presidencia_month()))
        for month in target_months
        if month in presidencia
    }
    payload = {
        "updated_at": now_iso(),
        "latest_month": latest_month,
        "first_month": START_MONTH,
        "months": ordered_months,
        "ipca_fator": ipca_fator,
        "gestoes": existing.get("gestoes") or GESTOES,
        "presidencia": ordered_presidencia,
    }
    DATA_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    print(
        f"Gerado {DATA_FILE.relative_to(ROOT_DIR)} com "
        f"{len(ordered_months)} meses agregados e "
        f"{len(ordered_presidencia)} meses com dados da Presidência."
    )


if __name__ == "__main__":
    build()
