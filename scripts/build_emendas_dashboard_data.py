#!/usr/bin/env python3
import argparse
import csv
import datetime as dt
import gzip
import io
import json
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path


DOWNLOAD_URL = "https://portaldatransparencia.gov.br/download-de-dados/emendas-parlamentares/UNICO"
FALLBACK_ZIP_URL = (
    "https://dadosabertos-download.cgu.gov.br/PortalDaTransparencia/saida/"
    "emendas-parlamentares/EmendasParlamentares.zip"
)
DOCUMENTS_DOWNLOAD_URL_TEMPLATE = (
    "https://portaldatransparencia.gov.br/download-de-dados/"
    "emendas-parlamentares-documentos/{year}"
)
DOCUMENTS_FALLBACK_ZIP_URL_TEMPLATE = (
    "https://dadosabertos-download.cgu.gov.br/PortalDaTransparencia/saida/"
    "emendas-parlamentares-documentos/{year}_EmendasParlamentaresPorDocumento.zip"
)
APOIAMENTO_DOWNLOAD_URL_TEMPLATE = (
    "https://portaldatransparencia.gov.br/download-de-dados/"
    "apoiamento-emendas-parlamentares-documentos/{year}"
)
APOIAMENTO_FALLBACK_ZIP_URL_TEMPLATE = (
    "https://dadosabertos-download.cgu.gov.br/PortalDaTransparencia/saida/"
    "apoiamento-emendas-parlamentares-documentos/{year}_ApoiamentoEmendasParlamentares.zip"
)
EXECUCAO_ANO_CORRENTE_ENDPOINT_TEMPLATE = (
    "https://portaldatransparencia.gov.br/emendas/execucao-despesas-ano-corrente/"
    "resultadoGrafico?ano={year}"
)
CAMARA_DEPUTADOS_URL = (
    "https://dadosabertos.camara.leg.br/api/v2/deputados"
    "?itens=700&ordem=ASC&ordenarPor=nome"
)
SENADO_LISTA_URL = "https://legis.senado.leg.br/dadosabertos/senador/lista/atual"
SIOP_PANEL_URL = (
    "https://www1.siop.planejamento.gov.br/QvAJAXZfc/opendoc.htm"
    "?document=IAS%2FExecucao_Orcamentaria.qvw&host=QVS%40pqlk04&anonymous=true"
)
SIOP_DEFAULT_RP_FILTERS = (
    "6 - Emendas Individuais",
    "7 - Emendas de Bancada Estadual",
    "8 - Emendas de Comissão",
)
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)

ROOT_DIR = Path(__file__).resolve().parents[1]
DASH_DIR = ROOT_DIR / "emendas"
DATA_DIR = DASH_DIR / "data"
STATE_DIR = DATA_DIR / "state"
REPORT_FILE = DATA_DIR / "report_data.json"
META_FILE = DATA_DIR / "metadata.json"
HISTORY_FILE = DATA_DIR / "daily_history.json"
SIOP_HISTORY_FILE = DATA_DIR / "siop_daily_history.json"
STATE_FILE = STATE_DIR / "latest_aggregates.json.gz"


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def normalize_text(value):
    if value is None:
        return ""
    text = str(value).replace("\ufeff", "").strip()
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def normalize_column(value):
    text = normalize_text(value)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text


def normalize_person_name(value):
    text = normalize_text(value)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.upper()
    text = re.sub(r"[^A-Z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_ddmmyyyy(value):
    text = normalize_text(value)
    if not text:
        return None
    try:
        return dt.datetime.strptime(text, "%d/%m/%Y").date()
    except ValueError:
        return None


def parse_currency(value):
    text = normalize_text(value)
    if not text:
        return Decimal("0")
    text = text.replace("R$", "").replace(" ", "")
    text = text.replace(".", "").replace(",", ".")
    text = re.sub(r"[^0-9.\-]", "", text)
    if text in {"", "-", ".", "-."}:
        return Decimal("0")
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return Decimal("0")


def to_float(value):
    if isinstance(value, Decimal):
        return float(value.quantize(Decimal("0.01")))
    return float(value)


def parse_brl_number(value):
    return to_float(parse_currency(value))


def download_with_urllib(url, target):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=300) as resp:
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
        except Exception as exc:
            errors.append(str(exc))
            target.unlink(missing_ok=True)
    raise RuntimeError("Falha no download: " + " | ".join(errors))


def fetch_headers(url):
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return {
                "etag": normalize_text(resp.headers.get("ETag")),
                "last_modified": normalize_text(resp.headers.get("Last-Modified")),
                "content_length": normalize_text(resp.headers.get("Content-Length")),
                "final_url": normalize_text(resp.geturl()),
            }
    except Exception:
        return {"etag": "", "last_modified": "", "content_length": "", "final_url": ""}


