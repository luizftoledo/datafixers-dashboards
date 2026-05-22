from __future__ import annotations

import datetime as dt
import re
import unicodedata
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Optional

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

PLANALTO_MINISTERS_URL = "https://www.gov.br/planalto/pt-br/conheca-a-presidencia/ministros-e-ministras"
JANJA_SOURCE_URL = "https://www.gov.br/planalto/pt-br/acompanhe-o-planalto/agenda-da-primeira-dama"
STF_COMPOSITION_URL = (
    "https://portal.stf.jus.br/textos/verTexto.asp?"
    "servico=bibliotecaConsultaProdutoBibliotecaPastaMinistro&pagina=ComposicaoAtual"
)
CAMARA_DEPUTADOS_API = "https://dadosabertos.camara.leg.br/api/v2/deputados"
SENADO_ATUAL_JSON_URL = "https://legis.senado.leg.br/dadosabertos/senador/lista/atual.json"

LULA_SLUG = "luiz-inacio-lula-da-silva"
LULA_MANDATE = "Lula 3 (2023-)"

NAME_PARTICLES = {
    "a", "as", "da", "das", "de", "del", "della", "di", "do", "dos", "du", "e",
}
SUFFIX_TOKENS = {"filho", "neto", "junior", "júnior"}
COMMON_SURNAME_BAN = {
    "almeida", "alves", "araujo", "barbosa", "barros", "campos", "carvalho", "costa",
    "dias", "ferreira", "gomes", "jesus", "lima", "melo", "mendes", "oliveira", "pereira",
    "ribeiro", "rodrigues", "santos", "silva", "souza", "vieira",
}
SINGLE_ALIAS_BAN = COMMON_SURNAME_BAN | {
    "advocacia", "agricultura", "ambiente", "aeroportos", "brasil", "brasileia", "camara",
    "cidade", "cidades", "clima", "comunicacao", "controladoria", "cultura", "economia",
    "educacao", "esporte", "estado", "fazenda", "gabinete", "geral", "governo", "igualdade",
    "integracao", "justica", "ministra", "ministro", "ministros", "mulheres", "planejamento",
    "portos", "presidencia", "presidente", "primeira", "republica", "saude", "secretaria",
    "seguranca", "senado", "senador", "senadora", "social", "stf", "supremo", "trabalho",
    "transportes", "turismo", "uniao",
}
GROUP_LABELS = {
    "janja": "Janja",
    "ministro": "Ministros do governo",
    "stf": "STF",
    "deputado": "Deputados federais",
    "senador": "Senadores",
}


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def normalize_space(value: str) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\ufeff", " ")).strip()


