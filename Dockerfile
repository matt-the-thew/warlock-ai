# The API (`dnd-rag serve`) as a container for Cloud Run. Build it after
# `uv run dnd-rag ingest`: the index in data/srd.db is git-ignored, so it is
# copied in from the working tree rather than built here, which also keeps
# PyMuPDF (AGPL, the `ingest` extra) out of the image.
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    FASTEMBED_CACHE_PATH=/app/models

# Dependencies first, in their own layer, so code changes rebuild quickly.
COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src/dnd_rag ./src/dnd_rag
RUN uv sync --locked --no-dev

# Download the embedding model now, so cold starts don't fetch it.
RUN python -c "from fastembed import TextEmbedding; from dnd_rag.index import EMBED_MODEL; TextEmbedding(EMBED_MODEL)"

# cli.py finds data/ next to src/, so the index goes in /app/data. usage.db
# is created beside it at runtime and lives only as long as the container.
COPY data/srd.db ./data/srd.db

# Cloud Run sets $PORT, which `serve` listens on.
CMD ["dnd-rag", "serve", "--host", "0.0.0.0"]
