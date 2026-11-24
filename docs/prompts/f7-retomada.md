# Prompt — retomar e executar a F7 (Diário e calibração pessoal)

Copie o conteúdo abaixo para uma nova sessão do OpenCode, com o diretório do projeto `b3-opcoes`.

---

Retome **somente a F7 — Diário e calibração pessoal**. Não inicie a F8.

A F6 está implementada e validada tecnicamente, com commits/push em `main` até `11012c8`. A aceitação
final da F6 continua pendente da reconciliação manual de duas posições reais com a corretora; não
invente essa reconciliação nem marque a F6 completa.

Primeiro leia:

- `AGENTS.md`;
- `PLAN.md`, seções F6, F7 e F8;
- `docs/spec.md`, especialmente §6.12, §9 e §14;
- `docs/contratos/f6.md` e `docs/relatorios/2026-11-24-f6.md`;
- `docs/decisoes.md`, especialmente ADR-023/024 (sem sinal preditivo) e ADR-040/041 (PostgreSQL);
- `git status --short`, `git log --oneline -5` e `git diff`;
- este prompt.

## Objetivo da F7

Registrar decisões que **não criam posição** (por exemplo, “não entrei”) e medir a calibração das
**previsões estruturadas do dono**, por métrica e horizonte, com `n` e `void` explícitos.

O produto continua descritivo. O sinal F1b **não passou** (ADR-023/024): não comparar o acerto pessoal
com `premium_rich`, `premium_cheap`, taxa do backtest ou qualquer classe de sinal.

## Arquitetura obrigatória

- Manter **PostgreSQL + SQLAlchemy Core + Alembic**. O lago analítico continua Parquet/DuckDB.
- A migration `0001_f6_initial` já foi aplicada e **não deve ser editada**. Crie uma migration F7
  separada e sequencial (por exemplo `0002_f7_journal`) para as tabelas/eventos novos.
- Journal append-only: decisão e avaliação são eventos. Nunca fazer `UPDATE` em `prediction` para
  guardar `observed`/`status`; a avaliação deve inserir evento e o estado atual ser derivado.
- Triggers de `UPDATE`/`DELETE`/`TRUNCATE` nas novas tabelas; índices únicos previnem duplicação de
  decisão idempotente e de avaliação por previsão/data.
- GET não cria schema. Alembic é o único caminho de criação/evolução.
- Escritas exigem `X-App-Token`; leitura fica liberada na tailnet. Diário é **HTTP-only**: não crie
  tool MCP de escrita.
- Nunca leia, imprima ou revele `APP_TOKEN`/`.env`.
- Não altere `metrics/`, `ingest/`, `store/` nem `model_version`.
- Não crie `tests/` nem suíte automatizada (ADR-019).

## Estado conhecido da F6

- `position`, `position_leg`, `position_event` e `prediction` já existem no schema oficial.
- `prediction` da F6 é imutável e nasce `pending`; hoje ainda não há avaliação automática.
- `POST /api/positions` é idempotente por `client_position_id`.
- `POST /api/positions/{id}/close` grava snapshot completo; fechamento concorrente → `409`.
- API e MCP compartilham `QueryService`; consulta SQL fora dela é bug.
- Smokes devem usar schema PostgreSQL descartável com `search_path` próprio. Nunca apagar banco,
  volume ou arquivo; nunca aceitar `B3_JOURNAL_PATH`.
- O PWA já usa TokenGate, `sessionStorage` para rascunho/idempotência e `NetworkOnly` em posições.

## Tarefas

### 1. Modelo append-only de decisão e avaliação

- Definir `decision` para registro sem posição, com id, papel, sessão, tipo/contexto, notas e
  `client_decision_id` opcional para idempotência.
- Vincular previsão estruturada à decisão quando houver.
- Cobrir também as previsões já vinculadas a posições da F6.
- Definir evento de avaliação append-only com `prediction_id`, sessão de avaliação, `observed`,
  `status` (`hit|miss|void`) e motivo de `void`.
- Derivar o estado atual da previsão pelo evento mais recente, sem UPDATE.
- Documentar o schema no contrato F7 e registrar ADR curta se a escolha não estiver na spec.

### 2. API HTTP

Implementar, no mesmo `QueryService` e no envelope canônico:

- `POST /api/journal` — registra decisão, com previsão opcional;
- `GET /api/journal?date=&max_rows=&offset=` — lista decisões + desfecho, paginada;
- `GET /api/journal/{id}` se o detalhe for necessário à UI.

