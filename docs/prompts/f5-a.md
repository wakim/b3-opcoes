# Sessão — F5-A: API de leitura + fundação do PWA (scaffold, rotas novas, cliente tipado)

Você é o orquestrador desta sessão. Trabalhe sozinho, sem pedir confirmação a cada passo.
Este documento é o contrato: o que fazer, em que ordem, como provar, quando parar, como registrar.

**Pasta:** `C:\Users\wakim\Projects\b3-opcoes` · **Runner:** `podman compose` (o host não tem Docker nem Node) · **Data de referência:** a do lake, nunca o relógio.

> **Esta é a sessão 1 de 4 do F5.** Ela entrega **infraestrutura + API + cliente tipado + wireframe funcional**; a UI final (9 passes do unslop), o builder de estrutura, o PWA offline e a instalação no celular são as sessões seguintes (§10). Não tente fechar o F5 inteiro aqui.

---

## 1. Estado de partida (medido em 23/11/2026 — não redescubra)

- **F0–F4 aceitos**: lake Parquet 2024–2026 (722 sessões), `model_version=2`, `metrics_*` com 7,77 M séries.
- **F3/F4 no ar**: `b3o.api:app` sobe HTTP + MCP Streamable HTTP em `/mcp`, uma única `QueryService` no lifespan. 7 tools: `health`, `screen`, `underlying`, `chain`, `series`, `events`, `structure_eval`. Envelope `{as_of_session, model_version, data, meta}`, `max_rows` 20/60, teto de 200 KB.
- **Provas existentes**: `backend/scripts/mcp_smoke.py` (**53/53**) e `backend/scripts/structure_smoke.py` (**93/93**) no dado real. Porta 8080 do host ocupada por outro projeto → `$env:B3_APP_PORT="8081"`.
- **Números de referência (19/11/2026)**: `screen` 289 = 163 `ok` + 126 excluídos (`no_options` 124, `no_trade` 2); IV mediana 31,43 (n=35); PETR4 spot 42,67, IV ATM 30d 31,43, 88/782 séries, 25 vencimentos; coberta 4.109 / 41,09 / 207.
- **Frontend**: **só** `frontend/README.md`. O serviço `web` do `compose.yaml` é um placeholder (`echo`) e o estágio `web` do `Dockerfile` também. `.gitignore` já ignora `node_modules/`, `frontend/dist/`; `.dockerignore` já ignora `frontend/node_modules/`.
- **Design**: `docs/direcao.md` (direção aprovada "A · Bancada fria") e os mockups `docs/ui-mobile.html` / `docs/ui-mockup.html` existem. Mockup é referência de **estrutura/conteúdo**, não alvo visual.
- **Armadilha que vai te morder (AGENTS, runtime F3)**: o app MCP está montado em **`/`** (`streamable_http_path="/mcp"` + `app.mount("/", mcp_app)`). **Não monte `StaticFiles` em `/` sem um dispatcher**: o mount do MCP engole tudo (ou o static engole `/mcp`). O padrão que funciona é um ASGI montado em `/` que manda `/mcp` para o `mcp_app` e o resto para o SPA (fallback `index.html`), com as rotas `/api` registradas **antes**.
- **Armadilha do build**: `.dockerignore` exclui `docs/` e `*.md` — o glossário precisa de exceção explícita para entrar na imagem (§4.2). `node_modules` nunca no bind do Windows: volume nomeado.
- **Sem suíte de testes** (ADR-019): validar é rodar no dado real e no navegador; não crie `tests/`.
- **`config.yaml` tem 4 chaves fechadas** (`CONFIG_KEYS`): chave nova = `bad_config`. Use **variável de ambiente** para caminho do `dist` (`B3_FRONTEND_DIR`), não config.

---

## 2. Leitura obrigatória (nesta ordem)

