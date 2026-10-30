"""HTTP API for the site: `POST /api/ask` streams agent events, `GET /api/search`.

A FastAPI app served by uvicorn. Answers stream as server-sent events, one
JSON `{"type", "data"}` object per `data:` line, so the browser can render
each `Agent.ask()` event as it arrives. Errors are JSON `{"error": str}`.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException

from .agent import Agent
from .index import Hit, SrdIndex

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


class Sessions:
    """Conversations by session id, oldest first, so history survives a page's
    follow-up questions. Kept in memory and lost on restart."""

    def __init__(self, index: LockedIndex):
        self.index = index
        self._agents: dict[str, Agent] = {}
        self._lock = threading.Lock()

    def get_or_create(self, session: str | None) -> tuple[str, Agent]:
        with self._lock:
            if session is not None and session in self._agents:
                return session, self._agents[session]
            if len(self._agents) >= MAX_SESSIONS:
                oldest = next(iter(self._agents))
                self._agents.pop(oldest)
            session = uuid.uuid4().hex
            agent = Agent(self.index)  # type: ignore[arg-type]
            self._agents[session] = agent
            return session, agent


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


def create_app(db: Path) -> FastAPI:
    app = FastAPI(title="dnd-rag", docs_url=None, redoc_url=None, openapi_url=None)
    index = LockedIndex(db)
    sessions = Sessions(index)

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

    @app.post("/api/ask")
    def ask(body: AskRequest) -> Response:
        question = body.question.strip()
        if question == "":
            return JSONResponse({"error": "Missing question"}, status_code=400)
        session, agent = sessions.get_or_create(body.session)
        return StreamingResponse(
            stream_answer(session, agent, question),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    return app


def serve(db: Path, host: str, port: int) -> None:
    uvicorn.run(create_app(db), host=host, port=port)
