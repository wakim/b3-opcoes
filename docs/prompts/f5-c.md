# Sessão — F5-C: builder de estrutura + PWA offline + cobertura

Você é o implementador desta sessão. Trabalhe sozinho, sem pedir confirmação a cada passo.
Este documento é o contrato: o que fazer, em que ordem, como provar, quando parar, como registrar.

**Pasta:** `C:\Users\wakim\Projects\b3-opcoes` · **Runner:** `podman compose` (o host não tem Node) ·
**App:** `127.0.0.1:${B3_APP_PORT}` (a 8080 do host está ocupada; 8081 pode estar com o container da
sessão anterior — use outra porta e não pare container alheio). Rebuild da imagem (`podman compose
build app`) depois de mexer em `frontend/` ou `docs/glossario.md`: os dois entram na imagem.

> Esta é uma sessão **ÚNICA**, sem sessão paralela. Pode tocar `frontend/`, `frontend/scripts/`,
> `backend/scripts/` e a documentação. **Não** toque em `backend/src/b3o/{metrics,ingest,store,derive}`
> nem em `model_version`; `POST /api/structure` já existe (F4) e é o único cálculo novo permitido.
> Se precisar mudar backend de cálculo, PARE e registre (motivo 2 ou 4 do §10).

---

## 0. Estado de partida (não redescubra)

- **F5-B está mergeado na `main`** (`a62c218`): Hoje/Ticker/Série com os 9 passes do unslop, réguas,
  gráficos SVG, `MetricModal`, tabela dividida em **Com opções (163)** / **Sem opções (126)** com a
  primeira coluna sticky, labels expandidos ("Volatilidade implícita", "Prêmio de volatilidade",
  "Call / Europeia", "Perna") e `screen` paginado por `offset` (o frontend busca todas as páginas e
  mostra os 289).
- **F5-PreB mergeado**: a ficha do Aprender é **accordion independente** (ADR-033, vários abertos,
  `staleTime` 1 h), padrão **diferente** do `MetricModal`. Não deduplique os dois.
- **O que não pode regredir:** carimbo `sessão 2026-11-19 · D-1` vindo de `/api/health`; 6 rotas
  file-based; Hoje 289/163/126 + medianas; Ticker 6 métricas/3 réguas/2 gráficos/cadeia; Série
  identidade/graph/8 pregões; ficha em modal com foco/Tab/Escape/backdrop; coluna sticky; zero erros
  de console; `RootLayout` só retorna `<QueryClientProvider><AppShell /></QueryClientProvider>` e o
  `useQuery` do carimbo mora no filho (armadilha do F5-A).
- **APIs prontas:** `/api/health`, `/api/screen?offset=&max_rows=`, `/api/tickers`, `/api/glossary`,
  `/api/t/{ticker}`, `/api/t/{ticker}/chain`, `/api/t/{ticker}/events`, `/api/s/{ticker}/{maturity}`,
  `POST /api/structure`.
- **Fonte de estrutura:** `docs/contratos/onda-j.md` §2 (corpo canônico) e §3 (números). O contrato
  do F4 já devolve `net_premium_brl`, `breakeven_brl`, `max_loss_brl`, `max_gain_brl`,
  `capital_committed_brl`, gregas agregadas, `payoff` e `scenarios`.
- **Referência de conferência (PETR4, 19/11/2026, spot 42,67):** coberta (100 PETR4 + PETRL44
  vendida 100) prêmio **4.109,00**, breakeven **41,09**, perda máx **4.109**, ganho máx **207**;
  put caixa (PETRX42 vendida 100) **−72 / 40,19 / 4.019 / 72**; trava de alta (PETRL447 + / PETRL44 −)
  **35 / 42,76 / 35 / 40**; trava de baixa (PETRX14 + / PETRX42 −) **99 / 42,42 / 99 / 151**;
  straddle 4316 **315 / 40,01 e 46,31 / 315 / ilimitado**.