1. `AGENTS.md` inteiro — frontend (stack, tokens, unslop), armadilhas de runtime F3/F4, ambiente (Node só no container, bind do Windows).
2. `docs/spec.md` §9 (rotas/cache), §10 e §10.1 (telas, stack, PWA), §8 (envelope/limites).
3. `PLAN.md` F5 e "Estado atual".
4. `docs/direcao.md` — o **contrato visual** (paleta, tipografia, espaço, raio, movimento, proibições). Nesta sessão ele vale só para os tokens base; os 9 passes do unslop são a F5-B.
5. `docs/ui-mobile.html` e `docs/ui-mockup.html` — estrutura e conteúdo de cada tela.
6. `docs/glossario.md` — a fonte dos verbetes que a rota `/api/glossary` vai servir.
7. `docs/contratos/onda-i.md` e `onda-j.md` — envelope, erros, limites, `_brl`, payloads.
8. `docs/relatorios/2026-11-23-revisao-f3.md` e `-f4.md` — o que já foi provado e as armadilhas.
9. Código: `backend/src/b3o/api/{app,payloads,serialize,state}.py`, `query/service.py` (`screen_bundle`, `spot_tickers`, `_fetch`), `compose.yaml`, `Dockerfile`, `frontend/README.md`, `backend/scripts/mcp_smoke.py`.

**Ordem de precedência:** spec > PLAN > AGENTS > decisões — exceto runtime/ambiente, onde o AGENTS é a palavra mais recente.

---

## 3. Regras que não se quebram

1. **O Python calcula, o frontend exibe.** Nenhum número nasce de TS/LLM; a UI só formata o que `/api` devolve.
2. **Uma camada de consulta.** SQL só em `store/`/`query/`; as rotas novas chamam a mesma `QueryService` (uma instância no lifespan). HTTP e MCP continuam iguais.
3. **Descritivo** (ADR-023/024): `signal_class` não aparece; nada de "caro/barato", ranking, acerto, recomendação. O glossário explica, não sugere.
4. **Falha explícita.** Erro = `{error:{code,message,hint}}`; ausência = `null` + flag; **nunca** traceback no corpo; `dist` ausente na `/` é erro claro (não página em branco).
5. **Limites.** `max_rows` 20/60, teto de 200 KB, toda resposta com `as_of_session` e `model_version`. Nenhuma lista nova passa de 60.
6. **Container é o caminho oficial.** Build e dev do frontend no `node:26-slim`; nada de `npm`/`node` no host. `package-lock.json` versionado; `npm ci` no build. Volume nomeado para `node_modules`.
7. **Backend derivado congelado.** `metrics/`, `ingest/`, `store/` e o `derive/` existente não mudam; `model_version` segue **2**; `uv.lock` não é tocado (sem dependência Python nova). Leitura nova em `query/service.py` é permitida.
8. **Sem escrita.** Journal/posições/token de escrita são F6. `POST /api/structure` continua sendo cálculo.
9. **SPA pura.** Vite + React + TS + TanStack Router (file-based) + TanStack Query; sem SSR, sem servidor no frontend, sem biblioteca de gráfico (SVG inline quando houver gráfico).
10. **Direção visual é contrato.** Tokens de `docs/direcao.md`; zero matiz fora do gráfico; números mono tabulares; raio 4–6; sem pill. **Nesta sessão**: wireframe funcional com os tokens base; **não** tente o desenho final.
11. **Unslop** (`~/.config/opencode/skills/unslop`, CC BY 4.0 — manter o crédito) será aplicado na F5-B; se você tocar em qualquer estilo, não introduza os sinais proibidos (gradiente, glow, badge "New ✨", emoji como rótulo, uppercase tracking).
12. **Commit + push por pacote verde** (`tipo(escopo): resultado`), `origin` = `git@github.com:wakim/b3-opcoes.git`, branch `main` (SSH).
13. **Armadilha nova → `AGENTS.md`** na mesma tarefa; decisão sem dono → ADR curto em `docs/decisoes.md` e siga.

---

## 4. Escopo — o que construir

### 4.1 F5.0 — Scaffold do frontend + serviços

