#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

import build_lai_dashboard_data as base


SOURCES = {
    "ampla": {
        "id": "ampla",
        "label": "Todos os pedidos e recursos (Fala.BR/CGU)",
        "portal_url": "https://falabr.cgu.gov.br/web/dadosabertoslai",
        "download_url_template": (
            "https://dadosabertos-download.cgu.gov.br/FalaBR/Arquivos_FalaBR/"
            "Pedidos_csv_{year}.zip"
        ),
        "start_year": 2012,
        "cache_subdir": "yearly_ampla",
        "report_file": "report_data.json",
        "metadata_file": "metadata.json",
        "samples_file": "request_samples.jsonl.gz",
    },
    "publica": {
        "id": "publica",
        "label": "Pedidos e recursos marcados como públicos (BuscaLAI)",
        "portal_url": "https://buscalai.cgu.gov.br/DownloadDados/DownloadDados",
        "download_url_template": (
            "https://dadosabertos-download.cgu.gov.br/FalaBR/Arquivos_FalaBR_Filtrado/"
            "Arquivos_csv_{year}.zip"
        ),
        "start_year": 2015,
        "cache_subdir": "yearly_publica",
        "report_file": "report_data_publica.json",
        "metadata_file": "metadata_publica.json",
        "samples_file": "request_samples_publica.jsonl.gz",
    },
}


def configure_source(cfg):
    base.DOWNLOAD_URL_TEMPLATE = cfg["download_url_template"]
    base.DOWNLOAD_PORTAL_URL = cfg["portal_url"]
    base.SOURCE_ID = cfg["id"]
    base.SOURCE_LABEL = cfg["label"]
    base.START_YEAR_DEFAULT = int(cfg["start_year"])
    base.YEARLY_CACHE_DIR = base.CACHE_DIR / cfg["cache_subdir"]
    base.REPORT_FILE = base.DATA_DIR / cfg["report_file"]
    base.METADATA_FILE = base.DATA_DIR / cfg["metadata_file"]
    base.SAMPLES_FILE = base.DATA_DIR / cfg["samples_file"]


def build_source(source_key, force=False, end_year=None):
    cfg = SOURCES[source_key]
    configure_source(cfg)
    base.run(
        force=force,
        start_year=int(cfg["start_year"]),
        end_year=end_year,
    )


def write_sources_index(default_source="ampla"):
    payload = {
        "default_source": default_source,
        "sources": {
            key: {
                "id": cfg["id"],
                "label": cfg["label"],
                "portal_url": cfg["portal_url"],
                "report_file": f"./data/{cfg['report_file']}",
                "metadata_file": f"./data/{cfg['metadata_file']}",
                "samples_file": f"./data/{cfg['samples_file']}",
            }
            for key, cfg in SOURCES.items()
        },
    }
    target = base.DATA_DIR / "report_sources.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] índice de fontes salvo em: {target}")


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Gera duas bases para a dashboard LAI: "
            "ampla (Fala.BR) e pública (BuscaLAI)."
        )
    )
    parser.add_argument(
        "--source",
        choices=["all", "ampla", "publica"],
        default="all",
        help="Fonte a processar (padrão: all).",
    )
    parser.add_argument("--force", action="store_true", help="Reprocessa todos os anos.")
    parser.add_argument(
        "--end-year",
        type=int,
        default=None,
        help="Ano final (padrão: ano atual).",
    )
    parser.add_argument(
        "--default-source",
        choices=["ampla", "publica"],
        default="ampla",
        help="Fonte padrão no seletor da dashboard.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.source == "all":
        build_source("ampla", force=args.force, end_year=args.end_year)
        build_source("publica", force=args.force, end_year=args.end_year)
    else:
        build_source(args.source, force=args.force, end_year=args.end_year)

    write_sources_index(default_source=args.default_source)


if __name__ == "__main__":
    main()