def strip_accents(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def fold_text(value: str) -> str:
    return normalize_space(strip_accents(str(value or "")).lower())


def normalize_match_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", fold_text(value))).strip()


def slugify(value: str) -> str:
    folded = normalize_match_text(value)
    return folded.replace(" ", "-") or "item"


def uniq(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        text = normalize_space(value)
        if not text:
            continue
        key = normalize_match_text(text)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def person_tokens(value: str) -> List[str]:
    raw = [tok for tok in re.split(r"[^\wÀ-ÿ0-9]+", normalize_space(value)) if tok]
    return [tok for tok in raw if fold_text(tok) not in NAME_PARTICLES]


def build_pair_aliases(tokens: List[str]) -> List[str]:
    if len(tokens) < 2:
        return []
    combos = set()
    last_idx = len(tokens) - 1
    for i, left in enumerate(tokens):
        if len(left) < 3:
            continue
        for j in range(i + 1, len(tokens)):
            right = tokens[j]
            if len(right) < 3:
                continue
            if j == i + 1 or i == 0 or j == last_idx or fold_text(right) in SUFFIX_TOKENS:
                combos.add(f"{left} {right}")
    if fold_text(tokens[-1]) in SUFFIX_TOKENS and len(tokens) >= 2:
        combos.add(f"{tokens[-2]} {tokens[-1]}")
    combos.add(f"{tokens[0]} {tokens[-1]}")
    return sorted(combos, key=lambda item: (-len(person_tokens(item)), -len(item)))


def build_entity(role_kind: str, display_name: str, official_name: str, role: str, source_url: str, **extra) -> dict:
    base_name = display_name or official_name
    entity = {
        "id": f"{role_kind}-{slugify(base_name)}",
        "kind": role_kind,
        "group_label": GROUP_LABELS.get(role_kind, role_kind),
        "name": normalize_space(base_name),
        "official_name": normalize_space(official_name or base_name),
        "role": normalize_space(role),
        "source_url": normalize_space(source_url),
        "aliases_source": [],
    }
    for key, value in extra.items():
        if value is not None and value != "":
            entity[key] = value
    return entity


def fetch_text(url: str, user_agent: str, timeout: int = 30) -> str:
    resp = requests.get(
        url,
        timeout=timeout,
        headers={"User-Agent": user_agent, "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"},
    )
    resp.raise_for_status()
    return resp.text


def fetch_json(url: str, user_agent: str, timeout: int = 30) -> dict:
    resp = requests.get(
        url,
        timeout=timeout,
        headers={"User-Agent": user_agent, "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"},
    )
    resp.raise_for_status()
    return resp.json()


def fetch_browser_html(url: str, user_agent: str, timeout_ms: int = 60000) -> str:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(ignore_https_errors=True, user_agent=user_agent)
        page = context.new_page()
        page.goto(url, wait_until="networkidle", timeout=timeout_ms)
        html = page.content()
        browser.close()
    return html


def fetch_planalto_ministers(user_agent: str) -> List[dict]:
    html = fetch_text(PLANALTO_MINISTERS_URL, user_agent)
    soup = BeautifulSoup(html, "html.parser")
    paragraphs = [
        normalize_space(node.get_text(" ", strip=True))
        for node in soup.select("#parent-fieldname-text p")
    ]
    entities: List[dict] = []
    for idx in range(0, len(paragraphs) - 1, 2):
        role = paragraphs[idx]
        name = paragraphs[idx + 1]
        if len(role) < 4 or len(name) < 4:
            continue
        entity = build_entity(
            "ministro",
            display_name=name,
            official_name=name,
            role=role,
            source_url=PLANALTO_MINISTERS_URL,
        )
        entity["aliases_source"] = uniq([name])
        entities.append(entity)
    return entities


def fetch_stf_ministers(user_agent: str) -> List[dict]:
    html = fetch_browser_html(STF_COMPOSITION_URL, user_agent)
    soup = BeautifulSoup(html, "html.parser")
    entities: List[dict] = []
    for cell in soup.select("#conteudo td"):
        text = normalize_space(cell.get_text(" ", strip=True))
        if "Ministr" not in text:
            continue
        link = cell.select_one("a[href]")
        role_match = re.match(r"Ministr[oa]\s+(.+?)(?:\s+\((.+)\))?$", text)
        if not role_match:
            continue
        name = normalize_space(role_match.group(1))
        stf_role = normalize_space(role_match.group(2) or "Ministro do STF")
        entity = build_entity(
            "stf",
            display_name=name,
            official_name=name,
            role=stf_role,
            source_url=normalize_space(link.get("href", "")) if link else STF_COMPOSITION_URL,
        )
        entity["aliases_source"] = uniq([name])
        entities.append(entity)
    return entities


def fetch_camara_deputados(user_agent: str) -> List[dict]:
    entities: List[dict] = []
    next_url = f"{CAMARA_DEPUTADOS_API}?itens=100&ordem=ASC&ordenarPor=nome"
    while next_url:
        payload = fetch_json(next_url, user_agent)
        for item in payload.get("dados", []):
            name = normalize_space(item.get("nome", ""))
            if not name:
                continue
            entity = build_entity(
                "deputado",
                display_name=name,
                official_name=name,
                role="Deputado federal",
                source_url=normalize_space(item.get("uri", "")) or CAMARA_DEPUTADOS_API,
                party=normalize_space(item.get("siglaPartido", "")),
                state=normalize_space(item.get("siglaUf", "")),
            )
            entity["aliases_source"] = uniq([name])
            entities.append(entity)
        next_url = ""
        for link in payload.get("links", []):
            if normalize_space(link.get("rel", "")).lower() == "next":
                next_url = normalize_space(link.get("href", ""))
                break
    return entities


def fetch_senado_senadores(user_agent: str) -> List[dict]:
    payload = fetch_json(SENADO_ATUAL_JSON_URL, user_agent)
    block = payload.get("ListaParlamentarEmExercicio", {}).get("Parlamentares", {})
    current = block.get("Parlamentar") or []
    if isinstance(current, dict):
        current = [current]
    entities: List[dict] = []
    for item in current:
        ident = item.get("IdentificacaoParlamentar", {})
        common_name = normalize_space(ident.get("NomeParlamentar", ""))
        full_name = normalize_space(ident.get("NomeCompletoParlamentar", "")) or common_name
        if not common_name:
            continue
        entity = build_entity(
            "senador",
            display_name=common_name,
            official_name=full_name,
            role="Senador",
            source_url=normalize_space(ident.get("UrlPaginaParlamentar", "")) or SENADO_ATUAL_JSON_URL,
            party=normalize_space(ident.get("SiglaPartidoParlamentar", "")),
            state=normalize_space(ident.get("UfParlamentar", "")),
        )
        entity["aliases_source"] = uniq([common_name, full_name])
        entities.append(entity)
    return entities


def build_janja_entity() -> dict:
    entity = build_entity(
        "janja",
        display_name="Janja",
        official_name="Rosangela Lula da Silva",
        role="Primeira-dama",
        source_url=JANJA_SOURCE_URL,
    )
    entity["aliases_source"] = uniq(
        [
            "Janja",
            "Janja Lula da Silva",
            "Rosangela Lula da Silva",
            "Rosangela da Silva",
            "Rosangela",
        ]
    )
    return entity


def collect_entities(user_agent: str) -> List[dict]:
    entities = []
    entities.extend(fetch_planalto_ministers(user_agent))
    entities.append(build_janja_entity())
    entities.extend(fetch_stf_ministers(user_agent))
    entities.extend(fetch_camara_deputados(user_agent))
    entities.extend(fetch_senado_senadores(user_agent))
    deduped = []
    seen = set()
    for entity in entities:
        if entity["id"] in seen:
            continue
        seen.add(entity["id"])
        deduped.append(entity)
    return deduped


def build_candidate_aliases(entity: dict) -> List[str]:
    aliases = []
    official_name = entity.get("official_name") or entity.get("name") or ""
    display_name = entity.get("name") or official_name
    aliases.extend(entity.get("aliases_source") or [])
    aliases.extend([display_name, official_name])

    official_tokens = person_tokens(official_name)
    display_tokens = person_tokens(display_name)

    if entity["kind"] == "janja":
        aliases.extend(build_pair_aliases(official_tokens))
        if display_tokens != official_tokens:
            aliases.extend(build_pair_aliases(display_tokens))
        aliases.extend(display_tokens[:1] or official_tokens[:1])
    elif entity["kind"] in {"ministro", "stf"}:
        aliases.extend(build_pair_aliases(official_tokens))
        if display_tokens != official_tokens:
            aliases.extend(build_pair_aliases(display_tokens))
        base_tokens = display_tokens or official_tokens
        if len(base_tokens) == 2:
            aliases.extend(base_tokens)
        elif base_tokens:
            aliases.append(base_tokens[0])
    else:
        if display_tokens and fold_text(display_tokens[-1]) in SUFFIX_TOKENS and len(display_tokens) >= 2:
            aliases.append(f"{display_tokens[-2]} {display_tokens[-1]}")
        if official_tokens and fold_text(official_tokens[-1]) in SUFFIX_TOKENS and len(official_tokens) >= 2:
            aliases.append(f"{official_tokens[-2]} {official_tokens[-1]}")

    cleaned = []
    for alias in uniq(aliases):
        normalized = normalize_match_text(alias)
        if len(normalized) < 3:
            continue
        cleaned.append(alias)
    return cleaned


def prepare_entity_aliases(entities: List[dict]) -> None:
    alias_owner_count = Counter()
    token_owner_count = Counter()
    raw_candidates: Dict[str, List[str]] = {}

    for entity in entities:
        candidates = build_candidate_aliases(entity)
        raw_candidates[entity["id"]] = candidates
        alias_owner_count.update({normalize_match_text(alias): 1 for alias in candidates})
        token_owner_count.update(
            {
                normalize_match_text(token): 1
                for alias in candidates
                for token in person_tokens(alias)
                if normalize_match_text(token)
            }
        )

    for entity in entities:
        aliases = []
        for alias in raw_candidates[entity["id"]]:
            folded = normalize_match_text(alias)
            tokens = folded.split()
            if not tokens:
                continue

            if entity["kind"] in {"deputado", "senador"}:
                if len(tokens) == 1:
                    continue
                if tokens[0] in NAME_PARTICLES:
                    continue

            if len(tokens) == 1:
                token = tokens[0]
                min_len = 4 if entity["kind"] == "janja" else 5
                if len(token) < min_len:
                    continue
                if token_owner_count[token] != 1:
                    continue
                if token in SINGLE_ALIAS_BAN:
                    continue
            else:
                if alias_owner_count[folded] > 1 and folded not in {
                    normalize_match_text(entity.get("name", "")),
                    normalize_match_text(entity.get("official_name", "")),
                }:
                    continue

            aliases.append(alias)

        entity["aliases_counted"] = uniq(
            sorted(aliases, key=lambda item: (-len(person_tokens(item)), -len(item), fold_text(item)))
        )
        anchor_tokens = set()
        for alias in entity["aliases_counted"]:
            for token in person_tokens(alias):
                folded_token = normalize_match_text(token)
                if len(folded_token) >= 4 and folded_token not in SINGLE_ALIAS_BAN:
                    anchor_tokens.add(folded_token)
        entity["anchor_tokens"] = sorted(anchor_tokens)
        entity["search_text"] = normalize_match_text(
            " ".join(
                [
                    entity.get("name", ""),
                    entity.get("official_name", ""),
                    entity.get("group_label", ""),
                    entity.get("role", ""),
                    entity.get("party", ""),
                    entity.get("state", ""),
                    " ".join(entity.get("aliases_counted") or []),
                ]
            )
        )


def build_alias_patterns(aliases: List[str]) -> List[tuple]:
    patterns = []
    for alias in aliases:
        folded = normalize_match_text(alias)
        if not folded:
            continue
        escaped = re.escape(folded).replace("\\ ", r"\s+")
        pattern = re.compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])")
        patterns.append((alias, pattern))
    return patterns


