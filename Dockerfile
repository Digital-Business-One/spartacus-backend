# ─── Stage: dev (docker-compose local) ────────────────────────────────────────
FROM python:3.12-slim AS dev

WORKDIR /app

RUN pip install uv

# Copy the lockfile so deps resolve to the EXACT pinned versions. Without it,
# `uv sync` re-resolves the loose `>=` ranges in pyproject.toml to whatever is
# newest at build time — which silently shipped a newer Starlette whose router
# behavior dropped all include_router() routes in prod (BUG-02).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen

# Código montado como volume no docker-compose (hot reload)
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ─── Stage: prod (Cloud Run) ───────────────────────────────────────────────────
FROM python:3.12-slim AS prod

WORKDIR /app

RUN pip install uv

# Copy the lockfile so prod installs the EXACT pinned versions (see dev stage).
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen

COPY app/ ./app/

EXPOSE 8080
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
