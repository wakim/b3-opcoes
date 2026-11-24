# Prompt — retomar e concluir a F6

Copie o conteúdo abaixo para uma nova sessão do OpenCode, com o diretório do projeto `b3-opcoes`.

---

Retome **somente a F6 — Posições/Carteira** do projeto `b3-opcoes`. Não inicie F7 nem F8.

A F6 está parcialmente implementada e deliberadamente **não commitada/pushed**. Primeiro leia:

- `AGENTS.md`
- `PLAN.md`, seções F6, F7 e F8
- `docs/contratos/f6.md`
- `docs/decisoes.md`, especialmente ADR-040
- `git status --short` e `git diff`
- `docs/prompts/f6-retomada.md`

## Decisão de arquitetura

O journal operacional deve usar **PostgreSQL + SQLAlchemy + Alembic**. O lago analítico continua em
Parquet/DuckDB. Como `data/journal.db` oficial nunca existiu, não há dados SQLite para migrar:
use **uma única migration inicial**, já criada em
`backend/migrations/versions/0001_f6_initial.py`. Não crie outra migration.

Regras obrigatórias:

- Python calcula; frontend só formata.
- Journal é append-only; fechamento é evento, nunca UPDATE.
- Triggers bloqueiam UPDATE/DELETE nas tabelas do diário.
- GET não cria schema nem posição; Alembic é o único caminho de criação do schema.
- Escritas exigem `X-App-Token`; leitura fica liberada na tailnet.
- Nunca leia, imprima ou revele `APP_TOKEN`/`.env`.
- Não altere `metrics/`, `ingest/`, `store/` nem `model_version`.
- Não crie `tests/` nem suíte automatizada.
- Não invente posição, P&L ou reconciliação com corretora.

## Estado atual

Já foi feito, mas ainda não validado após a troca para PostgreSQL:

- `backend/pyproject.toml` e `backend/uv.lock` com SQLAlchemy, psycopg e Alembic.
- `backend/alembic.ini`, `backend/migrations/env.py`, template e migration inicial `0001_f6_initial`.
- `backend/src/b3o/journal.py` reescrito com SQLAlchemy Core, transações, `SELECT ... FOR UPDATE`,
  índices únicos para open/close e leitura sem criar schema.
- `QueryService` parcialmente ajustado para PostgreSQL e para os problemas da revisão:
  consulta histórica, marca zero, posição vencida, strike vigente no payoff, paginação,
  previsão coerente com o calendário, capital ilimitado e IV fora do universo.
- API com `offset` em posições e autenticação por dependência.
- MCP `positions` com `max_rows`/`offset`.
- Frontend F6 inicial: TokenGate, formulário, lista, detalhe, pernas, marcas, payoff, tese e fechamento.
- `PLAN.md` atualizado com o progresso e com a futura F8 de shadcn + Tailwind.

Validações feitas **antes** da migração PostgreSQL e que não provam o estado atual:

- ruff limpo;
- `positions_smoke.py`: 24/24 em SQLite temporário;
- `mcp_smoke.py`: 57/57;
- frontend lint, typecheck, coverage e duas builds limpas idênticas;
- navegador provou leitura sem token, TokenGate/localStorage e abertura via formulário.

A UI também revelou dois problemas que precisam de confirmação/correção:

1. SQLite recebia `disk I/O error` no bind virtiofs do Windows; por isso a decisão foi PostgreSQL.
2. após o POST, a lista ficou momentaneamente vazia apesar de o GET imediato conter a posição;
   confirme se era cache do service worker ou corrida do TanStack Query e corrija.

## Ambiente temporário

- App principal continua na **8083**; não a derrube sem necessidade.
- A app antiga de teste da **8084** ainda pode existir:
  `b3-opcoes-app-run-f91fc5465096`.
- O volume SQLite descartável `b3o-f6-ui-journal` ainda pode existir.
- `data/out/f6-ui` já foi removido do host.
- PostgreSQL ainda não foi iniciado.
- `data/journal.db` oficial continua inexistente.

Remova somente os temporários após criar o isolamento PostgreSQL. Nunca use o banco oficial para UI/smoke.

## Ordem recomendada

### 1. Deixar o backend importável e o Compose consistente

- Rode ruff/importação e corrija erros da refatoração parcial.
- Termine `compose.yaml`:
  - serviço `db` com `postgres:latest`, volume nomeado `b3o-postgres` e healthcheck;
  - `DATABASE_URL` para app/dev/test;
  - app/dev com `depends_on` saudável e `alembic upgrade head` antes do uvicorn;
  - remova `b3o-journal`, `B3_JOURNAL_PATH` e qualquer referência SQLite restante.
- Copie `backend/alembic.ini` e `backend/migrations/` nos stages `runtime` e `dev` do Dockerfile.
- Confirme que `podman compose config --quiet` passa sem imprimir segredos.
- Inicie apenas o PostgreSQL e aplique a migration inicial uma vez.

### 2. Validar o schema append-only

Confirme no banco temporário:

- migration `0001_f6_initial` aplicada;
- tabelas `position`, `position_leg`, `position_event`, `prediction`;
- triggers de UPDATE/DELETE nas quatro;
- índices únicos de no máximo um `open` e um `close` por posição;
- abertura e fechamento em transação;
- segundo fechamento concorrente não cria evento duplicado;
- GET sem linha não cria posição/schema.

### 3. Isolar UI e smokes em PostgreSQL

