# frontend/ — SPA do b3o

Vite + React + TypeScript + TanStack Router (file-based) + TanStack Query + Tailwind v4,
**SPA pura** — sem TanStack Start, sem servidor, sem biblioteca de gráfico, sem axios.
O build roda **sempre no container** (`podman compose --profile build run --rm web`,
imagem `node:26-slim`) e gera `dist/`, servido pelo FastAPI em `/` junto de `/api` e `/mcp`.

## Rodar

```powershell
# build one-shot (caminho oficial) — gera frontend/dist no bind
podman compose --profile build run --rm web

# dev server (profile dev) — NÃO é o caminho oficial de build
podman compose --profile dev up web-dev

# checagem (no container, node_modules vem do volume nomeado)
podman compose --profile build run --rm --entrypoint sh web -c "npm run lint && npm run typecheck"
```

## Contrato de dependências (ADR-008 + ADR F5-stack)

Versões auditadas com `npm view <pkg> version` **dentro do `node:26-slim`** em 23/11/2026:
React 19.3.1, `@tanstack/react-router` 1.170.46, `@tanstack/react-query` 5.108.4,
Tailwind 4.3.5 + `@tailwindcss/vite` 4.3.5, Vite 8.3.8, ESLint 10.15.0,
`typescript-eslint` 8.74.0, `@tanstack/router-plugin` 1.169.0. TypeScript fica em
**5.9.3** (última 5.x estável): o `typescript-eslint` estável mais novo ainda limita o
compilador a `<6.1.0`, e a única compatível com TS 7 é uma alpha canary — ADR registra.

`package-lock.json` é versionado; o build usa `npm ci` (nunca `npm install` solto).
`node_modules` vive no volume nomeado `b3o-node-modules`, nunca no bind do Windows.

## Regras

- nada de lógica de servidor aqui: o backend é um só (`QueryService` única);
- `src/lib/api.ts` é o **único** lugar com `fetch` e com os tipos da API;
- dados via TanStack Query: 60 s para leitura de sessão, 1 h para glossário/tickers;
- gráficos, quando entrarem, são SVG inline — sem biblioteca;
- nada depende de hover; alvo de toque ≥ 44 px;
- o visual segue `docs/direcao.md` (a direção aprovada). Nesta sessão (F5-A) o que
  existe é um **wireframe funcional** com os tokens base; os 9 passes do unslop e o
  desenho final entram na F5-B.
