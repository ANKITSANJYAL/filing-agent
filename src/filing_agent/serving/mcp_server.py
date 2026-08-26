"""MCP server (PROPOSAL.md §4.3 / T2.5) — the same tools over a second transport.

The tool bodies are imported from `agent.tools`, not reimplemented. That is the point of
the exercise: MCP is a transport, so a divergence between what the agent can do and what
Claude Desktop can do would be a bug, not a feature.

Run:  uv run python -m filing_agent.serving.mcp_server
"""

from __future__ import annotations

import json
from typing import Any, Final

from ..agent.tools import (
    CalculatorInput,
    RetrieveInput,
    ToolError,
    XbrlLookupInput,
    calculator,
    known_fiscal_years,
    known_tickers,
    retrieve,
    xbrl_lookup,
)

SERVER_NAME: Final[str] = "filing-agent"

TOOL_DESCRIPTIONS: Final[dict[str, str]] = {
    "search_filings": (
        "Search SEC 10-K/10-Q text for a company. Returns filing excerpts with the "
        "accession number, fiscal year and item section needed to cite them. "
        "Use the company's name as it appears in filings ('Apple'), not its ticker."
    ),
    "lookup_financial_fact": (
        "Look up an audited figure from SEC XBRL data — the authoritative source. "
        "Prefer this over any number found in filing text. Accepts plain labels such "
        "as 'revenue', 'net income' or 'total assets'; the correct XBRL concept for "
        "that specific company is resolved automatically."
    ),
    "calculate": (
        "Evaluate an arithmetic expression. Use this for any computation rather than "
        "doing arithmetic mentally, so the result is reproducible."
    ),
}


def _serialise(model: Any) -> str:
    return json.dumps(model.model_dump(mode="json"), indent=2, default=str)


def build_server(conn: Any, encoder: Any | None = None):
    """Construct the MCP server. `conn` and `encoder` are injected so tests can fake them."""
    # mcp 2.x renamed FastMCP -> MCPServer; the decorator surface is unchanged.
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(SERVER_NAME)

    @server.tool(description=TOOL_DESCRIPTIONS["search_filings"])
    def search_filings(
        query: str,
        tickers: list[str] | None = None,
        fiscal_years: list[int] | None = None,
        top_k: int = 5,
    ) -> str:
        vector = None
        if encoder is not None:
            vector = encoder.encode([query], normalize_embeddings=True)[0]
        out = retrieve(
            conn,
            RetrieveInput(query=query, tickers=tickers,
                          fiscal_years=fiscal_years, top_k=top_k),
            query_vector=vector,
        )
        return _serialise(out)

    @server.tool(description=TOOL_DESCRIPTIONS["lookup_financial_fact"])
    def lookup_financial_fact(ticker: str, concept: str, fiscal_year: int) -> str:
        return _serialise(
            xbrl_lookup(conn, XbrlLookupInput(
                ticker=ticker, concept=concept, fiscal_year=fiscal_year))
        )

    @server.tool(description=TOOL_DESCRIPTIONS["calculate"])
    def calculate(expression: str) -> str:
        try:
            return _serialise(calculator(CalculatorInput(expression=expression)))
        except ToolError as exc:
            # Returned, not raised: the caller should see why and retry, not get a
            # transport-level failure for a bad expression.
            return json.dumps({"error": str(exc), "expression": expression})

    @server.resource("filing-agent://corpus")
    def corpus_scope() -> str:
        """What this server can answer about — stops callers asking out of scope."""
        return json.dumps({
            "tickers": list(known_tickers()),
            "fiscal_years": list(known_fiscal_years()),
            "forms": ["10-K", "10-Q"],
            "note": (
                "Research and citation tool over public SEC filings. Not investment "
                "advice. Numeric answers are verified against SEC XBRL data."
            ),
        }, indent=2)

    return server


def main() -> None:  # pragma: no cover - process entry point
    from ..retrieval import db
    from ..retrieval.embed import load_encoder

    build_server(db.connect(), load_encoder()).run()


if __name__ == "__main__":  # pragma: no cover
    main()
