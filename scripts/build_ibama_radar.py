#!/usr/bin/env python3
"""Build a small, aggregate editorial radar from the official Ibama ZIP."""
import argparse
import csv
import io
import json
import sys
import unicodedata
import zipfile
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

csv.field_size_limit(sys.maxsize)


def city_key(value):
    value = (value or "").strip().upper()
    return "".join(c for c in unicodedata.normalize("NFD", value) if not unicodedata.combining(c))


def read_year(archive, year, max_month_day=None):
    counts = Counter()
    newest = None
    name = f"auto_infracao_{year}.csv"
    with archive.open(name) as raw:
        reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline=""), delimiter=";")
        for row in reader:
            day = (row.get("DAT_HORA_AUTO_INFRACAO") or "")[:10]
            if not day.startswith(f"{year}-"):
                continue
            try:
                parsed = date.fromisoformat(day)
            except ValueError:
                continue
            if max_month_day and (parsed.month, parsed.day) > max_month_day:
                continue
            uf = (row.get("UF") or "").strip().upper()
            city = city_key(row.get("MUNICIPIO"))
            if len(uf) != 2 or not city:
                continue
            counts[(city, uf)] += 1
            if newest is None or parsed > newest:
                newest = parsed
    return counts, newest


def build(zip_path):
    with zipfile.ZipFile(zip_path) as archive:
        years = sorted(int(n[-8:-4]) for n in archive.namelist() if n.startswith("auto_infracao_") and n.endswith(".csv"))
        year = years[-1]
        current, newest = read_year(archive, year)
        if not newest or newest.year != year:
            raise RuntimeError("No dated autos in latest annual CSV")
        previous, _ = read_year(archive, year - 1, (newest.month, newest.day))
    ranked = []
    for (city, uf), now in current.items():
        before = previous[(city, uf)]
        delta = now - before
        if now >= 30 and delta >= 20 and now >= before * 1.8:
            ranked.append({"city": city, "uf": uf, "current": now, "previous": before, "delta": delta})
    ranked.sort(key=lambda item: (-item["delta"], -item["current"], item["city"]))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "year": year,
        "through": newest.isoformat(),
        "previous_year": year - 1,
        "total_current": sum(current.values()),
        "total_previous": sum(previous.values()),
        "places": ranked[:9],
        "method": "Autos por municipio e UF com data do auto entre 1 jan e a data de corte em cada ano. Normalizacao de caixa e acentos. Selecionados apenas locais com >=30 autos no ano atual, >=20 a mais que no anterior e pelo menos 1,8 vez o volume anterior.",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("zip_file", type=Path)
    parser.add_argument("--out", type=Path, default=Path("ibama/data/radar.json"))
    args = parser.parse_args()
    report = build(args.zip_file)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Radar {report['year']} through {report['through']}: {len(report['places'])} places")


if __name__ == "__main__":
    main()
