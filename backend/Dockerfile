FROM ghcr.io/astral-sh/uv:0.11.28@sha256:0f36cb9361a3346885ca3677e3767016687b5a170c1a6b88465ec14aefec90aa AS uv
FROM python:3.12.13-slim-bookworm@sha256:8a7e7cc04fd3e2bd787f7f24e22d5d119aa590d429b50c95dfe12b3abe52f48b AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

COPY --from=uv /uv /uvx /bin/
WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
RUN uv sync --locked --no-dev --no-editable

FROM python:3.12.13-slim-bookworm@sha256:8a7e7cc04fd3e2bd787f7f24e22d5d119aa590d429b50c95dfe12b3abe52f48b AS runtime

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 agent-platform \
    && useradd --uid 10001 --gid agent-platform --no-create-home --shell /usr/sbin/nologin agent-platform

WORKDIR /app
COPY --from=builder --chown=agent-platform:agent-platform /app/.venv /app/.venv
COPY --from=builder --chown=agent-platform:agent-platform /app/migrations /app/migrations
COPY --from=builder --chown=agent-platform:agent-platform /app/alembic.ini /app/alembic.ini

USER agent-platform
EXPOSE 8000
CMD ["uvicorn", "agent_platform.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
