"""A monthly dollar cap on Claude spend, shared by every caller.

The per-identity token budgets in `limits` bound what one person can use, but
not the total: enough signed-in users together could spend any amount. This
module prices each Claude response at list rates, keeps a running total per
calendar month (UTC) in the usage database, and lets the API refuse new
questions once the month's total reaches `MONTHLY_SPEND_LIMIT_USD`.

The total is an estimate at list price, not the invoice. The hard backstop is
the spend limit on the Claude Console workspace that issued the API key; set
this cap a little below it so chat stops politely before the API starts
returning errors mid-answer. Like the token budgets, the check runs before an
answer starts, so the last answer of the month may overshoot it slightly.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import anthropic

LIMIT_ENV = "MONTHLY_SPEND_LIMIT_USD"
# Below the $10 Console workspace limit, leaving room for answers in flight.
DEFAULT_LIMIT_USD = 9.50

# Spend is stored in whole micro-dollars so repeated additions never drift.
MICRODOLLARS_PER_USD = 1_000_000
TOKENS_PER_MTOK = 1_000_000

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Rates:
    """List prices in USD per million tokens.

    Cache writes are priced from `input`: 5-minute writes at 1.25x and 1-hour
    writes at 2x. The cache-read discount varies by model, so it is listed.
    """

    input: float
    output: float
    cache_read: float

    @property
    def cache_write_5m(self) -> float:
        return self.input * 1.25

    @property
    def cache_write_1h(self) -> float:
        return self.input * 2


@dataclass(frozen=True)
class ModelPricing:
    """A model's rates, with a pricier card for prompts over `long_prompt_tokens`."""

    standard: Rates
    long_prompt: Rates | None = None
    long_prompt_tokens: int | None = None

    def rates_for(self, prompt_tokens: int) -> Rates:
        if self.long_prompt is None or self.long_prompt_tokens is None:
            return self.standard
        if prompt_tokens > self.long_prompt_tokens:
            return self.long_prompt
        return self.standard


# Keep in sync with `agent.MODEL` (and any fallback model it can answer on).
# Prices from https://docs.anthropic.com/en/docs/about-claude/pricing.
PRICING: dict[str, ModelPricing] = {
    "claude-haiku-5-5": ModelPricing(
        standard=Rates(input=0.10, output=0.50, cache_read=0.01),
        long_prompt=Rates(input=0.50, output=2.50, cache_read=0.05),
        long_prompt_tokens=100_000,
    ),
    "claude-opus-5-5": ModelPricing(
        standard=Rates(input=4.00, output=20.00, cache_read=0.20)
    ),
}
# A model missing from PRICING is charged at the dearest known rates, so a
# pricing gap can only make the cap stop chat early, never late.
_FALLBACK_PRICING = ModelPricing(
    standard=Rates(input=4.00, output=20.00, cache_read=0.20)
)


def _pricing_for(model: str) -> ModelPricing:
    pricing = PRICING.get(model)
    if pricing is None:
        log.warning("no price for %s; charging it at the highest known rates", model)
        return _FALLBACK_PRICING
    return pricing


def _cost_usd(
    pricing: ModelPricing,
    input_tokens: int,
    output_tokens: int,
    cache_write_5m_tokens: int,
    cache_write_1h_tokens: int,
    cache_read_tokens: int,
) -> float:
    prompt_tokens = (
        input_tokens + cache_write_5m_tokens + cache_write_1h_tokens + cache_read_tokens
    )
    rates = pricing.rates_for(prompt_tokens)
    dollars_per_mtok = (
        input_tokens * rates.input
        + output_tokens * rates.output
        + cache_write_5m_tokens * rates.cache_write_5m
        + cache_write_1h_tokens * rates.cache_write_1h
        + cache_read_tokens * rates.cache_read
    )
    return dollars_per_mtok / TOKENS_PER_MTOK


def _cache_writes(
    usage: anthropic.types.beta.BetaUsage
    | anthropic.types.beta.BetaMessageIterationUsage,
) -> tuple[int, int]:
    """(5-minute, 1-hour) cache write tokens; all 5-minute if not broken down."""
    if usage.cache_creation is None:
        return usage.cache_creation_input_tokens or 0, 0
    return (
        usage.cache_creation.ephemeral_5m_input_tokens,
        usage.cache_creation.ephemeral_1h_input_tokens,
    )


def response_cost_usd(model: str, usage: anthropic.types.beta.BetaUsage) -> float:
    """The list-price cost of one Claude response.

    A response that fell back to another model mid-request reports one
    `iterations` entry per model it ran on; each is priced at its own model's
    rates. `model` is the response's model, used where an entry names none.
    """
    iterations = usage.iterations or []
    if len(iterations) == 0:
        cache_write_5m, cache_write_1h = _cache_writes(usage)
        return _cost_usd(
            _pricing_for(model),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_write_5m_tokens=cache_write_5m,
            cache_write_1h_tokens=cache_write_1h,
            cache_read_tokens=usage.cache_read_input_tokens or 0,
        )

    total = 0.0
    for iteration in iterations:
        iteration_model = model
        if iteration.model is not None:
            iteration_model = iteration.model
        cache_write_5m, cache_write_1h = _cache_writes(iteration)
        total += _cost_usd(
            _pricing_for(iteration_model),
            input_tokens=iteration.input_tokens,
            output_tokens=iteration.output_tokens,
            cache_write_5m_tokens=cache_write_5m,
            cache_write_1h_tokens=cache_write_1h,
            cache_read_tokens=iteration.cache_read_input_tokens or 0,
        )
    return total


def limit_from_env() -> float:
    setting = os.environ.get(LIMIT_ENV, "").strip()
    if setting == "":
        return DEFAULT_LIMIT_USD
    return float(setting)


def _this_month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def _next_month_start() -> datetime:
    now = datetime.now(UTC)
    if now.month == 12:
        return datetime(now.year + 1, 1, 1, tzinfo=UTC)
    return datetime(now.year, now.month + 1, 1, tzinfo=UTC)


SCHEMA = """
CREATE TABLE IF NOT EXISTS spend (
    month TEXT PRIMARY KEY,     -- UTC month, YYYY-MM
    microdollars INTEGER NOT NULL
);
"""


@dataclass(frozen=True)
class MonthlySpend:
    """The whole service's estimated spend this month against the cap."""

    month: str
    spent_usd: float
    limit_usd: float
    resets_at: datetime

    @property
    def is_exhausted(self) -> bool:
        return self.spent_usd >= self.limit_usd


class SpendLedger:
    """Estimated spend per month, in the usage database, shared by request threads."""

    def __init__(self, db: Path, limit_usd: float):
        db.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db, check_same_thread=False)
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()
        self.limit_usd = limit_usd

    def record(self, usd: float) -> None:
        microdollars = round(usd * MICRODOLLARS_PER_USD)
        with self._lock:
            self._db.execute(
                """
                INSERT INTO spend (month, microdollars) VALUES (?, ?)
                ON CONFLICT (month) DO UPDATE SET microdollars = microdollars + excluded.microdollars
                """,
                (_this_month(), microdollars),
            )
            self._db.commit()

    def current(self) -> MonthlySpend:
        month = _this_month()
        with self._lock:
            row = self._db.execute(
                "SELECT microdollars FROM spend WHERE month = ?", (month,)
            ).fetchone()
        microdollars = 0
        if row is not None:
            microdollars = row[0]
        return MonthlySpend(
            month=month,
            spent_usd=microdollars / MICRODOLLARS_PER_USD,
            limit_usd=self.limit_usd,
            resets_at=_next_month_start(),
        )
