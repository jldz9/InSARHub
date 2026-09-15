# -*- coding: utf-8 -*-
"""
Shared HTTP helper for the pair-quality feature extractors.

Why this exists
---------------
Several independent code paths ask Open-Meteo for the *same* ERA5 archive
range during a single pair-selection run: a pre-filter that has since been
removed, the background ``PairQualityDB`` build, and the on-demand
``/api/pair-quality`` scorer.  Firing those simultaneously earned an immediate
``HTTP 429 {"reason": "Too many concurrent requests"}`` from the archive
endpoint, and because nothing retried, a whole stack was judged with no
weather data at all.

This module gives every extractor one door to the network:

* **Concurrency cap** — at most ``MAX_CONCURRENT`` requests in flight per
  *host* (``HOST_LIMITS`` tightens individual ones), so a thread pool cannot
  stampede a rate-limited host, and a slow host cannot block an unrelated one.
* **Retry with backoff** — 429/5xx/timeout are retried with exponential
  backoff and jitter, honouring ``Retry-After`` when the server sends it.
* **Request de-duplication** — identical URLs are fetched once.  ERA5 archive
  data is immutable historical reanalysis, so a successful payload is reused
  for the life of the process; a failure is remembered only briefly, which
  stops three callers from each grinding through a full retry chain.

De-duplication here is exact-URL only.  Two callers wanting overlapping *date
ranges* build different URLs and would both come through; collapsing those into
one request is ``_archive``'s job, one layer up.

Failures raise :class:`FetchError`.  Callers are expected to degrade
gracefully — and, importantly, to *not* cache the result of a failure.
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict

logger = logging.getLogger(__name__)

# Maximum simultaneous outbound requests from this process, per host.  The cap
# is per-host on purpose: a shared global one would make an unrelated slow host
# (S3 coherence rasters, say) queue behind a rate-limited one.
MAX_CONCURRENT: int = 2

# Hosts that need a tighter cap than the default.  Open-Meteo's free archive
# endpoint answers a burst with 429 {"reason": "Too many concurrent requests"}
# long before any daily quota is touched, and since _archive collapses a whole
# stack into a single request there is nothing to gain from overlapping them.
HOST_LIMITS: dict[str, int] = {
    "archive-api.open-meteo.com": 1,
}

# Retry policy for transient failures.
MAX_ATTEMPTS: int = 4
BACKOFF_BASE: float = 2.0      # seconds; doubled each attempt
BACKOFF_CAP: float = 30.0      # never sleep longer than this between attempts

# How long a failed URL is remembered so concurrent callers fail fast instead
# of each running their own retry chain.  Short: the next run must try again.
NEGATIVE_TTL: float = 60.0

# Successful payloads are immutable reanalysis data — cap the memo rather than
# expiring it.
MEMO_MAX: int = 256

_RETRY_STATUS = {429, 500, 502, 503, 504}

_semaphores: dict[str, threading.BoundedSemaphore] = {}
_semaphores_guard = threading.Lock()

_memo: OrderedDict[str, dict] = OrderedDict()
_negative: dict[str, tuple[float, str]] = {}
_memo_lock = threading.Lock()

_url_locks: dict[str, threading.Lock] = {}
_url_locks_guard = threading.Lock()


class FetchError(Exception):
    """A request failed after exhausting retries (or failed unretryably)."""


# ── Memo helpers ──────────────────────────────────────────────────────────────

def _memo_get(url: str) -> dict | None:
    with _memo_lock:
        payload = _memo.get(url)
        if payload is not None:
            _memo.move_to_end(url)
        return payload


def _memo_put(url: str, payload: dict) -> None:
    with _memo_lock:
        _memo[url] = payload
        _memo.move_to_end(url)
        while len(_memo) > MEMO_MAX:
            _memo.popitem(last=False)
        _negative.pop(url, None)


def _negative_get(url: str) -> str | None:
    """Return the remembered error message for *url*, if still fresh."""
    with _memo_lock:
        entry = _negative.get(url)
        if entry is None:
            return None
        expires_at, message = entry
        if time.monotonic() >= expires_at:
            _negative.pop(url, None)
            return None
        return message


def _negative_put(url: str, message: str) -> None:
    with _memo_lock:
        _negative[url] = (time.monotonic() + NEGATIVE_TTL, message)


def clear_cache() -> None:
    """Forget every memoised response. Intended for tests and force-refresh."""
    with _memo_lock:
        _memo.clear()
        _negative.clear()


def _url_lock(url: str) -> threading.Lock:
    """Return the per-URL lock, so concurrent callers coalesce into one fetch."""
    with _url_locks_guard:
        lock = _url_locks.get(url)
        if lock is None:
            # Drop locks nobody is holding before the table can grow without
            # bound in a long-lived GUI process.
            if len(_url_locks) > MEMO_MAX * 2:
                for key, value in list(_url_locks.items()):
                    if not value.locked():
                        del _url_locks[key]
            lock = _url_locks[url] = threading.Lock()
        return lock


# ── Request ───────────────────────────────────────────────────────────────────

def _retry_after_seconds(exc: urllib.error.HTTPError) -> float | None:
    """Parse a Retry-After header expressed in seconds, if present and sane."""
    raw = exc.headers.get("Retry-After") if exc.headers else None
    if not raw:
        return None
    try:
        return max(0.0, min(float(raw.strip()), BACKOFF_CAP))
    except (TypeError, ValueError):
        return None


def _sleep_for(attempt: int, override: float | None = None) -> float:
    """Exponential backoff with jitter, so parallel retries do not re-collide."""
    if override is not None:
        delay = override
    else:
        delay = min(BACKOFF_BASE * (2 ** attempt), BACKOFF_CAP)
    return delay * (0.5 + random.random() / 2.0)


def _host_semaphore(url: str) -> threading.BoundedSemaphore:
    """Return the concurrency gate for *url*'s host, creating it on first use."""
    host = urllib.parse.urlsplit(url).hostname or ""
    with _semaphores_guard:
        sem = _semaphores.get(host)
        if sem is None:
            limit = HOST_LIMITS.get(host, MAX_CONCURRENT)
            sem = _semaphores[host] = threading.BoundedSemaphore(limit)
        return sem


