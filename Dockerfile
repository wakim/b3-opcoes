# syntax=docker/dockerfile:1
#
# Imagens do b3o (ADR-002). Critério F0: build do zero em < 5 min.
#   deps    → só dependências (cacheável entre edições de src/)
#   runtime → app sem dev-deps (serviços app, prepare, prepare-watch)
#   dev     → com dev-deps, src e tests (serviço test: pytest/ruff)
#   web     → inerte até o F5.0 (só marca o lugar do build Node)

########## deps ##########
# Política de dependências (pré-requisito permanente do dono): bibliotecas e imagens de base
# ficam sempre na última versão estável. As tags abaixo são flutuantes de propósito — rebuild
# puxa o patch novo. Auditoria de 22/11/2026: python:3.12-slim = 3.12.16
# (index sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b);
# uv:latest = 0.13.9 (index sha256:d3b601f0c7b118bc1753febf7c41cc80981df678445e0254284eecf84db79a7d).
# `uv` também fica em `:latest` (e não em tag de versão): só o binário é copiado, o bump é
# reversível e o pré-requisito pede sempre a última estável. Python fica em 3.12 (AGENTS.md).
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
# node:22-slim (Node 22 "Jod") mantido: mudar para a LTS corrente (24/26) é decisão de spec
# (§4.1 / F5.0), não de auditoria de dependência. Auditoria 22/11/2026: 22-slim =
# sha256:e9f565d32859f0cbfb52125eafe8be39390f24dac302dc3cdf566e8ba3ab21ee (Node 22.23.5).
FROM node:22-slim AS web
WORKDIR /web
RUN echo "Estágio do build do frontend (F5.0): npm ci && npm run build -> dist/" > README.txt
