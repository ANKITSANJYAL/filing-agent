"""Rate-limited, cache-first HTTP transport for SEC EDGAR.

Knows nothing about EDGAR's schema — it enforces SEC etiquette (declared User-Agent,
<10 req/s) and makes re-runs free by never re-fetching a file already on disk.
Schema knowledge lives in the modules that call this one.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any, Final

import httpx

# SEC's ceiling is 10 req/s; we target 8. The ceiling is where they start blocking,
# not where we should aim.
DEFAULT_RATE_PER_SECOND: Final[float] = 8.0
DEFAULT_BURST: Final[float] = 8.0
RETRY_STATUSES: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS: Final[int] = 3


class SecAccessDenied(RuntimeError):
    """SEC returned 403 — almost always a User-Agent problem, not a bug in our code."""


class TokenBucket:
    """Classic token bucket: `capacity` tokens, refilled at `rate` per second.

    Chosen over a fixed sleep between calls because it permits a short burst after
    idle time while still holding the *average* under SEC's ceiling, which is what
    they actually enforce.
    """

    def __init__(self, rate_per_second: float = DEFAULT_RATE_PER_SECOND,
                 capacity: float = DEFAULT_BURST) -> None:
        if rate_per_second <= 0 or capacity <= 0:
            raise ValueError("rate and capacity must be positive")
        self._rate = rate_per_second
        self._capacity = capacity
        self._tokens = capacity
        self._last_refill = time.monotonic()
        # Held across the sleep on purpose: serializing waiters is exactly the
        # behaviour we want from a global rate limit.
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        self._tokens = min(self._capacity, self._tokens + (now - self._last_refill) * self._rate)
        self._last_refill = now

    def try_acquire(self, tokens: float = 1.0) -> bool:
        """Non-blocking variant: take tokens if available, else return False.

        Blocking is right for our own SEC crawling (we want to wait our turn) but wrong
        for serving HTTP, where a queued request ties up a worker instead of shedding
        load. Same bucket, two arrival policies.
        """
        with self._lock:
            self._refill()
            if self._tokens < tokens:
                return False
            self._tokens -= tokens
            return True

    def acquire(self, tokens: float = 1.0) -> float:
        """Block until `tokens` are available. Returns seconds actually waited."""
        if tokens > self._capacity:
            raise ValueError(f"cannot acquire {tokens} from a bucket of capacity {self._capacity}")
        waited = 0.0
        with self._lock:
            while True:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return waited
                delay = (tokens - self._tokens) / self._rate
                time.sleep(delay)
                waited += delay


def validate_user_agent(user_agent: str) -> str:
    """Fail locally on a User-Agent SEC would reject, rather than after a 403.

    A data assertion at a boundary: SEC documents the form "Name email@domain",
    so a value missing an address is wrong before we ever send it.
    """
    ua = (user_agent or "").strip()
    if not ua:
        raise ValueError(
            "SEC_USER_AGENT is unset. SEC requires a declared User-Agent and returns 403 "
            'without one. Set it in .env, e.g. SEC_USER_AGENT="Jane Doe jane@example.com"'
        )
    if "@" not in ua:
        raise ValueError(
            f"SEC_USER_AGENT={ua!r} has no contact address. SEC expects "
            '"Name email@domain" and may block requests that omit one.'
        )
    return ua


class EdgarClient:
    """Cache-first HTTP client for EDGAR. Idempotent: re-runs never re-hit SEC."""

    def __init__(
        self,
        user_agent: str | None = None,
        cache_dir: Path | str = "data/raw",
        rate_per_second: float = DEFAULT_RATE_PER_SECOND,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.user_agent = validate_user_agent(user_agent or os.environ.get("SEC_USER_AGENT", ""))
        self.cache_dir = Path(cache_dir)
        self._bucket = TokenBucket(rate_per_second)
        self._client = httpx.Client(
            headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=timeout,
            transport=transport,
            follow_redirects=True,
        )
        self.network_requests = 0  # observability: how much did this run actually cost SEC?

    def _request(self, url: str) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            self._bucket.acquire()
            self.network_requests += 1
            try:
                response = self._client.get(url)
            except httpx.TransportError as exc:  # network flake — retryable
                last_exc = exc
                time.sleep(2**attempt)
                continue
            if response.status_code == 403:
                raise SecAccessDenied(
                    f"SEC returned 403 for {url}. This is a User-Agent problem, not a code "
                    f"problem. Sent: {self.user_agent!r}. Response: {response.text[:200]!r}"
                )
            if response.status_code in RETRY_STATUSES:
                time.sleep(2**attempt)
                continue
            response.raise_for_status()
            return response
        raise RuntimeError(f"{url} failed after {MAX_ATTEMPTS} attempts") from last_exc

    def get_json(self, url: str) -> Any:
        return self._request(url).json()

    def download(self, url: str, dest: Path | str) -> tuple[Path, bool]:
        """Fetch `url` to `dest` unless it is already cached.

        Returns (path, hit_network). Writes via a .part file then renames, so an
        interrupted run can never leave a truncated file that later looks cached.
        """
        path = Path(dest)
        if path.exists() and path.stat().st_size > 0:
            return path, False
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(self._request(url).content)
        tmp.rename(path)
        return path, True

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> EdgarClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