- `frontend/` vira projeto npm: `package.json` + `package-lock.json` + `vite.config.ts` + `tsconfig*.json` + ESLint flat + `index.html` + `src/main.tsx` + `src/styles.css` (tokens do `direcao.md` como CSS variables).
- Dependências (última estável — ADR-008; audite com `npm view <pkg> version` **dentro do container** e registre as versões no ADR): `react`, `react-dom`, `@tanstack/react-router`, `@tanstack/router-plugin` (file-based), `@tanstack/react-query`, `tailwindcss` v4 + `@tailwindcss/vite`, `vite`, `typescript`, `eslint` + plugins de TS/React/hooks, `@types/react`, `@types/react-dom`. **Sem** biblioteca de gráfico, **sem** framework com servidor, **sem** axios (use `fetch`).
- `src/routes/` file-based, um arquivo por tela: `__root.tsx`, `index.tsx` (Hoje), `carteira.tsx`, `diario.tsx`, `aprender.tsx`, `t.$ticker.tsx`, `s.$ticker.$maturity.tsx`.
- `compose.yaml`:
  - `web`: node:26-slim, `working_dir: /frontend`, bind `./frontend:/frontend`, volume nomeado em `/frontend/node_modules`, `npm ci && npm run build` (grava `frontend/dist` no bind);
  - `web-dev` (profile `dev`): `npm run dev -- --host 0.0.0.0 --port 5173`, publica `127.0.0.1:${B3_WEB_PORT:-5173}:5173`, `CHOKIDAR_USEPOLLING=1`. Não é o caminho oficial de build.
- `Dockerfile`: o estágio `web` passa a `npm ci && npm run build`; o estágio `runtime` copia `--from=web /frontend/dist /app/frontend/dist` e define `B3_FRONTEND_DIR=/app/frontend/dist`. Atualize `.dockerignore` (`frontend/dist/` fora do contexto; o resto do frontend entra).
- `frontend/README.md` deixa de ser placeholder.

### 4.2 F5.1 — FastAPI servindo a SPA

- Servir `dist` **sem quebrar `/api` nem `/mcp`**: dispatcher ASGI montado em `/` (`/mcp` → `mcp_app`; resto → static com fallback `index.html` para rotas do SPA como `/t/PETR4`). Rotas `/api` continuam registradas antes de qualquer mount. Não invente um segundo processo/servidor.
- SPA fallback: caminho sem extensão → `index.html`; asset inexistente (`/assets/...`) → **404** (não devolver index).
- Cache: `index.html` → `no-store`; `/assets/*` (hash no nome) → `public, max-age=31536000, immutable`; JSON de `/api` → `no-cache`. `GZipMiddleware` para JSON/HTML/JS/CSS.
- `dist` ausente → resposta explícita `frontend_missing` (503, dica `npm run build` / `podman compose run --rm web`), nunca página em branco.
- `X-App-Token` **fica para o F6** (não há rota de escrita); registre no ADR.

### 4.3 F5.2 — Rotas de leitura novas

- `GET /api/tickers?q=&max_rows=` — busca de papel: tickers do mercado `010`/`020` da última sessão (com `corp_name`), filtro por `q` (case-insensitive, prefixo primeiro e depois contém), `in_universe` (do `prepare_state`) e `has_options` (tem série `070`/`080` no dia). Envelope, ≤ 60, `q` vazio devolve os primeiros alfabéticos. Dataclass `TickerRow` + método `QueryService.search_tickers(q, max_rows)` (SQL só aqui).
- `GET /api/glossary` — lista `{key, title, level}` de todos os verbetes do `docs/glossario.md`.
- `GET /api/glossary/{key}` — `{key, title, level, body_md}` do verbete. `key` = slug do título (minúsculas, sem acento, espaços/pontuação → `-`; ex.: "IV ATM 30d" → `iv-atm-30d`; "Preço (spot / fechamento)" → `preco-spot-fechamento`). Desconhecido → 404 `not_found` com dica. **Sem** injeção de exemplo da sessão neta versão (declare o desvio).
- Fonte do glossário: **um** arquivo (`docs/glossario.md`), nunca duplicado em Python. Para entrar na imagem: ajuste `.dockerignore` (`docs/*` + `!docs/glossario.md`, depois do `*.md`) e `COPY docs/glossario.md /app/docs/glossario.md` no runtime. Parser de Markdown→verbetes **puro** (texto entra, estrutura sai); leitura do arquivo na borda da API. Caminho por env `B3_GLOSSARY_PATH` (default `/app/docs/glossario.md`).
- Atualize o hint da rota `/api/{rest:path}` e o docstring do app.

### 4.4 F5.4 — Cliente tipado + TanStack Query