def week_key(date_iso: str) -> str:
    if not date_iso:
        return ""
    try:
        year, week, _weekday = dt.date.fromisoformat(date_iso).isocalendar()
    except ValueError:
        return ""
    return f"{year:04d}-W{week:02d}"


def select_best_display_name(entity: dict, observed_alias_stats: Dict[str, Dict[str, int]]) -> str:
    observed = sorted(
        observed_alias_stats.items(),
        key=lambda item: (-item[1]["docs"], -item[1]["occurrences"], -len(person_tokens(item[0])), item[0]),
    )
    for alias, stats in observed:
        if len(person_tokens(alias)) >= 2 and stats["docs"] >= 1:
            return alias
    return entity.get("name") or entity.get("official_name") or ""


def extract_snippet(text: str, aliases: List[str]) -> str:
    if not text:
        return ""
    segments = [
        normalize_space(part)
        for part in re.split(r"(?:\n{1,}|(?<=[\.\!\?])\s+)", text)
        if normalize_space(part)
    ]
    alias_norms = [normalize_match_text(alias) for alias in aliases if normalize_match_text(alias)]
    for segment in segments:
        norm_segment = normalize_match_text(segment)
        if any(alias in norm_segment for alias in alias_norms):
            if len(segment) <= 340:
                return segment
            return segment[:337].rstrip() + "..."
    fallback = normalize_space(text[:340])
    if len(text) > len(fallback):
        return fallback.rstrip() + "..."
    return fallback


