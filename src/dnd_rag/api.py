"""HTTP API for the site: `POST /api/ask` streams agent events, `GET /api/search`,
`GET /api/usage` reports the caller's token budget.

A FastAPI app served by uvicorn. Answers stream as server-sent events, one
JSON `{"type", "data"}` object per `data:` line, so the browser can render
each `Agent.ask()` event as it arrives. Errors are JSON `{"error": str}`.

Callers are identified by `auth` (Clerk token or IP) and `/api/ask` is
metered by `limits` per caller and capped by `spend` for the whole service
each month; search is public and unmetered since it never calls Claude.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import uuid
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import uvicorn
from anthropic.types.beta import BetaUsage
from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException

from .agent import Agent, UsageCallback
from .auth import (
    PUBLISHABLE_KEY_ENV,
    AuthError,
    ClerkVerifier,
    Identity,
    anonymous,
    bearer_token,
    verifier_from_env,
)
from .index import Hit, SrdIndex
from .limits import BurstLimiter, UsageStore, billable_tokens
from .spend import LIMIT_ENV, SpendLedger, limit_from_env, response_cost_usd

MAX_SESSIONS = 200
MAX_K = 20

log = logging.getLogger(__name__)


class LockedIndex:
    """One sqlite connection and embedding model shared by every request thread."""

    def __init__(self, db: Path):
        self._index = SrdIndex(db)
        self._lock = threading.Lock()

    def search(self, query: str, k: int = 5) -> list[Hit]:
        with self._lock:
            return self._index.search(query, k)


@dataclass
class Session:
    owner: Identity
    agent: Agent


class Sessions:
    """Conversations by session id, oldest first, so history survives a page's
    follow-up questions. Kept in memory and lost on restart.

    Each session belongs to the identity that started it and bills that
    identity's usage, so a leaked session id cannot spend someone else's
    budget: another caller presenting it simply gets a new session.
    """

    def __init__(self, index: LockedIndex, usage: UsageStore, spend: SpendLedger):
        self.index = index
        self.usage = usage
        self.spend = spend
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def get_or_create(self, session_id: str | None, owner: Identity) -> tuple[str, Agent]:
        with self._lock:
            if session_id is not None:
                existing = self._sessions.get(session_id)
                if existing is not None and existing.owner == owner:
                    return session_id, existing.agent

            if len(self._sessions) >= MAX_SESSIONS:
                oldest = next(iter(self._sessions))
                self._sessions.pop(oldest)
            new_id = uuid.uuid4().hex
            agent = Agent(self.index, on_usage=self._meter(owner))  # type: ignore[arg-type]
            self._sessions[new_id] = Session(owner, agent)
            return new_id, agent

    def _meter(self, owner: Identity) -> UsageCallback:
        def record(model: str, usage: BetaUsage) -> None:
            self.usage.record(owner, billable_tokens(usage))
            self.spend.record(response_cost_usd(model, usage))

        return record


class AskRequest(BaseModel):
    question: str
    session: str | None = None


def sse(type_: str, data: dict) -> str:
    payload = json.dumps({"type": type_, "data": data}, ensure_ascii=False)
    return f"data: {payload}\n\n"


def stream_answer(session: str, agent: Agent, question: str) -> Iterator[str]:
    """The SSE body for one question: the session id first, then agent events."""
    yield sse("session", {"session": session})
    try:
        for event in agent.ask(question):
            yield sse(event.type, event.data)
    except Exception:  # noqa: BLE001 - report to the client, keep serving
        log.exception("ask failed")
        yield sse("error", {"message": "Something went wrong answering that."})


def rate_limited(message: str, retry_after: float, **extra: object) -> JSONResponse:
    """A 429 with a `Retry-After` header and the seconds to wait in the body."""
    seconds = math.ceil(retry_after)
    return JSONResponse(
        {"error": message, "retry_after": seconds} | extra,
        status_code=429,
        headers={"Retry-After": str(seconds)},
    )


def create_app(
    db: Path,
    usage_db: Path,
    verifier: ClerkVerifier | None,
    monthly_limit_usd: float,
) -> FastAPI:
    """The API app. With no `verifier` (Clerk not configured) everyone is anonymous."""
    app = FastAPI(title="dnd-rag", docs_url=None, redoc_url=None, openapi_url=None)
    index = LockedIndex(db)
    usage = UsageStore(usage_db)
    spend = SpendLedger(usage_db, monthly_limit_usd)
    sessions = Sessions(index, usage, spend)
    burst = BurstLimiter()

    def identify(request: Request, authorization: str | None = Header(None)) -> Identity:
        """The caller: a verified Clerk user, or anonymous by IP if no token is sent.

        A token that is sent but invalid is a 401 rather than a silent downgrade
        to anonymous, so the site knows to refresh it.
        """
        ip = "unknown"
        if request.client is not None:
            ip = request.client.host
        if authorization is None:
            return anonymous(ip)

        token = bearer_token(authorization)
        if token is None:
            raise HTTPException(401, "Authorization must be 'Bearer <token>'")
        if verifier is None:
            raise HTTPException(401, "Sign-in is not configured on this server")
        try:
            user_id = verifier.verify(token)
        except AuthError as error:
            log.info("rejected token: %s", error)
            raise HTTPException(401, "Your session has expired; sign in again") from error
        return Identity(key=f"user:{user_id}", user_id=user_id)

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, error: HTTPException) -> JSONResponse:
        return JSONResponse({"error": error.detail}, status_code=error.status_code)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, error: RequestValidationError) -> JSONResponse:
        # Report only the first problem; loc is e.g. ("query", "k") or ("body",).
        first = error.errors()[0]
        field = ".".join(str(part) for part in first["loc"][1:])
        if field == "":
            message = "Body must be a JSON object"
        else:
            message = f"{field}: {first['msg']}"
        return JSONResponse({"error": message}, status_code=400)

    # Plain `def` endpoints run in FastAPI's thread pool, so the blocking index
    # search and the Claude stream never stall the event loop.
    @app.get("/api/search")
    def search(q: str = "", k: int = Query(8)) -> JSONResponse:
        query = q.strip()
        if query == "":
            return JSONResponse({"error": "Missing q"}, status_code=400)
        k = max(1, min(MAX_K, k))
        results = [asdict(hit) | {"label": hit.citation} for hit in index.search(query, k)]
        return JSONResponse({"results": results})

    @app.get("/api/usage")
    def get_usage(identity: Identity = Depends(identify)) -> JSONResponse:
        allowance = usage.allowance(identity)
        return JSONResponse(allowance.to_dict() | {"signed_in": identity.is_signed_in})

    @app.post("/api/ask")
    def ask(body: AskRequest, identity: Identity = Depends(identify)) -> Response:
        question = body.question.strip()
        if question == "":
            return JSONResponse({"error": "Missing question"}, status_code=400)

        # The service-wide cap comes first: when it is spent, no budget helps.
        monthly = spend.current()
        if monthly.is_exhausted:
            log.warning(
                "monthly spend cap reached: $%.2f of $%.2f", monthly.spent_usd, monthly.limit_usd
            )
            return rate_limited(
                "The warlock has spent this month's power. It returns on the 1st.",
                retry_after=(monthly.resets_at - datetime.now(UTC)).total_seconds(),
            )

        allowance = usage.allowance(identity)
        if allowance.is_exhausted:
            return rate_limited(
                "The warlock's daily power is spent. It returns at midnight UTC.",
                retry_after=(allowance.resets_at - datetime.now(UTC)).total_seconds(),
                allowance=allowance.to_dict(),
            )
        wait = burst.try_acquire(identity)
        if wait is not None:
            return rate_limited("Slow down, traveller. Ask again in a moment.", retry_after=wait)

        session, agent = sessions.get_or_create(body.session, identity)
        return StreamingResponse(
            stream_answer(session, agent, question),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    return app


def serve(db: Path, usage_db: Path, host: str, port: int) -> None:
    verifier = verifier_from_env()
    if verifier is None:
        log.warning("%s is not set: sign-in is disabled, everyone is anonymous", PUBLISHABLE_KEY_ENV)
    monthly_limit_usd = limit_from_env()
    print(f"Monthly spend cap: ${monthly_limit_usd:.2f} (set {LIMIT_ENV} to change)")
    uvicorn.run(create_app(db, usage_db, verifier, monthly_limit_usd), host=host, port=port)