def fetch_json_url(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        payload = resp.read().decode("utf-8")
    return json.loads(payload)


def load_json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def load_state():
    if not STATE_FILE.exists():
        return {
            "snapshot_date": "",
            "total_empenhado": 0.0,
            "author_totals": {},
            "destination_totals": {},
            "author_destination_totals": {},
        }
    with gzip.open(STATE_FILE, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def save_state(payload):
    with gzip.open(STATE_FILE, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)


def resolve_party(author_name, party_lookup):
    name = normalize_person_name(author_name)
    if not name:
        return "Nao identificado"
    if "BANCADA" in name:
        return "Bancada"
    if name.startswith("COM ") or name.startswith("COM.") or "COMISSAO" in name:
        return "Comissao"
    if "RELATOR" in name:
        return "Relatoria"
    return party_lookup.get(name) or "Nao identificado"


def classify_supporter_group(supporter_name):
    name = normalize_person_name(supporter_name)
    if not name:
        return "Nao identificado"
    if "BANCADA" in name:
        return "Bancada"
    if name.startswith("COM ") or name.startswith("COM.") or "COMISSAO" in name:
        return "Comissao"
    if "RELATOR" in name:
        return "Relatoria"
    return "Parlamentar"


def fetch_party_lookup():
    lookup = {}
    metadata = {"camara_mapeados": 0, "senado_mapeados": 0}

    try:
        data = fetch_json_url(CAMARA_DEPUTADOS_URL)
        for row in data.get("dados", []):
            name = normalize_person_name(row.get("nome", ""))
            party = normalize_text(row.get("siglaPartido", ""))
            if name and party:
                lookup[name] = party
                metadata["camara_mapeados"] += 1
    except Exception:
        pass

    try:
        req = urllib.request.Request(SENADO_LISTA_URL, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=180) as resp:
            xml_payload = resp.read()
        root = ET.fromstring(xml_payload)
        for parlamentar in root.findall(".//Parlamentar"):
            name = normalize_person_name(parlamentar.findtext(".//NomeParlamentar", default=""))
            party = normalize_text(parlamentar.findtext(".//SiglaPartidoParlamentar", default=""))
            if name and party and name not in lookup:
                lookup[name] = party
                metadata["senado_mapeados"] += 1
    except Exception:
        pass

    return lookup, metadata


def split_author_party_key(key):
    if "|||" not in key:
        return key, "Nao identificado"
    author, party = key.split("|||", 1)
    return author, party


def pick_primary_member(names):
    normalized = [name for name in names if name.lower().endswith(".csv")]
    for name in normalized:
        lower = name.lower()
        if "emendasparlamentares" in lower and "porfavorecido" not in lower and "convenios" not in lower:
            return name
    if normalized:
        return normalized[0]
    raise RuntimeError("ZIP sem CSV principal de emendas.")


def safe_share(value, total):
    if not total:
        return 0.0
    return float(value / total)


def sort_top(mapping, limit):
    return sorted(mapping.items(), key=lambda item: item[1], reverse=True)[:limit]


def apply_diff(curr_map, prev_map):
    positive = {}
    negative_total = Decimal("0")
    positive_total = Decimal("0")
    for key, curr in curr_map.items():
        prev = Decimal(str(prev_map.get(key, 0.0)))
        delta = curr - prev
        if delta > Decimal("0.005"):
            positive[key] = delta
            positive_total += delta
        elif delta < Decimal("-0.005"):
            negative_total += delta
    return positive, positive_total, negative_total


def fetch_execucao_ano_corrente(year):
    url = EXECUCAO_ANO_CORRENTE_ENDPOINT_TEMPLATE.format(year=year)
    try:
        payload = fetch_json_url(url)
    except Exception:
        return {"year": year, "endpoint_url": url, "stages": [], "stage_values": {}}

    stages = []
    stage_values = {}
    for row in payload:
        valores = row.get("valores", []) if isinstance(row, dict) else []
        if len(valores) < 2:
            continue
        stage = normalize_text(valores[0])
        value = parse_currency(valores[1])
        if not stage:
            continue
        amount = to_float(value)
        stages.append({"stage": stage, "value": amount})
        stage_values[normalize_column(stage)] = amount

    return {
        "year": year,
        "endpoint_url": url,
        "stages": stages,
        "stage_values": stage_values,
    }


def parse_siop_dates_from_text(body_text):
    last_update = ""
    base_siafi_date = ""
    m_last = re.search(
        r"Última atualização realizada em\s*(\d{1,2}/\d{1,2}/\d{4}(?:\s+\d{1,2}:\d{2}:\d{2})?)",
        body_text or "",
        flags=re.IGNORECASE,
    )
    if m_last:
        last_update = normalize_text(m_last.group(1))

    m_siafi = re.search(
        r"Dados referentes à Base SIAFI de\s*(\d{1,2}/\d{1,2}/\d{4})",
        body_text or "",
        flags=re.IGNORECASE,
    )
    if m_siafi:
        base_siafi_date = normalize_text(m_siafi.group(1))
    return last_update, base_siafi_date


def navigate_siop_to_emendas(driver, wait):
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC

    driver.get(SIOP_PANEL_URL)

    # Entrar na home do painel.
    wait.until(
        EC.element_to_be_clickable((By.XPATH, "/html/body/div[5]/div/div[10]/div[2]/table/tbody/tr/td"))
    ).click()
    time.sleep(6)

    # Tile textual (mais estável que clique por coordenada).
    wait.until(EC.element_to_be_clickable((By.XPATH, "//td[contains(@title,'Emendas Parlamentares')]"))).click()
    time.sleep(6)


def _try_click_xpath_candidates(driver, xpaths):
    from selenium.webdriver.common.by import By

    for xp in xpaths:
        nodes = driver.find_elements(By.XPATH, xp)
        for node in nodes:
            try:
                node.click()
                return True
            except Exception:
                try:
                    driver.execute_script("arguments[0].click();", node)
                    return True
                except Exception:
                    continue
    return False


def open_siop_step2_group(driver, wait, group_label="Por Partido", attempts=8):
    from selenium.webdriver.common.by import By

    tab_candidates = [
        "//td[contains(., 'Passo 2 - Visualize os Resultados')]",
        "//*[normalize-space(text())='Passo 2 - Visualize os Resultados']",
    ]
    group_candidates = [
        f"//td[normalize-space(text())='{group_label}']",
        f"//*[normalize-space(text())='{group_label}']",
    ]

    for _ in range(max(1, attempts)):
        _try_click_xpath_candidates(driver, tab_candidates)
        time.sleep(1.2)
        clicked_group = _try_click_xpath_candidates(driver, group_candidates)
        time.sleep(1.4)
        if not clicked_group:
            continue

        grid_ready = driver.find_elements(
            By.XPATH,
            "//div[contains(@class,'QvFrame') and @objtype='Grid' and contains(.,'Nro. Emenda')]",
        )
        if grid_ready:
            return

    raise RuntimeError(f"não foi possível abrir '{group_label}' no SIOP")


def open_siop_step1_filters(driver, wait, attempts=6):
    candidates = [
        "//td[contains(., 'Passo 1 - Selecione os Filtros')]",
        "//*[normalize-space(text())='Passo 1 - Selecione os Filtros']",
    ]

    for _ in range(max(1, attempts)):
        clicked = _try_click_xpath_candidates(driver, candidates)
        if not clicked:
            time.sleep(0.8)
            continue
        try:
            wait.until(
                lambda drv: drv.execute_script(
                    """
                    return !![...document.querySelectorAll('div.QvCaption')]
                      .find((el) => (el.getAttribute('title') || '').trim() === 'Ano');
                    """
                )
            )
            time.sleep(0.9)
            return
        except Exception:
            time.sleep(0.8)

    raise RuntimeError("não foi possível abrir 'Passo 1 - Selecione os Filtros' no SIOP")


def clear_siop_filters(driver):
    from selenium.webdriver.common.by import By

    try:
        clear_btn = driver.find_element(By.XPATH, "//*[@title='Limpar todos os filtros desbloqueados.']")
        try:
            clear_btn.click()
        except Exception:
            driver.execute_script("arguments[0].click();", clear_btn)
        time.sleep(1.1)
        return True
    except Exception:
        return False


def click_siop_filter_value(driver, caption_title, option_value, max_attempts=8):
    from selenium.common.exceptions import (
        ElementClickInterceptedException,
        NoSuchElementException,
        StaleElementReferenceException,
    )
    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.webdriver.common.by import By

    frame_xpath = (
        "//div[contains(@class,'QvFrame')]"
        f"[.//div[contains(@class,'QvCaption') and normalize-space(@title)='{caption_title}']]"
    )
    option_xpath = (
        ".//div[contains(@class,'QvOptional') and "
        f"(@title='{option_value}' or normalize-space(.)='{option_value}')]"
    )
    fallback_xpath = f".//*[normalize-space(text())='{option_value}']"

    for _ in range(max(1, max_attempts)):
        try:
            frame = driver.find_element(By.XPATH, frame_xpath)
            nodes = frame.find_elements(By.XPATH, option_xpath)
            if not nodes:
                nodes = frame.find_elements(By.XPATH, fallback_xpath)
            if not nodes:
                time.sleep(0.5)
                continue

            node = nodes[0]
            ActionChains(driver).move_to_element(node).pause(0.05).click(node).perform()
            time.sleep(0.75)
            return True
        except (
            ElementClickInterceptedException,
            NoSuchElementException,
            StaleElementReferenceException,
        ):
            time.sleep(0.55)
        except Exception:
            time.sleep(0.55)
    return False


def normalize_loose_text(value):
    text = normalize_text(value)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    return re.sub(r"\s+", " ", text).strip()


def siop_selection_confirmed(driver, year, rp_label):
    from selenium.webdriver.common.by import By

    body_text = driver.find_element(By.TAG_NAME, "body").text
    normalized = normalize_loose_text(body_text)
    year_token = normalize_loose_text(f"Emenda Parlamentar - Ano {year}")
    rp_token = normalize_loose_text(f"Emenda Parlamentar - Resultado Primário {rp_label}")
    return year_token in normalized and rp_token in normalized


def apply_siop_single_rp_filter(driver, wait, year, rp_label, retries=8):
    for _ in range(max(1, retries)):
        open_siop_step1_filters(driver, wait)
        clear_siop_filters(driver)

        year_ok = click_siop_filter_value(driver, "Ano", str(year))
        rp_ok = click_siop_filter_value(driver, "Resultado Primário (RP)", rp_label)
        if year_ok and rp_ok and siop_selection_confirmed(driver, year, rp_label):
            return True
        time.sleep(0.8)
    return False


def extract_siop_totals_from_grid(driver):
    totals = driver.execute_script(
        """
        function parsePx(v){
          const m = String(v || '').match(/(-?[0-9\\.]+)px/i);
          return m ? parseFloat(m[1]) : null;
        }

        const frame = [...document.querySelectorAll('.QvFrame[objtype="Grid"]')]
          .find((f) => getComputedStyle(f).display !== 'none' && (f.innerText || '').includes('Nro. Emenda'));
        if(!frame){
          return { error: 'grid de emendas não encontrado', totals: {} };
        }

        const topCells = [];
        for(const el of frame.querySelectorAll('[title]')){
          const title = (el.getAttribute('title') || '').trim();
          if(!title || title === 'Resize column') continue;
          const left = parsePx(el.style.left);
          const top = parsePx(el.style.top);
          const height = parsePx(el.style.height);
          if(left === null || top === null || height === null) continue;
          if(top !== 0 || height !== 39) continue;
          topCells.push({title, left});
        }

        const cols = [
          'Dotação Inicial Emenda',
          'Dotação Atual Emenda',
          'Empenhado',
          'Liquidado',
          'Pago',
        ];
        const out = {};
        for(const col of cols){
          const header = topCells.find((cell) => cell.title === col);
          if(!header){
            out[col] = '';
            continue;
          }
          const valueCell = topCells.find((cell) =>
            Math.abs(cell.left - header.left) <= 1 &&
            cell.title !== col &&
            /^[0-9\\.,]+$/.test(cell.title)
          );
          out[col] = valueCell ? valueCell.title : '';
        }

        return { error: '', totals: out };
        """
    )

    if not totals or totals.get("error"):
        raise RuntimeError((totals or {}).get("error") or "falha ao extrair totais do grid SIOP")

    raw = totals.get("totals", {})
    return {
        "dotacao_inicial_emenda": parse_brl_number(raw.get("Dotação Inicial Emenda", "")),
        "dotacao_atual_emenda": parse_brl_number(raw.get("Dotação Atual Emenda", "")),
        "empenhado": parse_brl_number(raw.get("Empenhado", "")),
        "liquidado": parse_brl_number(raw.get("Liquidado", "")),
        "pago": parse_brl_number(raw.get("Pago", "")),
    }


def extract_siop_totals_with_retry(driver, wait, group_label="Por Partido", attempts=6):
    last_error = ""
    for _ in range(max(1, attempts)):
        try:
            open_siop_step2_group(driver, wait, group_label=group_label)
            return extract_siop_totals_from_grid(driver)
        except Exception as exc:
            last_error = normalize_text(str(exc))[:220]
            time.sleep(1.0)
    raise RuntimeError(last_error or "falha ao extrair totais do grid SIOP")


def extract_siop_visible_chunk(driver):
    js = """
        function parsePx(v){
          const m = String(v || "").match(/(-?[0-9\\.]+)px/i);
          return m ? parseFloat(m[1]) : null;
        }

        function collectHeaders(pageEl){
          const out = [];
          for(const el of pageEl.children){
            const title = (el.getAttribute("title") || "").trim();
            if(!title || title === "Resize column" || title === "Total") continue;
            const left = parsePx(el.style.left);
            const top = parsePx(el.style.top);
            const width = parsePx(el.style.width);
            const height = parsePx(el.style.height);
            if(left === null || top !== 0 || height !== 39 || width === null) continue;
            out.push({name: title, left, width});
          }
          out.sort((a, b) => a.left - b.left);
          return out;
        }

        function collectCells(pageEl){
          const out = [];
          for(const el of pageEl.children){
            const title = (el.getAttribute("title") || "").trim();
            if(!title || title === "Resize column") continue;
            const left = parsePx(el.style.left);
            const top = parsePx(el.style.top);
            const width = parsePx(el.style.width);
            const height = parsePx(el.style.height);
            if(left === null || top === null || width === null || height === null) continue;
            out.push({title, left, top, width, height});
          }
          return out;
        }

        function nearestCol(left, headers){
          let best = null;
          for(const h of headers){
            if(best === null || Math.abs(left - h.left) < Math.abs(left - best.left)){
              best = h;
            }
          }
          return best ? best.name : "";
        }

        const frame = [...document.querySelectorAll('.QvFrame[objtype="Grid"]')]
          .find((f) => getComputedStyle(f).display !== "none" && (f.innerText || "").includes("Nro. Emenda"));
        if(!frame){
          return { error: "grid de emendas não encontrado" };
        }

        const pages = [...frame.querySelectorAll('div[page="0"]')];
        const headLeft = pages[0];
        const dataLeft = pages[1];
        const headRight = pages[3];
        const dataRight = pages[4];
        if(!headLeft || !dataLeft || !headRight || !dataRight){
          return { error: "estrutura de páginas do grid incompleta", pages_count: pages.length };
        }

        const headersLeft = collectHeaders(headLeft);
        const headersRight = collectHeaders(headRight);
        const rowHeight = 39;
        const rows = {};

        function ingest(cells, headers){
          for(const cell of cells){
            if(cell.top <= 0) continue; // ignora linha total do topo
            const start = Math.max(0, Math.round((cell.top - 39) / rowHeight));
            const span = Math.max(1, Math.round(cell.height / rowHeight));
            const col = nearestCol(cell.left, headers);
            if(!col) continue;
            for(let i = 0; i < span; i++){
              const idx = start + i;
              if(!rows[idx]) rows[idx] = {_row: idx};
              if(!rows[idx][col] || rows[idx][col] === ""){
                rows[idx][col] = cell.title;
              }
            }
          }
        }

        ingest(collectCells(dataLeft), headersLeft);
        ingest(collectCells(dataRight), headersRight);

        const rowsOut = Object.keys(rows)
          .map((k) => rows[k])
          .sort((a, b) => a._row - b._row);
        const nros = rowsOut
          .map((row) => (row["Nro. Emenda"] || "").trim())
          .filter((value) => /^\\d{8}$/.test(value));
        return {
          error: "",
          frame_id: frame.id,
          headers_left: headersLeft.map((h) => h.name),
          headers_right: headersRight.map((h) => h.name),
          rows: rowsOut,
          nros: [...new Set(nros)],
        };
    """
    return driver.execute_script(js)


def sweep_siop_rows_current_filter(driver, wait, max_steps=90, drag_offset=40):
    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.webdriver.common.by import By

    rows_by_key = {}
    signatures = []
    stop_reason = "max_steps"

    for _ in range(max_steps):
        chunk = extract_siop_visible_chunk(driver)
        if chunk.get("error"):
            stop_reason = f"chunk_error:{chunk.get('error')}"
            break

        nros = [normalize_text(nro) for nro in chunk.get("nros", []) if normalize_text(nro)]
        signature = (
            nros[0] if nros else "",
            nros[-1] if nros else "",
            len(nros),
        )
        signatures.append(signature)

        for row in chunk.get("rows", []):
            nro = normalize_text(row.get("Nro. Emenda", ""))
            if not re.fullmatch(r"\d{8}", nro):
                continue
            rp_key = normalize_text(row.get("RP", "")) or "rp_nao_informado"
            rows_by_key[f"{rp_key}::{nro}"] = row

        if len(signatures) >= 9 and len(set(signatures[-9:])) == 1:
            stop_reason = "stable_signature"
            break

        try:
            frame = wait.until(
                lambda drv: drv.find_element(
                    By.XPATH,
                    "//div[contains(@class,'QvFrame') and @objtype='Grid' and contains(.,'Nro. Emenda')]",
                )
            )
            thumb = frame.find_element(
                By.XPATH,
                ".//div[contains(@class,'TouchScrollbar') and "
                "contains(@style,'background-color: rgb(192, 192, 192)') and not(.//span)]",
            )
            ActionChains(driver).click_and_hold(thumb).move_by_offset(0, drag_offset).release().perform()
            time.sleep(0.45)
        except Exception:
            stop_reason = "scrollbar_move_failed"
            break

    return rows_by_key, len(signatures), stop_reason


def sweep_siop_rows_with_retry(driver, wait, group_label="Por Partido", attempts=5):
    last_reason = ""
    for _ in range(max(1, attempts)):
        try:
            open_siop_step2_group(driver, wait, group_label=group_label)
            rows, steps, reason = sweep_siop_rows_current_filter(driver, wait)
            if reason.startswith("chunk_error:") and not rows:
                last_reason = reason
                time.sleep(1.0)
                continue
            return rows, steps, reason
        except Exception as exc:
            last_reason = normalize_text(str(exc))[:220]
            time.sleep(1.0)
    return {}, 0, last_reason or "falha ao varrer grid SIOP"


def extract_siop_snapshot(year, rp_filters):
    """
    Extrai snapshot agregado diretamente no SIOP:
    Dotação Inicial, Dotação Atual (autorizado), Empenhado, Liquidado e Pago.
    """
    result = {
        "available": False,
        "source_url": SIOP_PANEL_URL,
        "last_update": "",
        "base_siafi_date": "",
        "filters": {"year": int(year), "rp": list(rp_filters)},
        "totals": {},
        "per_rp": [],
        "error": "",
    }

    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.support.ui import WebDriverWait
    except Exception as exc:
        result["error"] = f"selenium indisponível: {exc}"
        return result

    driver = None
    try:
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--ignore-certificate-errors")
        options.add_argument("--window-size=1800,2400")

        driver = webdriver.Chrome(options=options)
        wait = WebDriverWait(driver, 40)
        navigate_siop_to_emendas(driver, wait)
        body_text = driver.find_element("tag name", "body").text
        last_update, base_siafi_date = parse_siop_dates_from_text(body_text)
        result["last_update"] = last_update
        result["base_siafi_date"] = base_siafi_date

        aggregate = {
            "dotacao_inicial_emenda": 0.0,
            "dotacao_atual_emenda": 0.0,
            "empenhado": 0.0,
            "liquidado": 0.0,
            "pago": 0.0,
        }
        per_rp = []
        errors = []

        for rp_label in rp_filters:
            if not apply_siop_single_rp_filter(driver, wait, year=year, rp_label=rp_label):
                per_rp.append(
                    {
                        "rp": rp_label,
                        "available": False,
                        "totals": {},
                        "error": "filtro não confirmado",
                    }
                )
                errors.append(f"{rp_label}: filtro não confirmado")
                continue

            try:
                rp_totals = extract_siop_totals_with_retry(driver, wait, group_label="Por Partido")
                per_rp.append({"rp": rp_label, "available": True, "totals": rp_totals, "error": ""})
                for key in aggregate:
                    aggregate[key] += to_float(rp_totals.get(key, 0))
            except Exception as exc:
                per_rp.append(
                    {
                        "rp": rp_label,
                        "available": False,
                        "totals": {},
                        "error": normalize_text(str(exc))[:220],
                    }
                )
                errors.append(f"{rp_label}: {normalize_text(str(exc))[:120]}")

        result["per_rp"] = per_rp
        if per_rp and all(item.get("available") for item in per_rp):
            # Sanity check: dotação inicial/atual da União nunca é zero durante o ano.
            # Quando o Qlik muda o DOM e o sweep não acha as células, vem tudo 0 sem
            # exception — historicamente isso ficou 49 dias gravando zeros silenciosamente.
            if to_float(aggregate.get("dotacao_inicial_emenda", 0)) == 0 or to_float(aggregate.get("dotacao_atual_emenda", 0)) == 0:
                result["totals"] = {}
                result["available"] = False
                result["error"] = "dotação inicial/atual zerada — provável quebra do scraper Qlik (DOM mudou)"
            else:
                result["totals"] = {k: to_float(v) for k, v in aggregate.items()}
                result["available"] = True
                result["error"] = ""
        else:
            result["totals"] = {}
            result["available"] = False
            if errors:
                result["error"] = " ; ".join(errors)[:500]
            else:
                result["error"] = "falha ao confirmar os filtros RP no SIOP"
    except Exception as exc:
        result["error"] = normalize_text(str(exc))[:500]
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass

    if not result["available"]:
        # Log alto para aparecer nos logs do GitHub Actions — historicamente o
        # scraper do SIOP quebra silenciosamente quando o Qlik muda o DOM.
        print(f"[warn] SIOP snapshot indisponível — erro: {result.get('error','')[:400]}", flush=True)
    return result


def extract_siop_details(party_lookup, year, rp_filters):
    """
    Coleta linhas detalhadas do grid de emendas no SIOP (Passo 2 / Por Partido),
    varrendo a barra vertical e consolidando por Nro. Emenda.
    """
    result = {
        "available": False,
        "source_url": SIOP_PANEL_URL,
        "group_selected": "Por Partido",
        "filters": {"year": int(year), "rp": list(rp_filters)},
        "last_update": "",
        "base_siafi_date": "",
        "rows": [],
        "rows_count": 0,
        "unique_nro_emendas": 0,
        "sweep_steps": 0,
        "sweep_stop_reason": "",
        "top_authors": [],
        "top_parties": [],
        "top_orgaos": [],
        "per_rp": [],
        "error": "",
    }

    try:
        from selenium import webdriver
        from selenium.webdriver.common.by import By
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.support.ui import WebDriverWait
    except Exception as exc:
        result["error"] = f"selenium indisponível: {exc}"
        return result

    driver = None
    try:
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--ignore-certificate-errors")
        options.add_argument("--window-size=1800,2400")

        driver = webdriver.Chrome(options=options)
        wait = WebDriverWait(driver, 50)
        navigate_siop_to_emendas(driver, wait)

        body_text = driver.find_element(By.TAG_NAME, "body").text
        last_update, base_siafi_date = parse_siop_dates_from_text(body_text)
        result["last_update"] = last_update
        result["base_siafi_date"] = base_siafi_date

        rows_by_key = {}
        per_rp = []
        sweep_steps_total = 0
        sweep_reasons = []
        errors = []

        for rp_label in rp_filters:
            if not apply_siop_single_rp_filter(driver, wait, year=year, rp_label=rp_label):
                per_rp.append(
                    {
                        "rp": rp_label,
                        "available": False,
                        "rows_count": 0,
                        "sweep_steps": 0,
                        "sweep_stop_reason": "filtro não confirmado",
                        "error": "filtro não confirmado",
                    }
                )
                errors.append(f"{rp_label}: filtro não confirmado")
                continue

            try:
                rp_rows, rp_steps, rp_reason = sweep_siop_rows_with_retry(
                    driver,
                    wait,
                    group_label="Por Partido",
                )
                failed_rp = (not rp_rows) and (
                    rp_reason.startswith("chunk_error:")
                    or rp_reason.startswith("falha")
                    or rp_reason.startswith("não")
                )
                if failed_rp:
                    per_rp.append(
                        {
                            "rp": rp_label,
                            "available": False,
                            "rows_count": 0,
                            "sweep_steps": rp_steps,
                            "sweep_stop_reason": rp_reason,
                            "error": rp_reason,
                        }
                    )
                    errors.append(f"{rp_label}: {rp_reason}")
                    continue
                for key, row in rp_rows.items():
                    rows_by_key[key] = row
                per_rp.append(
                    {
                        "rp": rp_label,
                        "available": True,
                        "rows_count": len(rp_rows),
                        "sweep_steps": rp_steps,
                        "sweep_stop_reason": rp_reason,
                        "error": "",
                    }
                )
                sweep_steps_total += rp_steps
                sweep_reasons.append(f"{rp_label}: {rp_reason}")
            except Exception as exc:
                message = normalize_text(str(exc))[:220]
                per_rp.append(
                    {
                        "rp": rp_label,
                        "available": False,
                        "rows_count": 0,
                        "sweep_steps": 0,
                        "sweep_stop_reason": message,
                        "error": message,
                    }
                )
                errors.append(f"{rp_label}: {message}")

        result["per_rp"] = per_rp
        result["sweep_steps"] = sweep_steps_total
        result["sweep_stop_reason"] = " | ".join(sweep_reasons)[:500]

        if not per_rp or not all(item.get("available") for item in per_rp):
            result["available"] = False
            result["error"] = " ; ".join(errors)[:500] if errors else "falha ao confirmar filtros RP no SIOP"
            return result

        author_totals = defaultdict(lambda: Decimal("0"))
        party_totals = defaultdict(lambda: Decimal("0"))
        orgao_totals = defaultdict(lambda: Decimal("0"))
        cleaned_rows = []
        target_year = str(year)
        target_rps = {normalize_text(label) for label in rp_filters}
        for _, row in sorted(rows_by_key.items()):
            nro = normalize_text(row.get("Nro. Emenda", ""))
            if not re.fullmatch(r"\d{8}", nro):
                continue
            ano_row = normalize_text(row.get("Ano", ""))
            rp_row = normalize_text(row.get("RP", ""))
            if target_year and ano_row and ano_row != target_year:
                continue
            if target_rps and rp_row and rp_row not in target_rps:
                continue

            author = normalize_text(row.get("Autor", "")) or "Autor não informado"
            orgao = normalize_text(row.get("Órgão", "")) or "Órgão não informado"
            party_raw = normalize_text(row.get("Partido", ""))
            party = party_raw or resolve_party(author, party_lookup)

            dotacao_inicial = parse_currency(row.get("Dotação Inicial Emenda", ""))
            dotacao_atual = parse_currency(row.get("Dotação Atual Emenda", ""))
            empenhado = parse_currency(row.get("Empenhado", ""))
            liquidado = parse_currency(row.get("Liquidado", ""))
            pago = parse_currency(row.get("Pago", ""))

            cleaned_rows.append(
                {
                    "nro_emenda": nro,
                    "ano": ano_row,
                    "rp": rp_row,
                    "autor": author,
                    "tipo_autor": normalize_text(row.get("Tipo Autor", "")),
                    "partido": party,
                    "orgao": orgao,
                    "acao": normalize_text(row.get("Ação", "")),
                    "dotacao_inicial_emenda": to_float(dotacao_inicial),
                    "dotacao_atual_emenda": to_float(dotacao_atual),
                    "empenhado": to_float(empenhado),
                    "liquidado": to_float(liquidado),
                    "pago": to_float(pago),
                }
            )

            author_totals[author] += empenhado
            party_totals[party] += empenhado
            orgao_totals[orgao] += empenhado

        result["rows"] = cleaned_rows
        result["rows_count"] = len(cleaned_rows)
        result["unique_nro_emendas"] = len(cleaned_rows)
        result["top_authors"] = [
            {"author": name, "party": resolve_party(name, party_lookup), "empenhado": to_float(value)}
            for name, value in sort_top(author_totals, 20)
        ]
        result["top_parties"] = [
            {"party": name, "empenhado": to_float(value)}
            for name, value in sort_top(party_totals, 20)
        ]
        result["top_orgaos"] = [
            {"orgao": name, "empenhado": to_float(value)}
            for name, value in sort_top(orgao_totals, 20)
        ]
        result["available"] = True
        result["error"] = ""
    except Exception as exc:
        result["error"] = normalize_text(str(exc))[:500]
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
    return result


def build_documents_monitor(year, party_lookup):
    requested_url = DOCUMENTS_DOWNLOAD_URL_TEMPLATE.format(year=year)
    fallback_url = DOCUMENTS_FALLBACK_ZIP_URL_TEMPLATE.format(year=year)
    headers = fetch_headers(requested_url)

    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = Path(tmpdir) / f"docs-{year}.zip"
        try:
            download_source(requested_url, zip_path)
        except Exception:
            download_source(fallback_url, zip_path)
            if not headers.get("final_url"):
                headers["final_url"] = fallback_url

        with zipfile.ZipFile(zip_path) as zf:
            csv_members = [name for name in zf.namelist() if name.lower().endswith(".csv")]
            if not csv_members:
                raise RuntimeError("Arquivo de documentos de emendas sem CSV.")
            member = csv_members[0]
            with zf.open(member) as fh:
                reader = csv.DictReader(io.TextIOWrapper(fh, encoding="latin-1", newline=""), delimiter=";")
                if not reader.fieldnames:
                    raise RuntimeError("CSV de documentos sem cabeçalho.")

                col_map = {normalize_column(name): name for name in reader.fieldnames}

                def get_col(row, norm_name):
                    return normalize_text(row.get(col_map.get(norm_name, ""), ""))

                daily_empenhado = defaultdict(lambda: Decimal("0"))
                daily_pago = defaultdict(lambda: Decimal("0"))
                author_year_empenhado = defaultdict(lambda: Decimal("0"))
                author_year_pago = defaultdict(lambda: Decimal("0"))
                author_last_day_empenhado = defaultdict(lambda: Decimal("0"))
                destination_last_day_empenhado = defaultdict(lambda: Decimal("0"))

                rows_processed = 0
                valid_rows = 0
                max_date = None
                min_date = None

                buffered_rows = []
                for row in reader:
                    rows_processed += 1
                    data_doc = parse_ddmmyyyy(get_col(row, "data_documento"))
                    if not data_doc or data_doc.year != year:
                        continue
                    valid_rows += 1
                    if min_date is None or data_doc < min_date:
                        min_date = data_doc
                    if max_date is None or data_doc > max_date:
                        max_date = data_doc
                    buffered_rows.append((row, data_doc))

                if max_date is None:
                    return {
                        "year": year,
                        "source": {
                            "requested_url": requested_url,
                            "download_url": headers.get("final_url") or fallback_url,
                            "last_modified": headers.get("last_modified", ""),
                            "etag": headers.get("etag", ""),
                            "csv_member": member,
                        },
                        "rows_processed": rows_processed,
                        "rows_valid_year": 0,
                        "date_min": "",
                        "date_max": "",
                        "daily_series": [],
                        "top_authors_year": [],
                        "top_authors_last_day": [],
                        "top_destinations_last_day": [],
                        "totals": {
                            "total_empenhado_year": 0.0,
                            "total_pago_year": 0.0,
                            "total_empenhado_last_day": 0.0,
                            "total_pago_last_day": 0.0,
                        },
                    }

                for row, data_doc in buffered_rows:
                    date_key = data_doc.isoformat()
                    author = get_col(row, "nome_do_autor_da_emenda") or "Autor não informado"
                    party = resolve_party(author, party_lookup)
                    author_party_key = f"{author}|||{party}"
                    destination = (
                        get_col(row, "localidade_de_aplicacao_do_recurso")
                        or (
                            f"{get_col(row, 'municipio_de_aplicacao_do_recurso')} - {get_col(row, 'uf_de_aplicacao_do_recurso')}"
                            if get_col(row, "municipio_de_aplicacao_do_recurso") or get_col(row, "uf_de_aplicacao_do_recurso")
                            else "Destino não informado"
                        )
                    )

                    valor_empenhado = parse_currency(get_col(row, "valor_empenhado"))
                    valor_pago = parse_currency(get_col(row, "valor_pago"))

                    if valor_empenhado > 0:
                        daily_empenhado[date_key] += valor_empenhado
                        author_year_empenhado[author_party_key] += valor_empenhado
                        if data_doc == max_date:
                            author_last_day_empenhado[author_party_key] += valor_empenhado
                            destination_last_day_empenhado[destination] += valor_empenhado

                    if valor_pago > 0:
                        daily_pago[date_key] += valor_pago
                        author_year_pago[author_party_key] += valor_pago

                start_date = dt.date(year, 1, 1)
                end_date = max_date
                running_empenhado = Decimal("0")
                running_pago = Decimal("0")
                series = []
                cursor = start_date
                while cursor <= end_date:
                    key = cursor.isoformat()
                    day_emp = daily_empenhado.get(key, Decimal("0"))
                    day_pago = daily_pago.get(key, Decimal("0"))
                    running_empenhado += day_emp
                    running_pago += day_pago
                    series.append(
                        {
                            "date": key,
                            "empenhado": to_float(day_emp),
                            "pago": to_float(day_pago),
                            "acumulado_empenhado": to_float(running_empenhado),
                            "acumulado_pago": to_float(running_pago),
                        }
                    )
                    cursor += dt.timedelta(days=1)

                top_year_keys = sort_top(author_year_empenhado, 20)
                top_last_day_keys = sort_top(author_last_day_empenhado, 20)
                top_destinations_last_day = sort_top(destination_last_day_empenhado, 20)

                top_authors_year = []
                for key, empenhado in top_year_keys:
                    author, party = split_author_party_key(key)
                    pago = author_year_pago.get(key, Decimal("0"))
                    top_authors_year.append(
                        {
                            "author": author,
                            "party": party,
                            "empenhado": to_float(empenhado),
                            "pago": to_float(pago),
                        }
                    )

                top_authors_last_day = []
                for key, empenhado in top_last_day_keys:
                    author, party = split_author_party_key(key)
                    top_authors_last_day.append(
                        {"author": author, "party": party, "empenhado": to_float(empenhado)}
                    )

                return {
                    "year": year,
                    "source": {
                        "requested_url": requested_url,
                        "download_url": headers.get("final_url") or fallback_url,
                        "last_modified": headers.get("last_modified", ""),
                        "etag": headers.get("etag", ""),
                        "csv_member": member,
                    },
                    "rows_processed": rows_processed,
                    "rows_valid_year": valid_rows,
                    "date_min": min_date.isoformat() if min_date else "",
                    "date_max": max_date.isoformat() if max_date else "",
                    "daily_series": series,
                    "top_authors_year": top_authors_year,
                    "top_authors_last_day": top_authors_last_day,
                    "top_destinations_last_day": [
                        {"destination": destination, "empenhado": to_float(value)}
                        for destination, value in top_destinations_last_day
                    ],
                    "totals": {
                        "total_empenhado_year": to_float(sum(daily_empenhado.values(), Decimal("0"))),
                        "total_pago_year": to_float(sum(daily_pago.values(), Decimal("0"))),
                        "total_empenhado_last_day": to_float(daily_empenhado.get(max_date.isoformat(), Decimal("0"))),
                        "total_pago_last_day": to_float(daily_pago.get(max_date.isoformat(), Decimal("0"))),
                    },
                }


def build_apoiamento_monitor(current_year):
    selected_year = None
    zip_path = None
    headers = {}
    requested_url = ""
    fallback_url = ""

    for year in range(current_year, 2019, -1):
        requested_url = APOIAMENTO_DOWNLOAD_URL_TEMPLATE.format(year=year)
        fallback_url = APOIAMENTO_FALLBACK_ZIP_URL_TEMPLATE.format(year=year)
        headers = fetch_headers(requested_url)
        with tempfile.TemporaryDirectory() as tmpdir:
            candidate = Path(tmpdir) / f"apoiamento-{year}.zip"
            try:
                download_source(requested_url, candidate)
                selected_year = year
                zip_path = candidate.read_bytes()
                break
            except Exception:
                try:
                    download_source(fallback_url, candidate)
                    selected_year = year
                    if not headers.get("final_url"):
                        headers["final_url"] = fallback_url
                    zip_path = candidate.read_bytes()
                    break
                except Exception:
                    continue

    if selected_year is None or zip_path is None:
        return {
            "available": False,
            "year": None,
            "rows_processed": 0,
            "top_supporters": [],
            "source": {"requested_url": "", "download_url": ""},
        }

    supporters_empenhado = defaultdict(lambda: Decimal("0"))
    supporters_pago = defaultdict(lambda: Decimal("0"))
    supporters_authors = defaultdict(set)
    supporters_author_empenhado = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
    supporters_group_empenhado = defaultdict(lambda: Decimal("0"))
    author_supported_empenhado = defaultdict(lambda: Decimal("0"))
    total_empenhado = Decimal("0")
    total_pago = Decimal("0")
    rows_processed = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        zpath = Path(tmpdir) / "apoiamento.zip"
        zpath.write_bytes(zip_path)
        with zipfile.ZipFile(zpath) as zf:
            csv_members = [name for name in zf.namelist() if name.lower().endswith(".csv")]
            member = csv_members[0] if csv_members else ""
            if member:
                with zf.open(member) as fh:
                    reader = csv.DictReader(io.TextIOWrapper(fh, encoding="latin-1", newline=""), delimiter=";")
                    col_map = {normalize_column(name): name for name in (reader.fieldnames or [])}

                    def get_col(row, norm_name):
                        return normalize_text(row.get(col_map.get(norm_name, ""), ""))

                    for row in reader:
                        rows_processed += 1
                        apoiador = get_col(row, "apoiador") or "Apoiador não informado"
                        author = get_col(row, "nome_do_autor_da_emenda") or "Autor não informado"
                        valor_empenhado = parse_currency(get_col(row, "valor_empenhado"))
                        valor_pago = parse_currency(get_col(row, "valor_pago"))
                        if valor_empenhado > 0:
                            supporters_empenhado[apoiador] += valor_empenhado
                            supporters_author_empenhado[apoiador][author] += valor_empenhado
                            supporters_group_empenhado[classify_supporter_group(apoiador)] += valor_empenhado
                            author_supported_empenhado[author] += valor_empenhado
                            total_empenhado += valor_empenhado
                        if valor_pago > 0:
                            supporters_pago[apoiador] += valor_pago
                            total_pago += valor_pago
                        supporters_authors[apoiador].add(author)

    top_supporters = []
    for apoiador, value in sort_top(supporters_empenhado, 20):
        supporter_authors = supporters_author_empenhado.get(apoiador, {})
        top_supported_authors = [
            {"author": author, "empenhado": to_float(author_value)}
            for author, author_value in sort_top(supporter_authors, 3)
        ]
        top_supporters.append(
            {
                "supporter": apoiador,
                "group": classify_supporter_group(apoiador),
                "empenhado": to_float(value),
                "pago": to_float(supporters_pago.get(apoiador, Decimal("0"))),
                "authors_count": len(supporters_authors.get(apoiador, set())),
                "share_empenhado": safe_share(value, total_empenhado),
                "top_supported_authors": top_supported_authors,
            }
        )

    top_supported_authors = [
        {"author": author, "empenhado": to_float(value)}
        for author, value in sort_top(author_supported_empenhado, 20)
    ]
    top_supporter_groups = [
        {"group": group, "empenhado": to_float(value)}
        for group, value in sort_top(supporters_group_empenhado, 20)
    ]
    top1_value = top_supporters[0]["empenhado"] if top_supporters else 0.0
    top5_value = sum(item["empenhado"] for item in top_supporters[:5]) if top_supporters else 0.0

    return {
        "available": True,
        "year": selected_year,
        "rows_processed": rows_processed,
        "top_supporters": top_supporters,
        "top_supported_authors": top_supported_authors,
        "top_supporter_groups": top_supporter_groups,
        "totals": {
            "total_empenhado": to_float(total_empenhado),
            "total_pago": to_float(total_pago),
            "top1_share": safe_share(top1_value, to_float(total_empenhado) if total_empenhado else 0),
            "top5_share": safe_share(top5_value, to_float(total_empenhado) if total_empenhado else 0),
        },
        "source": {
            "requested_url": requested_url,
            "download_url": headers.get("final_url") or fallback_url,
            "last_modified": headers.get("last_modified", ""),
            "etag": headers.get("etag", ""),
        },
    }


def build_report(current, previous, headers, source_zip_size, has_previous):
    total_current = current["total_empenhado"]
    total_previous = Decimal(str(previous.get("total_empenhado", 0.0))) if has_previous else total_current
    current_year = int(current.get("current_year", dt.date.today().year))
    current_year_totals = current.get("current_year_totals", {})

    if has_previous:
        author_growth, author_positive_total, author_negative_total = apply_diff(
            current["author_totals"], previous.get("author_totals", {})
        )
        destination_growth, destination_positive_total, _ = apply_diff(
            current["destination_totals"], previous.get("destination_totals", {})
        )
        pair_growth, _, _ = apply_diff(
            current["author_destination_totals"],
            previous.get("author_destination_totals", {}),
        )
        delta_liquid = total_current - total_previous
        delta_positive = author_positive_total
    else:
        author_growth = {}
        destination_growth = {}
        pair_growth = {}
        author_positive_total = Decimal("0")
        destination_positive_total = Decimal("0")
        author_negative_total = Decimal("0")
        delta_liquid = Decimal("0")
        delta_positive = Decimal("0")

    top_author_growth = sort_top(author_growth, 20)
    top_destination_growth = sort_top(destination_growth, 20)
    top_pair_growth = sort_top(pair_growth, 50)

    top_authors_total = sort_top(current["author_totals"], 20)
    top_destinations_total = sort_top(current["destination_totals"], 20)
    top_authors_year = sort_top(current.get("current_year_author_totals", {}), 20)
    top_destinations_year = sort_top(current.get("current_year_destination_totals", {}), 20)

    pair_rows = []
    for key, delta in top_pair_growth:
        author, destination = key.split("|||", 1)
        party = current["author_party_map"].get(author, "Nao identificado")
        pair_rows.append(
            {
                "author": author,
                "party": party,
                "destination": destination,
                "delta_empenhado": to_float(delta),
                "current_empenhado": to_float(current["author_destination_totals"].get(key, Decimal("0"))),
            }
        )

    report = {
        "generated_at": now_iso(),
        "snapshot_date": dt.date.today().isoformat(),
        "source": {
            "label": "Portal da Transparência (CGU) - Emendas Parlamentares",
            "requested_url": DOWNLOAD_URL,
            "download_url": headers.get("final_url") or FALLBACK_ZIP_URL,
            "senado_reference_url": (
                "https://www9.senado.gov.br/QvAJAXZfc/opendoc.htm?document=senado%2F"
                "sigabrasilpainelcidadao.qvw&host=QVS%40www9&anonymous=true&Sheet=shOrcamentoVisaoGeral"
            ),
            "etag": headers.get("etag", ""),
            "last_modified": headers.get("last_modified", ""),
            "zip_bytes": source_zip_size,
        },
        "metrics": {
            "total_empenhado_atual": to_float(total_current),
            "total_empenhado_snapshot_anterior": to_float(total_previous),
            "delta_liquido_desde_snapshot_anterior": to_float(delta_liquid),
            "delta_positivo_desde_snapshot_anterior": to_float(delta_positive),
            "delta_negativo_desde_snapshot_anterior": to_float(author_negative_total),
            "autores_com_aumento": len(author_growth),
            "destinos_com_aumento": len(destination_growth),
            "pares_autor_destino_com_aumento": len(pair_growth),
            "total_autores_mapeados": len(current["author_totals"]),
            "total_destinos_mapeados": len(current["destination_totals"]),
            "total_linhas_csv": current["rows_processed"],
            "baseline_initialized": not has_previous,
            "current_year": current_year,
            "current_year_total_empenhado": to_float(current_year_totals.get("total_empenhado", Decimal("0"))),
            "current_year_total_liquidado": to_float(current_year_totals.get("total_liquidado", Decimal("0"))),
            "current_year_total_pago": to_float(current_year_totals.get("total_pago", Decimal("0"))),
        },
        "top_authors_today": [
            {
                "author": name,
                "party": current["author_party_map"].get(name, "Nao identificado"),
                "delta_empenhado": to_float(delta),
                "share_in_day": safe_share(delta, delta_positive),
                "current_empenhado": to_float(current["author_totals"].get(name, Decimal("0"))),
            }
            for name, delta in top_author_growth
        ],
        "top_destinations_today": [
            {
                "destination": name,
                "delta_empenhado": to_float(delta),
                "share_in_day": safe_share(delta, destination_positive_total),
                "current_empenhado": to_float(current["destination_totals"].get(name, Decimal("0"))),
            }
            for name, delta in top_destination_growth
        ],
        "top_author_destination_today": pair_rows,
        "top_authors_total": [
            {
                "author": name,
                "party": current["author_party_map"].get(name, "Nao identificado"),
                "total_empenhado": to_float(value),
            }
            for name, value in top_authors_total
        ],
        "top_destinations_total": [
            {"destination": name, "total_empenhado": to_float(value)}
            for name, value in top_destinations_total
        ],
        "top_authors_year": [
            {
                "author": name,
                "party": (
                    current.get("current_year_author_party_map", {}).get(name)
                    or current["author_party_map"].get(name, "Nao identificado")
                ),
                "total_empenhado": to_float(value),
            }
            for name, value in top_authors_year
        ],
        "top_destinations_year": [
            {"destination": name, "total_empenhado": to_float(value)}
            for name, value in top_destinations_year
        ],
        "unico_year_summary": {
            "year": current_year,
            "total_empenhado": to_float(current_year_totals.get("total_empenhado", Decimal("0"))),
            "total_liquidado": to_float(current_year_totals.get("total_liquidado", Decimal("0"))),
            "total_pago": to_float(current_year_totals.get("total_pago", Decimal("0"))),
        },
    }
    return report


def override_current_year_totals_from_documents(report):
    """Substitui os totais anuais pelos valores corretos vindos do PorDocumento.csv.

    O arquivo EmendasParlamentares.csv filtra por 'Ano da Emenda' (o ano do
    orçamento em que a emenda foi criada), o que é errado para medir o empenho
    e pagamento do ano corrente — deixa de fora a execução de 'restos a pagar'
    de emendas de anos anteriores. O arquivo PorDocumento.csv traz a 'Data
    Documento' de cada empenho/pagamento, que é a data fiscal correta.
    """
    parallel = report.get("parallel_monitor") or {}
    docs = parallel.get("documents") or {}
    doc_totals = docs.get("totals") or {}
    # só sobrescreve se o arquivo PorDocumento foi processado com sucesso
    if not doc_totals or not docs.get("date_max"):
        return
    metrics = report.setdefault("metrics", {})
    # valores antigos (por ano_da_emenda) vão para campos _por_ano_emenda
    metrics["current_year_total_empenhado_por_ano_emenda"] = metrics.get("current_year_total_empenhado")
    metrics["current_year_total_pago_por_ano_emenda"] = metrics.get("current_year_total_pago")
    # substitui pelos valores corretos (por data_documento)
    metrics["current_year_total_empenhado"] = doc_totals.get("total_empenhado_year", 0.0)
    metrics["current_year_total_pago"] = doc_totals.get("total_pago_year", 0.0)
    # current_year_total_liquidado segue vindo do arquivo principal (PorDocumento
    # não tem essa coluna); documenta a limitação no report:
    metrics["_pago_e_empenhado_source"] = "por_data_documento"
    # refletir também em unico_year_summary para consistência
    summary = report.setdefault("unico_year_summary", {})
    summary["total_empenhado"] = metrics["current_year_total_empenhado"]
    summary["total_pago"] = metrics["current_year_total_pago"]


def update_history(report, *, source_changed=True):
    """Anexa um registro ao daily_history.

    Se source_changed=False (Portal não atualizou desde o último run), ainda
    anexa um registro para o dia — com delta=0 e sinalizando que não houve
    mudança — para que o histórico fique contínuo dia a dia, sem gaps.
    """
    history = load_json(HISTORY_FILE, [])
    snapshot_date = report["snapshot_date"]
    # descobrir o snapshot anterior (último com data < snapshot_date)
    prior = [row for row in history if row.get("date") and row.get("date") < snapshot_date]
    previous_snapshot_date = prior[-1].get("date") if prior else ""
    interval_days = 0
    if previous_snapshot_date:
        try:
            a = dt.date.fromisoformat(previous_snapshot_date)
            b = dt.date.fromisoformat(snapshot_date)
            interval_days = (b - a).days
        except Exception:
            interval_days = 0
    history = [row for row in history if row.get("date") != snapshot_date]
    entry = {
        "date": snapshot_date,
        "delta_positivo": report["metrics"]["delta_positivo_desde_snapshot_anterior"] if source_changed else 0.0,
        "delta_liquido": report["metrics"]["delta_liquido_desde_snapshot_anterior"] if source_changed else 0.0,
        "total_empenhado_atual": report["metrics"]["total_empenhado_atual"],
        "autores_com_aumento": report["metrics"]["autores_com_aumento"] if source_changed else 0,
        "destinos_com_aumento": report["metrics"]["destinos_com_aumento"] if source_changed else 0,
        "previous_snapshot_date": previous_snapshot_date,
        "interval_days": interval_days,
        "source_changed": bool(source_changed),
    }
    history.append(entry)
    history.sort(key=lambda row: row.get("date", ""))
    if len(history) > 730:
        history = history[-730:]
    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    report["daily_history"] = history
    return history


def update_siop_history(report):
    history = load_json(SIOP_HISTORY_FILE, [])
    history_date = normalize_text(report.get("run_date")) or dt.date.today().isoformat()
    siop_snapshot = (((report.get("parallel_monitor") or {}).get("siop_snapshot")) or {})

    if not (history_date and siop_snapshot.get("available")):
        report["siop_daily_history"] = history
        return history

    totals = siop_snapshot.get("totals", {}) if isinstance(siop_snapshot, dict) else {}
    history = [row for row in history if row.get("date") != history_date]
    history.append(
        {
            "date": history_date,
            "last_update": siop_snapshot.get("last_update", ""),
            "base_siafi_date": siop_snapshot.get("base_siafi_date", ""),
            "dotacao_inicial_emenda": to_float(totals.get("dotacao_inicial_emenda", 0)),
            "dotacao_atual_emenda": to_float(totals.get("dotacao_atual_emenda", 0)),
            "empenhado": to_float(totals.get("empenhado", 0)),
            "liquidado": to_float(totals.get("liquidado", 0)),
            "pago": to_float(totals.get("pago", 0)),
        }
    )
    history.sort(key=lambda row: row.get("date", ""))
    if len(history) > 730:
        history = history[-730:]
    SIOP_HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    report["siop_daily_history"] = history
    return history


def apply_siop_fallback_from_previous(report):
    previous_report = load_json(REPORT_FILE, {})
    previous_parallel = previous_report.get("parallel_monitor", {}) if isinstance(previous_report, dict) else {}
    previous_snapshot = previous_parallel.get("siop_snapshot", {}) if isinstance(previous_parallel, dict) else {}
    previous_details = previous_parallel.get("siop_details", {}) if isinstance(previous_parallel, dict) else {}

    parallel = report.get("parallel_monitor", {})
    current_snapshot = parallel.get("siop_snapshot", {}) if isinstance(parallel, dict) else {}
    current_details = parallel.get("siop_details", {}) if isinstance(parallel, dict) else {}

    if (
        isinstance(current_snapshot, dict)
        and not current_snapshot.get("available")
        and isinstance(previous_snapshot, dict)
        and previous_snapshot.get("available")
    ):
        fallback_snapshot = dict(previous_snapshot)
        fallback_snapshot["fallback_from_previous"] = True
        fallback_snapshot["error"] = normalize_text(current_snapshot.get("error")) or ""
        parallel["siop_snapshot"] = fallback_snapshot

    if (
        isinstance(current_details, dict)
        and not current_details.get("available")
        and isinstance(previous_details, dict)
        and previous_details.get("available")
    ):
        fallback_details = dict(previous_details)
        fallback_details["fallback_from_previous"] = True
        fallback_details["error"] = normalize_text(current_details.get("error")) or ""
        parallel["siop_details"] = fallback_details

    report["parallel_monitor"] = parallel
    return report


def build_current_aggregates(zip_path, party_lookup):
    current_year = dt.date.today().year
    with zipfile.ZipFile(zip_path) as zf:
        member = pick_primary_member(zf.namelist())
        with zf.open(member) as fh:
            wrapper = io.TextIOWrapper(fh, encoding="latin-1", newline="")
            reader = csv.DictReader(wrapper, delimiter=";")
            if not reader.fieldnames:
                raise RuntimeError("CSV de emendas sem cabeçalho.")

            col_map = {}
            for original in reader.fieldnames:
                col_map[normalize_column(original)] = original

            def get_col(row, norm_name):
                return normalize_text(row.get(col_map.get(norm_name, ""), ""))

            rows_processed = 0
            total_empenhado = Decimal("0")
            year_total_empenhado = Decimal("0")
            year_total_liquidado = Decimal("0")
            year_total_pago = Decimal("0")
            author_totals = defaultdict(lambda: Decimal("0"))
            author_party_totals = defaultdict(lambda: Decimal("0"))
            author_party_map = {}
            destination_totals = defaultdict(lambda: Decimal("0"))
            author_destination_totals = defaultdict(lambda: Decimal("0"))
            year_author_totals = defaultdict(lambda: Decimal("0"))
            year_author_party_map = {}
            year_destination_totals = defaultdict(lambda: Decimal("0"))

            for row in reader:
                rows_processed += 1
                ano_emenda = int(get_col(row, "ano_da_emenda") or "0")
                valor_empenhado = parse_currency(get_col(row, "valor_empenhado"))
                valor_liquidado = parse_currency(get_col(row, "valor_liquidado"))
                valor_pago = parse_currency(get_col(row, "valor_pago"))
                if ano_emenda == current_year:
                    year_total_empenhado += valor_empenhado
                    year_total_liquidado += valor_liquidado
                    year_total_pago += valor_pago
                if valor_empenhado == 0:
                    continue

                author = (
                    get_col(row, "nome_do_autor_da_emenda")
                    or "Autor não informado"
                )
                party = resolve_party(author, party_lookup)
                destination = (
                    get_col(row, "localidade_de_aplicacao_do_recurso")
                    or (
                        f"{get_col(row, 'municipio')} - {get_col(row, 'uf')}"
                        if get_col(row, "municipio") or get_col(row, "uf")
                        else "Destino não informado"
                    )
                )
                pair_key = f"{author}|||{destination}"
                author_party_key = f"{author}|||{party}"

                total_empenhado += valor_empenhado
                author_totals[author] += valor_empenhado
                author_party_totals[author_party_key] += valor_empenhado
                if author not in author_party_map or author_party_map[author] == "Nao identificado":
                    author_party_map[author] = party
                destination_totals[destination] += valor_empenhado
                author_destination_totals[pair_key] += valor_empenhado
                if ano_emenda == current_year:
                    year_author_totals[author] += valor_empenhado
                    year_destination_totals[destination] += valor_empenhado
                    if (
                        author not in year_author_party_map
                        or year_author_party_map[author] == "Nao identificado"
                    ):
                        year_author_party_map[author] = party

    return {
        "rows_processed": rows_processed,
        "total_empenhado": total_empenhado,
        "current_year": current_year,
        "current_year_totals": {
            "total_empenhado": year_total_empenhado,
            "total_liquidado": year_total_liquidado,
            "total_pago": year_total_pago,
        },
        "author_totals": author_totals,
        "author_party_totals": author_party_totals,
        "author_party_map": author_party_map,
        "destination_totals": destination_totals,
        "author_destination_totals": author_destination_totals,
        "current_year_author_totals": year_author_totals,
        "current_year_author_party_map": year_author_party_map,
        "current_year_destination_totals": year_destination_totals,
    }


def write_metadata(report):
    metrics = report["metrics"]
    source = report["source"]
    parallel = report.get("parallel_monitor", {})
    documents = parallel.get("documents", {})
    apoiamento = parallel.get("apoiamento", {})
    siop_snapshot = parallel.get("siop_snapshot", {})
    siop_details = parallel.get("siop_details", {})
    siop_totals = siop_snapshot.get("totals", {}) if isinstance(siop_snapshot, dict) else {}
    siop_history = report.get("siop_daily_history", [])
    siop_history_last_date = siop_history[-1].get("date", "") if siop_history else ""
    metadata = {
        "updated_at": report["generated_at"],
        "snapshot_date": report["snapshot_date"],
        "source_requested_url": source["requested_url"],
        "source_download_url": source["download_url"],
        "source_senado_reference_url": source["senado_reference_url"],
        "source_last_modified": source.get("last_modified", ""),
        "source_etag": source.get("etag", ""),
        "source_zip_bytes": source.get("zip_bytes", 0),
        "rows_processed": metrics["total_linhas_csv"],
        "authors_mapped": metrics["total_autores_mapeados"],
        "destinations_mapped": metrics["total_destinos_mapeados"],
        "current_year": metrics.get("current_year"),
        "current_year_total_empenhado": metrics.get("current_year_total_empenhado", 0),
        "current_year_total_liquidado": metrics.get("current_year_total_liquidado", 0),
        "current_year_total_pago": metrics.get("current_year_total_pago", 0),
        "documents_source_url": ((documents.get("source") or {}).get("download_url")) or "",
        "documents_last_modified": ((documents.get("source") or {}).get("last_modified")) or "",
        "apoiamento_source_url": ((apoiamento.get("source") or {}).get("download_url")) or "",
        "apoiamento_last_modified": ((apoiamento.get("source") or {}).get("last_modified")) or "",
        "siop_snapshot_available": bool(siop_snapshot.get("available")),
        "siop_snapshot_fallback_from_previous": bool(siop_snapshot.get("fallback_from_previous")),
        "siop_snapshot_last_update": siop_snapshot.get("last_update", ""),
        "siop_base_siafi_date": siop_snapshot.get("base_siafi_date", ""),
        "siop_dotacao_atual_emenda": siop_totals.get("dotacao_atual_emenda", 0),
        "siop_dotacao_inicial_emenda": siop_totals.get("dotacao_inicial_emenda", 0),
        "siop_snapshot_error": siop_snapshot.get("error", ""),
        "siop_details_available": bool(siop_details.get("available")),
        "siop_details_fallback_from_previous": bool(siop_details.get("fallback_from_previous")),
        "siop_details_rows_count": siop_details.get("rows_count", 0),
        "siop_details_unique_nro_emendas": siop_details.get("unique_nro_emendas", 0),
        "siop_details_sweep_steps": siop_details.get("sweep_steps", 0),
        "siop_details_error": siop_details.get("error", ""),
        "siop_history_days": len(siop_history),
        "siop_history_last_date": siop_history_last_date,
        "siop_broken_days": _siop_broken_days(siop_history),
    }
    META_FILE.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def _siop_broken_days(siop_history):
    """Conta quantos dias consecutivos no final o SIOP retornou tudo zero.

    Quando o Qlik muda o DOM, o Selenium quebra em silêncio e deixa os valores
    zerados. Esse contador ajuda a saber há quantos dias o scraper está falhando
    (para alertar o mantenedor do dashboard).
    """
    broken = 0
    for row in reversed(siop_history or []):
        if (
            to_float(row.get("empenhado", 0)) == 0
            and to_float(row.get("pago", 0)) == 0
            and to_float(row.get("dotacao_atual_emenda", 0)) == 0
        ):
            broken += 1
        else:
            break
    return broken


def save_report(report):
    REPORT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def save_state_from_current(current, report):
    payload = {
        "snapshot_date": report["snapshot_date"],
        "total_empenhado": to_float(current["total_empenhado"]),
        "author_totals": {k: to_float(v) for k, v in current["author_totals"].items()},
        "destination_totals": {k: to_float(v) for k, v in current["destination_totals"].items()},
        "author_destination_totals": {
            k: to_float(v) for k, v in current["author_destination_totals"].items()
        },
    }
    save_state(payload)


def should_skip_build(headers, metadata, force):
    if force:
        return False
    if not REPORT_FILE.exists():
        return False
    last_modified = normalize_text(headers.get("last_modified"))
    etag = normalize_text(headers.get("etag"))
    if last_modified and last_modified == normalize_text(metadata.get("source_last_modified")):
        return True
    if etag and etag == normalize_text(metadata.get("source_etag")):
        return True
    return False


def build_parallel_monitor_payload(report, party_lookup, party_lookup_meta):
    current_year = int((report.get("unico_year_summary") or {}).get("year") or dt.date.today().year)
    siop_rp_filters = list(SIOP_DEFAULT_RP_FILTERS)
    execucao_ano_corrente = fetch_execucao_ano_corrente(current_year)
    siop_snapshot = extract_siop_snapshot(current_year, siop_rp_filters)
    siop_details = extract_siop_details(party_lookup, current_year, siop_rp_filters)
    documents_monitor = build_documents_monitor(current_year, party_lookup)
    apoiamento_monitor = build_apoiamento_monitor(current_year)
    return {
        "year": current_year,
        "execucao_ano_corrente": execucao_ano_corrente,
        "siop_snapshot": siop_snapshot,
        "siop_details": siop_details,
        "documents": documents_monitor,
        "apoiamento": apoiamento_monitor,
        "party_lookup": party_lookup_meta,
    }


def build_dashboard(force=False):
    ensure_dirs()
    headers = fetch_headers(DOWNLOAD_URL)
    metadata = load_json(META_FILE, {})
    run_date = dt.date.today().isoformat()

    if should_skip_build(headers, metadata, force):
        report = load_json(REPORT_FILE, {})
        if not report:
            print("[skip] Fonte sem atualização detectada e sem report local para refresh.")
            return False

        run_ts = now_iso()
        party_lookup, party_lookup_meta = fetch_party_lookup()
        report["run_date"] = run_date
        report["snapshot_date"] = run_date
        report["generated_at"] = run_ts
        report["updated_at"] = run_ts
        # zera deltas para refletir que NADA mudou no Portal desde o último run
        metrics = report.setdefault("metrics", {})
        metrics["delta_liquido_desde_snapshot_anterior"] = 0.0
        metrics["delta_positivo_desde_snapshot_anterior"] = 0.0
        metrics["delta_negativo_desde_snapshot_anterior"] = 0.0
        metrics["autores_com_aumento"] = 0
        metrics["destinos_com_aumento"] = 0
        metrics["pares_autor_destino_com_aumento"] = 0
        report["parallel_monitor"] = build_parallel_monitor_payload(report, party_lookup, party_lookup_meta)
        apply_siop_fallback_from_previous(report)
        override_current_year_totals_from_documents(report)
        update_history(report, source_changed=False)
        update_siop_history(report)
        save_report(report)
        write_metadata(report)
        print("[ok] Refresh diário SIOP concluído (fonte principal sem mudança).")
        return True

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        zip_path = tmpdir_path / "emendas.zip"

        try:
            download_source(DOWNLOAD_URL, zip_path)
        except Exception:
            download_source(FALLBACK_ZIP_URL, zip_path)
            if not headers.get("final_url"):
                headers["final_url"] = FALLBACK_ZIP_URL

        party_lookup, party_lookup_meta = fetch_party_lookup()
        current = build_current_aggregates(zip_path, party_lookup)
        previous = load_state()
        has_previous = bool(previous.get("snapshot_date")) and bool(previous.get("author_totals"))
        report = build_report(current, previous, headers, zip_path.stat().st_size, has_previous)
        report["run_date"] = run_date
        report["parallel_monitor"] = build_parallel_monitor_payload(report, party_lookup, party_lookup_meta)
        apply_siop_fallback_from_previous(report)
        override_current_year_totals_from_documents(report)
        update_history(report)
        update_siop_history(report)
        save_report(report)
        write_metadata(report)
        save_state_from_current(current, report)

    print(
        "[ok] emendas-dashboard atualizado:",
        f"delta_positivo={report['metrics']['delta_positivo_desde_snapshot_anterior']:.2f}",
        f"autores_com_aumento={report['metrics']['autores_com_aumento']}",
    )
    return True


def parse_args():
    parser = argparse.ArgumentParser(
        description="Atualiza dados da dashboard de emendas (monitor diário por variação de empenho)."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Força rebuild mesmo quando ETag/Last-Modified não mudaram.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    build_dashboard(force=args.force)


if __name__ == "__main__":
    main()
