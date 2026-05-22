#!/usr/bin/env python3
import argparse
import csv
import gzip
import json
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from build_aeronaves_dashboard_data import (
    DATA_FILE,
    META_FILE,
    build_dataset,
    clean_value,
    normalize_text,
    parse_source_updated_at,
)


DEFAULT_DOWNLOAD_URL = "https://sistemas.anac.gov.br/dadosabertos/Aeronaves/RAB/dados_aeronaves.csv"
DEFAULT_MIN_ROWS = 1000
DEFAULT_MIN_ROW_RATIO = 0.9
DEFAULT_TIMEOUT = 180
DEFAULT_RETRIES = 3
SUMMARY_SAMPLE_SIZE = 10
EXPECTED_FIELDS = {"MARCA", "NM_OPERADOR", "PROPRIETARIOS"}


def owner_names(raw_value):
    raw = clean_value(raw_value)
    if not raw:
        return []
    names = []
    for chunk in raw.split(";"):
        name = chunk.split("|", 1)[0]
        normalized = normalize_text(name)
        if normalized:
            names.append(normalized)
    return names


def summarize_source_csv(path):
    summary = {
        "path": str(path),
        "file": path.name,
        "size_bytes": path.stat().st_size,
        "source_header": "",
        "source_updated_at": "",
        "rows": 0,
        "prefixes": set(),
        "operators": set(),
        "owners": set(),
        "fieldnames": [],
    }

    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        summary["source_header"] = fh.readline().strip()
        summary["source_updated_at"] = parse_source_updated_at(summary["source_header"])
        reader = csv.DictReader(fh, delimiter=";")
        summary["fieldnames"] = reader.fieldnames or []

        for row in reader:
            if not any(clean_value(value) for value in row.values()):
                continue

            summary["rows"] += 1

            prefix = clean_value(row.get("MARCA")).upper()
            if prefix:
                summary["prefixes"].add(prefix)

            operator = normalize_text(row.get("NM_OPERADOR"))
            if operator:
                summary["operators"].add(operator)

            summary["owners"].update(owner_names(row.get("PROPRIETARIOS")))

    return summary


