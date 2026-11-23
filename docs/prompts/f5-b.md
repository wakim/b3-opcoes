# Sessão — F5-B: visual + telas de leitura (unslop) — sessão paralela ao F5-PreB

Você é o implementador desta sessão. Trabalhe sozinho, sem pedir confirmação a cada passo.
Este documento é o contrato: o que fazer, em que ordem, como provar, quando parar, como registrar.

**Pasta:** `C:\Users\wakim\Projects\b3-opcoes` · **Runner:** `podman compose` (o host não tem Node) ·
**App:** `127.0.0.1:8081` (`$env:B3_APP_PORT="8081"`; a 8080 do host está ocupada).

> ⚠️ **Esta sessão roda EM PARALELO com `docs/prompts/f5-pre-b.md`.** As duas não podem editar os
> mesmos arquivos. A divisão de donos no §3 é **rigorosa**. Se você precisar tocar num arquivo da
> outra sessão, PARE e registre a necessidade — não edite.

---

## 0. Coordenação com a sessão paralela

| Sessão | Donos exclusivos |
|---|---|
| **F5-PreB** (já lançada) | `aprender.tsx`, `styles.css`, `lib/ui.tsx` |
| **F5-B (você)** | `__root.tsx`, `index.tsx`, `t.$ticker.tsx`, `s.$ticker.$maturity.tsx`, `main.tsx` (só o `import` do CSS novo), `src/components/**` (novo), `src/styles-view.css` (novo) |

- Rode numa **worktree própria** (`tools.opencode.worktree_create`, depois `session_move`) ou
  branch separada. O merge/rebase acontece **depois** que as duas terminarem; quem integrar
  resolve conflitos. Não faça `push` em cima da outra sessão sem `pull --rebase`.
- **Não importe nada de `lib/ui.tsx`** — ele pode mudar embaixo de você. Componente de sheet que
  precisar, implemente o seu em `components/`; a integração depois deduplica com o da outra sessão.
- **Não edite `styles.css`**: os tokens (`--bg`, `--surface`, `--line`, `--text`, `--accent`,
  `--r-card` etc.) já estão lá. Seu `styles-view.css` **só consome** as variáveis e acrescenta
  classes de componente, sem redefinir tokens.

---

## 1. Estado de partida (não redescubra)

- **F5-A APROVADO** (`docs/relatorios/2026-11-23-revisao-f5a.md`, commits até `623ad7e`; prompt
  original `docs/prompts/f5-a.md`). SPA serve `/api` + `/mcp` + `dist` no mesmo FastAPI.
- **O que já funciona e não pode regressar:** carimbo `sessão 2026-11-19 · D-1` vindo de
  `/api/health`; 6 rotas file-based; Hoje com 289/163/126 + medianas + 20 linhas; Ticker com
  resumo/contadores; Série com identidade/último ponto; zero erros de console no navegador.
- **Armadilha crítica:** em `__root.tsx`, `RootLayout` **só** retorna
  `<QueryClientProvider><AppShell /></QueryClientProvider>`; o `useQuery` do carimbo mora no filho.
  Mexer nisso quebra a SPA com `No QueryClient set` e `lint`/`tsc` não pegam. Preserve o padrão.
- `docs/direcao.md` é o **contrato visual**: **A · Bancada fria**. Grafite frio, **zero matiz fora
  do gráfico**, acento = branco-gelo como única marca clara (a **agulha**), números mono tabulares
  em coluna, rótulos em caixa de frase (11 px, `text-2`, sem tracking), raio 4/6/8, hierarquia por
  tamanho e cor. Um gráfico por tela é o momento e leva a cor; os outros são listas/tabelas curtas.
- **Proibido:** gradiente (inclusive em texto), glow, sombra colorida, blob, borda neon, badge
  colorido, pill, caixa alta com tracking, ícone decorativo em rótulo, `animate-pulse` em coisa que
  não é ao vivo, `hover:scale`, `transition-all`, emoji na interface.
- Skill **unslop** instalada em `~/.config/opencode/skills/unslop` (CC BY 4.0 — manter crédito).
  Ela é escrita para desktop: **adapte ao mobile** (412 px, ≥ 44 px de alvo, nada depende de hover).

---

## 2. Objetivo