- `frontend/src/lib/api.ts` é **o único** lugar com `fetch` e com os tipos da API (contrato: escrito à mão a partir de `/api/openapi.json` + contratos; os endpoints devolvem `dict`, então a geração não traz schema útil — registre no ADR). Tipos: `Envelope<T>`, `Health`, `ScreenSummary/ScreenRow`, `Underlying/Term/Smile/History`, `ChainRow`, `SeriesDetail`, `Events`, `Structure`, `TickerRow`, `GlossaryEntry`.
- `apiGet` trata o envelope de erro (`{error:{code,message,hint}}`), status HTTP e `truncated`; nada de `fetch` solto em componente.
- TanStack Query provider no `__root`; `staleTime` 60 s para leitura de sessão, 1 h para glossário/tickers; estados de erro/`not_found` explícitos.
- `VITE_API_BASE` default vazio (mesma origem).

### 4.5 F5.3 (parcial) — Router + wireframe funcional

- As 6 rotas existem e navegam; o carimbo `sessão AAAA-MM-DD · D-1` no topo vem de `/api/health` (`as_of_session`).
- **Hoje**: contagens do `screen` (289/163/126), mediana de IV/VRP e a tabela das primeiras linhas (ordem alfabética), com os tokens base.
- **Ticker**: resumo do papel + nº de vencimentos/séries do termo (dados de `/api/t/{ticker}`) e a cadeia (nº de linhas, sem filtros finos).
- **Série**: identidade + último ponto do histórico.
- **Aprender**: lista do `/api/glossary` e abre o verbete.
- **Carteira** e **Diário**: placeholders explícitos ("F6"), com o carimbo.
- **Sem** sheet de métrica, **sem** gráficos e **sem** builder de estrutura — são F5-B/F5-C.

---

## 5. Donos de arquivo e congelados

| Pacote | Arquivos |
|---|---|
| F5.0 | `frontend/**` (novo projeto), `compose.yaml`, `Dockerfile`, `.dockerignore`, `frontend/README.md` |
| F5.1 | `backend/src/b3o/api/app.py` (dispatcher/static/cache), `api/serialize.py` (se precisar de status novo) |
| F5.2 | `backend/src/b3o/query/service.py` (`TickerRow` + `search_tickers`), `api/payloads.py`, `api/glossary.py` (**novo**, parser puro + leitura), `api/app.py` |
| F5.3/F5.4 | `frontend/src/**` |
| docs | `docs/decisoes.md` (ADR), `AGENTS.md`, `PLAN.md`, `README.md` (comandos), `docs/relatorios/2026-11-23-revisao-f5a.md` (parecer) |
| **congelado** | `backend/src/b3o/metrics/`, `ingest/`, `store/`, `derive/` existente (`structure.py` inclusive), `backend/pyproject.toml`, `backend/uv.lock`, `data/` |

- Sem chave nova no `config.yaml`; caminhos por env (`B3_FRONTEND_DIR`, `B3_GLOSSARY_PATH`, `B3_WEB_PORT`).
- Nome de campo em inglês com unidade; arredondar só na serialização; R$ com `_brl`.

---

## 6. Protocolo de execução

- **Implementador** (subagente `general`) por pacote; **revisor** em **sessão nova**, sem ser o autor, com acesso só de leitura (parecer com veredito e evidência). Duas rodadas no máximo; na terceira o orquestrador decide e registra ADR.
- Ordem: **F5.0 scaffold → F5.1 static → F5.2 rotas → revisão → F5.4 cliente → F5.3 wireframe → prova**. Pare num pacote verde, commite e registre o estado.
- Validação no dado real e no navegador (via `curl`/`podman exec`); não crie `tests/`.

---

## 7. Critério de pronto (prove com saída colada)

