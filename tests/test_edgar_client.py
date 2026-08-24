"""Transport-layer tests for the EDGAR client. No network: httpx.MockTransport throughout.

Covers the three things that would actually hurt us in production — exceeding SEC's
rate limit, sending a User-Agent SEC rejects, and re-downloading cached filings.
"""

import time

import httpx
import pytest

from filing_agent.ingest.edgar_client import (
    EdgarClient,
    SecAccessDenied,
    TokenBucket,
    validate_user_agent,
)

VALID_UA = "Test User test@example.com"


def _transport(status: int = 200, body: bytes = b"<html>10-K</html>", counter: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if counter is not None:
            counter.append(request.url)
        return httpx.Response(status, content=body)

    return httpx.MockTransport(handler)


# --- Rate limiting -------------------------------------------------------------

def test_burst_is_allowed_up_to_capacity() -> None:
    bucket = TokenBucket(rate_per_second=100.0, capacity=5)
    assert sum(bucket.acquire() for _ in range(5)) == 0.0


def test_average_rate_is_enforced_once_burst_is_spent() -> None:
    """The invariant SEC cares about: 20 requests through a rate-100 bucket with a
    burst of 2 cannot complete faster than (20 - 2) / 100 seconds."""
    rate, capacity, n = 100.0, 2.0, 20
    bucket = TokenBucket(rate_per_second=rate, capacity=capacity)
    start = time.monotonic()
    for _ in range(n):
        bucket.acquire()
    assert time.monotonic() - start >= (n - capacity) / rate


def test_cannot_acquire_more_than_capacity() -> None:
    with pytest.raises(ValueError):
        TokenBucket(rate_per_second=8.0, capacity=8).acquire(9)


# --- User-Agent (SEC etiquette, CLAUDE.md §4) ----------------------------------

def test_valid_user_agent_is_accepted() -> None:
    assert validate_user_agent(VALID_UA) == VALID_UA


def test_missing_user_agent_fails_before_any_request(monkeypatch) -> None:
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    with pytest.raises(ValueError, match="SEC_USER_AGENT is unset"):
        EdgarClient(transport=_transport())


def test_malformed_user_agent_without_contact_is_rejected() -> None:
    """Deliberate malformed-header test: see the failure, don't just handle it."""
    with pytest.raises(ValueError, match="no contact address"):
        validate_user_agent("filing-agent-bot/1.0")


def test_user_agent_header_is_actually_sent() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"] = request.headers["User-Agent"]
        return httpx.Response(200, json={})

    with EdgarClient(user_agent=VALID_UA, transport=httpx.MockTransport(handler)) as client:
        client.get_json("https://data.sec.gov/submissions/CIK0000320193.json")
    assert seen["ua"] == VALID_UA


def test_403_raises_a_message_that_names_the_real_cause() -> None:
    transport = _transport(403, b"Request Rate Threshold")
    with (
        EdgarClient(user_agent=VALID_UA, transport=transport) as client,
        pytest.raises(SecAccessDenied, match="User-Agent problem"),
    ):
        client.get_json("https://data.sec.gov/anything.json")


# --- Cache-first behaviour (idempotent re-runs) --------------------------------

def test_first_download_writes_file_and_hits_network(tmp_path) -> None:
    calls: list = []
    dest = tmp_path / "NVDA" / "0001045810-24-000029" / "nvda-10k.htm"
    with EdgarClient(user_agent=VALID_UA, transport=_transport(counter=calls)) as client:
        path, hit_network = client.download("https://sec.gov/x.htm", dest)
    assert hit_network is True
    assert path.read_bytes() == b"<html>10-K</html>"
    assert len(calls) == 1


def test_second_download_is_free(tmp_path) -> None:
    calls: list = []
    dest = tmp_path / "cached.htm"
    with EdgarClient(user_agent=VALID_UA, transport=_transport(counter=calls)) as client:
        client.download("https://sec.gov/x.htm", dest)
        _, hit_network = client.download("https://sec.gov/x.htm", dest)
        assert client.network_requests == 1
    assert hit_network is False
    assert len(calls) == 1


def test_no_partial_file_is_left_behind_on_failure(tmp_path) -> None:
    """A truncated .part must never be renamed into place and later look cached."""
    dest = tmp_path / "boom.htm"
    with (
        EdgarClient(user_agent=VALID_UA, transport=_transport(404)) as client,
        pytest.raises(httpx.HTTPStatusError),
    ):
        client.download("https://sec.gov/missing.htm", dest)
    assert not dest.exists()
    assert list(tmp_path.glob("*.part")) == []
