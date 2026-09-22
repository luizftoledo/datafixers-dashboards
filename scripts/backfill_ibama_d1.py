#!/usr/bin/env python3
"""Reconcile official IBAMA IDs with D1 and prepare a small daily backfill.

The free D1 plan counts writes to indexes as well as table rows. This script
only prepares missing IDs; the workflow applies at most 5,000 autos per day.
"""
import argparse
import csv
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from sync_ibama_d1 import to_sql

csv.field_size_limit(sys.maxsize)


def d1_query(account_id, database_id, token, sql):
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/d1/database/{database_id}/query"
    request = urllib.request.Request(
        url,
        data=json.dumps({"sql": sql}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"D1 HTTP {error.code}: {error.read(500).decode(errors='replace')}") from error
    if not payload.get("success") or not payload.get("result") or not payload["result"][0].get("success"):
        raise RuntimeError(f"D1 query failed: {payload.get('errors') or payload.get('result')}")
    return payload["result"][0]


def load_existing_ids(account_id, database_id, token):
    ids = set()
    cursor = -1
    size = 0
    while True:
        result = d1_query(
            account_id, database_id, token,
            f"SELECT seq_auto FROM autos_infracao WHERE seq_auto > {cursor} ORDER BY seq_auto LIMIT 10000",
        )
        rows = result.get("results") or []
        size = result.get("meta", {}).get("size_after") or size
        ids.update(int(row["seq_auto"]) for row in rows)
        if not rows:
            break
        next_cursor = int(rows[-1]["seq_auto"])
        if next_cursor <= cursor:
            raise RuntimeError("D1 ID pagination did not advance")
        cursor = next_cursor
    return ids, size


def prepare(zip_path, known, out_dir, limit):
    out_dir.mkdir(parents=True, exist_ok=True)
    official = set()
    candidates = []
    oversized = []
    invalid = 0
    with zipfile.ZipFile(zip_path) as archive:
        names = sorted(n for n in archive.namelist() if re.fullmatch(r"auto_infracao_\d{4}\.csv", n))
        if not names:
            raise RuntimeError("Official ZIP has no annual auto_infracao CSV files")
        for name in names:
            with archive.open(name) as raw:
                rows = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline=""), delimiter=";")
                for row in rows:
                    key = (row.get("SEQ_AUTO_INFRACAO") or "").strip()
                    if not key.isdigit():
                        invalid += 1
                        continue
                    auto_id = int(key)
                    if auto_id in official:
                        continue
                    official.add(auto_id)
                    if auto_id in known or len(candidates) >= limit:
                        continue
                    statement = to_sql(row)
                    if statement is None:
                        continue
                    if len(statement.encode()) > 90000:
                        oversized.append(auto_id)
                        continue
                    candidates.append(statement)
    for index in range(0, len(candidates), 100):
        (out_dir / f"batch-{index // 100:04d}.sql").write_text("".join(candidates[index:index+100]), encoding="utf-8")
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "official_valid": len(official),
        "loaded_before": len(known),
        "missing_before": len(official - known),
        "prepared": len(candidates),
        "invalid_source_ids": invalid,
        "oversized_ids": oversized[:20],
        "oversized_count": len(oversized),
    }
    (out_dir / "coverage.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("zip_file", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--account-id", default=os.environ.get("CLOUDFLARE_ACCOUNT_ID"))
    parser.add_argument("--database-id", default="2bba3c11-c416-47c9-af39-d01f40c7136f")
    args = parser.parse_args()
    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    if not args.account_id or not token:
        parser.error("CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN are required")
    if not 1 <= args.limit <= 5000:
        parser.error("limit must be between 1 and 5000")
    known, size = load_existing_ids(args.account_id, args.database_id, token)
    if size >= 450_000_000:
        raise RuntimeError(f"D1 is near its 500 MB per-database limit ({size} bytes); backfill paused")
    report = prepare(args.zip_file, known, args.out_dir, args.limit)
    report["database_size_before"] = size
    (args.out_dir / "coverage.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    loaded_official = report["official_valid"] - report["missing_before"] + report["prepared"]
    missing_after = report["missing_before"] - report["prepared"]
    checked_at = report["checked_at"].replace("'", "''")
    (args.out_dir / "coverage.sql").write_text(
        "CREATE TABLE IF NOT EXISTS ibama_coverage ("
        "id INTEGER PRIMARY KEY, checked_at TEXT, official_valid INTEGER, "
        "loaded_official INTEGER, missing_official INTEGER, invalid_source_ids INTEGER);\n"
        "INSERT INTO ibama_coverage (id, checked_at, official_valid, loaded_official, missing_official, invalid_source_ids) "
        f"VALUES (1, '{checked_at}', {report['official_valid']}, {loaded_official}, {missing_after}, {report['invalid_source_ids']}) "
        "ON CONFLICT(id) DO UPDATE SET checked_at=excluded.checked_at, official_valid=excluded.official_valid, "
        "loaded_official=excluded.loaded_official, missing_official=excluded.missing_official, "
        "invalid_source_ids=excluded.invalid_source_ids;\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
