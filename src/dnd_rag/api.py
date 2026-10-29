"""HTTP API for the site: `POST /api/ask` streams agent events, `GET /api/search`.

Standard library only. Answers stream as server-sent events, one JSON
`{"type", "data"}` object per `data:` line, so the browser can render each
`Agent.ask()` event as it arrives.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .agent import Agent
from .index import Hit, SrdIndex

MAX_SESSIONS = 200
MAX_K = 20


class LockedIndex:
    """One sqlite connection and embedding model shared by every request thread."""

    def __init__(self, db: Path):
        self._index = SrdIndex(db)
        self._lock = threading.Lock()

    def search(self, query: str, k: int = 5) -> list[Hit]:
        with self._lock:
            return self._index.search(query, k)


class Api:
    def __init__(self, db: Path):
        self.index = LockedIndex(db)
        # Conversations by session id, oldest first, so history survives a page's
        # follow-up questions.
        self.sessions: dict[str, Agent] = {}

    def agent(self, session: str | None) -> tuple[str, Agent]:
        if session and session in self.sessions:
            return session, self.sessions[session]
        if len(self.sessions) >= MAX_SESSIONS:
            self.sessions.pop(next(iter(self.sessions)))
        session = uuid.uuid4().hex
        self.sessions[session] = Agent(self.index)  # type: ignore[arg-type]
        return session, self.sessions[session]

    def search(self, query: str, k: int) -> list[dict]:
        return [asdict(h) | {"label": h.citation} for h in self.index.search(query, k)]


def make_handler(api: Api) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            url = urlparse(self.path)
            if url.path != "/api/search":
                return self._json(404, {"error": "Not found"})
            params = parse_qs(url.query)
            query = params.get("q", [""])[0].strip()
            if not query:
                return self._json(400, {"error": "Missing q"})
            try:
                k = max(1, min(MAX_K, int(params.get("k", ["8"])[0])))
            except ValueError:
                return self._json(400, {"error": "k must be a number"})
            self._json(200, {"results": api.search(query, k)})

        def do_POST(self) -> None:
            if urlparse(self.path).path != "/api/ask":
                return self._json(404, {"error": "Not found"})
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError):
                return self._json(400, {"error": "Body must be JSON"})
            question = str(body.get("question", "")).strip()
            if not question:
                return self._json(400, {"error": "Missing question"})
            session, agent = api.agent(body.get("session"))

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self._event("session", {"session": session})
            try:
                for event in agent.ask(question):
                    self._event(event.type, event.data)
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as e:  # noqa: BLE001 - report to the client, keep serving
                self.log_error("ask failed: %r", e)
                self._event("error", {"message": "Something went wrong answering that."})

        def _event(self, type_: str, data: dict) -> None:
            payload = json.dumps({"type": type_, "data": data}, ensure_ascii=False)
            self.wfile.write(f"data: {payload}\n\n".encode())
            self.wfile.flush()

        def _json(self, status: int, body: dict) -> None:
            raw = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    return Handler


def serve(db: Path, host: str, port: int) -> None:
    server = ThreadingHTTPServer((host, port), make_handler(Api(db)))
    print(f"Serving on http://{host}:{port}")
    server.serve_forever()
