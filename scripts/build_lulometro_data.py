#!/usr/bin/env python3
"""
Build dataset for the Lulometro dashboard.

Outputs:
- lulometro-dashboard/data/records.jsonl.gz   (full text records)
- lulometro-dashboard/data/items.json         (metadata table)
- lulometro-dashboard/data/super_tabela.csv   (portable table)
- lulometro-dashboard/data/metadata.json      (update metadata)
- lulometro-dashboard/data/sources.json       (crawl diagnostics)
- lulometro-dashboard/data/people_directory.json (tracked public figures + alias methodology)
- lulometro-dashboard/data/people_mentions.json  (precomputed Lula mentions by document)

The scraper covers:
- Current Planalto pages (Lula): interviews and speeches.
- Biblioteca de ex-presidentes pages (Bolsonaro only).

Incremental mode:
- Existing records are loaded from records.jsonl.gz.
- Only new or incomplete URLs are fetched in detail.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qsl, urljoin, urlparse, urlunparse

import requests
import urllib3
from bs4 import BeautifulSoup
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from lulometro_people_index import build_people_index
from lulometro_bluesky import fetch_all_bluesky_records

try:
    from pypdf import PdfReader
except Exception:  # pragma: no cover - optional dependency in local runs
    PdfReader = None

try:
    import fitz  # PyMuPDF
except Exception:  # pragma: no cover - optional dependency in local runs
    fitz = None


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

ROOT_DIR = Path(__file__).resolve().parents[1]
DASH_DIR = ROOT_DIR / "lulometro"
DATA_DIR = DASH_DIR / "data"
STATE_DIR = DATA_DIR / "state"

RECORDS_GZ = DATA_DIR / "records.jsonl.gz"
ITEMS_JSON = DATA_DIR / "items.json"
CSV_FILE = DATA_DIR / "super_tabela.csv"
META_JSON = DATA_DIR / "metadata.json"
SOURCES_JSON = DATA_DIR / "sources.json"
PEOPLE_DIRECTORY_JSON = DATA_DIR / "people_directory.json"
PEOPLE_MENTIONS_JSON = DATA_DIR / "people_mentions.json"
MAX_BINARY_BYTES = 20 * 1024 * 1024
DETAIL_CHECKPOINT_EVERY = 500

PLANALTO_LISTINGS = [
    {
        "url": "https://www.gov.br/planalto/pt-br/acompanhe-o-planalto/entrevistas",
        "type": "entrevista",
        "source": "planalto",
        "president_slug": "luiz-inacio-lula-da-silva",
        "president": "Luiz Inacio Lula da Silva",
    },
    {
        "url": "https://www.gov.br/planalto/pt-br/acompanhe-o-planalto/discursos-e-pronunciamentos",
        "type": "discurso",
        "source": "planalto",
        "president_slug": "luiz-inacio-lula-da-silva",
        "president": "Luiz Inacio Lula da Silva",
    },
]

BIBLIOTECA_ROOT = "https://www.biblioteca.presidencia.gov.br/presidencia/ex-presidentes"
BIBLIOTECA_SEED_CATEGORIES = [
    {
        "url": "https://www.biblioteca.presidencia.gov.br/presidencia/ex-presidentes/bolsonaro/discursos",
        "type": "discurso",
        "source": "biblioteca",
        "president_slug": "bolsonaro",
    },
    {
        "url": "https://www.biblioteca.presidencia.gov.br/presidencia/ex-presidentes/bolsonaro/entrevistas",
        "type": "entrevista",
        "source": "biblioteca",
        "president_slug": "bolsonaro",
    },
]

MONTHS_PT = {
    "janeiro": 1,
    "fevereiro": 2,
    "marco": 3,
    "março": 3,
    "abril": 4,
    "maio": 5,
    "junho": 6,
    "julho": 7,
    "agosto": 8,
    "setembro": 9,
    "outubro": 10,
    "novembro": 11,
    "dezembro": 12,
}

DATE_BR_RE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b")
DATE_PT_RE = re.compile(
    r"\b(\d{1,2}|1º)\s+de\s+([a-zA-ZçÇãÃáÁàÀâÂéÉêÊíÍóÓôÔõÕúÚ]+)\s+de\s+(\d{4})\b",
    re.IGNORECASE,
)
YEAR_RE = re.compile(r"\b(18|19|20)\d{2}\b")

KNOWN_PRESIDENT_NAMES = {
    "luiz-inacio-lula-da-silva": "Luiz Inacio Lula da Silva",
    "bolsonaro": "Jair Bolsonaro",
    "michel-temer": "Michel Temer",
    "dilma-rousseff": "Dilma Rousseff",
    "fernando-henrique-cardoso": "Fernando Henrique Cardoso",
    "itamar-franco": "Itamar Franco",
    "fernando-collor": "Fernando Collor de Mello",
    "jose-sarney": "Jose Sarney",
    "tancredo-neves": "Tancredo Neves",
    "jb-figueiredo": "Joao Baptista Figueiredo",
    "ernesto-geisel": "Ernesto Geisel",
    "emilio-medici": "Emilio Garrastazu Medici",
    "souza-mello": "Marcio de Souza Mello",
    "augusto-rademaker": "Augusto Rademaker",
    "aureliol-tavares": "Aurelio Lyra Tavares",
    "costa-silva": "Artur da Costa e Silva",
    "pedro-aleixo": "Pedro Aleixo",
    "castello-branco": "Humberto Castelo Branco",
    "ranieri-mazzilli": "Pascoal Ranieri Mazzilli",
    "joao-goulart": "Joao Goulart",
    "janio-quadros": "Janio Quadros",
    "jk": "Juscelino Kubitschek",
    "nereu-ramos": "Nereu Ramos",
    "carlos-luz": "Carlos Luz",
    "cafe-filho": "Cafe Filho",
    "getulio-vargas": "Getulio Vargas",
    "gaspar-dutra": "Eurico Gaspar Dutra",
    "jose-linhares": "Jose Linhares",
    "augusto-fragoso": "Augusto Fragoso",
    "isaias-noronha": "Isaias de Noronha",
    "menna-barreto": "Menna Barreto",
    "julio-prestes": "Julio Prestes",
    "washigton-luis": "Washington Luis",
    "arthur-bernardes": "Artur Bernardes",
    "epitacio-pessoa": "Epitacio Pessoa",
    "delfim-moreira": "Delfim Moreira",
    "wenceslau-braz": "Wenceslau Braz",
    "hermes-fonseca": "Hermes da Fonseca",
    "nilo-pecanha": "Nilo Pecanha",
    "affonso-penna": "Afonso Pena",
}

# start, end, label (inclusive)
MANDATE_RULES = {
    "luiz-inacio-lula-da-silva": [
        ("2003-01-01", "2006-12-31", "Lula 1 (2003-2006)"),
        ("2007-01-01", "2010-12-31", "Lula 2 (2007-2010)"),
        ("2023-01-01", "2030-12-31", "Lula 3 (2023-)"),
    ],
    "bolsonaro": [("2019-01-01", "2022-12-31", "Bolsonaro (2019-2022)")],
    "michel-temer": [("2016-08-31", "2018-12-31", "Temer (2016-2018)")],
    "dilma-rousseff": [
        ("2011-01-01", "2014-12-31", "Dilma 1 (2011-2014)"),
        ("2015-01-01", "2016-08-30", "Dilma 2 (2015-2016)"),
    ],
    "fernando-henrique-cardoso": [
        ("1995-01-01", "1998-12-31", "FHC 1 (1995-1998)"),
        ("1999-01-01", "2002-12-31", "FHC 2 (1999-2002)"),
    ],
    "itamar-franco": [("1992-12-29", "1994-12-31", "Itamar (1992-1994)")],
    "fernando-collor": [("1990-03-15", "1992-12-29", "Collor (1990-1992)")],
    "jose-sarney": [("1985-03-15", "1990-03-14", "Sarney (1985-1990)")],
    "jb-figueiredo": [("1979-03-15", "1985-03-14", "Figueiredo (1979-1985)")],
    "ernesto-geisel": [("1974-03-15", "1979-03-14", "Geisel (1974-1979)")],
    "emilio-medici": [("1969-10-30", "1974-03-14", "Medici (1969-1974)")],
    "costa-silva": [("1967-03-15", "1969-08-31", "Costa e Silva (1967-1969)")],
    "castello-branco": [("1964-04-15", "1967-03-14", "Castelo Branco (1964-1967)")],
    "joao-goulart": [("1961-09-08", "1964-04-01", "Joao Goulart (1961-1964)")],
    "janio-quadros": [("1961-01-31", "1961-08-25", "Janio Quadros (1961)")],
    "jk": [("1956-01-31", "1961-01-30", "Juscelino (1956-1961)")],
    "getulio-vargas": [
        ("1930-11-03", "1945-10-29", "Vargas 1 (1930-1945)"),
        ("1951-01-31", "1954-08-24", "Vargas 2 (1951-1954)"),
    ],
}

TARGET_MANDATES = {
    "Lula 3 (2023-)",
    "Bolsonaro (2019-2022)",
}
TARGET_BIBLIOTECA_SLUGS = {"bolsonaro"}


@dataclass
class CrawlCategory:
    url: str
    doc_type: str
    source: str
    president_slug: str = ""
    president: str = ""


class FetchError(RuntimeError):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class Fetcher:
    def __init__(self, user_agent: str, use_biblioteca: bool = True):
        self.user_agent = user_agent
        self.use_biblioteca = use_biblioteca
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
            }
        )
        self.last_biblioteca_bootstrap = ""
        self.last_biblioteca_bootstrap_ts = 0.0

    def close(self):
        self.session.close()

    def bootstrap_biblioteca(self, force: bool = False):
        if not self.use_biblioteca:
            return

        now = time.time()
        # Avoid repeating expensive browser bootstrap too frequently.
        if not force and self.last_biblioteca_bootstrap_ts and (now - self.last_biblioteca_bootstrap_ts) < 45:
            return

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(ignore_https_errors=True, user_agent=self.user_agent)
            page = context.new_page()
            page.goto(BIBLIOTECA_ROOT, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1200)
            cookies = context.cookies()
            browser.close()

        for ck in cookies:
            self.session.cookies.set(
                ck.get("name", ""),
                ck.get("value", ""),
                domain=ck.get("domain"),
                path=ck.get("path", "/"),
            )
        self.last_biblioteca_bootstrap = now_iso()
        self.last_biblioteca_bootstrap_ts = time.time()

    def fetch_html(self, url: str, timeout: int = 30) -> Tuple[str, str, int]:
        parsed = urlparse(url)
        is_biblioteca = "biblioteca.presidencia.gov.br" in parsed.netloc
        verify_ssl = not is_biblioteca

        tries = 2 if is_biblioteca else 1
        last_error: Optional[Exception] = None
        for attempt in range(tries):
            try:
                resp = self.session.get(url, timeout=(10, timeout), verify=verify_ssl)
                status = resp.status_code
                text = resp.text
                final_url = resp.url

                if status >= 400:
                    retryable = is_biblioteca and status in {403, 429, 500, 502, 503, 504}
                    raise FetchError(f"HTTP {status} for {url}", retryable=retryable)

                if is_biblioteca and is_bot_challenge(text):
                    raise FetchError(f"Bot challenge still active for {url}", retryable=True)

                return text, final_url, status
            except (requests.RequestException, FetchError) as exc:
                last_error = exc
                retryable = isinstance(exc, requests.RequestException) or (
                    isinstance(exc, FetchError) and exc.retryable
                )
                if is_biblioteca and retryable and attempt < (tries - 1):
                    try:
                        self.bootstrap_biblioteca()
                    except Exception:
                        pass
                    continue
                break

        raise RuntimeError(str(last_error or f"Failed fetching {url}"))

    def fetch_bytes(
        self,
        url: str,
        timeout: int = 35,
        max_bytes: int = MAX_BINARY_BYTES,
    ) -> Tuple[bytes, str, str]:
        parsed = urlparse(url)
        is_biblioteca = "biblioteca.presidencia.gov.br" in parsed.netloc
        verify_ssl = not is_biblioteca

        tries = 2 if is_biblioteca else 1
        last_error: Optional[Exception] = None
        for attempt in range(tries):
            try:
                resp = self.session.get(url, timeout=(10, timeout), verify=verify_ssl, stream=True)
                status = resp.status_code
                final_url = resp.url
                content_type = normalize_space(resp.headers.get("Content-Type", ""))

                if status >= 400:
                    retryable = is_biblioteca and status in {403, 429, 500, 502, 503, 504}
                    raise FetchError(f"HTTP {status} for {url}", retryable=retryable)

                length_header = normalize_space(resp.headers.get("Content-Length", ""))
                if length_header.isdigit() and int(length_header) > max_bytes:
                    raise FetchError(
                        f"Payload too large ({length_header} bytes) for {url}",
                        retryable=False,
                    )

                chunks: List[bytes] = []
                total = 0
                for chunk in resp.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > max_bytes:
                        raise FetchError(f"Payload exceeded {max_bytes} bytes for {url}", retryable=False)
                data = b"".join(chunks)

                if is_biblioteca and "text/html" in content_type.lower():
                    text = data.decode("utf-8", errors="ignore")
                    if is_bot_challenge(text):
                        raise FetchError(f"Bot challenge still active for {url}", retryable=True)

                return data, final_url, content_type
            except (requests.RequestException, FetchError) as exc:
                last_error = exc
                retryable = isinstance(exc, requests.RequestException) or (
                    isinstance(exc, FetchError) and exc.retryable
                )
                if is_biblioteca and retryable and attempt < (tries - 1):
                    try:
                        self.bootstrap_biblioteca()
                    except Exception:
                        pass
                    continue
                break

        raise RuntimeError(str(last_error or f"Failed fetching {url}"))


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def normalize_space(value: str) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\ufeff", " ")).strip()


def strip_accents(value: str) -> str:
    text = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def fold_text(value: str) -> str:
    return normalize_space(strip_accents(str(value or "")).lower())


def canonical_article_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    scheme = parsed.scheme.lower() or "https"
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    return urlunparse((scheme, netloc, path, "", "", ""))


def canonical_page_url(url: str) -> str:
    parsed = urlparse(url)
    scheme = parsed.scheme.lower() or "https"
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/")
    params = parse_qsl(parsed.query, keep_blank_values=True)
    keep = []
    for key, val in params:
        k = key.strip()
        if k in {"b_start:int", "page", "pagina"}:
            keep.append((k, val.strip()))
    keep.sort()
    query = "&".join(f"{k}={v}" for k, v in keep)
    return urlunparse((scheme, netloc, path, "", query, ""))


def safe_title_from_slug(slug: str) -> str:
    if not slug:
        return "Presidente nao identificado"
    words = [w for w in slug.replace("_", "-").split("-") if w]
    if not words:
        return "Presidente nao identificado"
    return " ".join(w.capitalize() for w in words)


def infer_president_slug(url: str) -> str:
    parts = [part for part in urlparse(url).path.split("/") if part]
    if "ex-presidentes" in parts:
        idx = parts.index("ex-presidentes")
        if idx + 1 < len(parts):
            slug = parts[idx + 1].strip().lower()
            return slug
    return ""


def president_name(slug: str) -> str:
    if not slug:
        return "Presidente nao identificado"
    if slug in KNOWN_PRESIDENT_NAMES:
        return KNOWN_PRESIDENT_NAMES[slug]
    return safe_title_from_slug(slug)


def infer_doc_type(url: str, title: str = "") -> str:
    ref = f"{url} {title}".lower()
    if "entrevist" in ref:
        return "entrevista"
    return "discurso"


def is_bot_challenge(text: str) -> bool:
    sample = fold_text(text)
    return (
        "evitar o acesso automatizado" in sample
        or "qual codigo esta sendo exibido" in sample
        or "support id" in sample
        or "request rejected" in sample
        or "the requested url was rejected" in sample
    )


def parse_date_from_text(text: str) -> Optional[dt.date]:
    raw = normalize_space(text)
    if not raw:
        return None

    for match in DATE_PT_RE.finditer(raw):
        day_raw, month_raw, year_raw = match.groups()
        day = 1 if day_raw == "1º" else int(day_raw)
        month_key = strip_accents(month_raw.lower())
        month = MONTHS_PT.get(month_key)
        if not month:
            continue
        year = int(year_raw)
        try:
            return dt.date(year, month, day)
        except ValueError:
            continue

    for match in DATE_BR_RE.finditer(raw):
        day_s, month_s, year_s = match.groups()
        day = int(day_s)
        month = int(month_s)
        year = int(year_s)
        if year < 100:
            year += 2000
        try:
            return dt.date(year, month, day)
        except ValueError:
            continue

    return None


def extract_date_iso(
    title: str,
    description: str,
    first_lines: List[str],
    published_line: str,
    url: str,
) -> Tuple[str, str]:
    candidates = [description, published_line]
    candidates.extend(first_lines[:3])
    candidates.append(title)

    for chunk in candidates:
        maybe = parse_date_from_text(chunk)
        if maybe:
            return maybe.isoformat(), "text"

    years = [int(y.group(0)) for y in YEAR_RE.finditer(url)]
    years = [y for y in years if 1880 <= y <= dt.date.today().year + 1]
    if years:
        return f"{years[0]:04d}-01-01", "url-year"

    return "", ""


def clean_location(raw: str) -> str:
    text = normalize_space(raw)
    if not text:
        return ""
    text = re.sub(r"^(em|na|no|nas|nos)\s+", "", text, flags=re.IGNORECASE)
    text = text.strip(" ,.;:-")
    return text


def extract_location(title: str, description: str, first_lines: List[str]) -> str:
    probes = []
    probes.extend(first_lines[:3])
    probes.append(description)
    probes.append(title)

    patterns = [
        re.compile(
            r"^(.{2,130}?),\s*(?:\d{1,2}|1º)\s+de\s+[a-zA-ZçÇãÃáÁàÀâÂéÉêÊíÍóÓôÔõÕúÚ]+\s+de\s+\d{4}",
            re.IGNORECASE,
        ),
        re.compile(r"^(.{2,130}?),\s*\d{1,2}[/-]\d{1,2}[/-]\d{4}", re.IGNORECASE),
        re.compile(
            r"\bem\s+([^,.;]{2,120}?)(?:,\s*(?:no dia\s*)?(?:\d{1,2}|1º)\s+de\s+[a-zA-ZçÇãÃáÁàÀâÂéÉêÊíÍóÓôÔõÕúÚ]+\s+de\s+\d{4}|$)",
            re.IGNORECASE,
        ),
    ]

    for text in probes:
        value = normalize_space(text)
        if not value:
            continue
        for pat in patterns:
            m = pat.search(value)
            if not m:
                continue
            loc = clean_location(m.group(1))
            if len(loc) >= 2:
                return loc

    return ""


def parse_body_lines(soup: BeautifulSoup) -> List[str]:
    selectors = [
        "#parent-fieldname-text",
        "#content-core",
        "article",
        "main",
    ]

    for selector in selectors:
        container = soup.select_one(selector)
        if not container:
            continue

        clone = BeautifulSoup(str(container), "html.parser")
        for bad in clone.select(
            "script,style,noscript,iframe,form,button,header,footer,nav,aside,figure,figcaption"
        ):
            bad.decompose()

        lines = []
        for node in clone.select("p,li,blockquote,h2,h3"):
            text = normalize_space(node.get_text(" ", strip=True))
            if text:
                lines.append(text)

        if not lines:
            text = normalize_space(clone.get_text(" ", strip=True))
            if text:
                lines = [text]

        text_len = sum(len(item) for item in lines)
        if text_len >= 160:
            return lines

    return []


def extract_text_from_pdf_bytes(payload: bytes) -> str:
    if not payload:
        return ""

    if fitz is not None:
        try:
            doc = fitz.open(stream=payload, filetype="pdf")
            parts = []
            for idx, page in enumerate(doc):
                if idx >= 12:
                    break
                text = normalize_space(page.get_text("text") or "")
                if text:
                    parts.append(text)
                if sum(len(chunk) for chunk in parts) >= 20000:
                    break
            text_joined = "\n".join(parts).strip()
            if text_joined:
                return text_joined
        except Exception:
            pass

    if PdfReader is None:
        return ""
    try:
        reader = PdfReader(io.BytesIO(payload))
    except Exception:
        return ""

    parts = []
    for idx, page in enumerate(reader.pages):
        if idx >= 12:
            break
        try:
            text = normalize_space(page.extract_text() or "")
        except Exception:
            text = ""
        if text:
            parts.append(text)
        if sum(len(chunk) for chunk in parts) >= 20000:
            break
    return "\n".join(parts).strip()


def build_attachment_candidates(soup: BeautifulSoup, current_url: str) -> List[str]:
    candidates = []
    seen = set()

    base = canonical_article_url(current_url)
    low_base = base.lower()
    if low_base.endswith(".pdf"):
        seen.add(base)
        candidates.append(base)
    if low_base.endswith(".pdf/view"):
        pdf_url = base[: -len("/view")]
        if pdf_url not in seen:
            seen.add(pdf_url)
            candidates.append(pdf_url)

    for anchor in soup.select("a[href]"):
        href = normalize_space(anchor.get("href", ""))
        if not href:
            continue
        full = canonical_article_url(urljoin(current_url, href))
        if full in seen:
            continue
        low = full.lower()
        if (
            "@@download/file" in low
            or low.endswith(".pdf")
            or ".pdf/view" in low
            or low.endswith(".txt")
        ):
            seen.add(full)
            candidates.append(full)
    return candidates


def extract_attachment_text(fetcher: Fetcher, soup: BeautifulSoup, current_url: str) -> str:
    candidates = build_attachment_candidates(soup, current_url)
    if not candidates:
        return ""

    for candidate in candidates[:4]:
        low = candidate.lower()
        try:
            data, _final, content_type = fetcher.fetch_bytes(candidate)
        except Exception:
            continue

        if ".pdf" in low or "application/pdf" in content_type.lower():
            text = extract_text_from_pdf_bytes(data)
            if len(text) >= 80:
                return text
            continue

        if low.endswith(".txt") or "text/plain" in content_type.lower():
            txt = data.decode("utf-8", errors="ignore")
            txt = normalize_space(txt)
            if len(txt) >= 80:
                return txt

    return ""


def is_probable_article_page(soup: BeautifulSoup) -> bool:
    heading = soup.select_one("h1.documentFirstHeading") or soup.select_one("h1")
    if not heading:
        return False

    title = fold_text(heading.get_text(" ", strip=True))
    if title in {
        "discursos",
        "entrevistas",
        "ultimos discursos e pronunciamentos",
        "ultimas entrevistas",
    }:
        return False

    para_count = len(soup.select("#parent-fieldname-text p, #content-core p"))
    if para_count >= 3:
        return True

    body_lines = parse_body_lines(soup)
    text_len = sum(len(item) for item in body_lines)
    return text_len >= 250


def extract_listing_links(
    soup: BeautifulSoup,
    current_url: str,
    doc_type: str,
    source: str,
    president_slug: str,
) -> List[Tuple[str, str]]:
    selectors = [
        "span.summary a[href]",
        "a.state-published.url[href]",
        "a.summary.url[href]",
        "h2.tileHeadline a[href]",
        "h3.tileHeadline a[href]",
    ]

    out: List[Tuple[str, str]] = []
    seen = set()

    for selector in selectors:
        for anchor in soup.select(selector):
            href = normalize_space(anchor.get("href", ""))
            if not href:
                continue
            abs_url = canonical_article_url(urljoin(current_url, href))
            if not abs_url:
                continue
            low = abs_url.lower()

            if source == "planalto":
                if doc_type == "entrevista":
                    if "/acompanhe-o-planalto/entrevistas/" not in low:
                        continue
                    if low.endswith("/entrevistas") or low.endswith("/listagem"):
                        continue
                else:
                    if "/acompanhe-o-planalto/discursos-e-pronunciamentos/" not in low:
                        continue
                    if low.endswith("/discursos-e-pronunciamentos") or low.endswith("/ultimos-discursos"):
                        continue
            else:
                if "/presidencia/ex-presidentes/" not in low:
                    continue
                if president_slug and f"/{president_slug}/" not in low:
                    continue
                if doc_type == "entrevista" and "/entrevist" not in low:
                    continue
                if doc_type == "discurso" and "/discurs" not in low:
                    continue

            title_hint = normalize_space(anchor.get_text(" ", strip=True))
            key = (abs_url, title_hint)
            if key in seen:
                continue
            seen.add(key)
            out.append((abs_url, title_hint))

    return out


def extract_pagination_urls(soup: BeautifulSoup, page_url: str, base_url: str) -> List[str]:
    base_path = urlparse(canonical_article_url(base_url)).path
    urls = []
    seen = set()

    for anchor in soup.select("a[href]"):
        href = normalize_space(anchor.get("href", ""))
        if "b_start:int=" not in href:
            continue
        full = canonical_page_url(urljoin(page_url, href))
        parsed = urlparse(full)
        if parsed.path != base_path:
            continue
        if full in seen:
            continue
        seen.add(full)
        urls.append(full)

    def sort_key(url: str):
        match = re.search(r"b_start:int=(\d+)", url)
        return int(match.group(1)) if match else 0

    urls.sort(key=sort_key)
    return urls


def extract_child_category_urls(
    soup: BeautifulSoup,
    current_url: str,
    doc_type: str,
    president_slug: str,
) -> List[str]:
    if not president_slug:
        return []

    current_canon = canonical_article_url(current_url)
    out = []
    seen = set()

    for anchor in soup.select("a[href]"):
        href = normalize_space(anchor.get("href", ""))
        if not href:
            continue

        full = canonical_article_url(urljoin(current_url, href))
        if not full or full == current_canon:
            continue

        low = full.lower()
        if "/presidencia/ex-presidentes/" not in low:
            continue
        if f"/{president_slug}/" not in low:
            continue
        if doc_type == "discurso" and "/discurs" not in low:
            continue
        if doc_type == "entrevista" and "/entrevist" not in low:
            continue

        last = (urlparse(full).path.rstrip("/").split("/")[-1] or "").lower()
        text = fold_text(anchor.get_text(" ", strip=True))

        looks_category = (
            bool(re.fullmatch(r"\d{4}", last))
            or "mandato" in last
            or "mandato" in text
            or last in {
                "discursos",
                "entrevistas",
                "discursos-do-presidente-da-republica",
                "entrevistas-concedidas-pelo-presidente-michel-temer",
                "entrevistas-presidenta",
            }
        )

        if not looks_category:
            continue

        if full in seen:
            continue
        seen.add(full)
        out.append(full)

    return out


def mandate_from_date(slug: str, president: str, date_iso: str, url: str) -> str:
    url_low = url.lower()
    if slug == "luiz-inacio-lula-da-silva":
        if "/1o-mandato/" in url_low:
            return "Lula 1 (2003-2006)"
        if "/2o-mandato/" in url_low:
            return "Lula 2 (2007-2010)"

    rules = MANDATE_RULES.get(slug, [])
    if date_iso:
        for start, end, label in rules:
            if start <= date_iso <= end:
                return label

    if rules and not date_iso:
        if len(rules) == 1:
            return rules[0][2]
        if slug == "dilma-rousseff":
            return "Dilma (2011-2016)"
        if slug == "fernando-henrique-cardoso":
            return "FHC (1995-2002)"
        if slug == "getulio-vargas":
            return "Vargas (1930-1945 / 1951-1954)"

    if date_iso:
        year = date_iso[:4]
        return f"{president} ({year})"
    return f"{president} (mandato nao identificado)"


def normalize_target_mandate(
    source: str,
    president_slug: str,
    mandate: str,
) -> str:
    current = normalize_space(mandate)
    if source == "planalto" and president_slug == "luiz-inacio-lula-da-silva":
        return "Lula 3 (2023-)"
    return current


def is_target_mandate(mandate: str) -> bool:
    return normalize_space(mandate) in TARGET_MANDATES


def should_keep_candidate_scope(url: str, source: str, president_slug: str, mandate: str) -> bool:
    src = normalize_space(source).lower()
    slug = normalize_space(president_slug).lower()
    low_url = (url or "").lower()

    if is_target_mandate(mandate):
        return True
    if slug == "bolsonaro" or "/presidencia/ex-presidentes/bolsonaro/" in low_url:
        return True
    if src == "planalto" or "gov.br/planalto" in low_url:
        return True
    if slug == "luiz-inacio-lula-da-silva":
        return True
    return False


def write_json(path: Path, payload: dict):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_existing_records() -> Dict[str, dict]:
    records: Dict[str, dict] = {}
    if not RECORDS_GZ.exists():
        return records

    with gzip.open(RECORDS_GZ, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            url = canonical_article_url(record.get("url", ""))
            if not url:
                continue
            record["url"] = url
            records[url] = record
    return records


def save_records(records: List[dict]):
    with gzip.open(RECORDS_GZ, "wt", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def parse_document_record(
    url: str,
    html: str,
    fetcher: Fetcher,
    listing_info: dict,
    previous: dict,
) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    title = ""
    for selector in ["h1.documentFirstHeading", "h1", "meta[property='og:title']", "title"]:
        node = soup.select_one(selector)
        if not node:
            continue
        if node.name == "meta":
            title = normalize_space(node.get("content", ""))
        else:
            title = normalize_space(node.get_text(" ", strip=True))
        if title:
            break

    description = ""
    for selector in ["div.documentDescription", "p.documentDescription", "meta[name='description']"]:
        node = soup.select_one(selector)
        if not node:
            continue
        if node.name == "meta":
            description = normalize_space(node.get("content", ""))
        else:
            description = normalize_space(node.get_text(" ", strip=True))
        if description:
            break

    published_line = ""
    pub_node = soup.select_one("span.documentPublished, time")
    if pub_node:
        published_line = normalize_space(pub_node.get_text(" ", strip=True))

    lines = parse_body_lines(soup)
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    if len(text) < 80:
        attachment_text = extract_attachment_text(fetcher, soup, url)
        if attachment_text:
            text = attachment_text
            lines = [line for line in attachment_text.split("\n") if normalize_space(line)]

    first_lines = [line for line in lines if line][:5]
    if not first_lines and text:
        first_lines = [text[:200]]

    date_iso, date_source = extract_date_iso(title, description, first_lines, published_line, url)
    location = extract_location(title, description, first_lines)

    source = listing_info.get("source") or previous.get("source") or "biblioteca"
    doc_type = (
        listing_info.get("type")
        or previous.get("type")
        or infer_doc_type(url, title)
    )

    president_slug = (
        listing_info.get("president_slug")
        or previous.get("president_slug")
        or infer_president_slug(url)
    )
    if source == "planalto":
        president_slug = "luiz-inacio-lula-da-silva"

    president = (
        listing_info.get("president")
        or previous.get("president")
        or president_name(president_slug)
    )

    mandate = mandate_from_date(president_slug, president, date_iso, url)

    title_hint = listing_info.get("title_hint", "")
    if not title and title_hint:
        title = title_hint

    word_count = len(re.findall(r"\w+", text, flags=re.UNICODE))

    return {
        "id": hashlib.sha1(url.encode("utf-8")).hexdigest()[:20],
        "url": url,
        "source": source,
        "type": doc_type,
        "president_slug": president_slug,
        "president": president,
        "mandate": mandate,
        "date": date_iso,
        "date_source": date_source,
        "title": title,
        "location": location,
        "description": description,
        "text": text,
        "word_count": word_count,
        "updated_at": now_iso(),
    }


def parse_binary_record(
    url: str,
    payload: bytes,
    content_type: str,
    listing_info: dict,
    previous: dict,
) -> Optional[dict]:
    low_ct = (content_type or "").lower()
    low_url = url.lower()

    text = ""
    if ".pdf" in low_url or "application/pdf" in low_ct:
        text = extract_text_from_pdf_bytes(payload)
    elif "text/plain" in low_ct or low_url.endswith(".txt"):
        text = payload.decode("utf-8", errors="ignore")
        text = normalize_space(text)

    if not text:
        return None

    title = (
        listing_info.get("title_hint")
        or previous.get("title")
        or normalize_space(previous.get("description", ""))
    )
    description = previous.get("description", "")
    first_lines = [normalize_space(line) for line in text.splitlines() if normalize_space(line)][:5]
    date_iso, date_source = extract_date_iso(title, description, first_lines, "", url)
    location = extract_location(title, description, first_lines)

    source = listing_info.get("source") or previous.get("source") or "biblioteca"
    doc_type = (
        listing_info.get("type")
        or previous.get("type")
        or infer_doc_type(url, title)
    )
    president_slug = (
        listing_info.get("president_slug")
        or previous.get("president_slug")
        or infer_president_slug(url)
    )
    if source == "planalto":
        president_slug = "luiz-inacio-lula-da-silva"
    president = (
        listing_info.get("president")
        or previous.get("president")
        or president_name(president_slug)
    )
    mandate = mandate_from_date(president_slug, president, date_iso, url)
    word_count = len(re.findall(r"\w+", text, flags=re.UNICODE))

    return {
        "id": hashlib.sha1(url.encode("utf-8")).hexdigest()[:20],
        "url": url,
        "source": source,
        "type": doc_type,
        "president_slug": president_slug,
        "president": president,
        "mandate": mandate,
        "date": date_iso,
        "date_source": date_source,
        "title": title,
        "location": location,
        "description": description,
        "text": text.strip(),
        "word_count": word_count,
        "updated_at": now_iso(),
    }


def crawl_category(
    fetcher: Fetcher,
    category: CrawlCategory,
    max_pages: int,
    errors: List[dict],
) -> Tuple[Dict[str, dict], int]:
    queue = [canonical_page_url(category.url)]
    seen_pages = set()
    entries: Dict[str, dict] = {}
    visited_count = 0

    while queue and visited_count < max_pages:
        page_url = queue.pop(0)
        if page_url in seen_pages:
            continue
        seen_pages.add(page_url)
        visited_count += 1

        try:
            html, final_url, _status = fetcher.fetch_html(page_url)
        except Exception as exc:
            errors.append({"scope": "listing", "url": page_url, "error": str(exc)})
            continue

        soup = BeautifulSoup(html, "html.parser")
        listing_links = extract_listing_links(
            soup,
            final_url,
            category.doc_type,
            category.source,
            category.president_slug,
        )

        for doc_url, title_hint in listing_links:
            if doc_url not in entries:
                entries[doc_url] = {
                    "url": doc_url,
                    "title_hint": title_hint,
                    "type": category.doc_type,
                    "source": category.source,
                    "president_slug": category.president_slug,
                    "president": category.president or president_name(category.president_slug),
                    "listing_url": category.url,
                }

        if not listing_links and is_probable_article_page(soup):
            doc_url = canonical_article_url(final_url)
            if doc_url not in entries:
                title_hint = ""
                heading = soup.select_one("h1.documentFirstHeading") or soup.select_one("h1")
                if heading:
                    title_hint = normalize_space(heading.get_text(" ", strip=True))
                entries[doc_url] = {
                    "url": doc_url,
                    "title_hint": title_hint,
                    "type": category.doc_type,
                    "source": category.source,
                    "president_slug": category.president_slug,
                    "president": category.president or president_name(category.president_slug),
                    "listing_url": category.url,
                }

        for pag_url in extract_pagination_urls(soup, final_url, category.url):
            if pag_url not in seen_pages and pag_url not in queue:
                queue.append(pag_url)

        if not listing_links:
            child_urls = extract_child_category_urls(
                soup,
                final_url,
                category.doc_type,
                category.president_slug,
            )
            for child in child_urls:
                child_page = canonical_page_url(child)
                if child_page not in seen_pages and child_page not in queue:
                    queue.append(child_page)

    return entries, visited_count


def discover_biblioteca_categories(fetcher: Fetcher, errors: List[dict]) -> List[CrawlCategory]:
    categories: Dict[Tuple[str, str], CrawlCategory] = {}

    # Seed with known paths from the user.
    for seed in BIBLIOTECA_SEED_CATEGORIES:
        url = canonical_article_url(seed["url"])
        cat = CrawlCategory(
            url=url,
            doc_type=seed["type"],
            source="biblioteca",
            president_slug=seed.get("president_slug", ""),
            president=president_name(seed.get("president_slug", "")),
        )
        categories[(cat.url, cat.doc_type)] = cat

    try:
        root_html, root_final, _ = fetcher.fetch_html(BIBLIOTECA_ROOT)
    except Exception as exc:
        errors.append({"scope": "discovery", "url": BIBLIOTECA_ROOT, "error": str(exc)})
        return list(categories.values())

    root_soup = BeautifulSoup(root_html, "html.parser")
    president_pages = []
    seen_pres_pages = set()

    for anchor in root_soup.select("a[href]"):
        href = normalize_space(anchor.get("href", ""))
        if not href:
            continue
        full = canonical_article_url(urljoin(root_final, href))
        low = full.lower()
        if "/presidencia/ex-presidentes/" not in low:
            continue
        if full == canonical_article_url(BIBLIOTECA_ROOT):
            continue
        if full in seen_pres_pages:
            continue
        seen_pres_pages.add(full)
        president_pages.append(full)

    for pres_page in president_pages:
        pres_slug = infer_president_slug(pres_page)
        if pres_slug not in TARGET_BIBLIOTECA_SLUGS:
            continue
        pres_name = president_name(pres_slug)
        try:
            html, final_url, _ = fetcher.fetch_html(pres_page)
        except Exception as exc:
            errors.append({"scope": "discovery", "url": pres_page, "error": str(exc)})
            continue

        soup = BeautifulSoup(html, "html.parser")
        for anchor in soup.select("a[href]"):
            href = normalize_space(anchor.get("href", ""))
            if not href:
                continue
            full = canonical_article_url(urljoin(final_url, href))
            low = full.lower()
            if "/presidencia/ex-presidentes/" not in low:
                continue
            if pres_slug and f"/{pres_slug}/" not in low:
                continue

            doc_type = ""
            if "/entrevist" in low:
                doc_type = "entrevista"
            elif "/discurs" in low:
                doc_type = "discurso"
            if not doc_type:
                continue

            key = (full, doc_type)
            if key not in categories:
                categories[key] = CrawlCategory(
                    url=full,
                    doc_type=doc_type,
                    source="biblioteca",
                    president_slug=pres_slug,
                    president=pres_name,
                )

    ordered = sorted(
        categories.values(),
        key=lambda item: (item.doc_type, item.president_slug, item.url),
    )
    return ordered


def build_items_json(records: List[dict]) -> List[dict]:
    items = []
    for rec in records:
        text = rec.get("text", "")
        items.append(
            {
                "id": rec.get("id", ""),
                "url": rec.get("url", ""),
                "source": rec.get("source", ""),
                "type": rec.get("type", ""),
                "president_slug": rec.get("president_slug", ""),
                "president": rec.get("president", ""),
                "mandate": rec.get("mandate", ""),
                "date": rec.get("date", ""),
                "title": rec.get("title", ""),
                "location": rec.get("location", ""),
                "description": rec.get("description", ""),
                "word_count": rec.get("word_count", 0),
                "text_length": len(text),
            }
        )
    return items


def write_csv(records: List[dict]):
    fields = [
        "date",
        "president",
        "mandate",
        "type",
        "title",
        "location",
        "url",
        "source",
        "word_count",
    ]

    with CSV_FILE.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for rec in records:
            writer.writerow({field: rec.get(field, "") for field in fields})


def sort_records(records: Iterable[dict]) -> List[dict]:
    def key(rec: dict):
        date = rec.get("date") or "0000-00-00"
        return (
            date,
            rec.get("president", ""),
            rec.get("title", ""),
            rec.get("url", ""),
        )

    return sorted(records, key=key, reverse=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Lulometro dashboard dataset")
    parser.add_argument("--force-details", action="store_true", help="Refetch detail pages even if already stored")
    parser.add_argument("--skip-biblioteca", action="store_true", help="Skip ex-presidentes source")
    parser.add_argument("--skip-planalto", action="store_true", help="Skip gov.br Planalto source")
    parser.add_argument(
        "--skip-crawl",
        action="store_true",
        help="Skip listings crawl and reprocess only URLs already in records.jsonl.gz",
    )
    parser.add_argument(
        "--max-new-details",
        type=int,
        default=0,
        help="Only fetch up to N newly discovered detail URLs (debug mode)",
    )
    parser.add_argument(
        "--max-pages-per-category",
        type=int,
        default=500,
        help="Safety limit for pagination crawling in each category",
    )
    parser.add_argument(
        "--max-details",
        type=int,
        default=0,
        help="Limit detail fetch to first N URLs (useful for chunked runs)",
    )
    parser.add_argument(
        "--max-failures",
        type=int,
        default=3,
        help="Skip URLs that already failed this many detail attempts",
    )
    parser.add_argument(
        "--skip-bluesky",
        action="store_true",
        help="Pula raspagem do Bluesky (perfis em lulometro_bluesky.BLUESKY_ACCOUNTS)",
    )
    args = parser.parse_args()

    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    ensure_dirs()
    started = time.time()

    existing_records = load_existing_records()
    existing_keys_before = set(existing_records.keys())

    fetcher = Fetcher(USER_AGENT, use_biblioteca=not args.skip_biblioteca)
    crawl_errors: List[dict] = []

    try:
        if not args.skip_biblioteca:
            try:
                fetcher.bootstrap_biblioteca()
            except (PlaywrightError, Exception) as exc:
                crawl_errors.append(
                    {
                        "scope": "bootstrap",
                        "url": BIBLIOTECA_ROOT,
                        "error": f"Playwright bootstrap failed: {exc}",
                    }
                )

        categories: List[CrawlCategory] = []
        biblioteca_categories: List[CrawlCategory] = []
        candidate_by_url: Dict[str, dict] = {}
        pages_by_source = defaultdict(int)

        if args.skip_crawl:
            for url, rec in existing_records.items():
                candidate_by_url[url] = {
                    "url": url,
                    "title_hint": rec.get("title", ""),
                    "type": rec.get("type", ""),
                    "source": rec.get("source", ""),
                    "president_slug": rec.get("president_slug", ""),
                    "president": rec.get("president", ""),
                    "listing_url": "",
                }
        else:
            if not args.skip_planalto:
                for cfg in PLANALTO_LISTINGS:
                    categories.append(
                        CrawlCategory(
                            url=canonical_article_url(cfg["url"]),
                            doc_type=cfg["type"],
                            source=cfg["source"],
                            president_slug=cfg["president_slug"],
                            president=cfg["president"],
                        )
                    )

            if not args.skip_biblioteca:
                biblioteca_categories = discover_biblioteca_categories(fetcher, crawl_errors)
                categories.extend(biblioteca_categories)

            for idx, category in enumerate(categories, start=1):
                entries, visited_pages = crawl_category(
                    fetcher,
                    category,
                    max_pages=args.max_pages_per_category,
                    errors=crawl_errors,
                )
                pages_by_source[category.source] += visited_pages

                for doc_url, info in entries.items():
                    existing = candidate_by_url.get(doc_url)
                    if not existing:
                        candidate_by_url[doc_url] = info
                        continue

                    if not existing.get("title_hint") and info.get("title_hint"):
                        existing["title_hint"] = info["title_hint"]
                    if not existing.get("president_slug") and info.get("president_slug"):
                        existing["president_slug"] = info["president_slug"]
                    if not existing.get("president") and info.get("president"):
                        existing["president"] = info["president"]

                if idx % 10 == 0 or len(entries) > 0:
                    print(
                        f"[{idx}/{len(categories)}] {category.source}:{category.doc_type} "
                        f"{category.url} -> {len(entries)} urls ({visited_pages} pages)",
                        flush=True,
                    )

        # Merge listing hints with existing cache.
        for url, hint in candidate_by_url.items():
            rec = existing_records.get(url, {})
            rec["url"] = url
            rec["source"] = hint.get("source") or rec.get("source") or ""
            rec["type"] = hint.get("type") or rec.get("type") or ""
            rec["president_slug"] = hint.get("president_slug") or rec.get("president_slug") or ""
            rec["president"] = (
                hint.get("president")
                or rec.get("president")
                or president_name(rec.get("president_slug", ""))
            )
            if hint.get("title_hint") and not rec.get("title"):
                rec["title"] = hint["title_hint"]
            existing_records[url] = rec

        # Keep only URLs that belong to Lula 3 / Bolsonaro scope.
        scoped_candidates: Dict[str, dict] = {}
        for url, hint in candidate_by_url.items():
            prev = existing_records.get(url, {})
            if should_keep_candidate_scope(
                url=url,
                source=hint.get("source") or prev.get("source", ""),
                president_slug=hint.get("president_slug") or prev.get("president_slug", ""),
                mandate=prev.get("mandate", ""),
            ):
                scoped_candidates[url] = hint
        candidate_by_url = scoped_candidates

        needs_detail = []
        new_urls = []

        for url in candidate_by_url:
            rec = existing_records.get(url, {})
            is_new = url not in existing_keys_before
            if is_new:
                new_urls.append(url)
            if args.force_details:
                needs_detail.append(url)
                continue

            if not rec.get("text"):
                fail_count = int(rec.get("fetch_failures") or 0)
                if args.max_failures > 0 and fail_count >= args.max_failures:
                    continue
                needs_detail.append(url)
                continue
            if not rec.get("title"):
                fail_count = int(rec.get("fetch_failures") or 0)
                if args.max_failures > 0 and fail_count >= args.max_failures:
                    continue
                needs_detail.append(url)
                continue

        if args.max_new_details and args.max_new_details > 0:
            allow_new = set(new_urls[: args.max_new_details])
            filtered = []
            for url in needs_detail:
                if url in existing_keys_before or url in allow_new:
                    filtered.append(url)
            needs_detail = filtered

        def detail_priority(url: str) -> Tuple[int, int, int]:
            rec = existing_records.get(url, {})
            fail_count = int(rec.get("fetch_failures") or 0)
            low = url.lower()
            # Prioritize direct HTML docs before attachments/PDF views.
            heavy = 1 if ("/view" in low or ".pdf" in low or "@@download/file" in low) else 0
            return (fail_count, heavy, len(low))

        needs_detail.sort(key=detail_priority)

        if args.max_details and args.max_details > 0:
            needs_detail = needs_detail[: args.max_details]

        print(
            f"Candidate URLs: {len(candidate_by_url)} | "
            f"New URLs: {len(new_urls)} | "
            f"Need detail fetch: {len(needs_detail)}",
            flush=True,
        )

        fetched_ok = 0
        for idx, url in enumerate(needs_detail, start=1):
            hint = candidate_by_url.get(url, {})
            prev = existing_records.get(url, {})

            record = None
            detail_error = None
            low_url = url.lower()
            binary_candidates = []
            if low_url.endswith("/view"):
                binary_candidates.append(url[:-5])
            if ".pdf/view" in low_url:
                binary_candidates.append(url.replace("/view", ""))
            if ".pdf" in low_url:
                binary_candidates.append(url)
            if "@@download/file" in low_url:
                binary_candidates.append(url)

            seen_binary = set()
            for candidate in binary_candidates:
                b_url = canonical_article_url(candidate)
                if not b_url or b_url in seen_binary:
                    continue
                seen_binary.add(b_url)
                try:
                    payload, _final_bin, content_type = fetcher.fetch_bytes(b_url)
                    rec_bin = parse_binary_record(url, payload, content_type, hint, prev)
                    if rec_bin:
                        record = rec_bin
                        break
                except Exception as exc:
                    detail_error = exc
                    continue

            if record is None:
                try:
                    html, _final_url, _ = fetcher.fetch_html(url)
                except Exception as exc:
                    detail_error = exc
                else:
                    record = parse_document_record(url, html, fetcher, hint, prev)

            if record is not None:
                record["fetch_failures"] = 0
                record["last_error"] = ""
                existing_records[url] = record
                fetched_ok += 1
            elif detail_error is not None:
                failed_rec = dict(prev)
                failed_rec["url"] = url
                failed_rec["fetch_failures"] = int(prev.get("fetch_failures") or 0) + 1
                failed_rec["last_error"] = normalize_space(str(detail_error))[:500]
                failed_rec["updated_at"] = now_iso()
                existing_records[url] = failed_rec
                crawl_errors.append({"scope": "detail", "url": url, "error": str(detail_error)})

            if idx % 100 == 0 or idx == len(needs_detail):
                print(f"Detail fetch: {idx}/{len(needs_detail)}", flush=True)

            if idx % DETAIL_CHECKPOINT_EVERY == 0:
                checkpoint_records = sort_records(existing_records.values())
                save_records(checkpoint_records)
                print(f"Checkpoint saved at detail {idx}/{len(needs_detail)}", flush=True)

        # Normalize all records, keep fallback fields coherent and restrict to target mandates.
        normalized = []
        filtered_out = 0
        for url, rec in existing_records.items():
            rec["url"] = canonical_article_url(rec.get("url") or url)
            rec["source"] = rec.get("source") or (
                "planalto" if "gov.br/planalto" in rec["url"] else "biblioteca"
            )
            rec["type"] = rec.get("type") or infer_doc_type(rec["url"], rec.get("title", ""))

            slug = rec.get("president_slug") or infer_president_slug(rec["url"])
            if rec["source"] == "planalto":
                slug = "luiz-inacio-lula-da-silva"
            rec["president_slug"] = slug
            rec["president"] = rec.get("president") or president_name(slug)
            rec["mandate"] = normalize_target_mandate(
                rec["source"],
                slug,
                mandate_from_date(slug, rec["president"], rec.get("date", ""), rec["url"]),
            )
            rec["title"] = normalize_space(rec.get("title", ""))
            rec["location"] = normalize_space(rec.get("location", ""))
            rec["description"] = normalize_space(rec.get("description", ""))
            rec["text"] = rec.get("text", "")
            rec["word_count"] = rec.get("word_count") or len(
                re.findall(r"\w+", rec.get("text", ""), flags=re.UNICODE)
            )
            rec["updated_at"] = rec.get("updated_at") or now_iso()
            rec["id"] = rec.get("id") or hashlib.sha1(rec["url"].encode("utf-8")).hexdigest()[:20]
            rec["fetch_failures"] = int(rec.get("fetch_failures") or 0)
            rec["last_error"] = normalize_space(rec.get("last_error", ""))
            if not is_target_mandate(rec["mandate"]):
                filtered_out += 1
                continue
            normalized.append(rec)

        # ---- Bluesky (fonte adicional) ----
        if not args.skip_bluesky:
            try:
                print("→ Raspando Bluesky...", flush=True)
                bsky_records = fetch_all_bluesky_records()
                normalized.extend(bsky_records)
            except Exception as exc:
                crawl_errors.append({
                    "scope": "bluesky",
                    "url": "",
                    "error": f"Bluesky fetch failed: {exc}",
                })
        else:
            # Preserva posts do Bluesky que já estavam no records.jsonl.gz
            preserved_bsky = [
                r for r in existing_records.values() if r.get("source") == "bluesky"
            ]
            if preserved_bsky:
                print(f"→ Preservando {len(preserved_bsky)} posts Bluesky de execução anterior", flush=True)
                normalized.extend(preserved_bsky)

        records_sorted = sort_records(normalized)
        save_records(records_sorted)

        items = build_items_json(records_sorted)
        write_json(ITEMS_JSON, {"items": items})
        write_csv(records_sorted)

        people_summary = {}
        try:
            people_payload = build_people_index(records_sorted, USER_AGENT)
        except Exception as exc:
            crawl_errors.append(
                {
                    "scope": "people-index",
                    "url": "",
                    "error": f"Failed to build nominal mentions index: {exc}",
                }
            )
            people_summary = {
                "status": "error",
                "error": normalize_space(str(exc))[:500],
            }
        else:
            write_json(PEOPLE_DIRECTORY_JSON, people_payload["directory"])
            write_json(PEOPLE_MENTIONS_JSON, people_payload["mentions"])
            people_summary = {
                "status": "ok",
                **people_payload["summary"]["counts"],
                "date_range": people_payload["summary"].get("date_range", {}),
            }

        source_counter = Counter(rec.get("source", "") for rec in records_sorted)
        type_counter = Counter(rec.get("type", "") for rec in records_sorted)
        president_counter = Counter(rec.get("president", "") for rec in records_sorted)
        mandate_counter = Counter(rec.get("mandate", "") for rec in records_sorted)

        metadata = {
            "project": "Lulometro",
            "title": "Lulometro: analise de discurso de presidentes brasileiros",
            "generated_at": now_iso(),
            "timezone": "America/Sao_Paulo",
            "scope_mandates": sorted(TARGET_MANDATES),
            "total_records": len(records_sorted),
            "excluded_records": filtered_out,
            "candidate_urls": len(candidate_by_url),
            "new_urls": len(new_urls),
            "detail_fetched": fetched_ok,
            "sources": dict(source_counter),
            "types": dict(type_counter),
            "top_presidents": president_counter.most_common(20),
            "top_mandates": mandate_counter.most_common(20),
            "people_index": people_summary,
            "duration_seconds": round(time.time() - started, 2),
        }
        write_json(META_JSON, metadata)

        sources_payload = {
            "planalto_listings": PLANALTO_LISTINGS,
            "biblioteca_root": BIBLIOTECA_ROOT,
            "scope_mandates": sorted(TARGET_MANDATES),
            "target_biblioteca_slugs": sorted(TARGET_BIBLIOTECA_SLUGS),
            "biblioteca_categories": [
                {
                    "url": cat.url,
                    "type": cat.doc_type,
                    "source": cat.source,
                    "president_slug": cat.president_slug,
                    "president": cat.president,
                }
                for cat in biblioteca_categories
            ],
            "pages_visited_by_source": dict(pages_by_source),
            "errors": crawl_errors[:500],
        }
        write_json(SOURCES_JSON, sources_payload)

        print("Lulometro build complete.", flush=True)
        print(f"Records: {len(records_sorted)}", flush=True)
        print(f"Errors logged: {len(crawl_errors)}", flush=True)

    finally:
        fetcher.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
