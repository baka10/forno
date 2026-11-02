# syntax=docker/dockerfile:1.7
#
# One image, three roles (api / outbox-relay / projectors) invoked with different
# commands — see docs/architecture.md "Why one image with three roles".
# Targets: dev (editable install, hot reload), test (dev deps), prod (wheel only).

ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de AS base

# curl: python:*-slim ships neither curl nor wget — docker-compose.yml's `api`
# healthcheck needs it (`forno healthcheck` covers liveness via stdlib urllib,
# but the compose-level check intentionally hits /health/ready for real DB
# connectivity, which needs an HTTP client in the container).
RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1000 forno && \
    useradd --uid 1000 --gid forno --shell /bin/bash --create-home forno

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# --- deps layer, cached independently of source changes ----------------------
FROM base AS deps
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# --- dev: editable install + dev dependencies, hot reload ---------------------
FROM deps AS dev
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen
USER forno
ENTRYPOINT ["uv", "run", "forno"]
CMD ["serve", "--reload"]

# --- test: dev dependencies, no reload, used by CI ----------------------------
FROM dev AS test
USER root
RUN chown -R forno:forno /app
USER forno
ENTRYPOINT ["uv", "run"]
CMD ["pytest"]

# --- builder: produce a wheel for the prod stage ------------------------------
FROM deps AS builder
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv uv build --wheel

# --- prod: wheel only, no build toolchain, no source tree ---------------------
FROM base AS prod
COPY --from=builder /app/dist/*.whl /tmp/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system /tmp/*.whl && rm -rf /tmp/*.whl
USER forno
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD ["forno", "healthcheck"]
ENTRYPOINT ["forno"]
CMD ["serve"]
