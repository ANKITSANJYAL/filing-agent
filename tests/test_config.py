"""Guards the frozen experiment controls.

These tests exist so that an accidental edit to config.py fails CI loudly rather
than silently invalidating an in-flight ablation run.
"""

from filing_agent import config


def test_corpus_scope_matches_locked_decision() -> None:
    assert len(config.TICKERS) == 8, "proposal §5 forbids >8 tickers"
    assert len(config.FISCAL_YEARS) == 2, "proposal §5 forbids >2 fiscal years"


def test_ablation_model_is_a_single_pinned_string() -> None:
    # All three arms read this one constant; there is no per-arm override.
    assert isinstance(config.MODEL_ABLATION, str)
    assert config.MODEL_ABLATION == "claude-sonnet-5"


def test_judge_is_a_different_family_than_system_under_test() -> None:
    # Guards proposal §8.7: self-preference bias mitigation.
    assert not config.MODEL_JUDGE.startswith("claude")


def test_sec_rate_limit_stays_under_the_ceiling() -> None:
    effective = config.SEC_MAX_REQUESTS_PER_SECOND * config.SEC_RATE_LIMIT_SAFETY_FACTOR
    assert effective < 10.0
