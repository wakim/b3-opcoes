# Sessão — F5-PreB: ficha do Aprender como painel inferior (antes do F5-B)

Você é o implementador desta sessão. Trabalhe sozinho, sem pedir confirmação a cada passo.
Este documento é o contrato: o que fazer, como provar, quando parar, como registrar.

**Pasta:** `C:\Users\wakim\Projects\b3-opcoes` · **Runner:** `podman compose` (o host não tem Node) ·
**Fonte de verdade:** `docs/direcao.md` (direção visual aprovada), `AGENTS.md` → *Frontend* e
*Armadilhas do F5-A*.

> Micro-ajuste de design **antes** do F5-B. Não é o unslop completo, não é o visual final.
> Só resolve o problema concreto abaixo, com o menor diff possível e reversível.

---

## 1. Estado de partida (não redescubra)

- **F5-A entregue e APROVADO** (`docs/relatorios/2026-11-23-revisao-f5a.md`, commits até `623ad7e`).
  App em `127.0.0.1:8081` (`$env:B3_APP_PORT="8081"`; 8080 está ocupada por outro projeto).
- **Aprender funciona**, mas a ficha abre **no fim da página**: `frontend/src/routes/aprender.tsx`
  renderiza a lista de 30 verbetes e, quando um é tocado, o `<section className="card entry">`
  aparece **depois** da lista — é preciso rolar até o fim para ler a explicação.
- O conteúdo vem de `GET /api/glossary/{key}` via TanStack Query (`useQuery` com
  `enabled: selected !== null`, `staleTime` 1 h). Não há `fetch` fora de `src/lib/api.ts`.
- Tokens já em `frontend/src/styles.css` como CSS variables (`--bg`, `--surface`, `--line`,
  `--text`, `--text-2`, `--accent`, `--r-sheet: 8px`). O direção pede **sheet com raio 8**,
  sem gradiente, sem glow, sem pill, sem caixa alta com tracking, sem emoji, números mono
  tabulares, alvo de toque ≥ 44 px e **nada dependente de hover**.
- Padrão que já deu certo e **não pode regressar**: `RootLayout` em `__root.tsx` só retorna
  `<QueryClientProvider><AppShell /></QueryClientProvider>`; o `useQuery` do carimbo vive no
  filho `AppShell`. Se o provider subir ou descer errado, a SPA morre com `No QueryClient set`
  e `lint`/`tsc` não pegam. Valide no navegador, não só com `curl`.

---

## 2. Problema a resolver

Ao tocar num verbete, a explicação deve aparecer **sem rolagem de página**: um **painel inferior
fixo (bottom sheet)** sobreposto ao conteúdo, com o texto rolável dentro dele e fechamento
explícito. Alternativa aceita, se o implementador achar mais simples e igualmente estável:
**expandir/colapsar inline** no próprio item da lista. Nesse caso, registre ADR curto explicando
a escolha. **Recomendação: bottom sheet** — é o padrão já previsto em `docs/ui-mobile.html`
(`.sheet` / `.sheet-panel`) e usa o `--r-sheet: 8px` da direção.

---

## 3. Escopo — o que construir

### 3.1 Arquivos (donos)
- `frontend/src/routes/aprender.tsx` — estado de seleção, abertura/fechamento, conteúdo do sheet.
- `frontend/src/styles.css` — estilos do sheet, backdrop e travamento de scroll do `body`.
- `frontend/src/lib/ui.tsx` — **opcional**: extrair um componente `Sheet` reutilizável (F5-B vai
  usar de novo). Se extrair, mantenha sem `fetch` e sem tipos de API.
- **Mais nada.** Não tocar em backend, `compose.yaml`, `Dockerfile`, `package.json`,
  `package-lock.json`, `docs/direcao.md`, mockups, PLAN F5-B nem em nenhum arquivo congelado.

### 3.2 Requisitos de comportamento
1. Toque no verbete abre o sheet **no lugar**, sem mudar de rota e sem mover o `scrollTop` da
   página. A lista continua montada atrás.
2. O sheet fica **fixo na parte de baixo** da viewport (`position: fixed; inset-inline: 0;
   bottom: 0`), com `max-height: ~84dvh`, conteúdo rolável **dentro** dele
   (`overflow-y: auto`), `overscroll-behavior: contain`. Backdrop cobre o resto com cor escura
   sem gradiente.