def _request_once(url: str, timeout: float) -> dict:
    with _host_semaphore(url):
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read())


def _request_with_retry(url: str, timeout: float, attempts: int) -> dict:
    last: Exception | None = None

    for attempt in range(attempts):
        try:
            return _request_once(url, timeout)

        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code not in _RETRY_STATUS:
                raise FetchError(f"HTTP Error {exc.code}: {exc.reason}") from exc
            delay = _sleep_for(attempt, _retry_after_seconds(exc))

        except Exception as exc:       # timeout, URLError, malformed JSON
            last = exc
            delay = _sleep_for(attempt)

        if attempt == attempts - 1:
            break
        logger.debug("Request failed (%s) — retrying in %.1fs [%d/%d]",
                     last, delay, attempt + 1, attempts)
        time.sleep(delay)

    raise FetchError(str(last)) from last


def get_json(
    url: str,
    *,
    timeout: float = 30.0,
    attempts: int | None = None,
    memo: bool = True,
) -> dict:
    """GET *url* and return the decoded JSON body.

    Retries transient failures, caps process-wide concurrency, and coalesces
    identical concurrent requests into a single fetch.

    Raises
    ------
    FetchError
        The request failed, after retries where retrying could have helped.
        Callers must treat this as "no data" and must not cache a result
        derived from it.
    """
    attempts = MAX_ATTEMPTS if attempts is None else attempts

    if memo:
        hit = _memo_get(url)
        if hit is not None:
            return hit

    with _url_lock(url):
        # Re-check: another thread may have fetched this while we queued.
        if memo:
            hit = _memo_get(url)
            if hit is not None:
                return hit
            failure = _negative_get(url)
            if failure is not None:
                raise FetchError(failure)

        try:
            payload = _request_with_retry(url, timeout, attempts)
        except FetchError as exc:
            if memo:
                _negative_put(url, str(exc))
            raise

        if memo:
            _memo_put(url, payload)
        return payload