- **Aprender não é desta sessão.** Nada de reescrever a ficha do Aprender.
- **Entrou na `main` depois do F5-B (23/11, não redescubra):**
  - `6d2fa93` (ADR-034): **2026-10-26 é choque real de mercado** (IBOV −14,35%), não artefato. Os
    textos de "data-armadilha" foram corrigidos; o veredito do portão não mudou.
  - `424b9ff` (ADR-035): aviso de **pregão dominante na RV**. O `screen` (e o `summary` do
    `/api/t/{ticker}`, que é a mesma linha) traz `rv_shock_date`, `rv_shock_share_pct` (0–100) e a flag
    `rv_shock_day` (≥ 50% da variância de 30 pregões num dia só); o resumo traz `rv_shock_count` e
    `rv_shock_dates`. Na tela: aviso + link "O que é isso" no Hoje, marca `†` no prêmio de volatilidade
    (só quando há valor), métrica "Pregão dominante na realizada" + aviso no Ticker. Em 19/11:
    **116 de 163** papéis marcados (115 por 26/10). A fração é exibida com **piso** (`sharePct`): ABEV3
    tem 49,97% e não pode aparecer "50%" sem a marca.
  - `53b3141`: o `MetricModal` renderiza `**negrito**`, `` `código` `` e links do verbete via
    `components/InlineMd.tsx` (nós React, sem HTML cru e sem biblioteca).
  - Glossário servido: **31** verbetes (30 + "Pregão dominante na RV", slug `pregao-dominante-na-rv`,
    já mapeado em `metricKeys.ts` para `rv_shock_share_pct` e `rv_shock_date`).
- **Não pode regredir também:** o aviso/`†` do Hoje, a 7ª métrica e o aviso do Ticker, e o negrito da
  ficha. Ticker agora tem **7** métricas (não 6).

---

## 1. Objetivo

Fechar o **builder de estrutura** (F5.6), o **PWA offline** (F5.7), a **cobertura campo↔glossário** e
a **busca de papel** (B1). Carteira/Diário continuam placeholders explícitos até o F6.

---

## 2. Builder de estrutura (F5.6)

Arquivos novos: `frontend/src/components/StructureBuilder.tsx`, `frontend/src/components/PayoffChart.tsx`
(SVG inline, sem biblioteca). Alterar `frontend/src/routes/t.$ticker.tsx` e `frontend/src/lib/api.ts`
(tipos já existem: `Structure`, `apiPost`).

- **Montar as pernas** a partir da cadeia: cada linha expandida ganha **Comprar** / **Vender** com
  `quantity` (default **100 ações**, editável; deixe claro "100 ações = 1 contrato", sem multiplicar
  por 100 em lugar nenhum). A perna usa o `ticker` da série (o backend resolve pelo ISIN) e
  `kind: "option"`.
- **Perna de ação** (para coberta): botão "Adicionar ação" com `kind: "stock"`, `ticker` do papel,
  `side` long/short e quantity. Só a ação do próprio papel é aceita (o backend valida).
- **Lista de pernas** com remover e limpar; limite ≤ 60 (o backend recusa mais).
- **Calcular** chama `POST /api/structure` com `{underlying, date, legs, scenarios: true}`. **Nenhum
  número é calculado em TS**: prêmio, breakevens, perda/ganho máximo, capital, gregas e payoff vêm da
  resposta.
- **Exibir**: prêmio líquido, breakevens, perda/ganho máximo, capital comprometido, Δ/Γ/Θ/ν agregados e
  o **payoff no vencimento** em SVG (eixo x = `spot_brl`, y = `pnl_brl`, linha de zero; sem cor fora
  do gráfico). Cenários são opcionais; se exibir, é uma tabela curta, não um segundo gráfico.
- **Ausência honesta**: `max_loss_brl: null` + `unbounded_loss`; `scenarios: null` +
  `scenarios_unavailable`; `no_iv`/`no_rates` explícitos. Nunca zero silencioso.
- **Descritivo (ADR-024)**: nada de "recomendado", "caro/barato", ranking, acerto ou conselho. O
  builder é uma **calculadora de payoff**, não uma sugestão. Não grava nada (F6 é que grava).
- Mobile: entradas e botões ≥ 44 px; a lista e o resultado não dependem de hover; o painel é inline
  na Ticker (não modal) para não competir com o `MetricModal`.

---

## 3. PWA offline (F5.7)

Arquivos: `frontend/vite.config.ts`, `frontend/package.json` + `package-lock.json`,
`frontend/src/main.tsx` (registro do SW), `frontend/src/components/OfflineBanner.tsx` (novo),
`frontend/public/` (ícones) e `frontend/src/routes/__root.tsx` (banner).