Regras:

- `client_decision_id` repetido com o mesmo corpo devolve a decisão existente; corpo diferente →
  `409 decision_id_conflict` (nomear/documentar o código escolhido).
- Autenticação antes do parse JSON; corpo limitado; notas/ticker/métrica/tipos com limites explícitos.
- Inteiros acima do int64 e valores não finitos → `400`, nunca 500/zero silencioso.
- Listas ≤ 60, resposta ≤ 200 KB, `meta.total/offset/next_offset` honestos.
- `max_rows=0` não pode gerar cursor parado.
- Leitura sem token; escrita sem token → 401; token ausente no servidor → 503.

### 3. Avaliação automática no batch

- Avaliar no **primeiro pregão posterior a `evaluate_on`**, usando somente dados dessa sessão.
- `evaluate_on` precisa ser sessão válida e coerente com `horizon_sessions`, como na F6.
- Mapear explicitamente as métricas suportadas. Exemplos da spec: `iv_atm_30d`, `vrp`, `rr25`, `spot`.
  Informar unidade e fonte de cada uma; `spot` vem do fechamento, não de preço vivo.
- Comparar `observed` com `value` usando `<`, `<=`, `>`, `>=`.
- Métrica ausente/indisponível → `void` + motivo, nunca zero nem `miss` fabricado.
- Batch repetido é idempotente; execução concorrente não duplica evento.
- Integrar ao `prepare-watch` ou a um comando de batch chamado por ele, sem mudar o cálculo do lago.
- Posições fechadas continuam tendo previsão avaliável; fechamento não cancela a previsão.

### 4. Relatório de calibração

- Agregar por **métrica + horizonte**: `n`, `hit`, `miss`, `void` e taxa sobre `hit+miss`.
- Mostrar `n` junto da taxa; `void` fica separado e com motivo.
- Não ranquear decisões, não recomendar entrada/saída e não usar vocabulário de sinal F1b.
- Python calcula; frontend apenas formata.
- Definir uma rota HTTP read-only para o relatório ou devolvê-lo em `GET /api/journal`; documentar.

### 5. Frontend Diário

- Substituir o placeholder de `/diario` por lista + formulário mobile-first.
- Reaproveitar TokenGate, Dialog, tabelas focáveis, métricas/glossário e estados da Carteira.
- Rascunho + `client_decision_id` em `sessionStorage` até sucesso/descarte; token nunca no rascunho.
- No 401, preservar rascunho e limpar só o token; no 409, recarregar a decisão/lista.
- Durante POST, bloquear Escape/backdrop/cancelar e todos os campos.
- Mostrar previsão → observado → status/`void` sem transformar texto livre em previsão.
- Validar 412 px, alvos ≥ 44 px, zero overflow horizontal e zero erros de console.
- Não migrar para shadcn/Tailwind agora; isso é F8.

### 6. Smokes e regressão

- Criar `scripts/journal_smoke.py` seguindo o isolamento de `positions_smoke.py`.
- Provar: decisão sem posição, idempotência, conflito de UUID, previsão pendente, avaliação no
  pregão seguinte, `hit/miss/void`, ausência de dado, batch repetido/concorrente, triggers,
  paginação e schema oficial intocado.
- Rodar dentro dos containers:
  - `ruff check src scripts/journal_smoke.py scripts/positions_smoke.py scripts/mcp_smoke.py`;
  - frontend `npm run lint`, `npm run typecheck`, `npm run coverage`;
  - duas builds limpas idênticas;
  - `journal_smoke`, `positions_smoke`, `mcp_smoke` e `structure_smoke`;
  - navegador sem erros de console.
- Não marcar aceitação F7 sem uso real: **30 dias de diário sem lacuna** e **≥ 10 previsões
  avaliadas automaticamente** são critérios do dono, não dados a fabricar.

## Documentação e entrega

Atualizar:

- `docs/contratos/f7.md`;
- ADR sobre decisão/avaliação append-only;
- `docs/glossario.md` e mapas de campos tocáveis;
- `docs/relatorios/AAAA-MM-DD-f7.md` com evidências, números e pendências;
- `PLAN.md` e `AGENTS.md` com armadilhas novas.

Commit por pacote e push na `main`. Se a aceitação de uso real depender do dono, registrar
**“implementação validada, aceitação pendente”**.

Não inicie F8.
