# frontend/ — SPA do b3o (entra no F5.0)

Vite + React + TypeScript + TanStack Router (file-based) + TanStack Query + Tailwind v4,
**SPA pura** — sem TanStack Start e sem servidor. O build roda no container
(`podman compose run --rm web`, `node:26-slim`) e gera `dist/`, que o FastAPI serve
em `/` ao lado de `/api` e `/mcp`.

Regras que valem desde já (detalhe em `AGENTS.md` → *Frontend* e em `docs/spec.md` §10):

- nada de lógica de servidor aqui: o backend é um só;
- tipos da API em **um** arquivo (`src/lib/api.ts`), a partir de `/api/openapi.json`;
- gráficos em SVG inline, sem biblioteca de gráfico;
- nada depende de hover; alvo de toque ≥ 44 px;
- o visual segue `docs/direcao.md` (a direção aprovada), não o mockup.

Ainda não existe código: esta pasta nasce no F5.0, depois do portão do F1b.