- `vite-plugin-pwa@2` (suporta Vite 8; peers `workbox-build`/`workbox-window`). Instale **no container**
  (`npm install`/`npm ci` no serviço `web`) e versione o `package-lock.json`. Nada de `pip`/`npm`
  avulso no host.
- Manifest: nome "Opções B3", `display: standalone`, tema/fundo `#101418`, ícones **192/512** em
  `public/` (monocromático grafite + agulha; nada de gradiente/glow). `registerType: "autoUpdate"`.
- Workbox: precache do shell (`index.html`, assets) e **`NetworkFirst` em `/api/`** (cache
  `b3o-api`, timeout curto, `expiration`). `navigateFallback: "/index.html"` para as rotas SPA.
- **Offline banner**: quando `navigator.onLine === false` (ou um GET de `/api` falhar), mostrar
  "sem rede — última sessão <AAAA-MM-DD>" com a data do **último `/api/health` bem-sucedido**,
  persistida em `localStorage` a cada sucesso. Nunca inventar sessão: sem cache, "sessão indisponível".
- O carimbo online continua vindo de `/api/health`; offline usa o último valor persistido e diz que é
  offline. Não confunda cache de query em memória com persistência entre reloads.
- **O carimbo hoje mente por construção:** `components/Stamp.tsx` escreve `D-1` **fixo**, seja qual
  for a idade do dado. O `/api/health` já devolve `age_sessions` (pregões de atraso contra
  `expected_last_session`; 0 em 23/11 com o lake em 19/11, porque 20/11 foi feriado e 23/11 ainda não
  fechou). Derive o sufixo dele: `0` → `D-1`; `n > 0` → `D-{n+1} · atrasada` (texto quieto, sem
  matiz); ausente → só a data. Offline, mostre a data persistida + "offline" e **nenhum** `D-n` (não
  se sabe a idade sem o servidor). Nada vem do relógio do navegador.
- Build oficial no container (`node:26-slim`, serviço `web`); `npm run build` com `rm -rf dist` antes.
- **Teste de offline real**: carregar online, confirmar SW registrado + `manifest` + `caches.keys()`;
  **parar o container `app`**; recarregar a página; exigir shell + banner offline + carimbo da última
  sessão; religar o container. Se o SW não servir a navegação, é falha — não "quase".

---

## 4. Cobertura campo↔glossário

Arquivo novo: `frontend/scripts/glossary-coverage.mjs` + script `npm run coverage`.

- Ler `src/components/metricKeys.ts` e extrair os **slugs** do mapa `KEYS`.
- Ler os campos usados como tocáveis nas rotas/componentes (`field="..."`, `onMetric('...')`,
  `openMetric('...')`) e conferir que **todo campo tem slug no mapa**. Inclui o
  `openMetric('rv_shock_share_pct', …)` do aviso do Hoje.
- **Não é `pytest` nem suíte** (ADR-019): é um script de conferência no dado/servidor real, como o
  `mcp_smoke.py`. Não crie `tests/`.
- Chamar `GET /api/glossary` (base `B3_HTTP_URL`, default `http://host.containers.internal:8080`) e
  conferir que **todo slug do mapa existe** no glossário.
- Falha = lista do que falta + exit 1. Não invente slug: se a métrica é real e não tem verbete, o
  verbete entra em `docs/glossario.md`; se não é métrica, o rótulo não é tocável.
- Rodar no `web` (Node) ou como smoke documentado; entra no critério de pronto e no `README.md`.

---

## 5. Busca de papel (B1)

Componente novo `frontend/src/components/TickerSearch.tsx` (sem nova rota de nav).

- Input no topo de **Hoje** (abaixo do título), com `aria-label`; consulta `/api/tickers?q=` via
  TanStack Query (`staleTime` 1 h, `enabled` com `q` não vazio), mostra `ticker`, `corp_name`,
  `in_universe` e `has_options` como texto quieto; link para `/t/$ticker`.
- Prefixo primeiro é do servidor; ≤ 60 com `meta.total` honesto; `q` vazio não dispara.
- Alvos ≥ 44 px; navegação por teclado; sem hover como único caminho. Mobile 412 px sem overflow.

---

## 6. Carteira e Diário

Continuam **placeholders explícitos** (F6): "Posições reais entram no F6; nenhuma escrita nesta
versão." e "Decisões e previsões entram no F6/F7; nenhuma escrita nesta versão." Não invente posição,
P&L ou histórico.

