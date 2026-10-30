"""Rate limits for the chat: daily token budgets per plan, plus a burst cap.

Every Claude response's token usage is recorded against the caller's
identity (see `auth.Identity`) in a small SQLite database, separate from the
SRD index so a re-ingest never wipes it. Before an answer starts, the caller
must be under their plan's daily budget; one answer may overshoot it, since
its cost is unknown until it finishes. Budgets reset at midnight UTC.

The burst cap is in memory: it only has to stop rapid-fire requests, and
losing it on restart is harmless.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import anthropic

from .auth import Identity

# Daily token budget per plan. A typical answer uses roughly 5–15k tokens,
# most of it the growing conversation re-read on each search round.
# TODO: add paid plans here and read a signed-in user's plan from their Clerk
# session claims once billing exists; today every signed-in user is "free".
PLAN_DAILY_TOKENS: dict[str, int] = {
    "anonymous": 20_000,
    "free": 200_000,
}

MAX_ASKS_PER_MINUTE = 5
BURST_WINDOW_SECONDS = 60
# Past this many tracked identities, forget the ones with no recent requests.
BURST_SWEEP_THRESHOLD = 10_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
    identity TEXT NOT NULL,
    day TEXT NOT NULL,          -- UTC date, YYYY-MM-DD
    tokens INTEGER NOT NULL,
    PRIMARY KEY (identity, day)
);
"""


def plan_for(identity: Identity) -> str:
    if identity.is_signed_in:
        return "free"
    return "anonymous"


def billable_tokens(usage: anthropic.types.beta.BetaUsage) -> int:
    """Tokens a response counts against a budget.

    Every input token counts, including prompt-cache reads and writes, so a
    budget measures how much text was processed regardless of caching.
    """
    cache_writes = usage.cache_creation_input_tokens or 0
    cache_reads = usage.cache_read_input_tokens or 0
    return usage.input_tokens + cache_writes + cache_reads + usage.output_tokens


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _next_reset() -> datetime:
    tomorrow = datetime.now(UTC).date() + timedelta(days=1)
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=UTC)


@dataclass(frozen=True)
class Allowance:
    """Where a caller stands against their daily budget."""

    plan: str
    used: int
    limit: int
    resets_at: datetime

    @property
    def is_exhausted(self) -> bool:
        return self.used >= self.limit

    def to_dict(self) -> dict:
        return {
            "plan": self.plan,
            "used": self.used,
            "limit": self.limit,
            "resets_at": self.resets_at.isoformat(),
        }


class UsageStore:
    """Daily token totals per identity, in SQLite, shared by request threads."""

    def __init__(self, db: Path):
        db.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db, check_same_thread=False)
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()

    def record(self, identity: Identity, tokens: int) -> None:
        with self._lock:
            self._db.execute(
                """
                INSERT INTO usage (identity, day, tokens) VALUES (?, ?, ?)
                ON CONFLICT (identity, day) DO UPDATE SET tokens = tokens + excluded.tokens
                """,
                (identity.key, _today(), tokens),
            )
            self._db.commit()

    def allowance(self, identity: Identity) -> Allowance:
        with self._lock:
            row = self._db.execute(
                "SELECT tokens FROM usage WHERE identity = ? AND day = ?",
                (identity.key, _today()),
            ).fetchone()
        used = 0
        if row is not None:
            used = row[0]
        plan = plan_for(identity)
        return Allowance(
            plan=plan,
            used=used,
            limit=PLAN_DAILY_TOKENS[plan],
            resets_at=_next_reset(),
        )


class BurstLimiter:
    """At most `MAX_ASKS_PER_MINUTE` questions per identity in any sliding minute."""

    def __init__(self) -> None:
        self._recent: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def try_acquire(self, identity: Identity) -> float | None:
        """Record a request and return None, or return seconds until one is allowed."""
        now = time.monotonic()
        with self._lock:
            if len(self._recent) > BURST_SWEEP_THRESHOLD:
                self._sweep(now)
            recent = self._recent.setdefault(identity.key, deque())
            self._expire(recent, now)
            if len(recent) >= MAX_ASKS_PER_MINUTE:
                return BURST_WINDOW_SECONDS - (now - recent[0])
            recent.append(now)
            return None

    @staticmethod
    def _expire(recent: deque[float], now: float) -> None:
        while len(recent) > 0 and now - recent[0] >= BURST_WINDOW_SECONDS:
            recent.popleft()

    def _sweep(self, now: float) -> None:
        for key in list(self._recent):
            self._expire(self._recent[key], now)
            if len(self._recent[key]) == 0:
                del self._recent[key]
