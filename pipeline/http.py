"""One HTTP client, with the manners every free API in SPEC §5 asks for.

Three things live here because getting them wrong is how a free data source
stops being free:

* a User-Agent that can be traced back to a human. Wikimedia and the SEC both
  return 403 to a generic agent; this exact string was verified to return 200.
* per-host spacing. GDELT answers 429 above one request every five seconds, and
  CoinGecko answers 429 within a handful of calls unauthenticated.
* a raw response cache, so re-running the pipeline on the same day re-reads
  disk instead of the network. That is what makes `fetch` idempotent (§10) and
  is also the politest thing we can do. It stores the body base64-encoded: an
  earlier version stored `response.text`, which silently corrupted every binary
  payload, because decoding arbitrary bytes as UTF-8 replaces each invalid byte
  with U+FFFD and re-encoding then yields different bytes. The GPR workbook
  arrived 23% larger with its OLE2 magic replaced by three replacement
  characters. Entries written by that version are still readable and are
  treated as text.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from typing import Any

import httpx

from pipeline import paths

UA = ("MiniLizardTerminal/0.1 (personal research dashboard; "
      "+https://github.com/MinimumLizard/Crypto; contact via repo issues)")

# Minimum seconds between calls to a host. Values come from the probe, not from
# the documentation: GDELT states one per five seconds and enforces it.
HOST_SPACING = {
    # GDELT documents one request per five seconds. From a shared egress that
    # budget is contended and five seconds still returns 429, so this is set
    # slower than the documented minimum on purpose.
    "api.gdeltproject.org": 8.0,
    "api.coingecko.com": 2.5,
    "community-api.coinmetrics.io": 1.0,
    "api.llama.fi": 0.4,
    "wikimedia.org": 1.0,
    "gamma-api.polymarket.com": 1.0,
    "api.elections.kalshi.com": 1.0,
    "www.matteoiacoviello.com": 1.0,
    "www.policyuncertainty.com": 1.0,
    "hub.snapshot.org": 1.0,
    "api.github.com": 1.0,
}
DEFAULT_SPACING = 0.15

_last_call: dict[str, float] = {}


class FetchError(RuntimeError):
    """Raised inside a fetcher, caught by it, turned into a health row."""


def _wait(host: str) -> None:
    gap = HOST_SPACING.get(host, DEFAULT_SPACING)
    previous = _last_call.get(host)
    if previous is not None:
        remaining = gap - (time.monotonic() - previous)
        if remaining > 0:
            time.sleep(remaining)
    _last_call[host] = time.monotonic()


def _cache_key(method: str, url: str, params: Any, body: Any) -> str:
    blob = json.dumps([method, url, params, body], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


def request(
    url: str,
    *,
    method: str = "GET",
    params: dict | None = None,
    json_body: dict | None = None,
    headers: dict | None = None,
    cache_hours: float = 0.0,
    retries: int = 3,
    timeout: float = 45.0,
) -> httpx.Response:
    """Fetch, with spacing, retry-with-backoff and an optional disk cache.

    `cache_hours` of 0 disables the cache. Anything above 0 will re-serve a
    stored response younger than that, which is what makes a same-day re-run
    free. Raises FetchError on exhaustion; callers turn that into a health row.
    """
    host = httpx.URL(url).host
    key = _cache_key(method, url, params, json_body)
    cached = paths.RAW / f"{host}-{key}.json"

    if cache_hours > 0 and cached.exists():
        age_hours = (time.time() - cached.stat().st_mtime) / 3600
        if age_hours < cache_hours:
            stored = json.loads(cached.read_text())
            # "b64" is lossless. "body" is the old text-only format; an entry in
            # that format can only be served back as text, which is what it was.
            body = (base64.b64decode(stored["b64"]) if "b64" in stored
                    else stored["body"].encode())
            response = httpx.Response(
                status_code=stored["status"], content=body,
                headers=stored.get("headers", {}), request=httpx.Request(method, url))
            return response

    last_error = ""
    for attempt in range(retries):
        _wait(host)
        try:
            with httpx.Client(follow_redirects=True, timeout=timeout) as client:
                response = client.request(
                    method, url, params=params, json=json_body,
                    headers={"User-Agent": UA, **(headers or {})})
        except Exception as exc:  # noqa: BLE001 - every failure becomes a health row
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(2 ** attempt)
            continue

        # 429 and 5xx are worth waiting out; 4xx otherwise is a real answer.
        if response.status_code == 429 or response.status_code >= 500:
            last_error = f"HTTP {response.status_code}"
            retry_after = response.headers.get("retry-after")
            time.sleep(float(retry_after) if retry_after and retry_after.isdigit()
                       else 2 ** attempt * 2)
            continue

        if response.status_code >= 400:
            raise FetchError(f"HTTP {response.status_code}: {response.text[:180]}")

        if cache_hours > 0:
            paths.RAW.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps({
                "status": response.status_code,
                "b64": base64.b64encode(response.content).decode("ascii"),
                "headers": {"content-type": response.headers.get("content-type", "")},
            }))
        return response

    raise FetchError(f"gave up after {retries} attempts: {last_error}")


def get_json(url: str, **kwargs) -> Any:
    return request(url, **kwargs).json()


def post_json(url: str, json_body: dict, **kwargs) -> Any:
    return request(url, method="POST", json_body=json_body, **kwargs).json()
