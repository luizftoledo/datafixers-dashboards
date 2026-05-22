#!/usr/bin/env python3
"""Build data files for the Basometro dashboard.

This script uses annual CSV dumps from the Camara dos Deputados open data portal
(votacoes, votacoesVotos, votacoesOrientacoes), which remain available in 2026.

Outputs (under basometro-dashboard/data):
- report_data.json
- metadata.json
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import shutil
import tempfile
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)

BASE_DOWNLOAD_URL = "https://dadosabertos.camara.leg.br/arquivos"

ROOT_DIR = Path(__file__).resolve().parents[1]
DASH_DIR = ROOT_DIR / "basometro"
DATA_DIR = DASH_DIR / "data"
REPORT_FILE = DATA_DIR / "report_data.json"
META_FILE = DATA_DIR / "metadata.json"

# Government periods used in the dashboard.
# End dates are inclusive.
GOVERNMENT_PERIODS = [
    {"id": "lula1", "label": "Lula 1", "start": "2003-01-01", "end": "2006-12-31"},
    {"id": "lula2", "label": "Lula 2", "start": "2007-01-01", "end": "2010-12-31"},
    {"id": "dilma1", "label": "Dilma 1", "start": "2011-01-01", "end": "2014-12-31"},
    {"id": "dilma2", "label": "Dilma 2", "start": "2015-01-01", "end": "2016-05-12"},
    {"id": "temer", "label": "Temer", "start": "2016-05-13", "end": "2018-12-31"},
    {"id": "bolsonaro", "label": "Bolsonaro", "start": "2019-01-01", "end": "2022-12-31"},
    {"id": "lula3", "label": "Lula 3", "start": "2023-01-01", "end": None},
]

VOTE_NORMALIZATION = {
    "SIM": "Sim",
    "NÃO": "Não",
    "NAO": "Não",
    "ABSTENÇÃO": "Abstenção",
    "ABSTENCAO": "Abstenção",
    "OBSTRUÇÃO": "Obstrução",
    "OBSTRUCAO": "Obstrução",
    "ARTIGO 17": "Artigo 17",
    "ART. 17": "Artigo 17",
    "ART 17": "Artigo 17",
    "ART.17": "Artigo 17",
}

VOTE_KIND_LABELS = {
    "all": "Todas",
    "merito": "Mérito",
    "procedimental": "Procedimental",
}

# Context used in UI notes to avoid misinterpretation with extinct/fused parties.
PARTY_CONTEXT = {
    "PSL": "Partido extinto; parte da bancada migrou para o União Brasil (fusão PSL + DEM).",
    "DEM": "Partido extinto; fundiu-se com o PSL para formar o União Brasil.",
    "PSC": "Partido incorporado ao Progressistas (PP) em 2023.",
    "PTB": "Teve o registro partidário cancelado pelo TSE em 2022.",
    "PROS": "Partido incorporado ao Solidariedade em 2023.",
}

PROCEDURAL_KEYWORDS = [
    "requerimento",
    "urgencia",
    "urgência",
    "adiamento",
    "retirada de pauta",
    "inversao de pauta",
    "inversão de pauta",
    "encerramento da discussao",
    "encerramento da discussão",
    "preferencia",
    "preferência",
    "quebra de intersticio",
    "quebra de interstício",
    "art. 155",
    "art 155",
    "destaque",
]


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def text(value: object) -> str:
    if value is None:
        return ""
    return str(value).replace("\ufeff", "").strip()


def fold_text(value: object) -> str:
    raw = text(value).lower()
    if not raw:
        return ""
    for src, dst in (
        ("á", "a"),
        ("à", "a"),
        ("â", "a"),
        ("ã", "a"),
        ("é", "e"),
        ("ê", "e"),
        ("í", "i"),
        ("ó", "o"),
        ("ô", "o"),
        ("õ", "o"),
        ("ú", "u"),
        ("ç", "c"),
    ):
        raw = raw.replace(src, dst)
    return re.sub(r"\s+", " ", raw).strip()


def to_int(value: object) -> int:
    raw = text(value)
    if not raw:
        return 0
    try:
        return int(raw)
    except ValueError:
        return 0


def classify_vote_kind(description: str, approval: str) -> str:
    sample = f"{fold_text(description)} {fold_text(approval)}".strip()
    if not sample:
        return "merito"
    if any(token in sample for token in PROCEDURAL_KEYWORDS):
        return "procedimental"
    return "merito"


def serialize_vote_bucket(raw: dict, include_votacoes: bool = False) -> dict:
    out = {}
    for kind, stats in raw.items():
        total = int(stats.get("total", 0))
        row = {
            "pro_votes": int(stats.get("pro", 0)),
            "anti_votes": int(stats.get("anti", 0)),
            "total_votes": total,
            "alignment_pct": pct(int(stats.get("pro", 0)), total),
        }
        if include_votacoes:
            row["votacoes"] = int(stats.get("votacoes", 0))
        out[kind] = row
    return out


def normalize_vote(value: object) -> str:
    raw = text(value)
    if not raw:
        return ""
    key = raw.upper()
    key = key.replace("Á", "A").replace("À", "A").replace("Â", "A").replace("Ã", "A")
    key = key.replace("É", "E").replace("Ê", "E")
    key = key.replace("Í", "I")
    key = key.replace("Ó", "O").replace("Ô", "O").replace("Õ", "O")
    key = key.replace("Ú", "U")
    key = re.sub(r"\s+", " ", key).strip()
    return VOTE_NORMALIZATION.get(key, raw)


def parse_date(value: str) -> dt.date | None:
    try:
        return dt.datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_month(value: str) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.datetime.strptime(f"{value}-01", "%Y-%m-%d").date()
    except ValueError:
        return None


def iter_month_keys(start_month: str, end_month: str) -> list[str]:
    start = parse_month(start_month)
    end = parse_month(end_month)
    if not start or not end or start > end:
        return []

    out = []
    current = start
    while current <= end:
        out.append(f"{current.year:04d}-{current.month:02d}")
        if current.month == 12:
            current = dt.date(current.year + 1, 1, 1)
        else:
            current = dt.date(current.year, current.month + 1, 1)
    return out


def government_for_date(date_iso: str) -> tuple[str, str]:
    date_obj = parse_date(date_iso)
    if date_obj is None:
        return "outro", "Outro"

    for gov in GOVERNMENT_PERIODS:
        start = parse_date(gov["start"])
        end = parse_date(gov["end"]) if gov["end"] else None
        if start and date_obj >= start and (end is None or date_obj <= end):
            return gov["id"], gov["label"]

    return "outro", "Outro"


def pct(part: int, whole: int) -> float:
    if whole <= 0:
        return 0.0
    return round((part / whole) * 100, 2)


def download_file(url: str, target: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=300) as response:
        with target.open("wb") as out:
            shutil.copyfileobj(response, out, length=1024 * 1024)


def csv_reader(path: Path):
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh, delimiter=";")
        for row in reader:
            yield row


def download_year_files(year: int, tmp_dir: Path) -> dict[str, Path]:
    paths = {
        "votacoes": tmp_dir / f"votacoes-{year}.csv",
        "votos": tmp_dir / f"votacoesVotos-{year}.csv",
        "orientacoes": tmp_dir / f"votacoesOrientacoes-{year}.csv",
    }

    urls = {
        "votacoes": f"{BASE_DOWNLOAD_URL}/votacoes/csv/votacoes-{year}.csv",
        "votos": f"{BASE_DOWNLOAD_URL}/votacoesVotos/csv/votacoesVotos-{year}.csv",
        "orientacoes": f"{BASE_DOWNLOAD_URL}/votacoesOrientacoes/csv/votacoesOrientacoes-{year}.csv",
    }

    for key, url in urls.items():
        download_file(url, paths[key])

    return paths


def aggregate_year(year: int, files: dict[str, Path]) -> dict:
    votacoes_info: dict[str, dict] = {}

    for row in csv_reader(files["votacoes"]):
        votacao_id = text(row.get("id"))
        if not votacao_id:
            continue

        # Keep only plenary votes from Chamber.
        if text(row.get("idOrgao")) != "180":
            continue

        data_iso = text(row.get("data"))
        datetime_iso = text(row.get("dataHoraRegistro"))
        descricao = text(row.get("descricao"))
        aprovacao = text(row.get("aprovacao"))
        vote_uri = text(row.get("uri")) or f"https://dadosabertos.camara.leg.br/api/v2/votacoes/{votacao_id}"
        proposal_uri = text(row.get("ultimaApresentacaoProposicao_uriProposicao"))
        vote_kind = classify_vote_kind(descricao, aprovacao)

        gov_id, gov_label = government_for_date(data_iso)
        votacoes_info[votacao_id] = {
            "id": votacao_id,
            "date": data_iso,
            "datetime": datetime_iso,
            "description": descricao,
            "approval": aprovacao,
            "vote_uri": vote_uri,
            "proposal_uri": proposal_uri,
            "vote_kind": vote_kind,
            "votos_sim_csv": to_int(row.get("votosSim")),
            "votos_nao_csv": to_int(row.get("votosNao")),
            "votos_outros_csv": to_int(row.get("votosOutros")),
            "government_id": gov_id,
            "government_label": gov_label,
        }

    orientacao_governo: dict[str, str] = {}
    for row in csv_reader(files["orientacoes"]):
        votacao_id = text(row.get("idVotacao"))
        if votacao_id not in votacoes_info:
            continue

        bancada = text(row.get("siglaBancada")).lower()
        if bancada != "governo":
            continue

        orient = normalize_vote(row.get("orientacao"))
        if not orient:
            continue

        orientacao_governo[votacao_id] = orient

    valid_vote_ids = set(votacoes_info.keys()) & set(orientacao_governo.keys())

    vote_level = {}
    seen_deputy_in_vote = defaultdict(set)
    last_party_by_deputy: dict[str, str] = {}
    last_uf_by_deputy: dict[str, str] = {}

    for row in csv_reader(files["votos"]):
        votacao_id = text(row.get("idVotacao"))
        if votacao_id not in valid_vote_ids:
            continue

        vote_type = normalize_vote(row.get("voto"))
        if not vote_type:
            continue

        deputy_id = text(row.get("deputado_id"))
        deputy_name = text(row.get("deputado_nome"))
        deputy_key = deputy_id or f"name::{deputy_name}"
        if not deputy_key:
            continue

        if deputy_key in seen_deputy_in_vote[votacao_id]:
            continue
        seen_deputy_in_vote[votacao_id].add(deputy_key)

        raw_party = text(row.get("deputado_siglaPartido"))
        if raw_party:
            party = raw_party
            last_party_by_deputy[deputy_key] = raw_party
        else:
            party = last_party_by_deputy.get(deputy_key, "Sem partido")

        raw_uf = text(row.get("deputado_siglaUf"))
        if raw_uf:
            uf = raw_uf
            last_uf_by_deputy[deputy_key] = raw_uf
        else:
            uf = last_uf_by_deputy.get(deputy_key, "--")

        orient = orientacao_governo[votacao_id]
        is_pro = vote_type == orient

        entry = vote_level.setdefault(
            votacao_id,
            {
                "pro": 0,
                "anti": 0,
                "total": 0,
                "vote_kind": votacoes_info[votacao_id]["vote_kind"],
                "vote_breakdown": defaultdict(int),
                "deputies": {},
                "parties": {},
            },
        )

        entry["total"] += 1
        if is_pro:
            entry["pro"] += 1
        else:
            entry["anti"] += 1
        entry["vote_breakdown"][vote_type] += 1

        dep = entry["deputies"].setdefault(
            deputy_key,
            {
                "deputy_id": deputy_id,
                "name": deputy_name,
                "party": party,
                "uf": uf,
                "pro": 0,
                "anti": 0,
                "total": 0,
            },
        )
        dep["total"] += 1
        dep["pro"] += int(is_pro)
        dep["anti"] += int(not is_pro)
        # Keep the newest observed party/UF for display consistency.
        dep["party"] = party
        dep["uf"] = uf

        party_row = entry["parties"].setdefault(party, {"pro": 0, "anti": 0, "total": 0})
        party_row["total"] += 1
        party_row["pro"] += int(is_pro)
        party_row["anti"] += int(not is_pro)

    monthly = defaultdict(
        lambda: {
            "pro": 0,
            "anti": 0,
            "total": 0,
            "votacoes": 0,
            "government_id": "",
            "government_label": "",
            "by_vote_kind": defaultdict(lambda: {"pro": 0, "anti": 0, "total": 0, "votacoes": 0}),
        }
    )
    yearly = defaultdict(lambda: {"pro": 0, "anti": 0, "total": 0, "votacoes": 0})

    government_totals = defaultdict(
        lambda: {
            "pro": 0,
            "anti": 0,
            "total": 0,
            "votacoes": 0,
            "by_vote_kind": defaultdict(lambda: {"pro": 0, "anti": 0, "total": 0, "votacoes": 0}),
        }
    )
    party_totals = defaultdict(
        lambda: {
            "pro": 0,
            "anti": 0,
            "total": 0,
            "by_government": defaultdict(lambda: {"pro": 0, "anti": 0, "total": 0}),
            "by_vote_kind": defaultdict(lambda: {"pro": 0, "anti": 0, "total": 0}),
            "by_government_kind": defaultdict(lambda: defaultdict(lambda: {"pro": 0, "anti": 0, "total": 0})),
        }
    )
    deputy_totals = {}

    vote_summaries = []

    for votacao_id, counters in vote_level.items():
        info = votacoes_info[votacao_id]
        data_iso = info["date"]
        if not data_iso:
            continue

        total = counters["total"]
        if total <= 0:
            continue

        pro = counters["pro"]
        anti = counters["anti"]
        vote_kind = counters.get("vote_kind", "merito")

        month_key = data_iso[:7]
        year_key = data_iso[:4]

        month_row = monthly[month_key]
        month_row["pro"] += pro
        month_row["anti"] += anti
        month_row["total"] += total
        month_row["votacoes"] += 1
        month_row["government_id"] = info["government_id"]
        month_row["government_label"] = info["government_label"]
        month_kind = month_row["by_vote_kind"][vote_kind]
        month_kind["pro"] += pro
        month_kind["anti"] += anti
        month_kind["total"] += total
        month_kind["votacoes"] += 1

        yr = yearly[year_key]
        yr["pro"] += pro
        yr["anti"] += anti
        yr["total"] += total
        yr["votacoes"] += 1

        gov = government_totals[info["government_id"]]
        gov["pro"] += pro
        gov["anti"] += anti
        gov["total"] += total
        gov["votacoes"] += 1
        gov_kind = gov["by_vote_kind"][vote_kind]
        gov_kind["pro"] += pro
        gov_kind["anti"] += anti
        gov_kind["total"] += total
        gov_kind["votacoes"] += 1

        for party, pstats in counters["parties"].items():
            p = party_totals[party]
            p["pro"] += pstats["pro"]
            p["anti"] += pstats["anti"]
            p["total"] += pstats["total"]
            pg = p["by_government"][info["government_id"]]
            pg["pro"] += pstats["pro"]
            pg["anti"] += pstats["anti"]
            pg["total"] += pstats["total"]
            pk = p["by_vote_kind"][vote_kind]
            pk["pro"] += pstats["pro"]
            pk["anti"] += pstats["anti"]
            pk["total"] += pstats["total"]
            pgk = p["by_government_kind"][info["government_id"]][vote_kind]
            pgk["pro"] += pstats["pro"]
            pgk["anti"] += pstats["anti"]
            pgk["total"] += pstats["total"]

        for deputy_key, dstats in counters["deputies"].items():
            dep = deputy_totals.setdefault(
                deputy_key,
                {
                    "deputy_id": dstats["deputy_id"],
                    "name": dstats["name"],
                    "party": dstats["party"],
                    "uf": dstats["uf"],
                    "pro": 0,
                    "anti": 0,
                    "total": 0,
                    "by_government": defaultdict(lambda: {"pro": 0, "anti": 0, "total": 0}),
                    "by_vote_kind": defaultdict(lambda: {"pro": 0, "anti": 0, "total": 0}),
                    "by_government_kind": defaultdict(lambda: defaultdict(lambda: {"pro": 0, "anti": 0, "total": 0})),
                },
            )
            dep["pro"] += dstats["pro"]
            dep["anti"] += dstats["anti"]
            dep["total"] += dstats["total"]
            if dstats["party"] and dstats["party"] != "Sem partido":
                dep["party"] = dstats["party"]
            elif not dep.get("party"):
                dep["party"] = dstats["party"] or "Sem partido"
            if dstats["uf"] and dstats["uf"] != "--":
                dep["uf"] = dstats["uf"]
            elif not dep.get("uf"):
                dep["uf"] = dstats["uf"] or "--"
            dg = dep["by_government"][info["government_id"]]
            dg["pro"] += dstats["pro"]
            dg["anti"] += dstats["anti"]
            dg["total"] += dstats["total"]
            dk = dep["by_vote_kind"][vote_kind]
            dk["pro"] += dstats["pro"]
            dk["anti"] += dstats["anti"]
            dk["total"] += dstats["total"]
            dgk = dep["by_government_kind"][info["government_id"]][vote_kind]
            dgk["pro"] += dstats["pro"]
            dgk["anti"] += dstats["anti"]
            dgk["total"] += dstats["total"]

        vote_summaries.append(
            {
                "id": votacao_id,
                "date": data_iso,
                "datetime": info["datetime"],
                "description": info["description"],
                "vote_uri": info["vote_uri"],
                "proposal_uri": info["proposal_uri"],
                "government_id": info["government_id"],
                "government_label": info["government_label"],
                "gov_orientation": orientacao_governo[votacao_id],
                "approval": info["approval"],
                "vote_kind": vote_kind,
                "pro_votes": pro,
                "anti_votes": anti,
                "total_votes": total,
                "sim_votes": counters["vote_breakdown"].get("Sim", 0),
                "nao_votes": counters["vote_breakdown"].get("Não", 0),
                "abstencao_votes": counters["vote_breakdown"].get("Abstenção", 0),
                "obstrucao_votes": counters["vote_breakdown"].get("Obstrução", 0),
                "artigo17_votes": counters["vote_breakdown"].get("Artigo 17", 0),
                "outros_votes_csv": info.get("votos_outros_csv", 0),
                "alignment_pct": pct(pro, total),
            }
        )

    def serialize_party_totals(raw_dict):
        out = {}
        for party, stats in raw_dict.items():
            out[party] = {
                "pro": stats["pro"],
                "anti": stats["anti"],
                "total": stats["total"],
                "alignment_pct": pct(stats["pro"], stats["total"]),
                "by_government": {
                    gov_id: {
                        "pro": gstats["pro"],
                        "anti": gstats["anti"],
                        "total": gstats["total"],
                        "alignment_pct": pct(gstats["pro"], gstats["total"]),
                    }
                    for gov_id, gstats in stats["by_government"].items()
                },
                "by_vote_kind": serialize_vote_bucket(stats["by_vote_kind"]),
                "by_government_kind": {
                    gov_id: serialize_vote_bucket(kind_stats)
                    for gov_id, kind_stats in stats["by_government_kind"].items()
                },
            }
        return out

    def serialize_deputy_totals(raw_dict):
        out = {}
        for key, stats in raw_dict.items():
            out[key] = {
                "deputy_id": stats["deputy_id"],
                "name": stats["name"],
                "party": stats["party"],
                "uf": stats["uf"],
                "pro": stats["pro"],
                "anti": stats["anti"],
                "total": stats["total"],
                "alignment_pct": pct(stats["pro"], stats["total"]),
                "by_government": {
                    gov_id: {
                        "pro": gstats["pro"],
                        "anti": gstats["anti"],
                        "total": gstats["total"],
                        "alignment_pct": pct(gstats["pro"], gstats["total"]),
                    }
                    for gov_id, gstats in stats["by_government"].items()
                },
                "by_vote_kind": serialize_vote_bucket(stats["by_vote_kind"]),
                "by_government_kind": {
                    gov_id: serialize_vote_bucket(kind_stats)
                    for gov_id, kind_stats in stats["by_government_kind"].items()
                },
            }
        return out

    return {
        "year": year,
        "monthly": {
            month: {
                "pro": stats["pro"],
                "anti": stats["anti"],
                "total": stats["total"],
                "votacoes": stats["votacoes"],
                "alignment_pct": pct(stats["pro"], stats["total"]),
                "government_id": stats["government_id"],
                "government_label": stats["government_label"],
                "by_vote_kind": serialize_vote_bucket(stats["by_vote_kind"], include_votacoes=True),
            }
            for month, stats in monthly.items()
        },
        "yearly": {
            key: {
                "pro": stats["pro"],
                "anti": stats["anti"],
                "total": stats["total"],
                "votacoes": stats["votacoes"],
                "alignment_pct": pct(stats["pro"], stats["total"]),
            }
            for key, stats in yearly.items()
        },
        "government_totals": {
            gov_id: {
                "pro": stats["pro"],
                "anti": stats["anti"],
                "total": stats["total"],
                "votacoes": stats["votacoes"],
                "alignment_pct": pct(stats["pro"], stats["total"]),
                "by_vote_kind": serialize_vote_bucket(stats["by_vote_kind"], include_votacoes=True),
            }
            for gov_id, stats in government_totals.items()
        },
        "party_totals": serialize_party_totals(party_totals),
        "deputy_totals": serialize_deputy_totals(deputy_totals),
        "vote_summaries": vote_summaries,
        "counts": {
            "votacoes_plenario": len(votacoes_info),
            "votacoes_com_orientacao_governo": len(valid_vote_ids),
            "votacoes_nominais_validas": len(vote_summaries),
        },
    }


def merge_years(year_payloads: list[dict], start_year: int, end_year: int) -> dict:
    monthly = defaultdict(
        lambda: {
            "pro": 0,
            "anti": 0,
            "total": 0,
            "votacoes": 0,
            "government_id": "",
            "government_label": "",
            "by_vote_kind": defaultdict(lambda: {"pro": 0, "anti": 0, "total": 0, "votacoes": 0}),
        }
    )
    yearly = defaultdict(lambda: {"pro": 0, "anti": 0, "total": 0, "votacoes": 0})
    government_totals = defaultdict(
        lambda: {
            "pro": 0,
            "anti": 0,
            "total": 0,
            "votacoes": 0,
            "by_vote_kind": defaultdict(lambda: {"pro": 0, "anti": 0, "total": 0, "votacoes": 0}),
        }
    )

    party_totals = defaultdict(
        lambda: {
            "pro": 0,
            "anti": 0,
            "total": 0,
            "by_government": defaultdict(lambda: {"pro": 0, "anti": 0, "total": 0}),
            "by_vote_kind": defaultdict(lambda: {"pro": 0, "anti": 0, "total": 0}),
            "by_government_kind": defaultdict(lambda: defaultdict(lambda: {"pro": 0, "anti": 0, "total": 0})),
        }
    )
    deputy_totals = {}

    vote_summaries = []
    raw_counts = defaultdict(int)

    for payload in year_payloads:
        for month, stats in payload["monthly"].items():
            row = monthly[month]
            row["pro"] += stats["pro"]
            row["anti"] += stats["anti"]
            row["total"] += stats["total"]
            row["votacoes"] += stats["votacoes"]
            row["government_id"] = stats["government_id"]
            row["government_label"] = stats["government_label"]
            for kind, kind_stats in stats.get("by_vote_kind", {}).items():
                mk = row["by_vote_kind"][kind]
                mk["pro"] += int(kind_stats.get("pro_votes", 0))
                mk["anti"] += int(kind_stats.get("anti_votes", 0))
                mk["total"] += int(kind_stats.get("total_votes", 0))
                mk["votacoes"] += int(kind_stats.get("votacoes", 0))

        for year_key, stats in payload["yearly"].items():
            row = yearly[year_key]
            row["pro"] += stats["pro"]
            row["anti"] += stats["anti"]
            row["total"] += stats["total"]
            row["votacoes"] += stats["votacoes"]

        for gov_id, stats in payload["government_totals"].items():
            row = government_totals[gov_id]
            row["pro"] += stats["pro"]
            row["anti"] += stats["anti"]
            row["total"] += stats["total"]
            row["votacoes"] += stats["votacoes"]
            for kind, kind_stats in stats.get("by_vote_kind", {}).items():
                gk = row["by_vote_kind"][kind]
                gk["pro"] += int(kind_stats.get("pro_votes", 0))
                gk["anti"] += int(kind_stats.get("anti_votes", 0))
                gk["total"] += int(kind_stats.get("total_votes", 0))
                gk["votacoes"] += int(kind_stats.get("votacoes", 0))

        for party, stats in payload["party_totals"].items():
            p = party_totals[party]
            p["pro"] += stats["pro"]
            p["anti"] += stats["anti"]
            p["total"] += stats["total"]
            for gov_id, gstats in stats.get("by_government", {}).items():
                pg = p["by_government"][gov_id]
                pg["pro"] += gstats["pro"]
                pg["anti"] += gstats["anti"]
                pg["total"] += gstats["total"]
            for kind, kind_stats in stats.get("by_vote_kind", {}).items():
                pk = p["by_vote_kind"][kind]
                pk["pro"] += int(kind_stats.get("pro_votes", 0))
                pk["anti"] += int(kind_stats.get("anti_votes", 0))
                pk["total"] += int(kind_stats.get("total_votes", 0))
            for gov_id, gov_kind_stats in stats.get("by_government_kind", {}).items():
                for kind, kind_stats in gov_kind_stats.items():
                    pgk = p["by_government_kind"][gov_id][kind]
                    pgk["pro"] += int(kind_stats.get("pro_votes", 0))
                    pgk["anti"] += int(kind_stats.get("anti_votes", 0))
                    pgk["total"] += int(kind_stats.get("total_votes", 0))

        for dep_key, stats in payload["deputy_totals"].items():
            dep = deputy_totals.setdefault(
                dep_key,
                {
                    "deputy_id": stats["deputy_id"],
                    "name": stats["name"],
                    "party": stats["party"],
                    "uf": stats["uf"],
                    "pro": 0,
                    "anti": 0,
                    "total": 0,
                    "by_government": defaultdict(lambda: {"pro": 0, "anti": 0, "total": 0}),
                    "by_vote_kind": defaultdict(lambda: {"pro": 0, "anti": 0, "total": 0}),
                    "by_government_kind": defaultdict(lambda: defaultdict(lambda: {"pro": 0, "anti": 0, "total": 0})),
                },
            )
            dep["pro"] += stats["pro"]
            dep["anti"] += stats["anti"]
            dep["total"] += stats["total"]
            incoming_party = text(stats.get("party"))
            incoming_uf = text(stats.get("uf"))
            if incoming_party and incoming_party != "Sem partido":
                dep["party"] = incoming_party
            elif not dep.get("party"):
                dep["party"] = incoming_party or "Sem partido"
            if incoming_uf and incoming_uf != "--":
                dep["uf"] = incoming_uf
            elif not dep.get("uf"):
                dep["uf"] = incoming_uf or "--"
            for gov_id, gstats in stats.get("by_government", {}).items():
                dg = dep["by_government"][gov_id]
                dg["pro"] += gstats["pro"]
                dg["anti"] += gstats["anti"]
                dg["total"] += gstats["total"]
            for kind, kind_stats in stats.get("by_vote_kind", {}).items():
                dk = dep["by_vote_kind"][kind]
                dk["pro"] += int(kind_stats.get("pro_votes", 0))
                dk["anti"] += int(kind_stats.get("anti_votes", 0))
                dk["total"] += int(kind_stats.get("total_votes", 0))
            for gov_id, gov_kind_stats in stats.get("by_government_kind", {}).items():
                for kind, kind_stats in gov_kind_stats.items():
                    dgk = dep["by_government_kind"][gov_id][kind]
                    dgk["pro"] += int(kind_stats.get("pro_votes", 0))
                    dgk["anti"] += int(kind_stats.get("anti_votes", 0))
                    dgk["total"] += int(kind_stats.get("total_votes", 0))

        vote_summaries.extend(payload["vote_summaries"])

        for key, val in payload.get("counts", {}).items():
            raw_counts[key] += int(val)

    monthly_series = []
    month_keys = sorted(monthly.keys())
    filled_months = iter_month_keys(month_keys[0], month_keys[-1]) if month_keys else []
    for month in filled_months:
        stats = monthly.get(month)
        if stats:
            pro_votes = stats["pro"]
            anti_votes = stats["anti"]
            total_votes = stats["total"]
            votacoes = stats["votacoes"]
            alignment_pct = pct(pro_votes, total_votes)
            government_id = stats["government_id"]
            government_label = stats["government_label"]
            by_vote_kind = serialize_vote_bucket(stats["by_vote_kind"], include_votacoes=True)
            has_votes = votacoes > 0
        else:
            first_day = parse_date(f"{month}-01")
            government_id, government_label = government_for_date(first_day.isoformat() if first_day else "")
            pro_votes = 0
            anti_votes = 0
            total_votes = 0
            votacoes = 0
            alignment_pct = None
            by_vote_kind = {}
            has_votes = False

        monthly_series.append(
            {
                "month": month,
                "pro_votes": pro_votes,
                "anti_votes": anti_votes,
                "total_votes": total_votes,
                "votacoes": votacoes,
                "alignment_pct": alignment_pct,
                "government_id": government_id,
                "government_label": government_label,
                "has_votes": has_votes,
                "by_vote_kind": by_vote_kind,
            }
        )

    yearly_series = []
    for year_key in sorted(yearly.keys()):
        stats = yearly[year_key]
        yearly_series.append(
            {
                "year": year_key,
                "pro_votes": stats["pro"],
                "anti_votes": stats["anti"],
                "total_votes": stats["total"],
                "votacoes": stats["votacoes"],
                "alignment_pct": pct(stats["pro"], stats["total"]),
            }
        )

    government_series = []
    for gov in GOVERNMENT_PERIODS:
        gov_id = gov["id"]
        stats = government_totals.get(gov_id)
        if not stats:
            continue
        government_series.append(
            {
                "id": gov_id,
                "label": gov["label"],
                "start": gov["start"],
                "end": gov["end"],
                "pro_votes": stats["pro"],
                "anti_votes": stats["anti"],
                "total_votes": stats["total"],
                "votacoes": stats["votacoes"],
                "alignment_pct": pct(stats["pro"], stats["total"]),
                "by_vote_kind": serialize_vote_bucket(stats["by_vote_kind"], include_votacoes=True),
            }
        )

    party_ranking = []
    for party, stats in party_totals.items():
        party_ranking.append(
            {
                "party": party,
                "pro_votes": stats["pro"],
                "anti_votes": stats["anti"],
                "total_votes": stats["total"],
                "alignment_pct": pct(stats["pro"], stats["total"]),
                "by_government": {
                    gov_id: {
                        "pro_votes": gstats["pro"],
                        "anti_votes": gstats["anti"],
                        "total_votes": gstats["total"],
                        "alignment_pct": pct(gstats["pro"], gstats["total"]),
                    }
                    for gov_id, gstats in stats["by_government"].items()
                },
                "by_vote_kind": serialize_vote_bucket(stats["by_vote_kind"]),
                "by_government_kind": {
                    gov_id: serialize_vote_bucket(gov_kind_stats)
                    for gov_id, gov_kind_stats in stats["by_government_kind"].items()
                },
            }
        )
    party_ranking.sort(key=lambda item: item["total_votes"], reverse=True)

    deputy_ranking = []
    for dep in deputy_totals.values():
        deputy_ranking.append(
            {
                "deputy_id": dep["deputy_id"],
                "name": dep["name"],
                "party": dep["party"],
                "uf": dep["uf"],
                "pro_votes": dep["pro"],
                "anti_votes": dep["anti"],
                "total_votes": dep["total"],
                "alignment_pct": pct(dep["pro"], dep["total"]),
                "by_government": {
                    gov_id: {
                        "pro_votes": gstats["pro"],
                        "anti_votes": gstats["anti"],
                        "total_votes": gstats["total"],
                        "alignment_pct": pct(gstats["pro"], gstats["total"]),
                    }
                    for gov_id, gstats in dep["by_government"].items()
                },
                "by_vote_kind": serialize_vote_bucket(dep["by_vote_kind"]),
                "by_government_kind": {
                    gov_id: serialize_vote_bucket(gov_kind_stats)
                    for gov_id, gov_kind_stats in dep["by_government_kind"].items()
                },
            }
        )
    deputy_ranking.sort(key=lambda item: item["total_votes"], reverse=True)

    vote_summaries.sort(key=lambda item: (item["date"], item["datetime"], item["id"]))
    recent_votes = list(reversed(vote_summaries))[:80]

    total_pro = sum(item["pro_votes"] for item in government_series)
    total_anti = sum(item["anti_votes"] for item in government_series)
    total_votes = total_pro + total_anti
    summary_by_vote_kind_raw = defaultdict(lambda: {"pro": 0, "anti": 0, "total": 0, "votacoes": 0})
    for gov_stats in government_totals.values():
        for kind, kind_stats in gov_stats["by_vote_kind"].items():
            row = summary_by_vote_kind_raw[kind]
            row["pro"] += int(kind_stats.get("pro", 0))
            row["anti"] += int(kind_stats.get("anti", 0))
            row["total"] += int(kind_stats.get("total", 0))
            row["votacoes"] += int(kind_stats.get("votacoes", 0))

    summary = {
        "pro_votes": total_pro,
        "anti_votes": total_anti,
        "total_votes": total_votes,
        "alignment_pct": pct(total_pro, total_votes),
        "votacoes_validas": sum(item["votacoes"] for item in government_series),
        "partidos_com_voto": sum(1 for item in party_ranking if item["total_votes"] > 0),
        "deputados_com_voto": sum(1 for item in deputy_ranking if item["total_votes"] > 0),
        "by_vote_kind": serialize_vote_bucket(summary_by_vote_kind_raw, include_votacoes=True),
    }

    party_context_active = {
        party: note
        for party, note in PARTY_CONTEXT.items()
        if any(item["party"] == party for item in party_ranking)
    }
    missing_months = sum(1 for row in monthly_series if not row.get("has_votes"))

    return {
        "metadata": {
            "generated_at": now_iso(),
            "source": "Camara dos Deputados - dadosabertos (CSV anual)",
            "source_base_url": BASE_DOWNLOAD_URL,
            "start_year": start_year,
            "end_year": end_year,
            "raw_counts": raw_counts,
            "missing_months_without_votes": missing_months,
            "vote_kind_labels": VOTE_KIND_LABELS,
        },
        "governments": [
            {"id": gov["id"], "label": gov["label"], "start": gov["start"], "end": gov["end"]}
            for gov in GOVERNMENT_PERIODS
            if any(gs["id"] == gov["id"] for gs in government_series)
        ],
        "summary": summary,
        "government_series": government_series,
        "monthly_series": monthly_series,
        "yearly_series": yearly_series,
        "party_ranking": party_ranking,
        "deputy_ranking": deputy_ranking,
        "recent_votes": recent_votes,
        "party_context": party_context_active,
        "methodology": {
            "title": "Metodologia",
            "items": [
                "Consideramos apenas votacoes nominais de plenario (idOrgao=180) com orientacao explicita da bancada 'Governo'.",
                "Um voto e contado como pro-governo apenas quando o voto do deputado e igual a orientacao do Governo na votacao.",
                "Todos os demais votos validos (incluindo nao, abstencao, obstrucao e artigo 17) entram como contra-governo, mantendo o criterio historico do Basometro.",
                "Votacoes sem orientacao do Governo ou sem votos nominais registrados sao descartadas.",
                "Classificacao de tipo de votacao (merito/procedimental) e heuristica, baseada em palavras-chave da descricao oficial da votacao.",
            ],
            "notes": [
                "Em 2026, a Camara mantem os arquivos anuais de votacoes, orientacoes e votos em formato CSV.",
                "O endpoint de API v2 para listagem de votacoes tem limite de janela de 3 meses; por estabilidade, esta versao usa os dumps anuais oficiais.",
                "Pode haver revisoes retroativas na base oficial; por isso o painel e reprocessado periodicamente.",
                "Partidos extintos, fundidos ou incorporados permanecem no historico com a sigla original registrada no momento da votacao.",
            ],
        },
    }


def write_outputs(payload: dict) -> None:
    REPORT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    meta = {
        "updated_at": payload["metadata"]["generated_at"],
        "source": payload["metadata"]["source"],
        "start_year": payload["metadata"]["start_year"],
        "end_year": payload["metadata"]["end_year"],
        "summary": payload["summary"],
    }
    META_FILE.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def build_dataset(start_year: int, end_year: int) -> dict:
    year_payloads = []

    with tempfile.TemporaryDirectory(prefix="basometro-build-") as tmp:
        tmp_dir = Path(tmp)

        for year in range(start_year, end_year + 1):
            print(f"[basometro] Processando ano {year}...")
            files = download_year_files(year, tmp_dir)
            year_payload = aggregate_year(year, files)
            year_payloads.append(year_payload)

    return merge_years(year_payloads, start_year, end_year)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Basometro dashboard dataset")
    parser.add_argument("--start-year", type=int, default=2019, help="Primeiro ano da serie historica (padrao: 2019)")
    parser.add_argument(
        "--end-year",
        type=int,
        default=dt.date.today().year,
        help="Ultimo ano da serie historica (padrao: ano atual)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.start_year > args.end_year:
        raise SystemExit("--start-year nao pode ser maior que --end-year")

    ensure_dirs()

    payload = build_dataset(start_year=args.start_year, end_year=args.end_year)
    write_outputs(payload)

    summary = payload["summary"]
    print(
        "[basometro] Concluido:",
        f"votacoes={summary['votacoes_validas']}",
        f"votos={summary['total_votes']}",
        f"alinhamento={summary['alignment_pct']}%",
    )


if __name__ == "__main__":
    main()
