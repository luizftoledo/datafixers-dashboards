#!/usr/bin/env python3
"""
Pega posts do Bluesky de presidentes/figuras políticas e devolve no schema
do Lulômetro (id, url, source, type, president_slug, president, mandate,
date, title, location, description, text, word_count, updated_at).

API pública: https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed
Não precisa de auth pra perfis públicos. Sem rate limit duro pra polling
moderado (algumas centenas de req/hora).
"""
from __future__ import annotations
import datetime as dt
import hashlib
import re
import time
from typing import Iterable

import requests

BSKY_API = "https://public.api.bsky.app/xrpc"

BLUESKY_ACCOUNTS = [
    {
        "handle": "lulaoficialbluesky.bsky.social",
        "president_slug": "luiz-inacio-lula-da-silva",
        "president": "Luiz Inacio Lula da Silva",
        "mandate": "Lula 3 (2023-)",
    },
]


def _bsky_get(path: str, params: dict, timeout: int = 15) -> dict:
    r = requests.get(f"{BSKY_API}/{path}", params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _post_to_record(post: dict, account: dict) -> dict | None:
    """Converte um item da feed do Bluesky pro schema do Lulômetro."""
    p = post.get("post", {})
    record = p.get("record", {})
    text = (record.get("text") or "").strip()
    if not text:
        return None
    created = record.get("createdAt", "")
    try:
        date_iso = dt.datetime.fromisoformat(created.replace("Z", "+00:00")).date().isoformat()
    except Exception:
        date_iso = ""
    uri = p.get("uri", "")
    cid = p.get("cid", "")
    # at://did:plc:.../app.bsky.feed.post/RECORD_ID
    record_id_m = re.search(r"/app\.bsky\.feed\.post/([^/]+)$", uri)
    record_id = record_id_m.group(1) if record_id_m else cid[:16]
    pub_url = f"https://bsky.app/profile/{account['handle']}/post/{record_id}"
    title = text.split("\n", 1)[0][:120].strip() or "Post do Bluesky"
    rec_id = "bsky_" + hashlib.sha1((uri or pub_url).encode("utf-8")).hexdigest()[:18]
    return {
        "id": rec_id,
        "url": pub_url,
        "source": "bluesky",
        "type": "post",
        "president_slug": account["president_slug"],
        "president": account["president"],
        "mandate": account["mandate"],
        "date": date_iso,
        "title": title,
        "location": "",
        "description": "",
        "text": text,
        "word_count": len(re.findall(r"\w+", text)),
        "updated_at": created or "",
    }


def fetch_account_posts(
    handle: str,
    president_slug: str,
    president: str,
    mandate: str,
    max_pages: int = 50,
    sleep_between: float = 0.3,
) -> list[dict]:
    """Pagina pra trás até o final da feed (ou max_pages * 100 posts).
    Retorna lista de records no schema do Lulômetro."""
    account = {
        "handle": handle,
        "president_slug": president_slug,
        "president": president,
        "mandate": mandate,
    }
    out: list[dict] = []
    cursor: str | None = None
    for page in range(max_pages):
        params = {"actor": handle, "limit": 100}
        if cursor:
            params["cursor"] = cursor
        try:
            data = _bsky_get("app.bsky.feed.getAuthorFeed", params)
        except Exception as exc:
            print(f"   bsky erro pág {page}: {exc}", flush=True)
            break
        feed = data.get("feed", [])
        if not feed:
            break
        for item in feed:
            rec = _post_to_record(item, account)
            if rec:
                out.append(rec)
        cursor = data.get("cursor")
        if not cursor:
            break
        time.sleep(sleep_between)
    print(f"   bsky @{handle}: {len(out)} posts", flush=True)
    return out


def fetch_all_bluesky_records() -> list[dict]:
    out: list[dict] = []
    for acc in BLUESKY_ACCOUNTS:
        out.extend(
            fetch_account_posts(
                handle=acc["handle"],
                president_slug=acc["president_slug"],
                president=acc["president"],
                mandate=acc["mandate"],
            )
        )
    return out


if __name__ == "__main__":
    # Smoke test: imprime resumo
    recs = fetch_all_bluesky_records()
    if recs:
        print(f"Total: {len(recs)}")
        print(f"Mais recente: {recs[0]['date']} - {recs[0]['title'][:80]}")
        print(f"Mais antigo: {recs[-1]['date']} - {recs[-1]['title'][:80]}")
