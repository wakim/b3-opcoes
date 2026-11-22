# syntax=docker/dockerfile:1
#
# Imagens do b3o (ADR-002). Critério F0: build do zero em < 5 min.
#   deps    → só dependências (cacheável entre edições de src/)
#   runtime → app sem dev-deps (serviços app, prepare, prepare-watch)
#   dev     → com dev-deps, src e tests (serviço test: pytest/ruff)
#   web     → inerte até o F5.0 (só marca o lugar do build Node)

########## deps ##########
FROM python:3.12-slim AS deps

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Só lock: edições de src/ não invalidam esta camada.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev

########## runtime ##########
FROM deps AS runtime

COPY config.yaml /app/config.yaml
COPY src /app/src
# Instala o projeto b3o (editável) sobre as deps já resolvidas.
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    B3_DATA_DIR=/data

# CMD default: saúde do lake. O serviço `app` sobrepõe com o uvicorn (F3).
CMD ["b3o", "health"]

########## dev ##########
FROM deps AS dev

# Dev-deps (grupo dev) por cima das runtime-deps.
RUN uv sync --frozen --no-install-project

COPY config.yaml /app/config.yaml
COPY src /app/src
COPY tests /app/tests
RUN uv sync --frozen

ENV PATH="/app/.venv/bin:$PATH" \
    B3_DATA_DIR=/data

CMD ["pytest"]

########## web (inerte até o F5.0) ##########
FROM node:22-slim AS web
WORKDIR /web
RUN echo "Estágio do build do frontend (F5.0): npm ci && npm run build -> dist/" > README.txt
