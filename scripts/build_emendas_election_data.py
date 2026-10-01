#!/usr/bin/env python3
"""Cross official 2022 TSE municipal votes with 2026 CGU emenda documents.

The compact TSE baseline is committed once; daily refreshes only download CGU's
2026 document file. Run with --rebuild-votes to refresh the TSE baseline.
"""

import argparse
import csv
import io
import json
import re
import urllib.request
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "emendas" / "data"
CACHE = Path("/tmp/emendas-eleicao-data")
TSE_VOTES = "https://cdn.tse.jus.br/estatistica/sead/odsele/votacao_candidato_munzona/votacao_candidato_munzona_2022.zip"
TSE_CITIES = "https://cdn.tse.jus.br/estatistica/sead/odsele/municipio_tse_ibge/municipio_tse_ibge.zip"
CGU_DOCS = "https://dadosabertos-download.cgu.gov.br/PortalDaTransparencia/saida/emendas-parlamentares-documentos/2026_EmendasParlamentaresPorDocumento.zip"
BASELINE = DATA / "votes_2022_baseline.json"
OUTPUT = DATA / "election_2022.json"
TARGETS = {
    "lula": ("BR", "Presidente", "2", "280001607829"),
    "jair": ("BR", "Presidente", "2", "280001618036"),
    "hugo": ("PB", "Deputado Federal", "1", "150001619545"),
    "davi": ("AP", "Senador", "1", "30001607748"),
}
AUTHORS = {"flavio": "FLAVIO BOLSONARO", "hugo": "HUGO MOTTA", "davi": "DAVI ALCOLUMBRE"}


def cached_zip(url):
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / url.rsplit("/", 1)[-1]
    if not path.exists():
        temp = path.with_suffix(".download")
        urllib.request.urlretrieve(url, temp)
        temp.replace(path)
    return path


def csv_rows(zip_path, suffix):
    with zipfile.ZipFile(zip_path) as archive:
        name = next(name for name in archive.namelist() if name.endswith(suffix))
        with archive.open(name) as stream:
            yield from csv.DictReader(io.TextIOWrapper(stream, encoding="latin-1"), delimiter=";")


def make_votes():
    codes = {}
    for row in csv_rows(cached_zip(TSE_CITIES), "municipio_tse_ibge.csv"):
        ibge = row["CD_MUNICIPIO_IBGE"]
        if re.fullmatch(r"\d{7}", ibge):
            codes[(row["SG_UF"], int(row["CD_MUNICIPIO_TSE"]))] = ibge

    votes = defaultdict(lambda: defaultdict(int))
    missing = defaultdict(int)
    archive_path = cached_zip(TSE_VOTES)
    for uf in ("BR", "PB", "AP"):
        for row in csv_rows(archive_path, f"_2022_{uf}.csv"):
            if row["ST_VOTO_EM_TRANSITO"] == "S":
                continue
            office, round_ = row["DS_CARGO"], row["NR_TURNO"]
            if uf == "BR" and (office, round_) != ("Presidente", "2"):
                continue
            if uf == "PB" and (office, round_) != ("Deputado Federal", "1"):
                continue
            if uf == "AP" and (office, round_) != ("Senador", "1"):
                continue
            key = codes.get((row["SG_UF"], int(row["CD_MUNICIPIO"])))
            if not key:
                missing[row["SG_UF"]] += 1
                continue
            amount = int(row["QT_VOTOS_NOMINAIS_VALIDOS"])
            if not amount:
                continue
            if uf == "BR":
                votes[key]["president_valid"] += amount
            elif uf == "PB":
                votes[key]["deputy_valid"] += amount
            else:
                votes[key]["senate_valid"] += amount
            for target, (target_uf, target_office, target_round, candidate) in TARGETS.items():
                if (uf, office, round_, row["SQ_CANDIDATO"]) == (target_uf, target_office, target_round, candidate):
                    votes[key][target] += amount
    baseline = {
        "source": TSE_VOTES,
        "municipality_mapping_source": TSE_CITIES,
        "definitions": "Votos nominais válidos, agregados das zonas; presidente no 2º turno, deputado federal e senador no 1º. Exclui voto em trânsito.",
        "unmapped_rows": dict(missing),
        "municipalities": {key: dict(value) for key, value in sorted(votes.items())},
    }
    DATA.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(json.dumps(baseline, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return baseline


def cents(value):
    return int(Decimal(value.replace(".", "").replace(",", ".")) * 100)


def make_dashboard(baseline):
    cities = defaultdict(lambda: {"paid": 0, "committed": 0, "authors": defaultdict(lambda: {"paid": 0, "committed": 0})})
    total = {"paid": 0, "committed": 0, "localized_paid": 0, "localized_committed": 0, "rows": 0, "localized_rows": 0}
    observed_dates = []
    for row in csv_rows(cached_zip(CGU_DOCS), "_PorDocumento.csv"):
        if not row["Data Documento"].endswith("/2026"):
            continue
        paid, committed = cents(row["Valor Pago"]), cents(row["Valor Empenhado"])
        total["paid"] += paid
        total["committed"] += committed
        total["rows"] += 1
        observed_dates.append(row["Data Documento"])
        ibge = row["Código IBGE do município de aplicação do recurso"]
        if not re.fullmatch(r"\d{7}", ibge):
            continue
        total["localized_paid"] += paid
        total["localized_committed"] += committed
        total["localized_rows"] += 1
        city = cities[ibge]
        city["paid"] += paid
        city["committed"] += committed
        city["name"] = row["Município de aplicação do recurso"]
        city["uf"] = row["UF de aplicação do recurso"]
        author_name = row["Nome do Autor da Emenda"].upper().strip()
        for author, expected in AUTHORS.items():
            if author_name == expected:
                city["authors"][author]["paid"] += paid
                city["authors"][author]["committed"] += committed

    for key, value in total.items():
        if key.endswith("paid") or key.endswith("committed"):
            total[key] = value / 100
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "document_date_max": max(observed_dates, key=lambda x: datetime.strptime(x, "%d/%m/%Y")),
        "sources": {"votes": TSE_VOTES, "municipality_mapping": TSE_CITIES, "emendas": CGU_DOCS},
        "coverage": total,
        "method": "Somatório dos documentos de 2026 da CGU (inclusive emendas de anos anteriores), por código IBGE do município de aplicação. Valores negativos de estorno são mantidos. Emendas sem município identificado ficam fora do ranking local. Autoria é a registrada pela CGU; não equivale a prova de influência no voto.",
        "cities": [],
    }
    for ibge, city in sorted(cities.items()):
        vote = baseline["municipalities"].get(ibge, {})
        result["cities"].append({
            "ibge": ibge, "name": city["name"], "uf": city["uf"],
            "paid": city["paid"] / 100, "committed": city["committed"] / 100,
            "authors": {key: {metric: value / 100 for metric, value in amounts.items()} for key, amounts in city["authors"].items()},
            "votes": vote,
        })
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {len(result['cities'])} cities to {OUTPUT}; coverage: {total}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rebuild-votes", action="store_true")
    args = parser.parse_args()
    baseline = make_votes() if args.rebuild_votes or not BASELINE.exists() else json.loads(BASELINE.read_text(encoding="utf-8"))
    make_dashboard(baseline)