Aplicar os **9 passes do unslop durante a construção** — não "unslopar depois" — em **Hoje,
Ticker e Série**, com componentes base reutilizáveis, gráficos SVG inline, sheet de métrica e
estados de erro explícitos. **Aprender não é desta sessão** (está sendo tratado em paralelo).

---

## 3. O que construir

### 3.1 Componentes base (em `src/components/`, arquivos novos)
1. **`Metric` / `MetricGrid`** — rótulo em caixa de frase + número mono alinhado à direita,
   mesma casa decimal por coluna, hierarquia por tamanho/cor. Sem "card de estatística" com
   sparkline.
2. **`Ruler` (a assinatura)** — escala horizontal com agulha para métricas **posicionais**:
   `iv_rank_252`, `vrp_z_252`, `delta`, `days_to_liquidate`. O número continua visível; a régua
   mostra **onde** ele está. Uma forma só, reutilizada em todas as telas. Sem cor fora da régua.
3. **`Chain`** — linhas `put · strike · call` a partir de `/api/t/{ticker}/chain`. Toque expande
   `IV`, `spread_brl`, `spread_pct`, `delta`, `mid_brl`/`close_brl` e flags. **OI não está no
   payload da chain** — não invente; se quiser OI use `/api/t/{ticker}/events`? Não contém OI por
   série: **não exiba OI**. Respeite `null` de book (`bid_brl`/`ask_brl` nulos = sem oferta).
4. **`Spark`/`LineChart` em SVG inline** — sem biblioteca de gráfico. Séries ≤ 70 pontos.
   Regra de cor da direção: branco contra cinza tracejado; a cor fria só entra se precisar
   distinguir séries dentro do gráfico (nunca como estilo da tela).
   - Ticker: **estrutura a termo** (`underlying.term`: `maturity` × `iv_atm`) e **IV vs RV**
     (`underlying.history`: `iv_atm_30d` × `rv30`).
   - Série: histórico de preço/IV (`series.history`), com marcas de ajuste **só se o dado existir**
     (não desenhe linha de dividendo inventada).
5. **`MetricSheet`** — sheet de métrica ao tocar no nome de uma métrica: título, "o que é",
   "erro comum" e o número do dia. Fonte: `/api/glossary/{key}` (TanStack Query, `staleTime` 1 h)
   + o valor já recebido do `/api`. Não é a ficha do Aprender; é o mesmo conteúdo em contexto.
   Siga o padrão visual de sheet da direção (raio 8, `surface`, backdrop sem gradiente, fecha por
   botão/backdrop/Escape). Implemente independente de `lib/ui.tsx`.
6. **`ErrorState` / `Stamp`** — `ApiError` com `code`, `message` e `hint` visíveis; `not_found`
   explícito; carimbo no topo de todas as telas (já existe no `AppShell` — só refine o visual).

### 3.2 Aplicação nas rotas
- **Hoje (`index.tsx`)**: cartões de contagem (289/163/126) e medianas; tabela das primeiras
  linhas com `spot_brl`, `iv_atm_30d`, `vrp`, `n_liquid_series`, `n_expiries`; `status/exclusion`
  como texto quieto (sem badge colorido). Aviso visível quando `meta.truncated` (a API devolve 60
  de 289).
- **Ticker (`t.$ticker.tsx`)**: resumo do papel, 6 métricas com `Ruler` nas posicionais,
  seletor de vencimento simples (derivado de `underlying.term` — sem novo endpoint), 2 gráficos,
  cadeia com filtros já no servidor (`liquidity=all&delta_min=0&delta_max=1` para "todas";
  `strict` + 0,25–0,75 para "líquidas"), toque na métrica abre `MetricSheet`. **Sem builder de
  estrutura** (F5-C).
- **Série (`s.$ticker.$maturity.tsx`)**: identidade, gregas/IV do último ponto, histórico com
  gráfico SVG e última linha de pregões; toque no nome de métrica abre `MetricSheet`.
- **`__root.tsx`**: refinar appbar/nav com os tokens; manter exatamente o padrão do Provider.
  Nav com 4 destinos, ≥ 44 px, sem hover.

