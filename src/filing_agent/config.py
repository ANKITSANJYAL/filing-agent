"""Frozen experiment configuration: model identifiers and corpus scope.

These constants are the experimental controls. Changing MODEL_ABLATION mid-experiment
invalidates every arm of the architecture ablation (DECISIONS.md D-0002); it must be
treated as a schema change, not a config tweak.
"""

from typing import Final

# --- Models (proposal §8, revised by DECISIONS.md D-0002) ---

# Used identically by Arms A, B, and C. Holding this constant is what makes the
# ablation an architecture measurement rather than a model benchmark.
#
# NOTE ON PINNING: Anthropic publishes no dated snapshot variant for this model —
# "claude-sonnet-5" IS the complete, exact identifier, and appending a date suffix
# produces a 404. Reproducibility therefore comes from recording the `model` field
# echoed back on every API response into each eval run's metadata, alongside a hash
# of this file. See DECISIONS.md D-0002 condition (a).
MODEL_ABLATION: Final[str] = "claude-sonnet-5"

# Planner / classifier nodes only. This one does have a dated snapshot, so we pin it.
MODEL_ROUTER: Final[str] = "claude-haiku-4-5-20251001"

# Judge. Deliberately a different model family from the system under test, to reduce
# self-preference bias (proposal §8.7). Calibrated against hand labels regardless.
MODEL_JUDGE: Final[str] = "gpt-5.2"

# --- Corpus scope (proposal §4.1 — locked) ---

TICKERS: Final[tuple[str, ...]] = (
    "NVDA",
    "AAPL",
    "MSFT",
    "JPM",
    "XOM",
    "PFE",
    "WMT",
    "COST",
)

FISCAL_YEARS: Final[tuple[int, ...]] = (2024, 2025)

# --- SEC etiquette (CLAUDE.md §4) ---

SEC_MAX_REQUESTS_PER_SECOND: Final[float] = 10.0
SEC_RATE_LIMIT_SAFETY_FACTOR: Final[float] = 0.8  # target 8 req/s, not the ceiling
