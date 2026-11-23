# syntax=docker/dockerfile:1
#
# Imagens do b3o (ADR-002). Critério F0: build do zero em < 5 min.
# Monorepo: `backend/` (Python) e `frontend/` (Node/Vite) na raiz, cada um com seu
# projeto; `data/` e `docs/` são compartilhados. Dentro da imagem o código mora em
# /app (backend) e /frontend (build do frontend).
#   deps    → só dependências (cacheável entre edições de backend/src/)
#   web     → build do frontend (npm ci && npm run build) → /frontend/dist
#   runtime → app sem dev-deps (serviços app, prepare, prepare-watch)
#   dev     → com dev-deps e src (serviço test: container de ferramentas/python)
# A ordem importa: `web` vem ANTES de `runtime`, porque `COPY --from=web` só
# resolve stages já definidos quando o build é limitado por `--target runtime`.

########## deps ##########
# Política de dependências (pré-requisito permanente do dono): bibliotecas e imagens de base
# ficam sempre na última versão estável. As tags abaixo são flutuantes de propósito — rebuild
# puxa o patch novo. Auditoria de 22/11/2026: python:3.15-slim = 3.15.2
# uv:latest = 0.13.9 (index sha256:d3b601f0c7b118bc1753febf7c41cc80981df678445e0254284eecf84db79a7d).
# `uv` também fica em `:latest` (e não em tag de versão): só o binário é copiado, o bump é
# reversível e o pré-requisito pede sempre a última estável. Python 3.15 (duckdb publica wheel abi3 cp312+).
FROM python:3.15-slim AS deps

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Só lock: edições de backend/src/ não invalidam esta camada.
COPY backend/pyproject.toml backend/uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev

########## web (build do frontend, F5.0) ##########
# node:26-slim = Node 26 (LTS ativa e current em 22/11/2026; 22-slim era LTS de manutenção).
# Política ADR-008: sempre a mais nova. `npm ci` exige package-lock versionado.
FROM node:26-slim AS web
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

########## runtime ##########
FROM deps AS runtime

COPY backend/config.yaml /app/config.yaml
COPY backend/src /app/src
# Instala o projeto b3o (editável) sobre as deps já resolvidas.
RUN uv sync --frozen --no-dev

# SPA do F5: o mesmo container serve /api, /mcp e o dist do Vite.
COPY --from=web /frontend/dist /app/frontend/dist
COPY docs/glossario.md /app/docs/glossario.md

ENV PATH="/app/.venv/bin:$PATH" \
    B3_DATA_DIR=/data \
    B3_FRONTEND_DIR=/app/frontend/dist \
    B3_GLOSSARY_PATH=/app/docs/glossario.md

# CMD default: saúde do lake. O serviço `app` sobrepõe com o uvicorn (F3).
CMD ["b3o", "health"]

########## dev ##########
FROM deps AS dev

# Dev-deps (grupo dev) por cima das runtime-deps.
RUN uv sync --frozen --no-install-project

COPY backend/config.yaml /app/config.yaml
COPY backend/src /app/src
COPY backend/scripts /app/scripts
COPY docs/glossario.md /app/docs/glossario.md
RUN uv sync --frozen

ENV PATH="/app/.venv/bin:$PATH" \
    B3_DATA_DIR=/data \
    B3_GLOSSARY_PATH=/app/docs/glossario.md

CMD ["b3o", "--help"]