1. `podman compose config` válido; `podman compose run --rm web` gera `frontend/dist` **dentro do `node:26-slim`** (sem Node no host); `podman compose build app` (com o estágio web) e `up -d app` (`B3_APP_PORT=8081`).
2. `GET /` devolve o `index.html` com o carimbo da sessão; `GET /t/PETR4` devolve o **mesmo HTML** (SPA fallback); `GET /api/health` continua JSON com `model_version 2`; `GET /mcp` funciona (`mcp_smoke.py` **53/53** e `structure_smoke.py` **93/93** inalterados).
3. Headers: `index.html` `cache-control: no-store`; `/assets/*` `immutable`; `/api/*` `no-cache`; JSON/HTML com `content-encoding: gzip`; asset inexistente → 404; `dist` ausente → `frontend_missing` 503.
4. `GET /api/tickers?q=PETR` devolve PETR3/PETR4 (≤ 60, envelope, `in_universe`/`has_options`); `GET /api/glossary` lista **≥ 20** verbetes e `GET /api/glossary/iv-atm-30d` devolve o texto do glossário; chave desconhecida → 404 `not_found`.
5. `npm run lint` e `tsc --noEmit` **limpos no container**; `npm run build` duas vezes seguidas produz o mesmo `dist` (hash estável); **só** `frontend/src/lib/api.ts` contém `fetch` (grep).
6. `uv.lock` intacto; `git diff` de `metrics/ingest/store/derive` existente = vazio; `model_version` 2; `b3o health`, `b3o screen --date 2026-11-19` e `b3o struct` intactos.
7. Zero escrita (nenhum arquivo em `data/` muda com um GET); zero vocabulário preditivo novo; nenhuma biblioteca de gráfico; nenhum SSR/servidor no frontend.
8. `PLAN.md` F5 com o que foi cumprido; ADR com versões do stack, dispatcher, fonte do glossário e `X-App-Token` adiado; armadilhas novas no `AGENTS.md`; `git push` do pacote.

---

## 8. O que NÃO fazer

- UI final, 9 passes do unslop, sheet de métrica, gráficos, builder de estrutura (F5-B/F5-C).
- PWA/`vite-plugin-pwa`/offline (F5-C), Tailscale/instalação no celular (F5-D).
- Escritas, posições, journal, `APP_TOKEN` (F6); qualquer rota `POST` nova.
- Biblioteca de gráfico, axios, CSS-in-JS, SSR, TanStack Start, servidor no frontend.
- Recalcular métrica, tocar fórmula, subir `model_version`, criar `tests/`, afrouxar envelope/limites.
- Inventar número, preço, IV ou data; inventar versão de pacote (audite no container).

---

## 9. Ao terminar

1. `PLAN.md`: marcar só o que o critério provou; "Estado atual" com F5-A entregue e o que falta (F5-B visual, F5-C PWA, F5-D celular).
2. `docs/decisoes.md`: ADR do stack (versões), do dispatcher `/mcp` × SPA, da fonte do glossário e do `X-App-Token` adiado.
3. `AGENTS.md`: armadilhas novas (mount do MCP em `/` × static; `.dockerignore` do glossário; `node_modules` em volume; `npm ci` × lock; cache do `index.html`).
4. `docs/relatorios/2026-11-23-revisao-f5a.md` com o parecer independente.
5. Mensagem final curta: (1) o que está provado, (2) o que não está e por quê, (3) ADRs para ratificar, (4) próximo passo (F5-B).

---

## 10. Roadmap das próximas sessões do F5 (não execute agora)

- **F5-B (visual + telas de leitura):** 9 passes do unslop em Hoje, Ticker, Série e Aprender; componentes base (cartões, régua, cadeia put·strike·call, gráficos SVG); sheet de métrica; carimbo e estados de erro/offline.
- **F5-C (estrutura + PWA):** Carteira/Diário (leitura/placeholder até o F6), builder de estrutura tocando na cadeia → `POST /api/structure`, `vite-plugin-pwa` (manifest, precache do shell, `NetworkFirst` em `/api`, banner de offline com a última sessão).
- **F5-D (celular):** `tailscale serve --bg --https=443 http://127.0.0.1:8080`, `allowed_hosts` do MCP com `*.ts.net`, instalação no celular, teste de offline e de toque (≥ 44 px, nada dependente de hover).

---

## 11. Só quatro motivos para parar e perguntar

1. dinheiro, conta, credencial ou contrato (ex.: Tailscale/SSL exigindo ação do dono);
2. critério que falha e cuja correção muda a spec (fórmula, convenção, schema publicado);
3. dado que não existe na fonte depois de todos os fallbacks;
4. decisão que apaga ou reescreve trabalho do dono (docs de planejamento, direção visual, mockups).

Qualquer outro caso: default do `docs/decisoes.md`, ADR curto, siga.
