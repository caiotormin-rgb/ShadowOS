"""Web search and page reading through OpenClaw's Firecrawl provider.

The Firecrawl key stays in OpenClaw's config; this module only calls the CLI.
Everything returned is untrusted page content. Answers are cached for 7 days.
"""

from __future__ import annotations

import json
import re
import subprocess
import time

import notify

CACHE_SECONDS = 7 * 86400
RUN = subprocess.run
STATS = {"searches": 0, "fetches": 0}
WRAPPER = re.compile(r"<<<[^<>]*EXTERNAL_UNTRUSTED_CONTENT[^<>]*>>>|^Source: Web (?:Search|Fetch)\s*\n---\s*\n", re.M)


def clean(text) -> str:
    return WRAPPER.sub("", str(text or "")).strip()


def _cli(args: list[str], timeout: int):
    try:
        done = RUN([notify.openclaw_bin(), "infer", "web", *args, "--provider", "firecrawl", "--json"],
                   capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0 or "{" not in (done.stdout or ""):
        return None
    try:
        data = json.loads(done.stdout[done.stdout.find("{"):])
    except json.JSONDecodeError:
        return None
    outputs = data.get("outputs") or []
    return (outputs[0] or {}).get("result") if outputs else None


def _cached(conn, key: str):
    row = conn.execute("SELECT fetched_at, body FROM cache WHERE key=?", (key,)).fetchone()
    if row and time.time() - row["fetched_at"] < CACHE_SECONDS:
        return json.loads(row["body"])
    return None


def _keep(conn, key: str, value) -> None:
    with conn:
        conn.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?)", (key, time.time(), json.dumps(value)))


def search(conn, query: str, limit: int = 10) -> list[dict]:
    key = f"web:search:{limit}:{query}"
    hit = _cached(conn, key)
    if hit is not None:
        return hit
    STATS["searches"] += 1
    result = _cli(["search", f"--query={query}", f"--limit={limit}"], 120)
    if result is None:
        return []
    rows = [{"url": r.get("url", ""), "title": clean(r.get("title")), "description": clean(r.get("description"))}
            for r in result.get("results", []) if str(r.get("url", "")).startswith("http")]
    _keep(conn, key, rows)
    return rows


def fetch(conn, url: str) -> dict | None:
    key = f"web:fetch:{url}"
    hit = _cached(conn, key)
    if hit is not None:
        return hit
    STATS["fetches"] += 1
    result = _cli(["fetch", f"--url={url}"], 150)
    if not result or not str(result.get("status", "200")).startswith("2"):
        return None
    page = {"url": result.get("finalUrl") or url, "title": clean(result.get("title")), "text": clean(result.get("text"))}
    if not page["text"]:
        return None
    _keep(conn, key, page)
    return page