### 3.3 Cobertura de verbete
Crie `src/components/metricKeys.ts` com o mapa campo da API → slug do glossário
(`iv_atm_30d` → `iv-atm-30d`, `vrp` → `vrp`, `iv_rank_252` → `iv-rank-e-percentil`,
`days_to_liquidate` → `dias-para-liquidar`, etc.). Todo rótulo de métrica tocável usa esse mapa.
Não inventar chave: confira com `/api/glossary` (o smoke/auditoria de cobertura entra no F5-C).

### 3.4 Regras que não se quebram
- O Python calcula, o frontend só formata. Nenhum número nasce em TS.
- Uma camada de consulta: `fetch` **só** em `src/lib/api.ts`; dados por TanStack Query.
- Descritivo (ADR-023/024): sem `signal_class`, "caro/barato", ranking, acerto ou recomendação.
- Sem biblioteca de gráfico, sem axios, sem CSS-in-JS, sem SSR, sem servidor no frontend, sem
  dependência nova (`package.json`/lock intactos).
- Sem tocar em backend, `model_version`, SQL, journal, `docs/direcao.md`, mockups ou na parte de
  Aprender do F5-B (sessão paralela).

---

## 4. Como provar (saída colada)

1. **Unslop**: carregue a skill, aplique os 9 passes e **registre** o checklist no final de cada
   tela (Hoje, Ticker, Série). Se uma decisão nova surgir (ex.: cor funcional dentro do gráfico),
   ADR curto — mas a direção aprovada vence.
2. **Navegador** (skill `browser`; `curl` não prova UI): abra `/`, `/t/PETR4`,
   `/s/PETRL44/2026-12-18`; exija **zero erros de console**, carimbo visível, gráficos SVG
   presentes, `MetricSheet` abrindo e fechando, e **screenshot** (use `tabs.focus` antes).
   Meça com `evaluate`: largura 412 px, alvos de toque ≥ 44 px, nenhum `onMouseEnter`/`:hover`
   como único caminho de interação.
3. `podman compose --profile build run --rm --entrypoint sh web -c "npm run lint && npm run typecheck"`
   limpo; `npm run build` (script `rm -rf dist && vite build`) com hash estável em duas rodadas.
4. `podman compose build app` + `up -d app` (8081); `/api/health` `model_version 2`;
   `mcp_smoke.py` **53/53** e `structure_smoke.py` **93/93**.
5. `git diff --name-only <base>..HEAD -- backend/ docs/direcao.md docs/ui-mobile.html
   docs/ui-mockup.html frontend/src/routes/aprender.tsx frontend/src/styles.css
   frontend/src/lib/ui.tsx` = **vazio** (prova que você não invadiu os arquivos paralelos). Use como
   `<base>` o commit da sua worktree **após** o rebase — `b7a4d24` se o F5-PreB já estiver em `main`.
   O antigo `623ad7e..HEAD` agora inclui os commits `4ddaf63`/`b7a4d24` e daria falso positivo.
6. Nenhum arquivo em `data/` muda por GET.

---

## 5. Ao terminar

- `AGENTS.md`: só armadilha nova descoberta (não reescreva as do F5-A).
- `PLAN.md`: marque **só** os itens F5.5 (componentes base) e a parte de F5.3 correspondente a
  Hoje/Ticker/Série que o critério provou. **Não** marque F5-B inteiro enquanto Aprender estiver
  sendo tratado na sessão paralela.
- `docs/relatorios/2026-11-23-revisao-f5b.md`: parecer/auto-relatório com veredito, evidência do
  navegador e o checklist dos 9 passes por tela.
- Commit `style(f5-b): unslop em Hoje/Ticker/Série, régua, gráficos SVG e sheet de métrica` +
  `git push` (após `pull --rebase`, respeitando a sessão paralela).
- Mensagem final curta: componentes entregues, telas cobertas, fotos do navegador, o que ficou
  para a integração com F5-PreB, e próximo passo (F5-C).

---

## 6. Só quatro motivos para parar e perguntar

1. dinheiro, conta, credencial ou contrato;
2. critério que falha e cuja correção muda a spec (fórmula, convenção, schema publicado);
3. dado que não existe na fonte depois de todos os fallbacks;
4. decisão que apaga ou reescreve trabalho do dono (direção visual, mockups, PLAN F5-C).

Qualquer outro caso: default mais simples e reversível, ADR curto se couber, siga.
