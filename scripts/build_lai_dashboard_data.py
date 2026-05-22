#!/usr/bin/env python3
import argparse
import datetime as dt
import gzip
import json
import re
import shutil
import subprocess
import tempfile
import unicodedata
import urllib.error
import urllib.request
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd


DOWNLOAD_URL_TEMPLATE = (
    "https://dadosabertos-download.cgu.gov.br/FalaBR/Arquivos_FalaBR_Filtrado/"
    "Arquivos_csv_{year}.zip"
)
DOWNLOAD_PORTAL_URL = "https://buscalai.cgu.gov.br/DownloadDados/DownloadDados"
SOURCE_ID = "publica"
SOURCE_LABEL = "Pedidos e recursos marcados como públicos (BuscaLAI)"
PRECEDENTES_URL = (
    "https://www.gov.br/cgu/pt-br/acesso-a-informacao/dados-abertos/"
    "arquivos/busca-de-precedentes"
)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)
DOWNLOAD_TIMEOUT = 300
START_YEAR_DEFAULT = 2015
MIN_REQUESTS_TOP_DENIAL_RATE_CURRENT_YEAR = 200
MIN_REQUESTS_SIGILO100_RANKING = 200
REQUEST_INDEX_SCHEMA_VERSION = 2

ROOT_DIR = Path(__file__).resolve().parents[1]
DASH_DIR = ROOT_DIR / "lai"
DATA_DIR = DASH_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
YEARLY_CACHE_DIR = CACHE_DIR / "yearly"
REPORT_FILE = DATA_DIR / "report_data.json"
METADATA_FILE = DATA_DIR / "metadata.json"
SAMPLES_FILE = DATA_DIR / "request_samples.jsonl.gz"

CANONICAL_DECISIONS = (
    "Acesso Concedido",
    "Acesso Negado",
    "Acesso Parcialmente Concedido",
    "Não se trata de solicitação de informação",
    "Informação Inexistente",
    "Órgão não tem competência para responder sobre o assunto",
    "Pergunta Duplicada/Repetida",
)

DECISION_DENIED = "Acesso Negado"
DECISION_RESTRICTED = {"Acesso Negado", "Acesso Parcialmente Concedido"}

PERSONAL_REASON_KEYWORDS = (
    "dado pessoal",
    "dados pessoais",
    "informacao pessoal",
    "informações pessoais",
    "privacidade",
    "honra",
    "imagem",
    "lgpd",
    "sigilo bancario",
    "sigilo fiscal",
    "informacao de terceiro",
    "informação de terceiro",
    "terceiro",
)

THEME_RULES = [
    (
        "Dados pessoais e vida privada",
        [
            "dado pessoal",
            "dados pessoais",
            "informação pessoal",
            "informacao pessoal",
            "lgpd",
            "privacidade",
            "sigilo banc",
            "sigilo fiscal",
            "cpf",
            "cnpj",
            "prontuário",
            "prontuario",
            "endereço",
            "endereco",
        ],
    ),
    (
        "Contratos, licitações e fornecedores",
        [
            "contrato",
            "licita",
            "pregão",
            "pregao",
            "edital",
            "fornecedor",
            "aditivo",
            "ata de registro",
            "dispensa de licita",
            "inexigibilidade",
        ],
    ),
    (
        "Gastos públicos e orçamento",
        [
            "gasto",
            "despesa",
            "orçamento",
            "orcamento",
            "empenho",
            "pagamento",
            "nota fiscal",
            "diária",
            "diaria",
            "passagem",
            "custeio",
            "dotação",
            "dotacao",
        ],
    ),
    (
        "Servidores, salários e concursos",
        [
            "servidor",
            "remuneração",
            "remuneracao",
            "salário",
            "salario",
            "cargo",
            "concurso",
            "nomeação",
            "nomeacao",
            "lotação",
            "lotacao",
            "folha de pagamento",
            "benefício de servidor",
        ],
    ),
    (
        "Previdência e benefícios sociais",
        [
            "inss",
            "aposentadoria",
            "benefício",
            "beneficio",
            "pensão",
            "pensao",
            "auxílio",
            "auxilio",
            "bpc",
            "cadúnico",
            "cadunico",
            "bolsa família",
            "bolsa familia",
        ],
    ),
    (
        "Saúde e medicamentos",
        [
            "medicamento",
            "hospital",
            "leito",
            "sus",
            "tratamento",
            "vacina",
            "saúde",
            "saude",
            "fila de espera",
        ],
    ),
    (
        "Segurança pública e polícia",
        [
            "polícia",
            "policia",
            "delegacia",
            "boletim de ocorrência",
            "boletim de ocorrencia",
            "inquérito",
            "inquerito",
            "crime",
            "armamento",
            "prisão",
            "prisao",
            "pf",
        ],
    ),
    (
        "Educação e pesquisa",
        [
            "escola",
            "universidade",
            "enem",
            "educação",
            "educacao",
            "matrícula",
            "matricula",
            "bolsa",
            "aluno",
            "professor",
        ],
    ),
    (
        "Obras, infraestrutura e transportes",
        [
            "obra",
            "rodovia",
            "ponte",
            "aeroporto",
            "infraestrutura",
            "transporte",
            "pavimentação",
            "pavimentacao",
            "ferrovia",
        ],
    ),
    (
        "Meio ambiente e território",
        [
            "desmatamento",
            "licença ambiental",
            "licenca ambiental",
            "ibama",
            "icmbio",
            "terra indígena",
            "terra indigena",
            "queimada",
            "mineração",
            "mineracao",
        ],
    ),
    (
        "Processos administrativos e sanções",
        [
            "processo administrativo",
            "sindicância",
            "sindicancia",
            "pad",
            "multa",
            "auto de infração",
            "autos de infração",
            "sanção",
            "sancao",
        ],
    ),
]
DEFAULT_THEME = "Outros temas"

SEARCH_PRESETS = [
    {
        "id": "dados-pessoais",
        "label": "Negativas por dados pessoais",
        "filters": {
            "theme": "Dados pessoais e vida privada",
            "decision_group": "negado",
            "year": "",
            "org": "",
        },
    },
    {
        "id": "contratos",
        "label": "Contratos e licitações",
        "filters": {
            "theme": "Contratos, licitações e fornecedores",
            "decision_group": "negado",
            "year": "",
            "org": "",
        },
    },
    {
        "id": "servidores",
        "label": "Servidores e concursos",
        "filters": {
            "theme": "Servidores, salários e concursos",
            "decision_group": "negado",
            "year": "",
            "org": "",
        },
    },
    {
        "id": "inss-beneficios",
        "label": "INSS e benefícios",
        "filters": {
            "theme": "Previdência e benefícios sociais",
            "decision_group": "negado",
            "year": "",
            "org": "",
        },
    },
    {
        "id": "gastos-negados",
        "label": "Gastos públicos negados",
        "filters": {
            "theme": "Gastos públicos e orçamento",
            "decision_group": "negado",
            "year": "",
            "org": "",
        },
    },
]

REASON_NORMALIZATION = {
    "dados pessoais": "Dados pessoais",
    "dado pessoal": "Dados pessoais",
    "dados pessoais.": "Dados pessoais",
    "dados pessoais e sensiveis": "Dados pessoais e sensíveis",
    "dados pessoais e sensíveis": "Dados pessoais e sensíveis",
    "pedido generico": "Pedido genérico",
    "pedido genérico": "Pedido genérico",
    "pedido incompreensivel": "Pedido incompreensível",
    "pedido incompreensível": "Pedido incompreensível",
    "pedido desproporcional ou desarrazoado": "Pedido desproporcional ou desarrazoado",
    "informacao sigilosa de acordo com legislacao especifica": "Informação sigilosa de acordo com legislação específica",
    "informação sigilosa de acordo com legislação específica": "Informação sigilosa de acordo com legislação específica",
    "parte da informacao e sigilosa de acordo com legislacao especifica": "Parte da informação é sigilosa de acordo com legislação específica",
    "parte da informação é sigilosa de acordo com legislação específica": "Parte da informação é sigilosa de acordo com legislação específica",
    "informacao classificada conforme artigos 23 e 24 da lei n 12.527/2011": "Informação classificada conforme artigos 23 e 24 da Lei nº 12.527/2011",
    "informação classificada conforme artigos 23 e 24 da lei nº 12.527/2011": "Informação classificada conforme artigos 23 e 24 da Lei nº 12.527/2011",
    "parte da informacao classificada conforme artigos 23 e 24 da lei n 12.527/2011": "Parte da informação classificada conforme artigos 23 e 24 da Lei nº 12.527/2011",
    "parte da informação classificada conforme artigos 23 e 24 da lei nº 12.527/2011": "Parte da informação classificada conforme artigos 23 e 24 da Lei nº 12.527/2011",
    "informacao inexistente": "Informação inexistente",
    "parte da informacao e inexistente": "Parte da informação é inexistente",
    "parte da informação é inexistente": "Parte da informação é inexistente",
    "processo decisorio em curso": "Processo decisório em curso",
    "processo decisório em curso": "Processo decisório em curso",
    "parte da informacao esta relacionada a processo decisorio em curso": "Parte da informação está relacionada a processo decisório em curso",
    "parte da informação está relacionada a processo decisório em curso": "Parte da informação está relacionada a processo decisório em curso",
    "pedido exige tratamento adicional de dados": "Pedido exige tratamento adicional de dados",
    "parte da informacao demandara mais tempo para producao": "Parte da informação demandará mais tempo para produção",
    "parte da informação demandará mais tempo para produção": "Parte da informação demandará mais tempo para produção",
}