def match_entity_mentions(entity: dict, normalized_text: str) -> Optional[dict]:
    if not normalized_text:
        return None
    spans: List[tuple] = []
    matched_aliases = Counter()
    for alias, pattern in entity["_patterns"]:
        for match in pattern.finditer(normalized_text):
            span = match.span()
            overlap = False
            for left, right in spans:
                if not (span[1] <= left or span[0] >= right):
                    overlap = True
                    break
            if overlap:
                continue
            spans.append(span)
            matched_aliases[alias] += 1
    if not spans:
        return None
    spans.sort()
    return {
        "occurrences": len(spans),
        "matched_aliases": matched_aliases,
    }


def build_people_index(records: List[dict], user_agent: str) -> dict:
    entities = collect_entities(user_agent)
    prepare_entity_aliases(entities)
    for entity in entities:
        entity["_patterns"] = build_alias_patterns(entity.get("aliases_counted") or [])

    lula_records = [
        rec for rec in records
        if rec.get("president_slug") == LULA_SLUG
        and rec.get("mandate") == LULA_MANDATE
        and normalize_space(rec.get("text", ""))
    ]

    doc_cache = []
    for rec in lula_records:
        normalized_text = normalize_match_text(rec.get("text", ""))
        if not normalized_text:
            continue
        doc_cache.append(
            {
                "record": rec,
                "normalized_text": normalized_text,
                "token_set": set(normalized_text.split()),
            }
        )

    mention_rows: List[dict] = []
    alias_occurrences = defaultdict(Counter)
    alias_docs = defaultdict(lambda: defaultdict(set))

    for cached in doc_cache:
        rec = cached["record"]
        token_set = cached["token_set"]
        normalized_text = cached["normalized_text"]

        for entity in entities:
            anchor_tokens = set(entity.get("anchor_tokens") or [])
            if anchor_tokens and anchor_tokens.isdisjoint(token_set):
                continue

            match = match_entity_mentions(entity, normalized_text)
            if not match:
                continue

            matched_aliases = sorted(
                match["matched_aliases"].keys(),
                key=lambda item: (-match["matched_aliases"][item], -len(person_tokens(item)), item),
            )
            snippet = extract_snippet(rec.get("text", ""), matched_aliases)

            for alias, count in match["matched_aliases"].items():
                alias_occurrences[entity["id"]][alias] += count
                alias_docs[entity["id"]][alias].add(rec.get("id", ""))

            mention_rows.append(
                {
                    "entity_id": entity["id"],
                    "date": rec.get("date", ""),
                    "week": week_key(rec.get("date", "")),
                    "month": (rec.get("date", "") or "")[:7],
                    "year": (rec.get("date", "") or "")[:4],
                    "type": rec.get("type", ""),
                    "doc_id": rec.get("id", ""),
                    "title": rec.get("title", ""),
                    "url": rec.get("url", ""),
                    "location": rec.get("location", ""),
                    "occurrences": int(match["occurrences"]),
                    "matched_aliases": matched_aliases,
                    "snippet": snippet,
                }
            )

    entities_out = []
    mentions_total = 0
    mentions_docs = 0
    for entity in entities:
        observed = {}
        total_occ = 0
        total_docs = set()
        for alias, occ_count in alias_occurrences[entity["id"]].items():
            doc_ids = alias_docs[entity["id"]][alias]
            observed[alias] = {
                "occurrences": int(occ_count),
                "docs": len(doc_ids),
            }
            total_occ += int(occ_count)
            total_docs.update(doc_ids)

        entity_out = {
            key: value
            for key, value in entity.items()
            if not key.startswith("_") and key not in {"anchor_tokens", "search_text", "aliases_source"}
        }
        entity_out["name"] = select_best_display_name(entity, observed)
        entity_out["aliases_counted"] = entity.get("aliases_counted") or []
        entity_out["aliases_observed"] = [
            {
                "alias": alias,
                "occurrences": stats["occurrences"],
                "docs": stats["docs"],
            }
            for alias, stats in sorted(
                observed.items(),
                key=lambda item: (-item[1]["docs"], -item[1]["occurrences"], item[0]),
            )
        ]
        entity_out["search_text"] = entity.get("search_text", "")
        entity_out["mention_occurrences_total"] = total_occ
        entity_out["mention_docs_total"] = len(total_docs)
        mentions_total += total_occ
        mentions_docs += len(total_docs)
        entities_out.append(entity_out)

    entities_out.sort(
        key=lambda item: (
            GROUP_LABELS.get(item.get("kind", ""), item.get("kind", "")),
            -item.get("mention_occurrences_total", 0),
            fold_text(item.get("name", "")),
        )
    )
    mention_rows.sort(
        key=lambda row: (
            row.get("date", ""),
            row.get("occurrences", 0),
            row.get("title", ""),
            row.get("entity_id", ""),
        ),
        reverse=True,
    )

    dates = [row["date"] for row in mention_rows if row.get("date")]
    summary = {
        "generated_at": now_iso(),
        "scope_president": "Luiz Inacio Lula da Silva",
        "scope_mandate": LULA_MANDATE,
        "source_urls": {
            "planalto_ministros": PLANALTO_MINISTERS_URL,
            "planalto_janja": JANJA_SOURCE_URL,
            "stf_composicao": STF_COMPOSITION_URL,
            "camara_deputados": CAMARA_DEPUTADOS_API,
            "senado_senadores": SENADO_ATUAL_JSON_URL,
        },
        "counts": {
            "entities": len(entities_out),
            "entities_by_kind": dict(Counter(entity["kind"] for entity in entities_out)),
            "mention_rows": len(mention_rows),
            "mention_occurrences_total": mentions_total,
            "mention_docs_total": mentions_docs,
            "lula_docs_with_text": len(doc_cache),
        },
        "date_range": {
            "min": min(dates) if dates else "",
            "max": max(dates) if dates else "",
        },
    }

    return {
        "directory": {
            **summary,
            "entities": entities_out,
        },
        "mentions": {
            **summary,
            "mentions": mention_rows,
        },
        "summary": summary,
    }