Use um schema temporário com `search_path` ou um banco temporário descartável. O
`positions_smoke.py` atual ainda é perigoso porque apaga `B3_JOURNAL_PATH`: reescreva para nunca
apagar banco oficial e nunca aceitar path fora do isolamento temporário.

Adapte também `mcp_smoke.py`:

- comparar `totals.open_positions` com `meta.total`, não com `len(positions)`;
- não aplicar grep preditivo em `notes`, que é texto livre;
- validar paginação quando houver mais de uma página.

### 4. Fechar os achados da revisão de backend

Verifique se as correções parciais estão completas e cobri os casos:

- marca `0`, negativa ou não finita vira `null + mark_missing`; fechamento é bloqueado;
- posição é marcada como aberta na data pedida, mesmo que tenha fechado depois;
- detalhe fechado usa snapshot do fechamento e não mistura P&L com marcas de outra sessão;
- posição vencida não derruba a carteira; dias até vencimento não ficam negativos;
- payoff/breakevens usam strike vigente e incluem kinks/raízes;
- série é resolvida por `(ticker, maturity)` e lado C/P;
- `event_in_window` considera vencimento e evento de strike conhecido;
- `derive_kind` só usa nomes clássicos com quantidades compatíveis;
- risco ilimitado pode ser registrado com capital `null`, flag e sem porcentual fabricado;
- IV de entrada/atual vem de `metrics_underlying`, mesmo fora do universo;
- previsão exige `evaluate_on` posterior e coerente com `horizon_sessions`;
- token não ASCII não causa 500; autenticação ocorre antes da validação do corpo;
- inteiros grandes, overflow, notas, métrica e ticker têm limites explícitos;
- paginação `offset`/`next_offset` funciona na API e no MCP;
- resposta detail trata truncamento sem mostrar payoff parcial como completo.

O contrato atual prevê apenas a tool read-only `positions`; não crie tool de escrita.

### 5. Corrigir o frontend

Priorize:

- `/carteira/$positionId` está registrada como filha, mas `carteira.tsx` não tem `<Outlet />`.
  Torne a rota não aninhada (`carteira_.$positionId.tsx`) ou reestruture com layout/Outlet e regenerate
  `routeTree.gen.ts` pelo workaround já conhecido do bind Windows.
- Durante POST pendente, bloqueie fechar/Escape/backdrop, todos os campos e cancelar; idealmente
  acrescente idempotência na abertura para resposta perdida não duplicar posição.
- No 401, preserve o rascunho e limpe apenas o token.
- No 409 `position_closed`, invalide/recarregue posição e carteira.
- Use `leg_id` como chave das marcas.
- Como o resumo agora deve ter `breakeven_brl`, remova o GET completo de detalhe por cartão.
- Peça `max_rows=60` e implemente paginação se `meta.next_offset` existir.
- Exiba aviso de `meta.truncated`/`meta.note` no detalhe e nas escritas.
- Corrija foco do Dialog (`:disabled`, fieldset, foco conectado) e `aria-describedby` na confirmação.
- Tabelas horizontais do detalhe: `role="region"`, `tabIndex=0`, rótulo e foco visível.
- Rótulo de IV fechada deve ser “na sessão/no fechamento”, não “hoje”.
- Atualize o placeholder do Diário: Carteira já grava; decisões do Diário continuam F7.
- Expanda “IV” para “Volatilidade implícita” nas tabelas.
- Limite métrica livre e use `overflow-wrap:anywhere` na tese.
- Adicione verbetes/mapas de glossário para `pnl_pct`, `delta_exposure_brl`, `days_to_expiry`,
  `capital_limit_brl` e demais métricas tocáveis; rode `npm run coverage`.

Não migre para shadcn agora; isso está planejado como F8.

### 6. Validar a Carteira isolada

Suba uma app temporária em 8084 com schema PostgreSQL próprio e token descartável. Prove:

- leitura sem token;
- TokenGate e persistência do token no localStorage;
- abertura pelo formulário;
- lista, totais, detalhe, pernas, marcas, breakevens, payoff e tese;
- fechamento com confirmação;
- layout 412 px, alvos ≥ 44 px e zero erros de console;
- nenhum acesso ao schema oficial.

Remova a app e o schema/volume temporários ao terminar.

### 7. Regressão final

Rode dentro dos containers:

- `ruff check src scripts/positions_smoke.py scripts/mcp_smoke.py`
- frontend `npm run lint`, `npm run typecheck` e `npm run coverage`
- duas builds limpas idênticas
- `positions_smoke` com todos os critérios PASS
- `mcp_smoke` com todos os critérios PASS
- `structure_smoke` com todos os critérios PASS
- navegador sem erros de console

### 8. Documentação e entrega

Atualize:

- `docs/contratos/f6.md` para PostgreSQL/SQLAlchemy/Alembic;
- ADR-040 como superada e ADR-041 para PostgreSQL, volume, migration e backup;
- `AGENTS.md` com a armadilha do bind e o storage real;
- `docs/relatorios/2026-11-24-f6.md` com evidências, números e pendências;
- `PLAN.md`.

**Não marque F6 completa** sem a reconciliação manual de duas posições reais com a corretora.
Quando a implementação estiver verde, commite por pacote, rode `git pull --rebase` e `git push` na `main`.
Se a prova da corretora ainda depender do dono, registre F6 como “implementação validada, aceitação pendente”
e não invente a reconciliação.
