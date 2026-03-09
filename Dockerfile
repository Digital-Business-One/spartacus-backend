# ─── Stage: dev (docker-compose local) ────────────────────────────────────────
FROM python:3.12-slim AS dev

WORKDIR /app

RUN pip install uv

COPY pyproject.toml .
RUN uv sync

# Código montado como volume no docker-compose (hot reload)
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ─── Stage: prod (Cloud Run) ───────────────────────────────────────────────────
FROM python:3.12-slim AS prod

WORKDIR /app

RUN pip install uv

COPY pyproject.toml .
RUN uv sync --no-dev

COPY app/ ./app/

EXPOSE 8080
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
