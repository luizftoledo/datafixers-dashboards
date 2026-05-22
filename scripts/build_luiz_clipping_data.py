#!/usr/bin/env python3
"""Build a personal clipping dataset for Luiz Fernando Toledo."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

ROOT_DIR = Path(__file__).resolve().parents[1]
DASH_DIR = ROOT_DIR / "clipping"
DATA_DIR = DASH_DIR / "data"
ITEMS_JSON = DATA_DIR / "items.json"
META_JSON = DATA_DIR / "metadata.json"
SOURCES_JSON = DATA_DIR / "sources.json"

NAME_VARIANTS = [
    "Luiz Fernando Toledo",
    "Luiz Fernando Toledo Antunes",
]
IDENTITY_TOKENS = {
    "abraji",
    "bbc",
    "cambridge",
    "cnn",
    "columbia",
    "dados",
    "data journalism",
    "datafixers",
    "estadao",
    "estadão",
    "fiquem sabendo",
    "foia",
    "insper",
    "investigative",
    "investigativo",
    "journalist",
    "jornalismo",
    "jornalista",
    "londres",
    "london",
    "occrp",
    "oxford",
    "pesquisador",
    "polis",
    "professor",
    "public records",
    "reporter",
    "reuters institute",
    "transparencia",
    "transparência",
    "uol",
}
PROFILE_DOMAINS = {
    "brown.columbia.edu",
    "cam.ac.uk",
    "congresso.abraji.org.br",
    "devstudies.cam.ac.uk",
    "escavador.com",
    "gijc2025.org",
    "linkedin.com",
    "orcid.org",
    "reutersinstitute.politics.ox.ac.uk",
    "talks.ox.ac.uk",
    "theintercept.com",
    "udemy.com",
}
SOCIAL_DOMAINS = {
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "open.spotify.com",
    "spotify.com",
    "x.com",
    "youtube.com",
    "youtu.be",
}
CATEGORY_LABELS = {
    "reportagem_autoral": "Reportagens publicadas por mim",
    "republicacao": "Republicações / syndication",
    "eco_repercussao": "Repercussão / interpretação de apuração minha",
    "citacao_fonte": "Citado como fonte / especialista",
    "mencao": "Menções ao meu nome",
    "entrevista": "Entrevistas / podcasts",
    "palestra_curso": "Palestras / cursos / workshops",
    "perfil_bio": "Perfis / bios / páginas institucionais",
}
CATEGORY_ORDER = [
    "reportagem_autoral",
    "republicacao",
    "eco_repercussao",
    "citacao_fonte",
    "mencao",
    "entrevista",
    "palestra_curso",
    "perfil_bio",
]
DUCKDUCKGO_LITE_QUERIES = [
    '"Luiz Fernando Toledo"',
    '"Luiz Fernando Toledo Antunes"',
    '"segundo reportagem" "Luiz Fernando Toledo"',
    '"investigação da BBC" "Luiz Fernando Toledo"',
    '"de acordo com" "Luiz Fernando Toledo"',
]
GOOGLE_NEWS_QUERIES = [
    '"Luiz Fernando Toledo"',
    '"Luiz Fernando Toledo Antunes"',
    '"segundo reportagem" "Luiz Fernando Toledo"',
    '"levantamento" "Luiz Fernando Toledo"',
]
JINA_PREFIX = "https://r.jina.ai/http://"
# Domains from author pages — articles found here are considered "reportagem_autoral"
AUTHOR_PAGE_DOMAINS = {
    "bbc.com",
    "bbc.co.uk",
    "intercept.com.br",
    "theintercept.com",
    "piaui.folha.uol.com.br",
    "apublica.org",
    "ojoioeotrigo.com.br",
    "estadao.com.br",
    "g1.globo.com",
    "noticias.uol.com.br",
    "tab.uol.com.br",
}
KNOWN_SOURCE_PAGES = [
    {
        "url": "https://www.bbc.com/portuguese/topics/c5ydzy3vg0xt",
        "title": "Luiz Fernando Toledo - BBC News Brasil",
        "category_hint": "perfil_bio",
        "relation_hint": "pagina de autor",
        "source_label": "known-source",
        "notes": "Pagina de autor descoberta por busca na web.",
        "crawl_mode": "bbc_articles",
    },
    {
        "url": "https://www.intercept.com.br/equipe/luiz-fernando-toledo/",
        "title": "Luiz Fernando Toledo, Autor em Intercept Brasil",
        "category_hint": "perfil_bio",
        "relation_hint": "pagina de autor",
        "source_label": "known-source",
        "notes": "Pagina de autor descoberta por busca na web.",
        "crawl_mode": "year_paths",
    },
    {
        "url": "https://piaui.folha.uol.com.br/colaborador/luiz-fernando-toledo/",
        "title": "Luiz Fernando Toledo - revista piaui",
        "category_hint": "perfil_bio",
        "relation_hint": "pagina de autor",
        "source_label": "known-source",
        "notes": "Pagina de colaborador descoberta por busca na web.",
        "crawl_mode": "piaui_articles",
    },
    {
        "url": "https://apublica.org/autor/luizfernandotoledo/",
        "title": "Luiz Fernando Toledo, Autor em Agencia Publica",
        "category_hint": "perfil_bio",
        "relation_hint": "pagina de autor",
        "source_label": "known-source",
        "notes": "Pagina de autor descoberta por busca na web.",
        "crawl_mode": "year_paths",
    },
    {
        "url": "https://ojoioeotrigo.com.br/author/luiz-fernando-toledo/",
        "title": "Luiz Fernando Toledo - O Joio e O Trigo",
        "category_hint": "perfil_bio",
        "relation_hint": "pagina de autor",
        "source_label": "known-source",
        "notes": "Pagina de autor descoberta por busca na web.",
        "crawl_mode": "year_paths",
    },
    {
        "url": "https://www.estadao.com.br/autores/luiz-fernando-toledo/",
        "title": "Luiz Fernando Toledo - Estadao",
        "category_hint": "perfil_bio",
        "relation_hint": "pagina de autor",
        "source_label": "known-source",
        "notes": "Pagina de autor descoberta por busca na web.",
        "crawl_mode": "none",
    },
    {
        "url": "https://g1.globo.com/busca/?q=%22Luiz+Fernando+Toledo%22&species=notícias",
        "title": "Luiz Fernando Toledo - G1 Globo",
        "category_hint": "perfil_bio",
        "relation_hint": "busca de autor",
        "source_label": "known-source",
        "notes": "Busca de matérias no G1.",
        "crawl_mode": "none",
    },
]
KNOWN_DIRECT_ITEMS = [
    {
        "url": "https://www.escavador.com/sobre/3695893/luiz-fernando-toledo-antunes",
        "title": "Luiz Fernando Toledo Antunes - Escavador",
        "category_hint": "perfil_bio",
        "relation_hint": "perfil",
        "source_label": "known-direct",
        "notes": "Perfil externo encontrado por busca nominal.",
    },
    {
        "url": "https://www.icfj.org/about/profiles/luiz-fernando-toledo",
        "title": "Luiz Fernando Toledo - ICFJ",
        "category_hint": "perfil_bio",
        "relation_hint": "perfil",
        "source_label": "known-direct",
        "notes": "Perfil institucional encontrado por busca nominal.",
    },
    {
        "url": "https://www.devstudies.cam.ac.uk/news/luiz-fernando-toledo-2024s-fourth-most-awarded-journalist-brazil",
        "title": "Luiz Fernando Toledo 2024's fourth most awarded journalist in Brazil",
        "category_hint": "mencao",
        "relation_hint": "mencionado",
        "source_label": "known-direct",
        "notes": "Mencao institucional encontrada por busca nominal.",
    },
    {
        "url": "https://blogfca.pucminas.br/colab/luiz-fernando-toledo-entre-planilhas-pautas-e-fronteiras/",
        "title": "Luiz Fernando Toledo: entre planilhas, pautas e fronteiras",
        "category_hint": "perfil_bio",
        "relation_hint": "perfil",
        "source_label": "known-direct",
        "notes": "Perfil externo encontrado por busca nominal.",
    },
    {
        "url": "https://institutoling.org.br/index.php/bolsas-de-estudo/blog/bolsas-de-estudo/conheca-luiz-fernando-toledo-especialista-em-dados-publicos-diretor-da-abraji-e-cofundador-da-agencia-fiquem-sabendo",
        "title": "Conheca Luiz Fernando Toledo",
        "category_hint": "perfil_bio",
        "relation_hint": "perfil",
        "source_label": "known-direct",
        "notes": "Perfil externo encontrado por busca nominal.",
    },
    {
        "url": "https://www.tvgazeta.com.br/videos/luiz-fernando-toledo-cofundador-da-fiquem-sabendo-sobre-a-lei-de-acesso-a-informacao/",
        "title": "Luiz Fernando Toledo sobre a Lei de Acesso a Informacao",
        "category_hint": "entrevista",
        "relation_hint": "entrevistado",
        "source_label": "known-direct",
        "notes": "Entrevista encontrada por busca nominal.",
    },
    {
        "url": "https://reutersinstitute.politics.ox.ac.uk/people/luiz-fernando-toledo-antunes",
        "title": "Luiz Fernando Toledo Antunes | Reuters Institute",
        "category_hint": "perfil_bio",
        "relation_hint": "perfil",
        "source_label": "known-direct",
        "notes": "Perfil institucional encontrado por busca nominal.",
    },
    {
        "url": "https://brown.columbia.edu/portfolio/luiz-fernando-toledo/",
        "title": "Data Journalism (Columbia)",
        "category_hint": "perfil_bio",
        "relation_hint": "perfil",
        "source_label": "known-direct",
        "notes": "Perfil institucional encontrado por busca nominal.",
    },
    {
        "url": "https://repositorio.fgv.br/items/e0088610-e3e2-4c40-afad-4461cf6e2df6",
        "title": "Public Administration (FGV-EAESP)",
        "category_hint": "perfil_bio",
        "relation_hint": "perfil",
        "source_label": "known-direct",
        "notes": "Perfil institucional encontrado por busca nominal.",
    },
    {
        "url": "https://talks.ox.ac.uk/talks/id/cca01872-4114-45f5-a90f-64d5af4cea88/",
        "title": "POLIS-Cambridge",
        "category_hint": "perfil_bio",
        "relation_hint": "perfil",
        "source_label": "known-direct",
        "notes": "Perfil institucional encontrado por busca nominal.",
    },
    {
        "url": "https://www.cima.ned.org/blog/how-artificial-intelligence-can-facilitate-investigative-journalism/",
        "title": "Center for International Media Assistance",
        "category_hint": "perfil_bio",
        "relation_hint": "perfil",
        "source_label": "known-direct",
        "notes": "Perfil institucional encontrado por busca nominal.",
    },
    {
        "url": "https://www.scielo.br/j/cebape/a/JbgP4kK8GsbkPK8gL7c6MqK/abstract?lang=en",
        "title": "My research was used by the government",
        "category_hint": "perfil_bio",
        "relation_hint": "perfil",
        "source_label": "known-direct",
        "notes": "Mencao academica encontrada por busca nominal.",
    },
]
SKIP_QUERY_TOKENS = {
    "facebook",
    "instagram",
    "linkedin",
}
NOISE_PATH_TOKENS = (
    "/termos-de-uso",
    "/politica-de-privacidade",
    "/privacy-policy",
    "/politica-de-cookies",
    "/cookie-policy",
    "/cookies",
    "/newsletter",
    "/feed",
    "/tag/",
    "/category/",
    "/categoria/",
)
NOISE_TITLE_TOKENS = (
    "termos de uso",
    "política de privacidade",
    "politica de privacidade",
    "privacy policy",
    "cookie policy",
    "política de cookies",
    "politica de cookies",
)
FORCE_REFRESH_ARTICLE_DOMAINS = {
    "apublica.org",
    "bbc.com",
    "intercept.com.br",
    "ojoioeotrigo.com.br",
    "piaui.uol.com.br",
    "theintercept.com",
}
TITLE_SUFFIX_HINTS = {
    "agencia publica",
    "agência pública",
    "bbc",
    "bbc news brasil",
    "intercept brasil",
    "o joio e o trigo",
    "revista piaui",
    "revista piauí",
}
DEFAULT_TIMEOUT = 30


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def fold_text(value: str) -> str:
    import unicodedata

    return (
        unicodedata.normalize("NFD", (value or ""))
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", fold_text(value)).strip()


EXACT_NAME_QUERY_TERMS = {
    normalize_text("Luiz Fernando Toledo"),
    normalize_text("Luiz Fernando Toledo Antunes"),
}


def make_id(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def unwrap_url(raw_url: str) -> str:
    if not raw_url:
        return ""
    url = raw_url.strip()
    if url.startswith("//"):
        url = "https:" + url
    parsed = urllib.parse.urlparse(url)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        target = dict(urllib.parse.parse_qsl(parsed.query)).get("uddg")
        return urllib.parse.unquote(target) if target else url
    if "/embed/" in parsed.path and "youtube.com" in parsed.netloc:
        video_id = parsed.path.rstrip("/").split("/")[-1]
        return f"https://www.youtube.com/watch?v={video_id}"
    if parsed.netloc == "youtu.be":
        video_id = parsed.path.strip("/")
        return f"https://www.youtube.com/watch?v={video_id}" if video_id else url
    if "/embed/episode/" in parsed.path and "spotify.com" in parsed.netloc:
        return url.replace("/embed/episode/", "/episode/")
    return url


def canonicalize_url(raw_url: str) -> str:
    url = unwrap_url(raw_url)
    if not url:
        return ""
    parsed = urllib.parse.urlparse(url)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    query_pairs = []
    for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower().startswith("utm_"):
            continue
        if key.lower() in {"oc", "gaa_at", "ga_src", "ga_hp"}:
            continue
        if "youtube.com" in netloc and key.lower() not in {"v", "t", "start"}:
            continue
        query_pairs.append((key, value))
    query = urllib.parse.urlencode(query_pairs, doseq=True)
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urllib.parse.urlunparse((scheme, netloc, path, "", query, ""))


def domain_of(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


def parse_datetime(value: Any) -> Optional[str]:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    raw = raw.replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc).isoformat()
    except Exception:
        pass

    date_match = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", raw)
    if date_match:
        try:
            parsed = dt.datetime(
                int(date_match.group(1)),
                int(date_match.group(2)),
                int(date_match.group(3)),
                tzinfo=dt.timezone.utc,
            )
            return parsed.isoformat()
        except Exception:
            return None
    return None


def parse_rfc822_datetime(value: Any) -> Optional[str]:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        parsed = dt.datetime.strptime(raw, "%a, %d %b %Y %H:%M:%S %Z")
        return parsed.replace(tzinfo=dt.timezone.utc).isoformat()
    except Exception:
        return parse_datetime(raw)


def iso_to_date(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    parsed = parse_datetime(value)
    if not parsed:
        return None
    return parsed[:10]


def recent_days_from(date_str: Optional[str], now: dt.datetime) -> Optional[int]:
    if not date_str:
        return None
    try:
        target = dt.datetime.fromisoformat(parse_datetime(date_str) or "")
    except Exception:
        return None
    if target > now:
        return None
    return (now - target).days


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def text_has_name(value: str) -> List[str]:
    hay = normalize_text(value)
    matched = []
    for name in NAME_VARIANTS:
        if normalize_text(name) in hay:
            matched.append(name)
    return matched


def text_has_identity_token(value: str) -> List[str]:
    hay = normalize_text(value)
    matched = []
    for token in IDENTITY_TOKENS:
        if normalize_text(token) in hay:
            matched.append(token)
    return matched


def dedupe_keep_order(values: Iterable[str]) -> List[str]:
    seen = set()
    out = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def is_placeholder_title(value: str) -> bool:
    title = normalize_text(value)
    if not title:
        return True
    if title.startswith("http://") or title.startswith("https://"):
        return True
    if title in {
        "read full story",
        "view workshop on udemy",
        "brazil",
        "uk",
        "usa",
        "sweden",
        "malaysia",
        "portugal",
        "ned",
        "oxford",
    }:
        return True
    return (
        title.startswith("read full story")
        or title.startswith("view workshop")
        or title.startswith("ver mais ")
        or any(token in title for token in NOISE_TITLE_TOKENS)
    )


def looks_like_noise_page(url: str, title: str = "") -> bool:
    folded_url = normalize_text(url)
    folded_title = normalize_text(title)
    if any(token in folded_url for token in NOISE_PATH_TOKENS):
        return True
    if folded_title and any(token in folded_title for token in NOISE_TITLE_TOKENS):
        return True
    return False


def is_profile_domain(domain: str) -> bool:
    clean_domain = domain_of(domain) if "://" in domain else domain.lower().removeprefix("www.")
    return clean_domain in PROFILE_DOMAINS or any(clean_domain.endswith("." + base) for base in PROFILE_DOMAINS)


def strip_title_suffix(title: str, site_name: str = "") -> str:
    cleaned = clean_text(title)
    site_norm = normalize_text(site_name)
    for separator in (" | ", " - ", " — ", " – "):
        if separator not in cleaned:
            continue
        left, right = cleaned.rsplit(separator, 1)
        left = clean_text(left)
        right_norm = normalize_text(right)
        if not left:
            continue
        if site_norm and (right_norm == site_norm or right_norm in site_norm or site_norm in right_norm):
            return left
        if right_norm in TITLE_SUFFIX_HINTS:
            return left
    return cleaned


def select_preferred_title(url: str, soup: BeautifulSoup, current_title: str, site_name: str = "") -> str:
    path = urllib.parse.urlparse(url).path or "/"
    candidates: List[Tuple[int, str]] = []
    h1 = clean_text(soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else "")
    if h1 and not is_placeholder_title(h1) and not looks_like_noise_page(url, h1):
        score = 5 if ("/articles/" in path or re.search(r"/20\d\d/", path)) else 4
        candidates.append((score, strip_title_suffix(h1, site_name)))

    for selector, score in (
        ('meta[property="og:title"]', 3),
        ('meta[name="twitter:title"]', 2),
    ):
        tag = soup.select_one(selector)
        value = clean_text(tag.get("content") or "") if tag else ""
        if value and not is_placeholder_title(value) and not looks_like_noise_page(url, value):
            candidates.append((score, strip_title_suffix(value, site_name)))

    if current_title and not is_placeholder_title(current_title) and not looks_like_noise_page(url, current_title):
        candidates.append((1, strip_title_suffix(current_title, site_name)))

    if not candidates:
        return current_title

    candidates.sort(key=lambda item: (item[0], len(item[1])), reverse=True)
    return candidates[0][1]


def query_is_exact_name(query: str) -> bool:
    return normalize_text((query or "").replace('"', "")) in EXACT_NAME_QUERY_TERMS


def should_force_refresh(existing: Dict[str, Any]) -> bool:
    url = existing.get("url") or existing.get("canonical_url") or ""
    path = urllib.parse.urlparse(url).path or "/"
    domain = domain_of(url)
    title = existing.get("title") or ""
    if domain not in FORCE_REFRESH_ARTICLE_DOMAINS:
        return False
    if is_placeholder_title(title) or looks_like_noise_page(url, title):
        return True
    if domain == "bbc.com" and "/portuguese/articles/" in path:
        return True
    if re.search(r"/20\d\d/", path):
        return True
    return False


def has_textual_authorship(item: Dict[str, Any]) -> bool:
    hay = normalize_text(
        " ".join(
            [
                item.get("title", ""),
                item.get("description", ""),
                item.get("text_excerpt", ""),
            ]
        )
    )
    patterns = [
        "por luiz fernando toledo",
        "author luiz fernando toledo",
        "author, luiz fernando toledo",
        # BBC-style: "Author, Name1, Luiz Fernando Toledo"
        "author,",  # will be checked with name below
        # Video reporter patterns
        "reporter luiz fernando toledo",
        "reportera luiz fernando toledo",
        # Credit patterns
        "reportagem: luiz fernando toledo",
        "reportagem de luiz fernando toledo",
        "texto: luiz fernando toledo",
        "texto de luiz fernando toledo",
        "apuracao: luiz fernando toledo",
        "apuracao de luiz fernando toledo",
    ]
    # Direct pattern match
    for pattern in patterns:
        if pattern == "author,":
            # Special case: BBC format "Author, Name1[, Name2], Luiz Fernando Toledo"
            if "author," in hay and any(normalize_text(name) in hay for name in NAME_VARIANTS):
                # Check if the author section contains LFT's name
                author_match = re.search(r"author,\s*(.{0,200}?)(?:role,|$)", hay)
                if author_match and any(normalize_text(name) in author_match.group(1) for name in NAME_VARIANTS):
                    return True
        elif pattern in hay:
            return True

    # Also check: if the domain is a known author page domain AND the URL was crawled
    # from an author page, treat as authorship
    domain = domain_of(item.get("url", ""))
    source_labels = " ".join(item.get("source_labels") or [])
    if domain in AUTHOR_PAGE_DOMAINS and "crawl" in source_labels:
        # Only if name appears in the page content
        if any(normalize_text(name) in hay for name in NAME_VARIANTS):
            return True

    # Regex-based patterns: "o repórter [da BBC News Brasil] Luiz Fernando Toledo"
    for name in NAME_VARIANTS:
        name_norm = normalize_text(name)
        if name_norm in hay:
            # "reporter/repórter ... LFT" within 80 chars
            if re.search(r"(?:reporter|reportera?)\b.{0,80}" + re.escape(name_norm), hay):
                return True
            # "neste video, o reporter LFT"
            if re.search(r"neste video.{0,40}" + re.escape(name_norm), hay):
                return True

    return False


class Fetcher:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
            }
        )

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        response = self.session.get(url, timeout=kwargs.pop("timeout", DEFAULT_TIMEOUT), **kwargs)
        response.raise_for_status()
        return response

    def close(self) -> None:
        self.session.close()


FETCHER = Fetcher()


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_json_ld_node(node: Any, out: Dict[str, Any]) -> None:
    if isinstance(node, list):
        for item in node:
            parse_json_ld_node(item, out)
        return
    if not isinstance(node, dict):
        return

    for key in ("datePublished", "dateCreated", "dateModified", "uploadDate"):
        if key in node and not out.get("published_at"):
            out["published_at"] = parse_datetime(node.get(key))

    if "author" in node:
        authors = out.setdefault("author_names", [])
        author_value = node.get("author")
        author_candidates = author_value if isinstance(author_value, list) else [author_value]
        for author in author_candidates:
            if isinstance(author, dict):
                name = clean_text(author.get("name") or author.get("alternateName") or "")
                if name:
                    authors.append(name)
            elif isinstance(author, str):
                name = clean_text(author)
                if name:
                    authors.append(name)

    if "publisher" in node and isinstance(node.get("publisher"), dict) and not out.get("site_name"):
        site_name = clean_text(node["publisher"].get("name") or "")
        if site_name:
            out["site_name"] = site_name

    if not out.get("title") and clean_text(node.get("headline") or ""):
        out["title"] = clean_text(node.get("headline") or "")
    if not out.get("description") and clean_text(node.get("description") or ""):
        out["description"] = clean_text(node.get("description") or "")

    for value in node.values():
        parse_json_ld_node(value, out)


def build_excerpt(text: str, fallback_title: str = "") -> str:
    if not text:
        return ""
    plain = clean_text(text)
    if not plain:
        return ""
    lower = normalize_text(plain)
    indices = []
    for name in NAME_VARIANTS:
        hit = lower.find(normalize_text(name))
        if hit >= 0:
            indices.append(hit)
    if indices:
        start = max(0, min(indices) - 180)
        end = min(len(plain), min(indices) + 240)
        excerpt = plain[start:end]
        if start > 0:
            excerpt = "... " + excerpt
        if end < len(plain):
            excerpt += " ..."
        return excerpt
    if fallback_title and normalize_text(fallback_title) in lower:
        return plain[:420]
    return plain[:420]


def extract_html_metadata(url: str, html_text: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html_text, "html.parser")
    for element in soup(["script", "style", "noscript", "svg"]):
        element.extract()

    metadata: Dict[str, Any] = {
        "title": "",
        "description": "",
        "site_name": "",
        "canonical_url": "",
        "author_names": [],
        "published_at": None,
        "text_excerpt": "",
    }

    if soup.title:
        metadata["title"] = clean_text(soup.title.get_text(" ", strip=True))

    meta_candidates = {
        "description": [
            ('meta', {'name': 'description'}, 'content'),
            ('meta', {'property': 'og:description'}, 'content'),
            ('meta', {'name': 'twitter:description'}, 'content'),
        ],
        "site_name": [
            ('meta', {'property': 'og:site_name'}, 'content'),
            ('meta', {'name': 'application-name'}, 'content'),
        ],
        "canonical_url": [
            ('link', {'rel': 'canonical'}, 'href'),
            ('meta', {'property': 'og:url'}, 'content'),
        ],
        "published_at": [
            ('meta', {'property': 'article:published_time'}, 'content'),
            ('meta', {'name': 'parsely-pub-date'}, 'content'),
            ('meta', {'itemprop': 'datePublished'}, 'content'),
            ('time', {'datetime': True}, 'datetime'),
        ],
    }
    for key, selectors in meta_candidates.items():
        for tag_name, attrs, attr_name in selectors:
            tag = soup.find(tag_name, attrs=attrs)
            if not tag:
                continue
            value = tag.get(attr_name)
            if not value:
                continue
            if key == "published_at":
                parsed = parse_datetime(value)
                if parsed:
                    metadata[key] = parsed
                    break
            else:
                metadata[key] = clean_text(value)
                break

    author_values = []
    for attrs in (
        {"name": "author"},
        {"property": "article:author"},
        {"itemprop": "author"},
    ):
        for tag in soup.find_all("meta", attrs=attrs):
            content = clean_text(tag.get("content") or "")
            if content:
                author_values.append(content)
    metadata["author_names"] = dedupe_keep_order(author_values)

    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text(" ", strip=True)
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        parse_json_ld_node(payload, metadata)

    metadata["title"] = select_preferred_title(
        url,
        soup,
        metadata.get("title") or "",
        metadata.get("site_name") or "",
    )

    page_text = clean_text(soup.get_text(" ", strip=True))
    if "bbc.com" in domain_of(url) and not metadata["author_names"]:
        bbc_match = re.search(r"Author,\s*([^,]+?)\s+Role,", page_text)
        if bbc_match:
            metadata["author_names"] = [clean_text(bbc_match.group(1))]
    if not metadata.get("published_at") and "bbc.com" in domain_of(url):
        bbc_date = re.search(r"(\d{1,2})\s+([a-zA-ZçÇ]+)\s+(\d{4})", page_text)
        if bbc_date:
            months = {
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
            month = months.get(fold_text(bbc_date.group(2)))
            if month:
                metadata["published_at"] = dt.datetime(
                    int(bbc_date.group(3)), month, int(bbc_date.group(1)), tzinfo=dt.timezone.utc
                ).isoformat()

    metadata["text_excerpt"] = build_excerpt(page_text, metadata.get("title") or "")
    return metadata


def extract_jina_metadata(url: str, markdown_text: str) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {
        "title": "",
        "description": "",
        "site_name": "",
        "canonical_url": canonicalize_url(url),
        "author_names": [],
        "published_at": None,
        "text_excerpt": "",
    }
    lines = markdown_text.splitlines()
    content_start = 0
    for idx, line in enumerate(lines[:40]):
        if line.startswith("Title: "):
            metadata["title"] = clean_text(line.replace("Title: ", "", 1))
        elif line.startswith("URL Source: "):
            source_url = clean_text(line.replace("URL Source: ", "", 1))
            if source_url:
                metadata["canonical_url"] = canonicalize_url(source_url)
        elif line.startswith("Published Time: "):
            metadata["published_at"] = parse_datetime(line.replace("Published Time: ", "", 1))
        elif line.startswith("Markdown Content:"):
            content_start = idx + 1
            break
    body = clean_text("\n".join(lines[content_start:])) if content_start else clean_text(markdown_text)
    metadata["text_excerpt"] = build_excerpt(body, metadata.get("title") or "")
    metadata["description"] = metadata["text_excerpt"][:220]
    metadata["site_name"] = domain_of(url)
    return metadata


def fetch_via_jina(url: str) -> Dict[str, Any]:
    response = FETCHER.get(f"{JINA_PREFIX}{url}", allow_redirects=True, timeout=DEFAULT_TIMEOUT)
    metadata = extract_jina_metadata(url, response.text)
    return {
        "url": url,
        "canonical_url": metadata.get("canonical_url") or canonicalize_url(url),
        "status_code": response.status_code,
        "fetch_ok": True,
        "content_type": response.headers.get("Content-Type", "text/plain"),
        "title": metadata.get("title") or "",
        "description": metadata.get("description") or "",
        "site_name": metadata.get("site_name") or "",
        "author_names": metadata.get("author_names") or [],
        "published_at": metadata.get("published_at"),
        "text_excerpt": metadata.get("text_excerpt") or "",
        "resolved_url": url,
        "error": "",
        "fetched_via": "jina",
    }


def fetch_page(url: str) -> Dict[str, Any]:
    item: Dict[str, Any] = {
        "url": url,
        "canonical_url": canonicalize_url(url),
        "status_code": None,
        "fetch_ok": False,
        "content_type": "",
        "title": "",
        "description": "",
        "site_name": "",
        "author_names": [],
        "published_at": None,
        "text_excerpt": "",
        "resolved_url": url,
        "error": "",
    }
    try:
        response = FETCHER.get(url, allow_redirects=True)
        item["status_code"] = response.status_code
        item["content_type"] = response.headers.get("Content-Type", "")
        item["resolved_url"] = response.url
        item["canonical_url"] = canonicalize_url(response.url)
        item["fetch_ok"] = True
        if "text/html" in item["content_type"] or not item["content_type"]:
            metadata = extract_html_metadata(response.url, response.text)
            item.update(metadata)
        else:
            item["title"] = url
        item["fetched_via"] = "direct"
    except Exception as exc:
        item["error"] = str(exc)
        try:
            fallback = fetch_via_jina(url)
            item.update(fallback)
            item["error"] = ""
        except Exception as fallback_exc:
            item["error"] = f"{exc} | jina: {fallback_exc}"
    return item

def dedupe_seed_candidates(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        key = canonicalize_url(row.get("url") or "")
        if not key:
            continue
        if key not in merged:
            merged[key] = dict(row)
            merged[key]["url"] = key
            continue
        current = merged[key]
        for field in ("title", "notes", "category_hint", "relation_hint"):
            if not current.get(field) and row.get(field):
                current[field] = row.get(field)
        current["source_label"] = ", ".join(dedupe_keep_order([current.get("source_label", ""), row.get("source_label", "")]))
    return list(merged.values())


def seed_row(
    url: str,
    title: str,
    category_hint: str,
    relation_hint: str,
    source_label: str,
    notes: str = "",
    published_at: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "url": canonicalize_url(url),
        "title": clean_text(title),
        "category_hint": category_hint,
        "relation_hint": relation_hint,
        "source_label": source_label,
        "notes": notes,
        "published_at": published_at,
    }


def should_keep_source_link(base_url: str, href: str, text: str, crawl_mode: str) -> bool:
    parsed = urllib.parse.urlparse(href)
    path = parsed.path or "/"
    if not href.startswith("http"):
        return False
    if domain_of(href) != domain_of(base_url):
        return False
    if canonicalize_url(href) == canonicalize_url(base_url):
        return False
    if looks_like_noise_page(href, text):
        return False
    if crawl_mode == "bbc_articles":
        return "/articles/" in path
    if crawl_mode == "year_paths":
        return bool(re.search(r"/20\d\d/", path))
    if crawl_mode == "piaui_articles":
        if "/colaborador/" in path or "/page/" in path:
            return False
        if path.count("/") < 2:
            return False
        if len(clean_text(text)) < 12:
            return False
        return True
    return False


def discover_known_source_pages() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for page in KNOWN_SOURCE_PAGES:
        page_url = canonicalize_url(page.get("url") or "")
        if not page_url:
            continue
        items.append(
            seed_row(
                page_url,
                page.get("title") or page_url,
                page.get("category_hint") or "perfil_bio",
                page.get("relation_hint") or "perfil",
                page.get("source_label") or "known-source",
                page.get("notes") or "",
            )
        )
        crawl_mode = page.get("crawl_mode") or "none"
        if crawl_mode == "none":
            continue
        try:
            response = FETCHER.get(page_url, allow_redirects=True)
        except Exception:
            continue
        soup = BeautifulSoup(response.text, "html.parser")
        seen_links = set()
        for link in soup.select('a[href]'):
            href = link.get('href') or ''
            full_url = href if href.startswith('http') else urllib.parse.urljoin(response.url, href)
            text = clean_text(link.get_text(" ", strip=True))
            if not should_keep_source_link(page_url, full_url, text, crawl_mode):
                continue
            clean_url = canonicalize_url(full_url)
            if not clean_url or clean_url in seen_links:
                continue
            seen_links.add(clean_url)
            items.append(
                seed_row(
                    clean_url,
                    text or clean_url,
                    "",
                    "",
                    f"{page.get('source_label')}:crawl",
                    f"Link descoberto a partir de {page_url}; autoria precisa ser confirmada na pagina final.",
                )
            )
    for row in KNOWN_DIRECT_ITEMS:
        items.append(
            seed_row(
                row["url"],
                row["title"],
                row["category_hint"],
                row["relation_hint"],
                row["source_label"],
                row.get("notes", ""),
            )
        )
    return dedupe_seed_candidates(items)


def ddg_lite_search(query: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    try:
        response = requests.get(
            "https://lite.duckduckgo.com/lite/",
            params={"q": query},
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
                "Accept-Encoding": "identity",
            },
            timeout=DEFAULT_TIMEOUT,
        )
        response.raise_for_status()
    except Exception:
        return items

    soup = BeautifulSoup(response.text, "html.parser")
    links = soup.select("a.result-link")
    snippets = soup.select("td.result-snippet")
    if not links:
        return items

    for index, link in enumerate(links):
        url = canonicalize_url(link.get("href") or "")
        title = clean_text(link.get_text(" ", strip=True))
        snippet = clean_text(snippets[index].get_text(" ", strip=True)) if index < len(snippets) else ""
        if not url or not title:
            continue
        items.append(
            {
                "url": url,
                "title": title,
                "description": snippet,
                "source_label": f"duckduckgo-lite:{query}",
                "query": query,
            }
        )
    return items


def get_google_news_base64(source_url: str) -> Optional[str]:
    parsed = urllib.parse.urlparse(source_url)
    parts = [part for part in parsed.path.split("/") if part]
    if parsed.netloc != "news.google.com":
        return None
    if len(parts) < 2:
        return None
    if parts[0] == "rss" and len(parts) >= 3 and parts[1] == "articles":
        return parts[2]
    if parts[-2] in {"articles", "read"}:
        return parts[-1]
    return None


def decode_google_news_url(source_url: str) -> str:
    base64_str = get_google_news_base64(source_url)
    if not base64_str:
        return canonicalize_url(source_url)

    article_url = f"https://news.google.com/rss/articles/{base64_str}"
    response = FETCHER.get(article_url, allow_redirects=True, timeout=DEFAULT_TIMEOUT)
    soup = BeautifulSoup(response.text, "html.parser")
    data_element = soup.select_one("c-wiz > div[jscontroller]") or soup.select_one("[data-n-a-sg][data-n-a-ts]")
    if not data_element:
        return canonicalize_url(source_url)

    signature = data_element.get("data-n-a-sg")
    timestamp = data_element.get("data-n-a-ts")
    if not signature or not timestamp:
        return canonicalize_url(source_url)

    payload = [
        "Fbv4je",
        (
            f'["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
            f'"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{base64_str}",{timestamp},"{signature}"]'
        ),
    ]
    response = requests.post(
        "https://news.google.com/_/DotsSplashUi/data/batchexecute?rpcids=Fbv4je",
        headers={
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "User-Agent": USER_AGENT,
        },
        data=f"f.req={urllib.parse.quote(json.dumps([[payload]]))}",
        timeout=DEFAULT_TIMEOUT,
    )
    response.raise_for_status()
    chunks = response.text.split("\n\n")
    if len(chunks) < 2:
        return canonicalize_url(source_url)
    parsed = json.loads(chunks[1])[:-2]
    if not parsed or len(parsed[0]) < 3:
        return canonicalize_url(source_url)
    decoded = json.loads(parsed[0][2])[1]
    return canonicalize_url(decoded) or canonicalize_url(source_url)


def google_news_search(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    rss_url = (
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote(query)
        + "&hl=pt-BR&gl=BR&ceid=BR:pt-419"
    )
    try:
        response = FETCHER.get(rss_url)
        root = ET.fromstring(response.text)
    except Exception:
        return items

    for node in root.findall('./channel/item')[:limit]:
        title = clean_text(node.findtext('title') or '')
        link = clean_text(node.findtext('link') or '')
        description = clean_text(node.findtext('description') or '')
        pub_date = clean_text(node.findtext('pubDate') or '')
        if not link:
            continue
        resolved = canonicalize_url(link)
        try:
            resolved = decode_google_news_url(link)
        except Exception:
            try:
                head = FETCHER.get(link, allow_redirects=True, timeout=20)
                resolved = canonicalize_url(head.url)
            except Exception:
                pass
        items.append(
            {
                "url": resolved,
                "title": title,
                "description": description,
                "published_at": parse_datetime(pub_date),
                "source_label": f'google-news-rss:{query}',
                "query": query,
            }
        )
        time.sleep(0.2)
    return items


def merge_candidate(base: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    if incoming.get("title") and (not merged.get("title") or is_placeholder_title(merged.get("title") or "")):
        merged["title"] = incoming["title"]
    if incoming.get("description") and (not merged.get("description") or len(merged.get("description") or "") < 40):
        merged["description"] = incoming["description"]
    for key in ("category_hint", "relation_hint", "notes", "published_at"):
        if incoming.get(key) and not merged.get(key):
            merged[key] = incoming[key]
    merged["source_labels"] = dedupe_keep_order((merged.get("source_labels") or []) + [incoming.get("source_label", "")])
    if incoming.get("query"):
        merged["queries"] = dedupe_keep_order((merged.get("queries") or []) + [incoming.get("query")])
    return merged


def build_candidates(max_ddg_pages: int, max_google_items: int) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    diagnostics: Dict[str, Any] = {
        "seeds": {},
        "searches": {},
    }
    merged: Dict[str, Dict[str, Any]] = {}

    def add(row: Dict[str, Any]) -> None:
        key = canonicalize_url(row.get("url") or "")
        if not key:
            return
        row = dict(row)
        row["url"] = key
        if key not in merged:
            merged[key] = {
                "url": key,
                "title": row.get("title", ""),
                "description": row.get("description", ""),
                "category_hint": row.get("category_hint", ""),
                "relation_hint": row.get("relation_hint", ""),
                "notes": row.get("notes", ""),
                "published_at": row.get("published_at"),
                "source_labels": [],
                "queries": [],
            }
        merged[key] = merge_candidate(merged[key], row)

    known_sources = discover_known_source_pages()
    diagnostics["seeds"]["known_external_sources"] = len(known_sources)
    for row in known_sources:
        add(row)

    if max_ddg_pages > 0:
        for query in DUCKDUCKGO_LITE_QUERIES[: max(1, max_ddg_pages)]:
            results = ddg_lite_search(query)
            diagnostics["searches"][f"duckduckgo-lite:{query}"] = len(results)
            for row in results:
                add(row)

    if max_google_items > 0:
        for query in GOOGLE_NEWS_QUERIES:
            results = google_news_search(query, limit=max_google_items)
            diagnostics["searches"][f"google-news-rss:{query}"] = len(results)
            for row in results:
                add(row)

    return list(merged.values()), diagnostics


def match_identity_score(item: Dict[str, Any]) -> Tuple[int, List[str], List[str]]:
    reasons: List[str] = []
    textual_authorship = has_textual_authorship(item)
    matched_names = dedupe_keep_order(
        text_has_name(
            " ".join(
                [
                    item.get("title", ""),
                    item.get("description", ""),
                    item.get("text_excerpt", ""),
                    " ".join(item.get("author_names") or []),
                    item.get("notes", ""),
                ]
            )
        )
    )
    matched_tokens = dedupe_keep_order(
        text_has_identity_token(
            " ".join(
                [
                    item.get("title", ""),
                    item.get("description", ""),
                    item.get("text_excerpt", ""),
                    item.get("site_name", ""),
                    " ".join(item.get("author_names") or []),
                    " ".join(item.get("queries") or []),
                    item.get("notes", ""),
                    item.get("relation_hint", ""),
                ]
            )
        )
    )

    score = 0
    if item.get("category_hint"):
        score += 2
        reasons.append("seed")
    if matched_names:
        score += 3
        reasons.append("nome_exato")
    if matched_tokens:
        score += 2
        reasons.append("contexto_identidade")
    if any(normalize_text(name) in normalize_text(" ".join(item.get("author_names") or [])) for name in NAME_VARIANTS):
        score += 4
        reasons.append("byline")
    if textual_authorship:
        score += 4
        reasons.append("assinatura_textual")
    if is_profile_domain(item.get("url", "")):
        score += 1
        reasons.append("dominio_perfil")
    return score, matched_names, matched_tokens


def classify_item(item: Dict[str, Any], all_autoral_titles: Optional[set] = None) -> Optional[Dict[str, Any]]:
    """Classify an item into one of the content categories.

    Categories (in priority order):
    - reportagem_autoral: articles authored by LFT (byline match or known author domain)
    - republicacao: syndications/republications (same title as an autoral piece, different domain)
    - eco_repercussao: repercussions/interpretations of LFT's reporting by third parties
    - citacao_fonte: articles where LFT is cited as a source/expert (name in body + quoting verbs)
    - entrevista: interviews/podcasts featuring LFT
    - palestra_curso: talks/workshops/courses
    - perfil_bio: profile pages / institutional pages
    - mencao: other mentions of the name
    """
    score, matched_names, matched_tokens = match_identity_score(item)
    title_only = normalize_text(item.get("title", ""))
    title_desc = normalize_text(" ".join([item.get("title", ""), item.get("description", ""), item.get("notes", "")]))
    body = normalize_text(item.get("text_excerpt", ""))
    full_text = title_desc + " " + body
    domain = domain_of(item.get("url", ""))
    category_hint = item.get("category_hint") or ""
    byline_match = any(normalize_text(name) in normalize_text(" ".join(item.get("author_names") or [])) for name in NAME_VARIANTS)
    textual_authorship = has_textual_authorship(item)
    is_author_domain = domain in AUTHOR_PAGE_DOMAINS or any(domain.endswith("." + d) for d in AUTHOR_PAGE_DOMAINS)

    if all_autoral_titles is None:
        all_autoral_titles = set()

    if domain == "news.google.com":
        return None
    if domain == "luizftoledo.github.io":
        return None
    if looks_like_noise_page(item.get("url", ""), item.get("title", "")):
        return None
    if domain in {"linkedin.com", "facebook.com", "instagram.com"} and score < 4:
        return None
    if score < 2:
        return None

    category = category_hint or ""
    relation = item.get("relation_hint") or ""

    # Migrate old category names
    if category == "eco_republicacao":
        category = "eco_repercussao"

    # A URL ter saído de uma página de autor é apenas pista de descoberta, não prova.
    if category == "reportagem_autoral" and not (byline_match or textual_authorship):
        category = ""
        relation = ""

    # --- Keyword tuples for classification ---
    event_keywords = (
        "palestra", "palestrante", "speaker", "workshop", "conference",
        "congresso", "seminar", "seminario", "webinar", "training", "udemy",
    )
    interview_keywords = (
        "entrevista", "interview", "podcast", "episode",
        "stage talks", "youtube", "spotify",
    )
    profile_keywords = (
        "speaker profile", "resume", "curriculo", "portfolio",
        "people", "orcid", "staff profile",
    )

    # Keywords indicating the article is a repercussion/interpretation of LFT's reporting
    echo_keywords = (
        "apos reportagem",
        "apos investigacao",
        "apos apuracao",
        "apos levantamento",
        "apos denuncia",
        "investigacao da bbc",
        "reportagem da bbc",
        "segundo reportagem",
        "segundo levantamento",
        "segundo apuracao",
        "segundo investigacao",
        "conforme reportagem",
        "conforme apuracao",
        "conforme levantamento",
        "como mostrou reportagem",
        "como revelou reportagem",
        "como revelou investigacao",
        "como revelou apuracao",
        "como mostrou a bbc",
        "como revelou a bbc",
        "como mostrou o intercept",
        "como revelou o intercept",
        "levantamento da datafixers",
        "levantamento da fiquem sabendo",
        "reportagem de luiz fernando toledo",
        "investigacao de luiz fernando toledo",
        "apuracao de luiz fernando toledo",
        "reportagem publicada pelo",
        "reportagem publicada pela",
    )

    # Keywords indicating LFT is cited as a source/expert (NOT author)
    citation_keywords = (
        "segundo luiz fernando toledo",
        "de acordo com luiz fernando toledo",
        "afirma luiz fernando toledo",
        "explica luiz fernando toledo",
        "disse luiz fernando toledo",
        "diz luiz fernando toledo",
        "aponta luiz fernando toledo",
        "destaca luiz fernando toledo",
        "avalia luiz fernando toledo",
        "analisa luiz fernando toledo",
        "comenta luiz fernando toledo",
        "observa luiz fernando toledo",
        "ressalta luiz fernando toledo",
        "segundo o jornalista luiz",
        "segundo o pesquisador luiz",
        "segundo o reporter luiz",
        "o jornalista luiz fernando toledo",
        "o reporter luiz fernando toledo",
        "o pesquisador luiz fernando toledo",
        "o diretor luiz fernando toledo",
        "o cofundador luiz fernando toledo",
        "o professor luiz fernando toledo",
        "jornalista luiz fernando toledo",
        "according to luiz fernando toledo",
        "says luiz fernando toledo",
        "said luiz fernando toledo",
        "told luiz fernando toledo",
        "luiz fernando toledo, who",
        "luiz fernando toledo, diretor",
        "luiz fernando toledo, cofundador",
        "luiz fernando toledo, jornalista",
        "luiz fernando toledo, pesquisador",
        "luiz fernando toledo, reporter",
    )

    if not category:
        # 1. REPORTAGEM AUTORAL: byline match or textual authorship
        if byline_match or textual_authorship:
            category = "reportagem_autoral"
            relation = relation or "autor"

        # 2. EVENT / TALK
        elif any(keyword in title_only for keyword in event_keywords):
            category = "palestra_curso"
            relation = relation or "palestrante"

        # 3. INTERVIEW / PODCAST
        elif any(keyword in title_only for keyword in interview_keywords):
            category = "entrevista"
            relation = relation or "entrevistado"

        # 4. PROFILE / BIO PAGE
        elif is_profile_domain(domain) or "/staff/" in item.get("url", "") or any(keyword in title_only for keyword in profile_keywords):
            category = "perfil_bio"
            relation = relation or "perfil"

        # 5. CITACAO COMO FONTE: Name in body with quoting verbs but NOT author
        elif matched_names and any(keyword in full_text for keyword in citation_keywords):
            category = "citacao_fonte"
            relation = relation or "citado como fonte"

        # 6. ECO / REPERCUSSAO: references to LFT's reporting
        elif any(keyword in full_text for keyword in echo_keywords):
            category = "eco_repercussao"
            relation = relation or "repercussao de apuracao"

        # 7. REPUBLICACAO: same title as an autoral piece on a different domain
        elif all_autoral_titles and title_only and title_only in all_autoral_titles:
            category = "republicacao"
            relation = relation or "republicacao / syndication"

        # 8. MENCAO: exact name match from a search query
        elif matched_names and any(query_is_exact_name(query) for query in (item.get("queries") or [])):
            category = "mencao"
            relation = relation or "mencionado"

        # 9. MENCAO: name match + identity context tokens
        elif matched_names and matched_tokens:
            category = "mencao"
            relation = relation or "mencionado"

        else:
            return None

    # --- Default relations for each category ---
    default_relations = {
        "reportagem_autoral": "autor",
        "republicacao": "republicacao / syndication",
        "eco_repercussao": "repercussao de apuracao",
        "citacao_fonte": "citado como fonte",
        "entrevista": "entrevistado",
        "palestra_curso": "palestrante",
        "perfil_bio": "perfil",
        "mencao": "mencionado",
    }
    if not relation:
        relation = default_relations.get(category, "mencionado")

    item["category"] = category
    item["category_label"] = CATEGORY_LABELS.get(category, category)
    item["relation"] = relation
    item["match_score"] = score
    item["matched_name_variants"] = matched_names
    item["matched_identity_tokens"] = matched_tokens
    return item


def enrich_candidates(candidates: List[Dict[str, Any]], existing_items: Dict[str, Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    now_iso = utcnow().isoformat()
    diagnostics: Dict[str, Any] = {
        "fetched": 0,
        "reused": 0,
        "kept": 0,
        "dropped": 0,
        "categories": Counter(),
        "domains": Counter(),
        "errors": [],
    }
    enriched_items: List[Dict[str, Any]] = []

    for idx, candidate in enumerate(candidates, start=1):
        key = canonicalize_url(candidate.get("url") or "")
        if not key:
            continue
        existing = existing_items.get(key)
        should_refetch = bool(
            not existing
            or not existing.get("fetch_ok")
            or not existing.get("title")
            or is_placeholder_title(existing.get("title") or "")
            or should_force_refresh(existing)
            or (existing.get("domain") in {"tab.uol.com.br", "udemy.com"} and not existing.get("published_at"))
        )
        if existing and not should_refetch and existing.get("last_seen_at"):
            merged = merge_candidate(existing, candidate)
            merged["last_seen_at"] = now_iso
            diagnostics["reused"] += 1
        else:
            fetched = fetch_page(key)
            base = merge_candidate(existing, candidate) if existing else dict(candidate)
            merged = merge_candidate(base, fetched)
            if fetched.get("title") and not is_placeholder_title(fetched.get("title") or ""):
                merged["title"] = fetched["title"]
            if fetched.get("description"):
                merged["description"] = fetched["description"]
            if fetched.get("site_name"):
                merged["site_name"] = fetched["site_name"]
            if fetched.get("author_names"):
                merged["author_names"] = fetched["author_names"]
            if fetched.get("published_at"):
                merged["published_at"] = fetched["published_at"]
            if fetched.get("text_excerpt"):
                merged["text_excerpt"] = fetched["text_excerpt"]
            merged["url"] = fetched.get("canonical_url") or key
            merged["canonical_url"] = fetched.get("canonical_url") or key
            merged["resolved_url"] = fetched.get("resolved_url") or key
            merged["status_code"] = fetched.get("status_code")
            merged["fetch_ok"] = fetched.get("fetch_ok", False)
            merged["content_type"] = fetched.get("content_type", "")
            merged["error"] = fetched.get("error", "")
            diagnostics["fetched"] += 1
            if merged.get("error"):
                diagnostics["errors"].append({"url": merged["url"], "error": merged["error"]})
            time.sleep(0.15)

        existing_first_seen = existing.get("first_seen_at") if existing else None
        merged["first_seen_at"] = existing_first_seen or now_iso
        merged["last_seen_at"] = now_iso
        merged["author_names"] = dedupe_keep_order(merged.get("author_names") or [])
        merged["source_labels"] = [
            label
            for label in dedupe_keep_order(merged.get("source_labels") or [])
            if not label.startswith("portfolio-")
        ]
        merged["queries"] = dedupe_keep_order(merged.get("queries") or [])
        merged["domain"] = domain_of(merged.get("url") or "")
        merged["site_name"] = merged.get("site_name") or merged["domain"]
        merged["published_at"] = parse_datetime(merged.get("published_at")) or None
        merged["published_date"] = iso_to_date(merged.get("published_at"))
        merged["first_seen_date"] = iso_to_date(merged.get("first_seen_at"))
        merged["last_seen_date"] = iso_to_date(merged.get("last_seen_at"))
        merged["id"] = existing.get("id") if existing else make_id(merged["url"])
        enriched_items.append(merged)

        if idx % 25 == 0:
            print(f"Processed {idx}/{len(candidates)} candidates", file=sys.stderr)

    # --- Two-pass classification ---
    # Pass 1: classify everything without republication detection to find autoral titles
    autoral_titles: set = set()
    pass1_items: List[Dict[str, Any]] = []
    for item in enriched_items:
        classified = classify_item(dict(item))  # type: ignore[assignment]
        if not classified:
            diagnostics["dropped"] += 1
            continue
        pass1_items.append(classified)
        if classified.get("category") == "reportagem_autoral":
            title_norm = normalize_text(classified.get("title", ""))
            if title_norm:
                autoral_titles.add(title_norm)

    # Pass 2: reclassify with autoral titles for republication detection
    items: List[Dict[str, Any]] = []
    for item in pass1_items:
        # Only reclassify items that were marked as "mencao" — they could be republications
        if item.get("category") == "mencao" and autoral_titles:
            reclassified = classify_item(dict(item), all_autoral_titles=autoral_titles)
            if reclassified:
                item = reclassified
        diagnostics["kept"] += 1
        diagnostics["categories"][item["category"]] += 1
        diagnostics["domains"][item["domain"]] += 1
        items.append(item)

    items.sort(
        key=lambda item: (
            item.get("published_at") or "",
            item.get("first_seen_at") or "",
            item.get("match_score") or 0,
            item.get("title") or "",
        ),
        reverse=True,
    )
    return items, diagnostics


def build_metadata(items: List[Dict[str, Any]], source_diagnostics: Dict[str, Any]) -> Dict[str, Any]:
    now = utcnow()
    counts_by_category = {key: 0 for key in CATEGORY_ORDER}
    counts_by_domain = Counter(item.get("domain") or "" for item in items)
    recent = {
        "last_1_day": 0,
        "last_7_days": 0,
        "last_30_days": 0,
    }
    for item in items:
        counts_by_category[item["category"]] = counts_by_category.get(item["category"], 0) + 1
        if item.get("category") == "perfil_bio" or item.get("relation") == "pagina de autor":
            continue
        ref = item.get("published_at")
        days = recent_days_from(ref, now)
        if days is None:
            continue
        if days <= 1:
            recent["last_1_day"] += 1
        if days <= 7:
            recent["last_7_days"] += 1
        if days <= 30:
            recent["last_30_days"] += 1

    published_values = [item.get("published_date") for item in items if item.get("published_date")]
    return {
        "generated_at": now.isoformat(),
        "counts": {
            "items": len(items),
            "domains": len([domain for domain in counts_by_domain if domain]),
            "categories": counts_by_category,
            "recent": recent,
        },
        "date_range": {
            "min": min(published_values) if published_values else None,
            "max": max(published_values) if published_values else None,
        },
        "methodology": {
            "names": NAME_VARIANTS,
            "summary": (
                "Clipping incremental construido a partir de paginas de autor, perfis institucionais e mencoes externas "
                "descobertas fora do seu portfolio, combinadas com busca nominal em noticias indexadas e busca web. "
                "Quando um site bloqueia raspagem direta, o script tenta uma leitura alternativa so para metadados e trechos. "
                "Os recortes de ultimo dia, 7 dias e 30 dias usam apenas data publicada verificavel. "
                "A classificacao e por regras (byline, tipo de pagina, "
                "palavras-chave e contexto institucional). O resultado e amplo, mas nao exaustivo: buscadores podem perder paginas, "
                "alguns sites bloqueiam raspagem e homonimos podem exigir revisao manual."
            ),
        },
        "sources": source_diagnostics,
    }


def build_existing_index() -> Dict[str, Dict[str, Any]]:
    existing = load_json(ITEMS_JSON, [])
    index: Dict[str, Dict[str, Any]] = {}
    for item in existing:
        key = canonicalize_url(item.get("url") or item.get("canonical_url") or "")
        if key:
            index[key] = item
    return index


def run(max_ddg_pages: int, max_google_items: int) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    existing_items = build_existing_index()
    candidates, discovery_diagnostics = build_candidates(max_ddg_pages=max_ddg_pages, max_google_items=max_google_items)
    items, enrich_diagnostics = enrich_candidates(candidates, existing_items)
    metadata = build_metadata(items, {"discovery": discovery_diagnostics, "enrich": enrich_diagnostics})
    save_json(ITEMS_JSON, items)
    save_json(META_JSON, metadata)
    save_json(SOURCES_JSON, {"discovery": discovery_diagnostics, "enrich": enrich_diagnostics})

    print(
        json.dumps(
            {
                "items": len(items),
                "categories": metadata["counts"]["categories"],
                "recent": metadata["counts"]["recent"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build personal clipping data for Luiz Fernando Toledo")
    parser.add_argument("--max-ddg-pages", type=int, default=1)
    parser.add_argument("--max-google-items", type=int, default=60)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    try:
        run(max_ddg_pages=max(0, args.max_ddg_pages), max_google_items=max(0, args.max_google_items))
    finally:
        FETCHER.close()