def ensure_dirs():
    YEARLY_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def normalize_text(value):
    if value is None:
        return ""
    text = str(value).replace("\ufeff", "").strip()
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_for_match(value):
    text = normalize_text(value)
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"\s+", " ", text).strip()
    return text


def truncate_text(text, limit=280):
    cleaned = normalize_text(text)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."


def combine_text_fields(*values):
    parts = []
    for value in values:
        text = normalize_text(value)
        if text:
            parts.append(text)
    return " ".join(parts)


def build_buscalai_request_link(id_pedido):
    clean = normalize_text(id_pedido)
    if not clean:
        return ""
    return f"https://buscalai.cgu.gov.br/busca/{clean}"


def build_api_request_link(id_pedido):
    clean = normalize_text(id_pedido)
    if not clean:
        return ""
    return f"https://api-laibr.cgu.gov.br/buscar-pedidos/{clean}"


def canonicalize_decision(value):
    text = normalize_text(value)
    if not text:
        return "Sem decisão registrada"

    normalized = normalize_for_match(text)
    for decision in CANONICAL_DECISIONS:
        norm_dec = normalize_for_match(decision)
        if normalized == norm_dec:
            return decision
        if normalized.startswith(norm_dec + " "):
            return decision

    if len(text) > 120:
        return "Outros (texto livre)"
    return text


def canonicalize_reason(value):
    text = normalize_text(value)
    if not text:
        return "Motivo não informado"

    if len(text) > 180:
        return "Outros (texto livre)"

    norm = normalize_for_match(text)
    canonical = REASON_NORMALIZATION.get(norm)
    if canonical:
        return canonical

    if len(text) > 0:
        return text[0].upper() + text[1:]
    return "Motivo não informado"


def is_personal_reason(reason):
    norm = normalize_for_match(reason)
    if not norm:
        return False
    return any(token in norm for token in PERSONAL_REASON_KEYWORDS)


def merge_counter_dict(target, incoming):
    for key, value in incoming.items():
        target[key] += int(value)


def cache_file_for_year(year):
    return YEARLY_CACHE_DIR / f"{year}.json"


def load_year_cache(year):
    path = cache_file_for_year(year)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def save_year_cache(year_payload):
    year = int(year_payload["year"])
    path = cache_file_for_year(year)
    path.write_text(json.dumps(year_payload, ensure_ascii=False, indent=2), encoding="utf-8")


def download_with_urllib(url, target):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as resp:
        if getattr(resp, "status", 200) >= 400:
            raise urllib.error.HTTPError(url, resp.status, "http error", resp.headers, None)
        with target.open("wb") as out:
            shutil.copyfileobj(resp, out, length=1024 * 1024)


def download_with_curl(url, target):
    cmd = [
        "curl",
        "-L",
        "-A",
        USER_AGENT,
        "--fail",
        "--silent",
        "--show-error",
        "--output",
        str(target),
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "curl falhou")


def download_source(url, target):
    errors = []
    for downloader in (download_with_urllib, download_with_curl):
        try:
            downloader(url, target)
            if target.exists() and target.stat().st_size > 0:
                return
        except Exception as exc:  # pragma: no cover
            errors.append(str(exc))
            target.unlink(missing_ok=True)
    raise RuntimeError("Falha no download: " + " | ".join(errors))


def find_pedidos_member(zip_path):
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    candidates = [
        name
        for name in names
        if "Pedidos_csv_" in name and "Solicitantes" not in name and "LinkArquivo" not in name
    ]
    if not candidates:
        raise RuntimeError(f"ZIP sem arquivo Pedidos_csv: {zip_path.name}")
    return sorted(candidates)[0]


def find_pedidos_link_member(zip_path):
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    candidates = [name for name in names if "PedidosLinkArquivo" in name]
    if not candidates:
        return None
    return sorted(candidates)[0]


def build_theme_regex(theme_rules):
    compiled = []
    for theme, keywords in theme_rules:
        escaped = [re.escape(keyword.lower()) for keyword in keywords if keyword.strip()]
        if not escaped:
            continue
        pattern = "|".join(escaped)
        compiled.append((theme, pattern))
    return compiled


THEME_REGEX = build_theme_regex(THEME_RULES)


def detect_primary_theme(text_series):
    primary = pd.Series([DEFAULT_THEME] * len(text_series), index=text_series.index, dtype="object")
    for theme, pattern in THEME_REGEX:
        mask = primary.eq(DEFAULT_THEME) & text_series.str.contains(pattern, regex=True, na=False)
        primary.loc[mask] = theme
    return primary


def build_sample_rows(sample_df):
    rows = []
    for rec in sample_df.to_dict(orient="records"):
        id_pedido = normalize_text(rec.get("id_pedido", ""))
        request_attachment_link = normalize_text(rec.get("request_link", ""))
        request_buscalai_link = build_buscalai_request_link(id_pedido)
        request_api_link = build_api_request_link(id_pedido)
        request_public_link = request_api_link or request_buscalai_link
        request_subject = normalize_text(rec.get("subject", "Assunto não informado"))
        request_summary = normalize_text(rec.get("request_summary", ""))
        request_detail = normalize_text(rec.get("request_detail", ""))
        request_text = combine_text_fields(request_subject, request_summary, request_detail)
        response_text = normalize_text(rec.get("response_text", ""))
        response_detail = normalize_text(rec.get("response_detail", ""))
        response_joined = combine_text_fields(response_text, response_detail)
        rows.append(
            {
                "id_pedido": id_pedido,
                "year": int(rec.get("year", 0) or 0),
                "org": rec.get("org", ""),
                "decision": rec.get("decision", ""),
                "decision_group": rec.get("decision_group", "negado"),
                "restricted": bool(rec.get("restricted", False)),
                "reason": rec.get("reason", ""),
                "reason_raw": normalize_text(rec.get("reason_raw", "")),
                "subject": request_subject or "Assunto não informado",
                "theme": rec.get("theme", DEFAULT_THEME),
                "request_summary": request_summary,
                "request_detail": request_detail,
                "request_text": request_text,
                "text_excerpt": truncate_text(request_text, limit=420),
                "response_text": response_joined,
                "response_excerpt": truncate_text(response_joined, limit=420),
                "request_public_link": request_public_link,
                "request_api_link": request_api_link,
                "request_buscalai_link": request_buscalai_link,
                "request_attachment_link": request_attachment_link,
                "request_link": request_public_link or request_attachment_link or request_buscalai_link,
            }
        )
    return rows