3. Cabeçalho do sheet: título do verbete + nível (`nível 1/2/3`) + botão **fechar** com
   alvo ≥ 44 px e texto em caixa de frase (sem ✕ decorativo, sem emoji).
4. Fecha por: botão "fechar", toque no backdrop e tecla **Escape**. Ao fechar, devolver o foco
   ao botão que abriu (guardar o `ref`). Enquanto aberto, travar o scroll do `body`
   (`overflow: hidden`) e restaurar ao fechar. `aria-modal="true"` + `role="dialog"` +
   `aria-labelledby` no título.
5. Trocar de verbete **com o sheet aberto** atualiza o conteúdo sem piscar e sem fechar
   (a lista atrás continua clicável apenas via teclado? mantenha simples: backdrop cobre;
   para trocar, fecha e abre de novo — está OK, mas não pode haver two sheets simultâneos).
6. O corpo do verbete continua vindo de `/api/glossary/{key}` — o `plain()` atual (remove `**` e
   crases, quebra por parágrafos) pode melhorar para tratar `^>` e `^---$`, **sem** adicionar
   biblioteca de Markdown. Isso fecha a pendência **B2** do parecer.
7. Mantém `staleTime` de 1 h do glossário e o carimbo `sessão AAAA-MM-DD · D-1` visível no topo.
   O sheet não pode cobrir o carimbo (ele fica no topo; sheet na parte de baixo).
8. A pendência **B1** (`/api/tickers` sem UI) **não** entra nesta sessão — é F5-C.

### 3.3 Regras que não se quebram
- O Python calcula, o frontend só exibe. Nenhum número novo.
- Sem biblioteca nova, sem CSS-in-JS, sem axios, sem SSR, sem servidor no frontend.
- `frontend/src/lib/api.ts` continua sendo o **único** arquivo com `fetch` (grep).
- Sem gradientes, glow, sombra colorida, pill, caixa alta com tracking, `animate-pulse`.
- Sem tocar em `model_version`, métricas, SQL, journal ou qualquer rota de escrita.

---

## 4. Como provar (saída colada)

1. **No navegador** (skill `browser` — `curl` prova só o shell HTML e não pega erro de React):
   - abrir `http://127.0.0.1:8081/aprender`, esperar os 30 botões;
   - anotar `window.scrollY`, clicar em "IV ATM 30d" e conferir: sheet visível **sem scroll da
     página** (`scrollY` inalterado), título + "O que é / Erro comum / No painel" dentro do sheet;
   - `console` com **zero erros**;
   - fechar pelo botão, reabrir e fechar por clique no backdrop e por `Escape`;
   - confirmar que o `overflow` do `body` volta ao normal depois de fechar;
   - tirar `screenshot` e mostrar (use `tools.browser.tabs.focus` antes).
2. `podman compose --profile build run --rm --entrypoint sh web -c "npm run lint && npm run typecheck"`
   limpo, e `npm run build` (o script já é `rm -rf dist && vite build`) com hash estável em duas
   rodadas.
3. `podman compose build app` + `up -d app` em `B3_APP_PORT=8081`; `/api/health` segue
   `model_version 2`; `mcp_smoke.py` **53/53** e `structure_smoke.py` **93/93** inalterados.
4. `git diff --name-only 623ad7e..HEAD -- backend/ docs/direcao.md docs/ui-mobile.html
   docs/ui-mockup.html` = vazio. Nenhum arquivo em `data/` muda.

---

## 5. Ao terminar

- `AGENTS.md`: só se descobrir armadilha nova (ex.: foco/backdrop). Não reescreva o que já está.
- `PLAN.md`: **não** marcar F5-B como feito; se quiser, uma linha em "Estado atual" dizendo
  "ajuste pontual do Aprender concluído antes do F5-B".
- Commit `style(f5): ficha do Aprender abre em painel inferior fixo` e `git push origin main`.
- Mensagem final curta: (1) o que mudou, (2) evidência do navegador, (3) se escolheu
  expand/collapse em vez do sheet e por quê, (4) B1/B2: qual ficou fechada.

---

## 6. Só quatro motivos para parar e perguntar

1. dinheiro, conta, credencial ou contrato;
2. critério que falha e cuja correção muda a spec (fórmula, convenção, schema publicado);
3. dado que não existe na fonte depois de todos os fallbacks;
4. decisão que apaga ou reescreve trabalho do dono (direção visual, mockups, PLAN F5-B).

Qualquer outro caso: default mais simples e reversível, ADR curto se couber, siga.
