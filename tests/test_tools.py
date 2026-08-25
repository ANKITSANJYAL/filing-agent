"""Tool-contract tests. The calculator ones are security tests, not arithmetic tests."""

import pytest

from filing_agent.agent.tools import (
    CalculatorInput,
    ToolError,
    XbrlLookupInput,
    calculator,
    resolve_concept,
    xbrl_lookup,
)

# --- calculator: model output is untrusted input -------------------------------

@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("130497 - 60922", 69575.0),
        ("2 ** 10", 1024.0),
        ("-5 + 3", -2.0),
        ("7 % 3", 1.0),
    ],
)
def test_arithmetic_is_evaluated(expression, expected) -> None:
    assert calculator(CalculatorInput(expression=expression)).value == expected


def test_percentage_change_matches_the_tier1_key_formula() -> None:
    """The agent computes YoY change with this tool, not in its head."""
    out = calculator(CalculatorInput(expression="(130497 - 60922) / 60922 * 100"))
    assert out.value == pytest.approx(114.2036, rel=1e-4)


@pytest.mark.parametrize("expression", [
    "__import__('os').system('rm -rf /')",
    "open('/etc/passwd').read()",
    "(1).__class__.__bases__",
    "[x for x in range(10)]",
    "print(1)",
    "len('abc')",
])
def test_code_execution_attempts_are_refused(expression) -> None:
    """`eval` on model output is RCE with extra steps; the AST allowlist blocks it."""
    with pytest.raises(ToolError):
        calculator(CalculatorInput(expression=expression))


def test_division_by_zero_is_a_tool_error_not_a_crash() -> None:
    with pytest.raises(ToolError, match="division by zero"):
        calculator(CalculatorInput(expression="1 / 0"))


def test_unparseable_expression_is_a_tool_error() -> None:
    with pytest.raises(ToolError, match="could not parse"):
        calculator(CalculatorInput(expression="130497 +"))


def test_booleans_are_rejected_as_operands() -> None:
    with pytest.raises(ToolError, match="unsupported literal"):
        calculator(CalculatorInput(expression="True + 1"))


def test_overlong_expression_is_rejected_by_the_contract() -> None:
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        CalculatorInput(expression="1+" * 200 + "1")


# --- concept resolution: the D-0019 finding, made usable -----------------------

def test_revenue_resolves_per_filer() -> None:
    """No revenue tag is common to all eight; the model shouldn't have to know that."""
    assert resolve_concept("NVDA", "revenue") == "Revenues"
    assert resolve_concept("AAPL", "revenue") == (
        "RevenueFromContractWithCustomerExcludingAssessedTax"
    )
    assert resolve_concept("JPM", "revenue") == "RevenuesNetOfInterestExpense"


def test_shared_labels_resolve_identically_for_every_filer() -> None:
    assert resolve_concept("NVDA", "net income") == resolve_concept("JPM", "net income")


def test_exact_concept_names_pass_through() -> None:
    assert resolve_concept("NVDA", "NetIncomeLoss") == "NetIncomeLoss"


def test_label_matching_is_case_and_punctuation_tolerant() -> None:
    assert resolve_concept("NVDA", "Shareholders' Equity") == "StockholdersEquity"


def test_unknown_label_resolves_to_nothing_rather_than_guessing() -> None:
    """A wrong concept would produce a confidently wrong verification."""
    assert resolve_concept("NVDA", "gross margin percentage") is None


# --- xbrl_lookup ---------------------------------------------------------------

@pytest.fixture(scope="module")
def conn():
    from filing_agent.retrieval import db
    try:
        connection = db.connect()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no Postgres: {exc}")
    yield connection
    connection.close()


def _has_facts(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM xbrl_facts")
        return cur.fetchone()[0] > 0


def test_lookup_returns_a_traceable_fact(conn) -> None:
    if not _has_facts(conn):
        pytest.skip("no facts loaded")
    out = xbrl_lookup(conn, XbrlLookupInput(ticker="NVDA", concept="net income",
                                            fiscal_year=2025))
    assert out.found and out.value is not None
    assert out.accession_no and out.resolved_concept == "NetIncomeLoss"


def test_unknown_label_reports_what_is_available(conn) -> None:
    """A miss must tell the re-plan loop whether the concept or the period was wrong."""
    out = xbrl_lookup(conn, XbrlLookupInput(ticker="NVDA", concept="frobnication margin",
                                            fiscal_year=2025))
    assert not out.found and out.available_concepts and "no XBRL concept" in out.detail


def test_missing_period_reports_the_concepts_that_do_exist(conn) -> None:
    if not _has_facts(conn):
        pytest.skip("no facts loaded")
    out = xbrl_lookup(conn, XbrlLookupInput(ticker="NVDA", concept="net income",
                                            fiscal_year=1999))
    assert not out.found and out.resolved_concept == "NetIncomeLoss"
    assert "FY1999" in out.detail