def load_request_links(zip_path):
    member = find_pedidos_link_member(zip_path)
    if not member:
        return {}

    links = {}
    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(member) as handle:
            iterator = pd.read_csv(
                handle,
                sep=";",
                encoding="utf-16",
                dtype=str,
                usecols=["IdPedido", "TipoAnexo", "UrlArquivo"],
                chunksize=180_000,
                on_bad_lines="skip",
            )

            for chunk in iterator:
                if chunk.empty:
                    continue

                chunk = chunk.fillna("")
                id_series = chunk["IdPedido"].astype(str).map(normalize_text)
                url_series = chunk["UrlArquivo"].astype(str).map(normalize_text)
                tipo_series = chunk["TipoAnexo"].astype(str).map(normalize_for_match)

                valid = id_series.ne("") & url_series.ne("")
                if not valid.any():
                    continue

                valid_df = pd.DataFrame(
                    {
                        "id_pedido": id_series[valid],
                        "url": url_series[valid],
                        "tipo": tipo_series[valid],
                    }
                )
                if valid_df.empty:
                    continue

                # Prioridade: primeiro tenta "Anexo Resposta"; depois qualquer URL.
                valid_df["priority"] = valid_df["tipo"].map(
                    lambda t: 2 if "resposta" in t else (1 if "pedido" in t else 0)
                )

                for rec in valid_df.to_dict(orient="records"):
                    pid = rec["id_pedido"]
                    if pid not in links or rec["priority"] > links[pid]["priority"]:
                        links[pid] = {"url": rec["url"], "priority": rec["priority"]}

    return {pid: payload["url"] for pid, payload in links.items()}


def process_year_zip(year, zip_path, source_url):
    pedidos_member = find_pedidos_member(zip_path)
    request_links = load_request_links(zip_path)

    wanted_cols = [
        "IdPedido",
        "DataRegistro",
        "OrgaoDestinatario",
        "AssuntoPedido",
        "ResumoSolicitacao",
        "DetalhamentoSolicitacao",
        "Resposta",
        "Decisao",
        "EspecificacaoDecisao",
        "DetalhamentoDecisao",
        "MotivoNegativaAcesso",
    ]

    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(pedidos_member) as handle:
            header_df = pd.read_csv(
                handle,
                sep=";",
                encoding="utf-16",
                dtype=str,
                nrows=0,
                on_bad_lines="skip",
            )
            available_cols = set(header_df.columns)

    usecols = [col for col in wanted_cols if col in available_cols]

    total_requests = 0
    denied_total = 0
    restricted_total = 0
    personal_restricted_total = 0

    decision_counts = Counter()
    reason_counts = Counter()
    monthly_total = Counter()
    monthly_denied = Counter()
    monthly_restricted = Counter()
    monthly_personal = Counter()

    org_total = Counter()
    org_denied = Counter()
    org_restricted = Counter()
    org_personal = Counter()
    org_month_total = Counter()
    org_month_denied = Counter()

    theme_total = Counter()
    theme_restricted = Counter()
    theme_month_total = Counter()
    theme_month_restricted = Counter()

    org_theme_total = Counter()
    org_theme_restricted = Counter()
    org_theme_decision = Counter()

    request_samples = []

    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(pedidos_member) as handle:
            iterator = pd.read_csv(
                handle,
                sep=";",
                encoding="utf-16",
                dtype=str,
                usecols=usecols,
                chunksize=120_000,
                on_bad_lines="skip",
            )

            for chunk in iterator:
                if chunk.empty:
                    continue

                total_requests += len(chunk)

                id_pedido = chunk["IdPedido"].fillna("").astype(str).map(normalize_text)
                org = chunk["OrgaoDestinatario"].fillna("").astype(str).map(normalize_text)
                subject = chunk["AssuntoPedido"].fillna("").astype(str).map(normalize_text)
                subject = subject.mask(subject == "", "Assunto não informado")

                if "ResumoSolicitacao" in chunk.columns:
                    resumo = chunk["ResumoSolicitacao"].fillna("").astype(str).map(normalize_text)
                else:
                    resumo = pd.Series([""] * len(chunk), index=chunk.index, dtype="object")

                if "DetalhamentoSolicitacao" in chunk.columns:
                    detalhamento = (
                        chunk["DetalhamentoSolicitacao"].fillna("").astype(str).map(normalize_text)
                    )
                else:
                    detalhamento = pd.Series([""] * len(chunk), index=chunk.index, dtype="object")
                request_text = (subject + " " + resumo + " " + detalhamento).str.strip()
                request_text_lc = request_text.str.lower()

                decision = chunk["Decisao"].fillna("").astype(str).map(canonicalize_decision)

                if "EspecificacaoDecisao" in chunk.columns:
                    reason_source = (
                        chunk["EspecificacaoDecisao"].fillna("").astype(str).map(normalize_text)
                    )
                else:
                    reason_source = pd.Series([""] * len(chunk), index=chunk.index, dtype="object")

                if "MotivoNegativaAcesso" in chunk.columns:
                    motivo_fallback = (
                        chunk["MotivoNegativaAcesso"].fillna("").astype(str).map(normalize_text)
                    )
                else:
                    motivo_fallback = pd.Series([""] * len(chunk), index=chunk.index, dtype="object")
                reason_raw = reason_source.where(reason_source != "", motivo_fallback)
                reason = reason_raw.map(canonicalize_reason)

                if "Resposta" in chunk.columns:
                    resposta = chunk["Resposta"].fillna("").astype(str).map(normalize_text)
                else:
                    resposta = pd.Series([""] * len(chunk), index=chunk.index, dtype="object")

                if "DetalhamentoDecisao" in chunk.columns:
                    detalhamento_decisao = (
                        chunk["DetalhamentoDecisao"].fillna("").astype(str).map(normalize_text)
                    )
                else:
                    detalhamento_decisao = pd.Series([""] * len(chunk), index=chunk.index, dtype="object")

                data_registro = chunk["DataRegistro"].fillna("").astype(str).map(normalize_text)
                data_registro_dt = pd.to_datetime(data_registro, dayfirst=True, errors="coerce")
                month_num = data_registro_dt.dt.month.fillna(0).astype(int)
                month_str = month_num.astype(str).str.zfill(2)
                valid_month_mask = month_num.between(1, 12)

                denied_mask = decision == DECISION_DENIED
                restricted_mask = decision.isin(DECISION_RESTRICTED)
                personal_mask = restricted_mask & reason.map(is_personal_reason)

                denied_total += int(denied_mask.sum())
                restricted_total += int(restricted_mask.sum())
                personal_restricted_total += int(personal_mask.sum())

                decision_counts.update(decision.value_counts(dropna=False).to_dict())
                reason_counts.update(reason[restricted_mask].value_counts(dropna=False).to_dict())
                monthly_total.update(month_num[valid_month_mask].value_counts(dropna=False).to_dict())
                monthly_denied.update(
                    month_num[valid_month_mask & denied_mask].value_counts(dropna=False).to_dict()
                )
                monthly_restricted.update(
                    month_num[valid_month_mask & restricted_mask].value_counts(dropna=False).to_dict()
                )
                monthly_personal.update(
                    month_num[valid_month_mask & personal_mask].value_counts(dropna=False).to_dict()
                )

                primary_theme = detect_primary_theme(request_text_lc)
                theme_total.update(primary_theme.value_counts(dropna=False).to_dict())
                theme_restricted.update(primary_theme[restricted_mask].value_counts(dropna=False).to_dict())

                theme_valid_month_mask = valid_month_mask
                if theme_valid_month_mask.any():
                    theme_month_key = primary_theme[theme_valid_month_mask] + "|||" + month_str[theme_valid_month_mask]
                    theme_month_total.update(theme_month_key.value_counts(dropna=False).to_dict())

                    theme_month_restricted_key = (
                        primary_theme[theme_valid_month_mask & restricted_mask]
                        + "|||"
                        + month_str[theme_valid_month_mask & restricted_mask]
                    )
                    theme_month_restricted.update(
                        theme_month_restricted_key.value_counts(dropna=False).to_dict()
                    )

                valid_org_mask = org != ""
                org_total.update(org[valid_org_mask].value_counts(dropna=False).to_dict())
                org_denied.update(org[denied_mask & valid_org_mask].value_counts(dropna=False).to_dict())
                org_restricted.update(org[restricted_mask & valid_org_mask].value_counts(dropna=False).to_dict())
                org_personal.update(org[personal_mask & valid_org_mask].value_counts(dropna=False).to_dict())

                org_valid_month_mask = valid_org_mask & valid_month_mask
                if org_valid_month_mask.any():
                    org_month_key = org[org_valid_month_mask] + "|||" + month_str[org_valid_month_mask]
                    org_month_total.update(org_month_key.value_counts(dropna=False).to_dict())

                    org_month_denied_key = (
                        org[org_valid_month_mask & denied_mask]
                        + "|||"
                        + month_str[org_valid_month_mask & denied_mask]
                    )
                    org_month_denied.update(org_month_denied_key.value_counts(dropna=False).to_dict())

                org_theme_df = pd.DataFrame(
                    {
                        "org": org[valid_org_mask],
                        "theme": primary_theme[valid_org_mask],
                        "decision": decision[valid_org_mask],
                    }
                )

                if not org_theme_df.empty:
                    pairs = org_theme_df[["org", "theme"]].value_counts(sort=False)
                    for (org_name, theme_name), count in pairs.items():
                        org_theme_total[f"{org_name}|||{theme_name}"] += int(count)

                    pairs_restricted = org_theme_df[restricted_mask[valid_org_mask]][
                        ["org", "theme"]
                    ].value_counts(sort=False)
                    for (org_name, theme_name), count in pairs_restricted.items():
                        org_theme_restricted[f"{org_name}|||{theme_name}"] += int(count)

                    triples = org_theme_df[["org", "theme", "decision"]].value_counts(sort=False)
                    for (org_name, theme_name, decision_name), count in triples.items():
                        org_theme_decision[f"{org_name}|||{theme_name}|||{decision_name}"] += int(count)

                base_df = pd.DataFrame(
                    {
                        "id_pedido": id_pedido,
                        "year": year,
                        "org": org,
                        "decision": decision,
                        "decision_group": pd.Series(["negado"] * len(chunk), index=chunk.index),
                        "reason": reason,
                        "reason_raw": reason_raw,
                        "subject": subject,
                        "theme": primary_theme,
                        "request_summary": resumo,
                        "request_detail": detalhamento,
                        "request_text": request_text,
                        "response_text": resposta,
                        "response_detail": detalhamento_decisao,
                        "request_link": id_pedido.map(request_links).fillna(""),
                        "restricted": restricted_mask,
                    }
                )

                selected_df = base_df[denied_mask]
                if not selected_df.empty:
                    rows = build_sample_rows(selected_df)
                    request_samples.extend(rows)

    denied_rate = (denied_total / total_requests) if total_requests else 0.0
    restricted_rate = (restricted_total / total_requests) if total_requests else 0.0
    personal_share_in_restricted = (
        (personal_restricted_total / restricted_total) if restricted_total else 0.0
    )

    return {
        "year": int(year),
        "source_url": source_url,
        "zip_member": pedidos_member,
        "processed_at": now_iso(),
        "total_requests": int(total_requests),
        "denied_total": int(denied_total),
        "restricted_total": int(restricted_total),
        "personal_restricted_total": int(personal_restricted_total),
        "denied_rate": denied_rate,
        "restricted_rate": restricted_rate,
        "personal_share_in_restricted": personal_share_in_restricted,
        "decision_counts": dict(decision_counts),
        "reason_counts": dict(reason_counts),
        "monthly_total": dict(monthly_total),
        "monthly_denied": dict(monthly_denied),
        "monthly_restricted": dict(monthly_restricted),
        "monthly_personal": dict(monthly_personal),
        "theme_total": dict(theme_total),
        "theme_restricted": dict(theme_restricted),
        "theme_month_total": dict(theme_month_total),
        "theme_month_restricted": dict(theme_month_restricted),
        "org_total": dict(org_total),
        "org_denied": dict(org_denied),
        "org_restricted": dict(org_restricted),
        "org_personal": dict(org_personal),
        "org_month_total": dict(org_month_total),
        "org_month_denied": dict(org_month_denied),
        "org_theme_total": dict(org_theme_total),
        "org_theme_restricted": dict(org_theme_restricted),
        "org_theme_decision": dict(org_theme_decision),
        "request_index_schema_version": REQUEST_INDEX_SCHEMA_VERSION,
        "request_samples": request_samples,
    }


