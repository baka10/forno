# The simulator is an external client (docs/adr/0008-simulator-as-external-client.md):
# separate image, no shared base layers with the application beyond the Python runtime.
ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /usr/local/bin/

RUN groupadd --gid 1000 sim && useradd --uid 1000 --gid sim --create-home sim
WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 PYTHONUNBUFFERED=1

COPY simulator/pyproject.toml simulator/uv.lock* ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-install-project --no-dev

COPY simulator/ ./simulator/
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

USER sim
ENTRYPOINT ["uv", "run", "--no-sync", "python", "-m", "simulator"]