def summarize_current_dataset():
    if not DATA_FILE.exists() or not META_FILE.exists():
        return None

    metadata = json.loads(META_FILE.read_text(encoding="utf-8"))
    summary = {
        "path": str(DATA_FILE),
        "file": DATA_FILE.name,
        "size_bytes": metadata.get("source_size_bytes", 0),
        "source_header": metadata.get("source_header", ""),
        "source_updated_at": metadata.get("source_updated_at", ""),
        "rows": 0,
        "prefixes": set(),
        "operators": set(),
        "owners": set(),
    }

    with gzip.open(DATA_FILE, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue

            row = json.loads(line)
            summary["rows"] += 1

            prefix = clean_value(row.get("p")).upper()
            if prefix:
                summary["prefixes"].add(prefix)

            operator = normalize_text(row.get("n"))
            if operator:
                summary["operators"].add(operator)

            summary["owners"].update(owner_names(row.get("pr")))

    return summary


def set_change(old_set, new_set):
    added = sorted(new_set - old_set)
    removed = sorted(old_set - new_set)
    overlap = len(old_set & new_set)
    union = len(old_set | new_set) or 1
    return {
        "old": len(old_set),
        "new": len(new_set),
        "added": len(added),
        "removed": len(removed),
        "jaccard_similarity": round(overlap / union, 6),
        "added_sample": added[:SUMMARY_SAMPLE_SIZE],
        "removed_sample": removed[:SUMMARY_SAMPLE_SIZE],
    }


def build_report(current_summary, candidate_summary):
    current_rows = current_summary["rows"] if current_summary else 0
    candidate_rows = candidate_summary["rows"]
    row_delta = candidate_rows - current_rows
    row_delta_pct = None
    if current_rows:
        row_delta_pct = round((row_delta / current_rows) * 100, 4)

    current_prefixes = current_summary["prefixes"] if current_summary else set()
    current_operators = current_summary["operators"] if current_summary else set()
    current_owners = current_summary["owners"] if current_summary else set()

    prefix_changes = set_change(current_prefixes, candidate_summary["prefixes"])
    operator_changes = set_change(current_operators, candidate_summary["operators"])
    owner_changes = set_change(current_owners, candidate_summary["owners"])

    report = {
        "current_available": current_summary is not None,
        "current_source_updated_at": current_summary["source_updated_at"] if current_summary else "",
        "candidate_source_updated_at": candidate_summary["source_updated_at"],
        "current_rows": current_rows,
        "candidate_rows": candidate_rows,
        "row_delta": row_delta,
        "row_delta_pct": row_delta_pct,
        "current_source_size_bytes": current_summary["size_bytes"] if current_summary else 0,
        "candidate_source_size_bytes": candidate_summary["size_bytes"],
        "prefixes": prefix_changes,
        "operators": operator_changes,
        "owners": owner_changes,
    }

    name_changes = (
        operator_changes["added"]
        + operator_changes["removed"]
        + owner_changes["added"]
        + owner_changes["removed"]
    )
    identifier_changes = prefix_changes["added"] + prefix_changes["removed"]
    report["meaningful_changes_detected"] = (
        current_summary is None
        or row_delta != 0
        or name_changes > 0
        or identifier_changes > 0
    )
    return report


def validate_candidate(current_summary, candidate_summary, min_rows, min_row_ratio):
    if candidate_summary["size_bytes"] <= 0:
        return False, "download vazio"

    if candidate_summary["rows"] < min_rows:
        return False, f"arquivo com poucas linhas ({candidate_summary['rows']})"

    missing_fields = sorted(EXPECTED_FIELDS - set(candidate_summary["fieldnames"]))
    if missing_fields:
        return False, f"CSV sem colunas esperadas: {', '.join(missing_fields)}"

    if current_summary and current_summary["rows"] > 0:
        min_allowed_rows = int(current_summary["rows"] * min_row_ratio)
        if candidate_summary["rows"] < min_allowed_rows:
            return (
                False,
                (
                    "arquivo significativamente menor que o atual "
                    f"({candidate_summary['rows']} vs {current_summary['rows']} linhas)"
                ),
            )

    return True, "arquivo aceito"


def download_csv(url, destination, timeout, retries):
    for attempt in range(1, retries + 1):
        try:
            curl_path = shutil.which("curl")
            if curl_path:
                subprocess.run(
                    [
                        curl_path,
                        "-L",
                        "--fail",
                        "--silent",
                        "--show-error",
                        "--connect-timeout",
                        "30",
                        "--max-time",
                        str(timeout),
                        url,
                        "-o",
                        str(destination),
                    ],
                    check=True,
                )
                return destination

            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "text/csv,*/*;q=0.8",
                },
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                with destination.open("wb") as out_fh:
                    shutil.copyfileobj(response, out_fh)
            return destination
        except (subprocess.CalledProcessError, urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt == retries:
                raise RuntimeError(f"falha ao baixar CSV da ANAC: {exc}") from exc
            time.sleep(attempt * 3)

    raise RuntimeError("falha inesperada ao baixar CSV da ANAC")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Baixa, compara e atualiza a dashboard de aeronaves apenas quando a base da ANAC mudou de forma confiavel."
    )
    parser.add_argument(
        "--input",
        type=Path,
        help="Usa um CSV local em vez de baixar da ANAC.",
    )
    parser.add_argument(
        "--download-url",
        default=DEFAULT_DOWNLOAD_URL,
        help=f"URL do CSV da ANAC (padrao: {DEFAULT_DOWNLOAD_URL})",
    )
    parser.add_argument(
        "--min-rows",
        type=int,
        default=DEFAULT_MIN_ROWS,
        help=f"Quantidade minima de linhas para considerar o download valido (padrao: {DEFAULT_MIN_ROWS})",
    )
    parser.add_argument(
        "--min-row-ratio",
        type=float,
        default=DEFAULT_MIN_ROW_RATIO,
        help=f"Proporcao minima de linhas em relacao a base atual para aceitar o download (padrao: {DEFAULT_MIN_ROW_RATIO})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"Timeout do download em segundos (padrao: {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=DEFAULT_RETRIES,
        help=f"Numero de tentativas de download (padrao: {DEFAULT_RETRIES})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Apenas analisa e reporta; nao regenera a dashboard.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    current_summary = summarize_current_dataset()

    if args.input:
        candidate_path = args.input.expanduser().resolve()
        if not candidate_path.exists():
            raise SystemExit(f"Arquivo nao encontrado: {candidate_path}")
        candidate_summary = summarize_source_csv(candidate_path)
    else:
        with tempfile.TemporaryDirectory(prefix="aeronaves-dashboard-") as tmp_dir:
            candidate_path = Path(tmp_dir) / "dados_aeronaves.csv"
            download_csv(args.download_url, candidate_path, args.timeout, args.retries)
            candidate_summary = summarize_source_csv(candidate_path)
            report = build_report(current_summary, candidate_summary)
            is_valid, validation_reason = validate_candidate(
                current_summary,
                candidate_summary,
                args.min_rows,
                args.min_row_ratio,
            )
            report["candidate_valid"] = is_valid
            report["candidate_validation_reason"] = validation_reason
            report["dry_run"] = args.dry_run
            report["would_rebuild"] = is_valid and report["meaningful_changes_detected"]
            print(json.dumps(report, ensure_ascii=False, indent=2))

            if not is_valid:
                print(f"[skip] {validation_reason}")
                return

            if not report["meaningful_changes_detected"]:
                print("[skip] sem mudancas relevantes em linhas, nomes ou prefixos.")
                return

            if args.dry_run:
                print("[dry-run] comparacao concluida; a base seria atualizada.")
                return

            metadata = build_dataset(candidate_path)
            print(
                "[ok] aeronaves-dashboard atualizado:",
                f"rows={metadata['rows']}",
                f"ativas={metadata['active_rows']}",
                f"canceladas={metadata['canceled_rows']}",
                f"fonte={candidate_summary['source_updated_at'] or candidate_summary['source_header']}",
            )
            return

    report = build_report(current_summary, candidate_summary)
    is_valid, validation_reason = validate_candidate(
        current_summary,
        candidate_summary,
        args.min_rows,
        args.min_row_ratio,
    )
    report["candidate_valid"] = is_valid
    report["candidate_validation_reason"] = validation_reason
    report["dry_run"] = args.dry_run
    report["would_rebuild"] = is_valid and report["meaningful_changes_detected"]
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if not is_valid:
        print(f"[skip] {validation_reason}")
        return

    if not report["meaningful_changes_detected"]:
        print("[skip] sem mudancas relevantes em linhas, nomes ou prefixos.")
        return

    if args.dry_run:
        print("[dry-run] comparacao concluida; a base seria atualizada.")
        return

    metadata = build_dataset(candidate_path)
    print(
        "[ok] aeronaves-dashboard atualizado:",
        f"rows={metadata['rows']}",
        f"ativas={metadata['active_rows']}",
        f"canceladas={metadata['canceled_rows']}",
        f"fonte={candidate_summary['source_updated_at'] or candidate_summary['source_header']}",
    )


if __name__ == "__main__":
    main()
