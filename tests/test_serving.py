"""Serving-layer tests: auth, rate limiting, and MCP/agent tool parity.

No database or API key required — the app takes injectable factories.
"""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from filing_agent.agent.schemas import Memo  # noqa: E402
from filing_agent.ingest.edgar_client import TokenBucket  # noqa: E402
from filing_agent.serving.api import API_KEY_ENV, create_app  # noqa: E402

KEY = "test-key-123"


class _FakeCursor:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, *a, **k): self._n = 7
    def fetchone(self): return (7,)


class _FakeConn:
    def cursor(self): return _FakeCursor()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV, KEY)
    app = create_app(conn_factory=_FakeConn,
                     client_factory=lambda: object(),
                     encoder_factory=lambda: object())
    return TestClient(app)


# --- Authentication: fail closed ------------------------------------------------

def test_request_without_a_key_is_rejected(client) -> None:
    assert client.post("/ask", json={"question": "What was Apple's revenue?"}).status_code == 401


def test_request_with_a_wrong_key_is_rejected(client) -> None:
    r = client.post("/ask", json={"question": "What was Apple's revenue?"},
                    headers={"X-API-Key": "nope"})
    assert r.status_code == 401


def test_service_refuses_to_run_open_when_no_key_is_configured(monkeypatch) -> None:
    """Failing closed is the only safe default for something that spends money."""
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    app = create_app(conn_factory=_FakeConn, client_factory=lambda: object(),
                     encoder_factory=lambda: object())
    r = TestClient(app).post("/ask", json={"question": "anything at all"},
                             headers={"X-API-Key": "whatever"})
    assert r.status_code == 503


def test_health_needs_no_key(client) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["chunks"] == 7


# --- Request validation ---------------------------------------------------------

def test_absurdly_long_question_is_rejected_by_the_contract(client) -> None:
    r = client.post("/ask", json={"question": "x" * 5000}, headers={"X-API-Key": KEY})
    assert r.status_code == 422


def test_empty_question_is_rejected(client) -> None:
    r = client.post("/ask", json={"question": ""}, headers={"X-API-Key": KEY})
    assert r.status_code == 422


# --- Rate limiting --------------------------------------------------------------

def test_bucket_sheds_load_without_blocking() -> None:
    """try_acquire returns False instead of queueing — a queued HTTP request would
    tie up a worker rather than shed load."""
    bucket = TokenBucket(rate_per_second=1.0, capacity=2)
    assert bucket.try_acquire() and bucket.try_acquire()
    assert bucket.try_acquire() is False


def test_burst_is_allowed_then_limited(client, monkeypatch) -> None:
    """The agent is stubbed, so this measures the limiter rather than the model."""
    monkeypatch.setattr("filing_agent.serving.api.answer",
                        lambda *a, **k: Memo(answer_summary="s", trace_id="t"))
    codes = [client.post("/ask", json={"question": "What was Apple's revenue?"},
                         headers={"X-API-Key": KEY}).status_code for _ in range(8)]
    assert 200 in codes and 429 in codes


def test_response_carries_the_disclaimer(client, monkeypatch) -> None:
    """PROPOSAL.md §3 requires the not-investment-advice disclaimer on the interface."""
    monkeypatch.setattr("filing_agent.serving.api.answer",
                        lambda *a, **k: Memo(answer_summary="s", trace_id="t"))
    body = client.post("/ask", json={"question": "What was Apple's revenue?"},
                       headers={"X-API-Key": KEY}).json()
    assert "not investment advice" in body["disclaimer"].lower()


# --- MCP parity -----------------------------------------------------------------

def test_mcp_server_exposes_the_same_tools_as_the_agent() -> None:
    """MCP is a transport. A capability gap between it and the agent would be a bug."""
    pytest.importorskip("mcp")
    from filing_agent.serving.mcp_server import TOOL_DESCRIPTIONS, build_server
    server = build_server(_FakeConn(), encoder=None)
    assert server is not None
    assert set(TOOL_DESCRIPTIONS) == {"search_filings", "lookup_financial_fact", "calculate"}


def test_mcp_tool_descriptions_tell_the_caller_to_prefer_xbrl() -> None:
    """The description is the only instruction an MCP client gets — it has to carry
    the rule that XBRL outranks numbers found in filing text."""
    pytest.importorskip("mcp")
    from filing_agent.serving.mcp_server import TOOL_DESCRIPTIONS
    assert "prefer this" in TOOL_DESCRIPTIONS["lookup_financial_fact"].lower()
    assert "not its ticker" in TOOL_DESCRIPTIONS["search_filings"].lower()