def maybe_process_year(year, force=False):
    cache = load_year_cache(year)
    current_year = dt.date.today().year
    cache_version = int((cache or {}).get("request_index_schema_version", 0)) if cache else 0
    should_refresh = (
        force
        or cache is None
        or year >= current_year
        or cache_version != REQUEST_INDEX_SCHEMA_VERSION
    )

    if not should_refresh and cache is not None:
        print(f"[cache] {year}: usando cache anual")
        return cache, False

    url = DOWNLOAD_URL_TEMPLATE.format(year=year)

    with tempfile.TemporaryDirectory(prefix=f"lai_{year}_") as tmp:
        zip_path = Path(tmp) / f"Arquivos_csv_{year}.zip"
        print(f"[download] {year}: {url}")
        try:
            download_source(url, zip_path)
        except Exception as exc:
            if cache is not None:
                print(f"[warn] {year}: falha no download ({exc}); mantendo cache")
                return cache, False
            print(f"[skip] {year}: sem arquivo disponível ({exc})")
            return None, False

        print(f"[process] {year}: processando Pedidos_csv")
        payload = process_year_zip(
            year=year,
            zip_path=zip_path,
            source_url=url,
        )
        save_year_cache(payload)
        return payload, True


def identify_pf_org(org_stats):
    best = None
    best_total = -1
    for org, stat in org_stats.items():
        norm = normalize_for_match(org)
        if "policia federal" in norm or "departamento de policia federal" in norm:
            total = int(stat.get("total_requests", 0))
            if total > best_total:
                best_total = total
                best = org
    return best


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl_gz(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as gz:
        for row in rows:
            gz.write(json.dumps(row, ensure_ascii=False) + "\n")


def to_int_key_counter(mapping):
    counter = Counter()
    for key, value in (mapping or {}).items():
        try:
            month = int(key)
        except (TypeError, ValueError):
            continue
        if 1 <= month <= 12:
            counter[month] += int(value)
    return counter


def ytd_sum(counter_by_month, month_limit):
    return sum(int(counter_by_month.get(month, 0)) for month in range(1, month_limit + 1))


def split_key_month_counter(mapping):
    result = defaultdict(Counter)
    for key, value in (mapping or {}).items():
        if "|||" not in key:
            continue
        left, month_str = key.rsplit("|||", 1)
        try:
            month = int(month_str)
        except (TypeError, ValueError):
            continue
        if not (1 <= month <= 12):
            continue
        result[left][month] += int(value)
    return result


def month_label_pt(month_num):
    labels = {
        1: "janeiro",
        2: "fevereiro",
        3: "março",
        4: "abril",
        5: "maio",
        6: "junho",
        7: "julho",
        8: "agosto",
        9: "setembro",
        10: "outubro",
        11: "novembro",
        12: "dezembro",
    }
    return labels.get(int(month_num), str(month_num))


def build_report(year_payloads):
    year_payloads = sorted(year_payloads, key=lambda x: int(x["year"]))

    global_decisions = Counter()
    global_reasons = Counter()
    global_theme_total = Counter()
    global_theme_restricted = Counter()

    global_org_total = Counter()
    global_org_denied = Counter()
    global_org_restricted = Counter()
    global_org_personal = Counter()

    global_org_theme_total = Counter()
    global_org_theme_restricted = Counter()
    global_org_theme_decision = Counter()

    all_samples = []
    yearly_series = []
    monthly_series = []
    year_context = {}

    for payload in year_payloads:
        year = int(payload["year"])
        total_requests = int(payload.get("total_requests", 0))
        denied_total = int(payload.get("denied_total", 0))
        restricted_total = int(payload.get("restricted_total", 0))
        personal_total = int(payload.get("personal_restricted_total", 0))

        month_total_counter = to_int_key_counter(payload.get("monthly_total", {}))
        month_denied_counter = to_int_key_counter(payload.get("monthly_denied", {}))
        month_restricted_counter = to_int_key_counter(payload.get("monthly_restricted", {}))
        month_personal_counter = to_int_key_counter(payload.get("monthly_personal", {}))
        org_month_total_counter = split_key_month_counter(payload.get("org_month_total", {}))
        org_month_denied_counter = split_key_month_counter(payload.get("org_month_denied", {}))
        theme_month_total_counter = split_key_month_counter(payload.get("theme_month_total", {}))
        theme_month_restricted_counter = split_key_month_counter(payload.get("theme_month_restricted", {}))

        for month in range(1, 13):
            month_total = int(month_total_counter.get(month, 0))
            month_denied = int(month_denied_counter.get(month, 0))
            month_restricted = int(month_restricted_counter.get(month, 0))
            month_personal = int(month_personal_counter.get(month, 0))
            monthly_series.append(
                {
                    "year": year,
                    "month": month,
                    "month_label": month_label_pt(month),
                    "total_requests": month_total,
                    "denied_total": month_denied,
                    "restricted_total": month_restricted,
                    "personal_restricted_total": month_personal,
                    "denied_rate": (month_denied / month_total) if month_total else 0.0,
                    "restricted_rate": (month_restricted / month_total) if month_total else 0.0,
                    "personal_share_in_restricted": (
                        (month_personal / month_restricted) if month_restricted else 0.0
                    ),
                }
            )

        yearly_series.append(
            {
                "year": year,
                "total_requests": total_requests,
                "denied_total": denied_total,
                "restricted_total": restricted_total,
                "personal_restricted_total": personal_total,
                "denied_rate": (denied_total / total_requests) if total_requests else 0.0,
                "restricted_rate": (restricted_total / total_requests) if total_requests else 0.0,
                "personal_share_in_restricted": (
                    (personal_total / restricted_total) if restricted_total else 0.0
                ),
            }
        )

        year_context[year] = {
            "payload": payload,
            "monthly_total": month_total_counter,
            "monthly_denied": month_denied_counter,
            "monthly_restricted": month_restricted_counter,
            "monthly_personal": month_personal_counter,
            "org_month_total": org_month_total_counter,
            "org_month_denied": org_month_denied_counter,
            "theme_month_total": theme_month_total_counter,
            "theme_month_restricted": theme_month_restricted_counter,
        }

        merge_counter_dict(global_decisions, payload.get("decision_counts", {}))
        merge_counter_dict(global_reasons, payload.get("reason_counts", {}))
        merge_counter_dict(global_theme_total, payload.get("theme_total", {}))
        merge_counter_dict(global_theme_restricted, payload.get("theme_restricted", {}))

        merge_counter_dict(global_org_total, payload.get("org_total", {}))
        merge_counter_dict(global_org_denied, payload.get("org_denied", {}))
        merge_counter_dict(global_org_restricted, payload.get("org_restricted", {}))
        merge_counter_dict(global_org_personal, payload.get("org_personal", {}))

        merge_counter_dict(global_org_theme_total, payload.get("org_theme_total", {}))
        merge_counter_dict(global_org_theme_restricted, payload.get("org_theme_restricted", {}))
        merge_counter_dict(global_org_theme_decision, payload.get("org_theme_decision", {}))

        all_samples.extend(payload.get("request_samples", []))

    overall_total = sum(item["total_requests"] for item in yearly_series)
    overall_denied = sum(item["denied_total"] for item in yearly_series)
    overall_restricted = sum(item["restricted_total"] for item in yearly_series)
    overall_personal = sum(item["personal_restricted_total"] for item in yearly_series)

    org_stats = {}
    for org, total in global_org_total.items():
        denied = int(global_org_denied.get(org, 0))
        restricted = int(global_org_restricted.get(org, 0))
        personal = int(global_org_personal.get(org, 0))
        org_stats[org] = {
            "org": org,
            "total_requests": int(total),
            "denied_total": denied,
            "restricted_total": restricted,
            "personal_restricted_total": personal,
            "denied_rate": (denied / total) if total else 0.0,
            "restricted_rate": (restricted / total) if total else 0.0,
            "personal_rate_in_total": (personal / total) if total else 0.0,
            "personal_rate_in_restricted": (personal / restricted) if restricted else 0.0,
        }

    org_by_denied = sorted(
        org_stats.values(),
        key=lambda row: (row["denied_total"], row["restricted_total"], row["total_requests"]),
        reverse=True,
    )

    org_by_low_rate = sorted(
        [row for row in org_stats.values() if row["total_requests"] >= 1500],
        key=lambda row: (row["denied_rate"], -row["total_requests"]),
    )

    top10 = org_by_denied[:10]
    top10_names = {row["org"] for row in top10}
    pf_org = identify_pf_org(org_stats)
    top10_plus_pf = list(top10)
    if pf_org and pf_org not in top10_names:
        top10_plus_pf.append(org_stats[pf_org])

    reason_top_labels = [label for label, _ in global_reasons.most_common(8)]
    reason_top_set = set(reason_top_labels)
    reason_series = []

    top_theme_labels = [theme for theme, _ in global_theme_total.most_common(7)]
    if DEFAULT_THEME not in top_theme_labels:
        top_theme_labels.append(DEFAULT_THEME)
    top_theme_set = set(top_theme_labels)
    theme_series = []

    for payload in year_payloads:
        year = int(payload["year"])
        restricted_total = int(payload.get("restricted_total", 0))
        year_total = int(payload.get("total_requests", 0))

        year_reason_counter = Counter()
        for reason, count in payload.get("reason_counts", {}).items():
            key = reason if reason in reason_top_set else "Outros motivos"
            year_reason_counter[key] += int(count)

        for reason in reason_top_labels:
            count = int(year_reason_counter.get(reason, 0))
            reason_series.append(
                {
                    "year": year,
                    "reason": reason,
                    "count": count,
                    "share_in_restricted": (count / restricted_total) if restricted_total else 0.0,
                }
            )

        outros_count = int(year_reason_counter.get("Outros motivos", 0))
        reason_series.append(
            {
                "year": year,
                "reason": "Outros motivos",
                "count": outros_count,
                "share_in_restricted": (outros_count / restricted_total) if restricted_total else 0.0,
            }
        )

        year_theme_counter = Counter()
        for theme, count in payload.get("theme_total", {}).items():
            key = theme if theme in top_theme_set else DEFAULT_THEME
            year_theme_counter[key] += int(count)

        for theme in top_theme_labels:
            count = int(year_theme_counter.get(theme, 0))
            restricted_theme_count = int(payload.get("theme_restricted", {}).get(theme, 0))
            theme_series.append(
                {
                    "year": year,
                    "theme": theme,
                    "count": count,
                    "share_in_year": (count / year_total) if year_total else 0.0,
                    "restricted_count": restricted_theme_count,
                    "restricted_rate_in_theme": (
                        (restricted_theme_count / count) if count else 0.0
                    ),
                }
            )

    org_theme_counter = defaultdict(Counter)
    org_theme_restricted_counter = defaultdict(Counter)
    org_theme_decision_counter = defaultdict(Counter)

    for key, count in global_org_theme_total.items():
        try:
            org, theme = key.split("|||", 1)
        except ValueError:
            continue
        org_theme_counter[org][theme] += int(count)

    for key, count in global_org_theme_restricted.items():
        try:
            org, theme = key.split("|||", 1)
        except ValueError:
            continue
        org_theme_restricted_counter[org][theme] += int(count)

    for key, count in global_org_theme_decision.items():
        try:
            org, theme, decision = key.split("|||", 2)
        except ValueError:
            continue
        org_theme_decision_counter[(org, theme)][decision] += int(count)

    samples_by_org_theme = defaultdict(list)
    samples_seen_text = defaultdict(set)
    for row in all_samples:
        org = normalize_text(row.get("org", ""))
        theme = normalize_text(row.get("theme", ""))
        text_excerpt = normalize_text(row.get("text_excerpt", ""))
        if not org or not theme or not text_excerpt:
            continue
        key = (org, theme)
        if len(samples_by_org_theme[key]) >= 2:
            continue
        if text_excerpt in samples_seen_text[key]:
            continue
        id_pedido = normalize_text(row.get("id_pedido", ""))
        request_buscalai_link = normalize_text(
            row.get("request_buscalai_link", "") or build_buscalai_request_link(id_pedido)
        )
        request_public_link = normalize_text(
            row.get("request_public_link", "") or build_api_request_link(id_pedido) or request_buscalai_link
        )
        request_attachment_link = normalize_text(row.get("request_attachment_link", ""))
        if not request_attachment_link:
            request_attachment_link = normalize_text(row.get("request_link", ""))

        samples_by_org_theme[key].append(
            {
                "text_excerpt": text_excerpt,
                "id_pedido": id_pedido,
                "request_public_link": request_public_link,
                "request_buscalai_link": request_buscalai_link,
                "request_attachment_link": request_attachment_link,
            }
        )
        samples_seen_text[key].add(text_excerpt)

    org_profiles = {}
    for org_row in top10_plus_pf:
        org_name = org_row["org"]
        theme_counts = org_theme_counter.get(org_name, Counter())

        top_themes = []
        for theme, theme_total in theme_counts.most_common(6):
            restricted_theme = int(org_theme_restricted_counter.get(org_name, Counter()).get(theme, 0))
            decisions_counter = org_theme_decision_counter.get((org_name, theme), Counter())
            top_decisions = [
                {
                    "decision": decision,
                    "count": int(count),
                    "share_in_theme": (int(count) / theme_total) if theme_total else 0.0,
                }
                for decision, count in decisions_counter.most_common(3)
            ]

            top_themes.append(
                {
                    "theme": theme,
                    "total_requests": int(theme_total),
                    "restricted_total": restricted_theme,
                    "restricted_rate": (restricted_theme / theme_total) if theme_total else 0.0,
                    "top_decisions": top_decisions,
                    "examples": samples_by_org_theme.get((org_name, theme), []),
                }
            )

        org_profiles[org_name] = {
            "org": org_name,
            "summary": org_row,
            "top_themes": top_themes,
        }

    personal_series = [
        {
            "year": item["year"],
            "count": item["personal_restricted_total"],
            "share_in_restricted": item["personal_share_in_restricted"],
        }
        for item in yearly_series
    ]

    personal_org_ranking_overall = sorted(
        [
            {
                **row,
                "personal_rate_in_denied": (
                    row["personal_restricted_total"] / row["denied_total"]
                    if row["denied_total"]
                    else 0.0
                ),
            }
            for row in org_stats.values()
            if row["total_requests"] >= MIN_REQUESTS_SIGILO100_RANKING
            and row["personal_restricted_total"] > 0
        ],
        key=lambda row: (
            row["personal_rate_in_total"],
            row["personal_restricted_total"],
            row["denied_total"],
            row["total_requests"],
        ),
        reverse=True,
    )[:60]

    personal_org_ranking_by_year = []
    for payload in year_payloads:
        year = int(payload["year"])
        year_org_total = {
            normalize_text(org): int(count)
            for org, count in (payload.get("org_total", {}) or {}).items()
            if normalize_text(org)
        }
        year_org_denied = {
            normalize_text(org): int(count)
            for org, count in (payload.get("org_denied", {}) or {}).items()
            if normalize_text(org)
        }
        year_org_restricted = {
            normalize_text(org): int(count)
            for org, count in (payload.get("org_restricted", {}) or {}).items()
            if normalize_text(org)
        }
        year_org_personal = {
            normalize_text(org): int(count)
            for org, count in (payload.get("org_personal", {}) or {}).items()
            if normalize_text(org)
        }

        ranking_rows = []
        for org, total_requests in year_org_total.items():
            denied_total = int(year_org_denied.get(org, 0))
            restricted_total = int(year_org_restricted.get(org, 0))
            personal_total = int(year_org_personal.get(org, 0))
            if total_requests < MIN_REQUESTS_SIGILO100_RANKING or personal_total <= 0:
                continue
            ranking_rows.append(
                {
                    "org": org,
                    "total_requests": total_requests,
                    "denied_total": denied_total,
                    "restricted_total": restricted_total,
                    "personal_restricted_total": personal_total,
                    "personal_rate_in_total": (
                        (personal_total / total_requests) if total_requests else 0.0
                    ),
                    "personal_rate_in_denied": (
                        (personal_total / denied_total) if denied_total else 0.0
                    ),
                    "personal_rate_in_restricted": (
                        (personal_total / restricted_total) if restricted_total else 0.0
                    ),
                }
            )

        ranking_rows = sorted(
            ranking_rows,
            key=lambda row: (
                row["personal_rate_in_total"],
                row["personal_restricted_total"],
                row["denied_total"],
                row["total_requests"],
            ),
            reverse=True,
        )[:60]

        personal_org_ranking_by_year.append(
            {
                "year": year,
                "rows": ranking_rows,
            }
        )

    personal_top_orgs = sorted(
        [
            {
                **row,
                "share_in_org_restricted": (
                    row["personal_restricted_total"] / row["restricted_total"]
                    if row["restricted_total"]
                    else 0.0
                ),
            }
            for row in org_stats.values()
            if row["personal_restricted_total"] > 0
        ],
        key=lambda row: (row["personal_restricted_total"], row["restricted_total"]),
        reverse=True,
    )[:20]

    latest_year = int(yearly_series[-1]["year"])
    latest_ctx = year_context.get(latest_year, {})
    latest_month = max(
        [month for month, count in (latest_ctx.get("monthly_total", {}) or {}).items() if int(count) > 0],
        default=0,
    )
    latest_month = int(latest_month or 0)
    latest_month_name = month_label_pt(latest_month) if latest_month else ""

    previous_years = sorted([year for year in year_context.keys() if int(year) < latest_year])
    comparison_years = previous_years[-3:]

    def safe_avg(values):
        return (sum(values) / len(values)) if values else 0.0

    def rate_status(delta_pp):
        if delta_pp >= 0.8:
            return "piorando"
        if delta_pp <= -0.8:
            return "melhorando"
        return "estável"

    current_ytd = {
        "total_requests": ytd_sum(latest_ctx.get("monthly_total", {}), latest_month) if latest_month else 0,
        "denied_total": ytd_sum(latest_ctx.get("monthly_denied", {}), latest_month) if latest_month else 0,
        "restricted_total": ytd_sum(latest_ctx.get("monthly_restricted", {}), latest_month) if latest_month else 0,
        "personal_restricted_total": ytd_sum(latest_ctx.get("monthly_personal", {}), latest_month) if latest_month else 0,
    }
    current_ytd["denied_rate"] = (
        (current_ytd["denied_total"] / current_ytd["total_requests"])
        if current_ytd["total_requests"]
        else 0.0
    )
    current_ytd["restricted_rate"] = (
        (current_ytd["restricted_total"] / current_ytd["total_requests"])
        if current_ytd["total_requests"]
        else 0.0
    )

    baseline_rows = []
    for year in comparison_years:
        ctx = year_context.get(year, {})
        total = ytd_sum(ctx.get("monthly_total", {}), latest_month) if latest_month else 0
        denied = ytd_sum(ctx.get("monthly_denied", {}), latest_month) if latest_month else 0
        restricted = ytd_sum(ctx.get("monthly_restricted", {}), latest_month) if latest_month else 0
        personal = ytd_sum(ctx.get("monthly_personal", {}), latest_month) if latest_month else 0
        baseline_rows.append(
            {
                "year": int(year),
                "total_requests": int(total),
                "denied_total": int(denied),
                "restricted_total": int(restricted),
                "personal_restricted_total": int(personal),
                "denied_rate": (denied / total) if total else 0.0,
                "restricted_rate": (restricted / total) if total else 0.0,
            }
        )

    baseline = {
        "years": [row["year"] for row in baseline_rows],
        "total_requests_avg": safe_avg([row["total_requests"] for row in baseline_rows]),
        "denied_total_avg": safe_avg([row["denied_total"] for row in baseline_rows]),
        "restricted_total_avg": safe_avg([row["restricted_total"] for row in baseline_rows]),
        "personal_restricted_total_avg": safe_avg(
            [row["personal_restricted_total"] for row in baseline_rows]
        ),
        "denied_rate_avg": safe_avg([row["denied_rate"] for row in baseline_rows]),
        "restricted_rate_avg": safe_avg([row["restricted_rate"] for row in baseline_rows]),
    }

    denied_delta_pp = (current_ytd["denied_rate"] - baseline["denied_rate_avg"]) * 100
    restricted_delta_pp = (
        (current_ytd["restricted_rate"] - baseline["restricted_rate_avg"]) * 100
    )

    top_denial_rate_current_year = []
    if latest_month:
        current_org_total_ytd = {}
        current_org_denied_ytd = {}
        for org, month_counter in (latest_ctx.get("org_month_total", {}) or {}).items():
            current_org_total_ytd[org] = int(ytd_sum(month_counter, latest_month))
        for org, month_counter in (latest_ctx.get("org_month_denied", {}) or {}).items():
            current_org_denied_ytd[org] = int(ytd_sum(month_counter, latest_month))

        for org, total_requests in current_org_total_ytd.items():
            if total_requests < MIN_REQUESTS_TOP_DENIAL_RATE_CURRENT_YEAR:
                continue
            denied_total = int(current_org_denied_ytd.get(org, 0))
            denied_rate = (denied_total / total_requests) if total_requests else 0.0
            top_denial_rate_current_year.append(
                {
                    "org": org,
                    "total_requests": int(total_requests),
                    "denied_total": int(denied_total),
                    "denied_rate": denied_rate,
                }
            )

    top_denial_rate_current_year = sorted(
        top_denial_rate_current_year,
        key=lambda row: (row["denied_rate"], row["denied_total"], row["total_requests"]),
        reverse=True,
    )[:5]

    org_spikes = []
    if latest_month and comparison_years:
        current_org_denied = {
            org: int(counter.get(latest_month, 0))
            for org, counter in (latest_ctx.get("org_month_denied", {}) or {}).items()
        }
        current_org_total = {
            org: int(counter.get(latest_month, 0))
            for org, counter in (latest_ctx.get("org_month_total", {}) or {}).items()
        }

        for org, current_denied in current_org_denied.items():
            if current_denied < 12:
                continue

            baseline_denied_values = []
            baseline_total_values = []
            for year in comparison_years:
                ctx = year_context.get(year, {})
                baseline_denied_values.append(
                    int((ctx.get("org_month_denied", {}).get(org, Counter())).get(latest_month, 0))
                )
                baseline_total_values.append(
                    int((ctx.get("org_month_total", {}).get(org, Counter())).get(latest_month, 0))
                )

            baseline_denied = safe_avg(baseline_denied_values)
            baseline_total = safe_avg(baseline_total_values)
            if baseline_denied < 6:
                continue

            delta_abs = current_denied - baseline_denied
            lift_ratio = ((current_denied / baseline_denied) - 1) if baseline_denied else 0.0
            current_total = int(current_org_total.get(org, 0))
            current_rate = (current_denied / current_total) if current_total else 0.0
            baseline_rate = (baseline_denied / baseline_total) if baseline_total else 0.0

            if current_denied >= baseline_denied * 1.35 and delta_abs >= 8:
                org_spikes.append(
                    {
                        "org": org,
                        "current_denied": int(current_denied),
                        "baseline_denied_avg": baseline_denied,
                        "delta_abs": delta_abs,
                        "lift_ratio": lift_ratio,
                        "current_denied_rate": current_rate,
                        "baseline_denied_rate_avg": baseline_rate,
                    }
                )

    org_spikes = sorted(
        org_spikes,
        key=lambda row: (row["lift_ratio"], row["delta_abs"]),
        reverse=True,
    )[:8]

    def theme_ytd_map(theme_month_map, month_limit):
        out = Counter()
        for theme, counter in (theme_month_map or {}).items():
            out[theme] += int(ytd_sum(counter, month_limit))
        return out

    theme_worsening = []
    if latest_month and comparison_years:
        current_theme_total = theme_ytd_map(latest_ctx.get("theme_month_total", {}), latest_month)
        current_theme_restricted = theme_ytd_map(
            latest_ctx.get("theme_month_restricted", {}), latest_month
        )

        baseline_theme_total_by_year = []
        baseline_theme_restricted_by_year = []
        for year in comparison_years:
            ctx = year_context.get(year, {})
            baseline_theme_total_by_year.append(
                theme_ytd_map(ctx.get("theme_month_total", {}), latest_month)
            )
            baseline_theme_restricted_by_year.append(
                theme_ytd_map(ctx.get("theme_month_restricted", {}), latest_month)
            )

        all_themes = set(current_theme_total.keys())
        for counter in baseline_theme_total_by_year:
            all_themes.update(counter.keys())

        for theme in all_themes:
            curr_total = int(current_theme_total.get(theme, 0))
            curr_restricted = int(current_theme_restricted.get(theme, 0))
            if curr_total < 120:
                continue

            base_total_values = [int(counter.get(theme, 0)) for counter in baseline_theme_total_by_year]
            base_restricted_values = [
                int(counter.get(theme, 0)) for counter in baseline_theme_restricted_by_year
            ]
            base_total = safe_avg(base_total_values)
            base_restricted = safe_avg(base_restricted_values)
            if base_total < 120:
                continue

            curr_rate = (curr_restricted / curr_total) if curr_total else 0.0
            base_rate = (base_restricted / base_total) if base_total else 0.0
            delta_pp = (curr_rate - base_rate) * 100

            if delta_pp >= 1.0:
                theme_worsening.append(
                    {
                        "theme": theme,
                        "current_restricted_rate": curr_rate,
                        "baseline_restricted_rate_avg": base_rate,
                        "delta_pp": delta_pp,
                        "current_total_requests": curr_total,
                        "current_restricted_total": curr_restricted,
                    }
                )

    theme_worsening = sorted(
        theme_worsening,
        key=lambda row: (row["delta_pp"], row["current_restricted_total"]),
        reverse=True,
    )[:8]

    monitoring = {
        "latest_year": latest_year,
        "latest_month": latest_month,
        "latest_month_label": latest_month_name,
        "is_partial_year": bool(latest_month and latest_month < 12),
        "comparison_years": comparison_years,
        "current_ytd": current_ytd,
        "baseline_ytd_avg": baseline,
        "denied_rate_delta_pp": denied_delta_pp,
        "restricted_rate_delta_pp": restricted_delta_pp,
        "denied_rate_status": rate_status(denied_delta_pp) if baseline_rows else "sem base",
        "restricted_rate_status": (
            rate_status(restricted_delta_pp) if baseline_rows else "sem base"
        ),
        "top_denial_rate_current_year": top_denial_rate_current_year,
        "top_denial_rate_min_requests": MIN_REQUESTS_TOP_DENIAL_RATE_CURRENT_YEAR,
        "org_spikes": org_spikes,
        "theme_worsening": theme_worsening,
    }

    sample_count = len(all_samples)
    top_org_names = [row["org"] for row in top10_plus_pf]
    top_org_name_set = set(top_org_names)
    top_org_rows_in_search = sum(
        1 for row in all_samples if row.get("org", "") in top_org_name_set
    )
    period_start_year = int(yearly_series[0]["year"]) if yearly_series else int(START_YEAR_DEFAULT)
    source_files = ["Pedidos_csv_YYYY.csv"]
    if "Filtrado" in DOWNLOAD_URL_TEMPLATE:
        source_files.append("PedidosLinkArquivo_csv_YYYY.csv (link do pedido/anexo no BuscaLAI)")
    request_link_rule = (
        "Cada pedido negado recebe URL de detalhe no formato "
        "https://api-laibr.cgu.gov.br/buscar-pedidos/{IdPedido}; também guarda "
        "https://buscalai.cgu.gov.br/busca/{IdPedido} como alternativa no portal. "
        "Quando houver registro no arquivo PedidosLinkArquivo, mantém URL de anexo."
        if "Filtrado" in DOWNLOAD_URL_TEMPLATE
        else (
            "Cada pedido negado recebe URL de detalhe no formato "
            "https://api-laibr.cgu.gov.br/buscar-pedidos/{IdPedido}, com opção de portal em "
            "https://buscalai.cgu.gov.br/busca/{IdPedido}. "
            "Nesta fonte não há tabela PedidosLinkArquivo para anexos."
        )
    )
    methodology = {
        "unit_of_analysis": "pedido individual da base Pedidos_csv",
        "data_scope": SOURCE_LABEL,
        "source_files": source_files,
        "period_rule": (
            f"Série anual de {period_start_year} até o ano corrente da execução. "
            "O ano corrente é parcial até 31 de dezembro."
        ),
        "decision_rules": {
            "denied_total": "Decisão canônica igual a 'Acesso Negado'.",
            "restricted_total": (
                "Decisão canônica em {'Acesso Negado', "
                "'Acesso Parcialmente Concedido'}."
            ),
            "decision_canonicalization": (
                "Padronização do campo Decisao para um conjunto canônico de respostas "
                "e agrupamento de textos livres longos em 'Outros (texto livre)'."
            ),
        },
        "negative_reason_rules": {
            "primary_source": "EspecificacaoDecisao",
            "fallback_source": "MotivoNegativaAcesso",
            "normalization": (
                "Padronização textual (acentos, espaços e variações comuns) para "
                "consolidar motivos equivalentes."
            ),
        },
        "personal_info_rule": {
            "description": (
                "Um caso entra em 'informação pessoal' quando está em restrição "
                "(negado ou parcial) e o motivo contém termos de privacidade."
            ),
            "keywords": list(PERSONAL_REASON_KEYWORDS),
        },
        "theme_classification": {
            "text_fields_used": [
                "AssuntoPedido",
                "ResumoSolicitacao",
                "DetalhamentoSolicitacao",
            ],
            "text_build_rule": "Concatenação desses 3 campos, com limpeza de espaços.",
            "assignment_rule": (
                "Classificação por regras de palavras-chave; vale o primeiro tema "
                "cujas palavras aparecem no texto."
            ),
            "default_theme": DEFAULT_THEME,
            "themes": [
                {"theme": theme, "keywords": keywords}
                for theme, keywords in THEME_RULES
            ],
        },
        "sampling_for_search": {
            "purpose": "Busca textual completa de pedidos negados no painel.",
            "method": (
                "Cobertura total de pedidos com decisão canônica 'Acesso Negado' "
                "em todos os órgãos e anos da série."
            ),
            "decision_scope": "Somente pedidos com decisão canônica 'Acesso Negado'.",
            "text_fields_stored": {
                "request": [
                    "AssuntoPedido",
                    "ResumoSolicitacao (quando disponível)",
                    "DetalhamentoSolicitacao (quando disponível)",
                ],
                "response": [
                    "Resposta (quando disponível)",
                    "DetalhamentoDecisao (quando disponível)",
                ],
            },
            "request_link_rule": request_link_rule,
            "sample_count": sample_count,
            "top_org_rows_in_search": top_org_rows_in_search,
            "top_orgs_covered": top_org_names,
        },
        "ranking_rules": {
            "top_denials": (
                "Ranking ordenado por quantidade absoluta de negativas "
                "(desempate por restrições e depois por volume total)."
            ),
            "lowest_denial_high_volume": (
                "Ranking de menor taxa de negativa considera apenas órgãos com "
                "pelo menos 1.500 pedidos na série."
            ),
            "sigilo100_proportional": (
                "Ranking proporcional de informação pessoal ordena por "
                "casos de informação pessoal dividido pelo total de pedidos, "
                f"com mínimo de {MIN_REQUESTS_SIGILO100_RANKING} pedidos no recorte."
            ),
            "pf_rule": (
                "A Polícia Federal é incluída no bloco principal mesmo fora do top 10."
            ),
        },
        "monitoring_rules": {
            "top_denial_rate_current_year": (
                "Top 5 por taxa de negativa no ano vigente = negativas acumuladas no ano "
                "até o mês mais recente dividido pelo total de pedidos no mesmo período, "
                f"com filtro mínimo de {MIN_REQUESTS_TOP_DENIAL_RATE_CURRENT_YEAR} pedidos."
            ),
            "recent_spike": (
                "Spike recente por órgão = mês mais recente do ano atual comparado à média "
                "do mesmo mês nos últimos até 3 anos (com filtros mínimos de volume)."
            ),
            "government_diagnosis": (
                "Diagnóstico YTD compara o ano atual até o mês mais recente com a média "
                "dos mesmos meses nos últimos até 3 anos."
            ),
            "theme_worsening": (
                "Piora por tema considera aumento da taxa de restrição no YTD contra a base "
                "histórica comparável de meses."
            ),
        },
        "incremental_update_rule": (
            "Cache anual incremental: anos passados ficam congelados; "
            "ano corrente é reprocessado em cada atualização."
        ),
    }

    sample_file_rel = f"./data/{SAMPLES_FILE.name}"

    report = {
        "generated_at": now_iso(),
        "source": {
            "source_id": SOURCE_ID,
            "source_label": SOURCE_LABEL,
            "portal_url": DOWNLOAD_PORTAL_URL,
            "download_url_template": DOWNLOAD_URL_TEMPLATE,
            "precedentes_url": PRECEDENTES_URL,
            "years_covered": [item["year"] for item in yearly_series],
        },
        "overall": {
            "total_requests": int(overall_total),
            "denied_total": int(overall_denied),
            "restricted_total": int(overall_restricted),
            "personal_restricted_total": int(overall_personal),
            "denied_rate": (overall_denied / overall_total) if overall_total else 0.0,
            "restricted_rate": (overall_restricted / overall_total) if overall_total else 0.0,
            "personal_share_in_restricted": (
                (overall_personal / overall_restricted) if overall_restricted else 0.0
            ),
        },
        "series": yearly_series,
        "monthly_series": monthly_series,
        "reason_series": reason_series,
        "theme_series": theme_series,
        "top_reasons": [
            {"reason": reason, "count": int(count)}
            for reason, count in global_reasons.most_common(15)
        ],
        "top_themes": [
            {
                "theme": theme,
                "count": int(count),
                "restricted_count": int(global_theme_restricted.get(theme, 0)),
                "restricted_rate_in_theme": (
                    (int(global_theme_restricted.get(theme, 0)) / int(count)) if int(count) else 0.0
                ),
            }
            for theme, count in global_theme_total.most_common(15)
        ],
        "org_ranking": org_by_denied[:60],
        "org_lowest_denial_high_volume": org_by_low_rate[:15],
        "org_top10_plus_pf": top10_plus_pf,
        "org_profiles": org_profiles,
        "personal_info": {
            "series": personal_series,
            "top_orgs": personal_top_orgs,
            "sigilo100_min_requests": MIN_REQUESTS_SIGILO100_RANKING,
            "org_ranking_overall": personal_org_ranking_overall,
            "org_ranking_by_year": personal_org_ranking_by_year,
        },
        "monitoring": monitoring,
        "search_dashboard": {
            "sample_file": sample_file_rel,
            "sample_count": sample_count,
            "sample_method": (
                "cobertura total dos pedidos negados (sem amostragem)"
            ),
            "decision_scope": "Acesso Negado",
            "top_orgs_covered": top_org_names,
            "top_org_rows_in_search": top_org_rows_in_search,
            "presets": SEARCH_PRESETS,
            "available_themes": [theme for theme, _ in global_theme_total.most_common(20)],
        },
        "methodology": methodology,
    }

    metadata = {
        "updated_at": report["generated_at"],
        "years_covered": report["source"]["years_covered"],
        "overall": report["overall"],
        "source": report["source"],
        "sample_count": sample_count,
    }

    return report, metadata, all_samples


def run(force=False, start_year=START_YEAR_DEFAULT, end_year=None):
    ensure_dirs()

    current_year = dt.date.today().year
    final_end_year = end_year if end_year is not None else current_year

    years = list(range(int(start_year), int(final_end_year) + 1))
    print(f"[info] anos avaliados: {years[0]}-{years[-1]}")

    year_payloads = []
    refreshed = 0

    for year in years:
        payload, was_refreshed = maybe_process_year(
            year,
            force=force,
        )
        if payload is None:
            continue
        year_payloads.append(payload)
        if was_refreshed:
            refreshed += 1

    if not year_payloads:
        raise RuntimeError("Nenhum ano disponível para gerar o painel LAI.")

    report, metadata, samples = build_report(year_payloads)
    metadata["years_refreshed_in_run"] = refreshed
    metadata["build_notes"] = (
        "Cache anual incremental: anos anteriores ficam congelados; "
        "ano corrente é reprocessado a cada execução."
    )

    write_json(REPORT_FILE, report)
    write_json(METADATA_FILE, metadata)
    # Samples (request_samples*.jsonl.gz) intentionally not written — dashboard now ships
    # only aggregated statistics. Re-enable by uncommenting if individual-request search is needed.
    # write_jsonl_gz(SAMPLES_FILE, samples)

    print(f"[ok] relatório salvo em: {REPORT_FILE}")
    print(f"[ok] metadata salva em: {METADATA_FILE}")
    print(f"[ok] samples não gravados (modo apenas-estatísticas)")
    print(f"[ok] anos processados/reutilizados: {len(year_payloads)}")
    print(f"[ok] anos atualizados nesta execução: {refreshed}")


def main():
    parser = argparse.ArgumentParser(
        description="Build incremental da dashboard LAI para GitHub Pages."
    )
    parser.add_argument("--force", action="store_true", help="Reprocessa todos os anos.")
    parser.add_argument(
        "--start-year",
        type=int,
        default=START_YEAR_DEFAULT,
        help=f"Ano inicial (padrão: {START_YEAR_DEFAULT}).",
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=None,
        help="Ano final (padrão: ano atual).",
    )

    args = parser.parse_args()
    run(force=args.force, start_year=args.start_year, end_year=args.end_year)


if __name__ == "__main__":
    main()