---

## 7. Regras que não se quebram

- **O Python calcula, o frontend só formata.** O builder não recalcula prêmio/breakeven/payoff.
- **Uma camada de consulta:** `fetch` **só** em `src/lib/api.ts`; dados por TanStack Query.
- **Descritivo (ADR-023/024):** sem `signal_class`, "caro/barato", ranking, acerto ou recomendação.
- **Sem biblioteca de gráfico**, sem CSS-in-JS, sem SSR, sem servidor no frontend. A única dependência
  nova é a do PWA (`vite-plugin-pwa` + peers); qualquer outra, justifique e registre.
- **Não tocar** em `docs/direcao.md`, mockups, `backend/src/b3o/{metrics,ingest,store,derive}`,
  `model_version`, SQL, journal, nem na ficha do Aprender.
- **`AGENTS.md`:** só armadilha nova descoberta, não reescreva as existentes.
- **`PLAN.md`:** marcar só o que o critério provou (F5.6, F5.7, cobertura e B1). Não marcar F5-D.
- **`data/`:** nenhum GET pode alterar arquivo; confira o manifesto antes/depois.

---

## 8. Como provar (saída colada)

1. `npm run lint`, `npm run typecheck` e `npm run build` limpos no container; duas builds seguidas com
   hashes idênticos (o `build` já faz `rm -rf dist`).
2. `npm run coverage` (ou o smoke equivalente) **verde**: todo campo tocável tem slug e todo slug
   existe em `/api/glossary`.
3. **Builder** no navegador: montar coberta, put caixa, trava de alta, trava de baixa e straddle com
   os tickers/quantidades da referência do §0 e conferir **um** caso à mão; os números exibidos têm de
   bater com o `POST /api/structure` (nada calculado em TS). Erros `bad_legs`/`underlying_mismatch`/
   `maturity_mismatch` aparecem no envelope, sem traceback.
4. **PWA**: manifest + SW registrados, `caches.keys()` com o precache e o `b3o-api`; teste de offline
   real (parar o `app`, recarregar, shell + banner + última sessão; religar). Sem erros de console no
   ciclo online→offline→online.
5. **Busca**: `/api/tickers?q=PETR` → PETR3/PETR4 com `in_universe`/`has_options`; digitar e navegar;
   ≤ 60; 412 px sem overflow e alvos ≥ 44 px.
6. **Regressão:** Hoje 289/163/126 + paginação `offset` + aviso "116 de 163" com `†`; Ticker com 7
   métricas e aviso de pregão dominante em B3SA3 (`26/10/2026 · 91%`); Série e `MetricModal` (com
   negrito) intactos; carimbo `D-1` derivado de `age_sessions` (não literal);
   `mcp_smoke.py` **53/53** e `structure_smoke.py` **93/93**; `GET /api/health` `model_version 2`;
   `npm run build` dentro do container; nenhum arquivo em `data/` muda por GET.
7. **Vocabulário:** grep de `premium_rich|premium_cheap|signal_class|acerto|ranking|caro|barato|recomend`
   nas respostas/UI = 0 (fora os nomes de campo `net_premium*`/`premium_brl`).

---

## 9. Ao terminar

- `docs/relatorios/2026-11-23-revisao-f5c.md`: veredito, evidência do navegador (online e offline),
  números do builder conferidos, o que ficou de fora.
- `PLAN.md`: marcar **F5.6**, **F5.7**, a cobertura e a busca (B1) que o critério provou; F5-D intacto.
- `AGENTS.md`: só armadilha nova (PWA/SW, builder, offline).
- Commit por pacote verde + `git push` (após `pull --rebase`, respeitando a `main`).
- Mensagem final curta: o que foi entregue, fotos do navegador, o que ficou para o F5-D e o próximo
  passo (F5-D: Tailscale/celular).

---

## 10. Só quatro motivos para parar e perguntar

1. dinheiro, conta, credencial ou contrato;
2. critério que falha e cuja correção muda a spec (fórmula, convenção, schema publicado);
3. dado que não existe na fonte depois de todos os fallbacks;
4. decisão que apaga ou reescreve trabalho do dono (direção visual, mockups, PLAN).

Qualquer outro caso: default mais simples e reversível, ADR curto se couber, siga.
