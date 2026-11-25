# Plano — consolidação e expansão da migração shadcn

**Data:** 2026-11-25  
**Branch:** `f8-shadcn-migration`  
**Base:** `main` em `a796afa`  
**Worktree:** `C:\Users\wakim\.local\share\opencode\worktree\54cf8a\b3o-shadcn-migration`  
**Status:** F8.5.0 e F8.5.1 concluídas e revisadas; F8.5.2–F8.5.5 pendentes

---

## Objetivo

Ampliar e consolidar o uso de shadcn no frontend, migrando os comportamentos interativos que ainda são implementados localmente, sem redesenhar a interface nem alterar contratos.

A F8 já entregue e aceita migrou a base da interface. Esta frente não reabre a F8: parte do estado aceito em `a796afa` e trata somente de ganhos adicionais de consistência, acessibilidade e manutenção.

## Princípios

1. **Preservar o visual aprovado.** `docs/direcao.md`, a ADR-043 e a ADR-046 continuam sendo o contrato.
2. **Não confundir quantidade com qualidade.** Um componente de domínio não deve virar primitive apenas para aumentar a cobertura shadcn.
3. **Não regenerar primitives existentes.** Os arquivos em `frontend/src/components/ui/` contêm ajustes de tipografia, foco, raio e altura validados.
4. **Não alterar backend, dados, fórmulas, API, rotas ou `model_version`.**
5. **Toda execução roda em container.** Nenhum comando oficial usa Node no host.
6. **Validação real, sem suíte automatizada nova.** Vale o critério do ADR-019.
7. **Uma fase só fecha quando seus critérios passam.** Falha vira correção da fase, não pendência silenciosa.

## Diagnóstico atual

Já estão em uso:

- `Button`, `Input`, `Table`, `Dialog`, `Textarea`, `Checkbox` e `NativeSelect`.
- O wrapper `components/Dialog.tsx`, com foco inicial, retorno de foco e bloqueio durante salvamento.
- Tokens e classes compartilhadas em `styles.css`, `styles-view.css` e `lib/classes.ts`.

Pontos de oportunidade:

- `Select` Radix existe, mas não tem consumidor.
- `HelpTip` ainda gerencia hover, foco, toque, Escape e posicionamento mobile manualmente.
- Três grupos de filtro repetem botões com `aria-pressed`.
- Há dois contratos de estado de erro: `ErrorState` e `ErrorBox`.
- Selects, checkbox e accordion ainda são implementações nativas/locais.
- `lib/controls.ts` concentra overrides longos que podem virar variantes dos primitives.
- As tabelas repetem `contained={false}`, tipografia e classes de cabeçalho.

## Escopo da migração

| Prioridade | Componente ou padrão | Destino | Condição |
|---|---|---|---|
| Alta | Imports Radix individuais | Pacote unificado `radix-ui` | Preservar comportamento atual |
| Alta | `HelpTip` e marcadores com `title` | shadcn `Tooltip` | Preservar hover, foco, toque, Escape e painel mobile |
| Alta | Filtros de Cadeia, Série e Liquidez | shadcn `ToggleGroup` | Preservar rótulos, 44 px e estado ativo |
| Alta | `ErrorState` e `ErrorBox` | shadcn `Alert` + componente único de status | Não perder `code`, `message`, `hint` nem `role="alert"` |
| Alta | Tabelas das telas | Composição local `ScreenTable` sobre `Table` | Preservar scroll e sticky |
| Média | Overrides de `Button`/`Input` | Variantes CVA nos primitives | Zero diferença de computed style |
| Média | `NativeSelect` dos formulários | shadcn `Select` | Somente após piloto mobile/teclado aprovado |
| Média | `NativeSelect` de vencimento | Decisão após piloto | Pode permanecer nativo se houver regressão |
| Média | Checkbox nativo | shadcn `Checkbox` | Passar `disabled` explicitamente |
| Média | Accordion do Aprender | shadcn `Accordion` | Modo múltiplo, preservando ADR-033 |
| Baixa | `<details>` de vencimentos sem IV | shadcn `Collapsible` | Opcional; ganho pequeno |
| Baixa | Setas e fechar como caracteres Unicode | Lucide + `Button` existente | Somente se não alterar geometria |

## Fora de escopo

Não migrar:

- gráficos SVG (`LineChart`, `PayoffChart`);
- régua e agulha (`Ruler`);
- cartões de métrica e posição;
- navegação inferior;
- busca de ticker;
- componentes de domínio (`Metric`, `PositionBits`, `Stamp`, `OfflineBanner`, `TokenGate`);
- wrapper de `Dialog`;
- rótulos nativos acessíveis dos formulários;
- exceções CSS da ADR-046 em `styles-view.css`.

Não adicionar sem necessidade comprovada:

- `Card`, `Badge` ou `Skeleton`;
- `Chart`, `Command`, `NavigationMenu` ou `Toast`;
- `Form` com `react-hook-form`;
- biblioteca de gráficos;
- migração para Base UI/Aria nesta mesma frente.

## Registro de execução

### F8.5.0 — baseline aceito como referência

- HEAD referenciado: `a796afa6fa3a570430db265eb4534f8407999d05` (F8 aceita).
- App temporária: `http://127.0.0.1:8093`, imagem `b3o-f85-baseline:a796afa`.
- Lago: sessão `2026-11-24`, `model_version=2`, 724 sessões; estável antes e depois.
- 48 estados (16 por viewport) em 412, 1280 e 1440 px, com 48 screenshots e hashes.
- Artefatos: `data/out/f85-baseline-final/`, `data/out/f85-baseline-final/supplemental/`, `data/out/f85-offline-baseline/result.json` e `data/out/f85-lake-{before,after}.json` (191 arquivos, idênticos).
- `lint`, typecheck, cobertura (26/63/45), offline (14/14) e `git diff --check` verdes.
- Revisão independente: **PASS** para o critério restrito da fase.

Achados do baseline a corrigir nas fases seguintes, sem reabrir a F8:

- links de ticker e voltar com 44 px de altura, mas largura menor;
- rotas de leitura com `isLoading || !data` antes de `isError`, prendendo o erro em “carregando…”;
- `HelpTip`: Escape propaga e fecha o diálogo; foco de toque pode conflitar com o clique.

### F8.5.1 — fundação Radix

- `Button`, `Dialog` e `Select` migrados para os namespaces de `radix-ui` (`Slot.Root`, `Dialog.*`, `Select.*`).
- Dependências individuais removidas do root; as mesmas versões permanecem transitivas de `radix-ui@1.8.1`.
- Select mantido para o piloto da F8.5.4.
- 48 estados sem diferença de geometria, estilo, foco, tabela ou texto; dois ruídos de rasterização de 4 pixels ±1.
- Revisão independente: **PASS**.

## Fases

### F8.5.0 — Baseline e guardas

**Estimativa:** 30 min

- Confirmar branch, base e working tree limpo.
- Registrar a sessão do lago antes das provas.
- Capturar o estado atual em 412, 1280 e 1440 px nas rotas principais.
- Incluir cadeia, builder, ajuda, modal de métrica, formulários e sequência de foco por Tab.
- Confirmar que o baseline é o estado aceito da F8, não uma imagem antiga.

**Critério:** referência registrada e nenhuma alteração de código.

### F8.5.1 — Fundação Radix e limpeza

**Estimativa:** 45 min

- Migrar os imports dos primitives existentes para `radix-ui`.
- Remover dependências individuais que deixarem de ser referenciadas.
- Decidir entre utilizar ou remover `frontend/src/components/ui/select.tsx`.
- Preservar integralmente os tokens e primitives personalizados.
- Não rodar inicialização ou regeneração de CSS pelo CLI.

**Critério:** lint e typecheck passam; comportamento e visual não mudam.

### F8.5.2 — Tooltip, ToggleGroup e Alert

**Estimativa:** 1–2 h

- Envolver `HelpTip` com o primitive Tooltip, mantendo o contrato de toque e o painel inferior no mobile.
- Trocar os marcadores `title` por ajuda acessível pelo mesmo primitive.
- Migrar os três grupos de filtro para ToggleGroup.
- Unificar os estados de erro sobre Alert, preservando o envelope de erro do produto.
- Manter o texto de carregamento atual, sem animação pulsante.

**Critério:** teclado, toque, foco, Escape e aparência preservados.

### F8.5.3 — Tabelas e variantes dos controles

**Estimativa:** 45 min

- Criar uma composição local `ScreenTable` sobre o `Table` shadcn.
- Centralizar `contained={false}`, tipografia, classes de cabeçalho e wrapper de scroll.
- Migrar as sete tabelas sem alterar nenhuma classe efetiva.
- Mover os overrides de builder/cadeia para variantes CVA de `Button` e `Input`.

**Critério:** cabeçalho e primeira coluna sticky; sem duplo scroll; zero diferença de geometria, fonte ou cor.

### F8.5.4 — Pilotos comportamentais

**Estimativa:** 1–2 h

- Testar o `Select` shadcn primeiro em um formulário.
- Verificar mobile, lista longa, teclado, estado desabilitado e integração com o builder.
- Se aprovado, migrar os selects de formulário; reavaliar o seletor de vencimento separadamente.
- Migrar o Checkbox para o primitive Radix com `disabled` explícito.
- Migrar o Aprender para Accordion em modo múltiplo.
- Considerar Collapsible e ícones Lucide somente após os itens anteriores.

**Critério:** nenhuma regressão de usabilidade mobile ou acessibilidade. Se houver, manter a implementação nativa e registrar o motivo.

### F8.5.5 — Validação e fechamento

**Estimativa:** 1 h

- Rodar lint, typecheck, build e cobertura do glossário no container.
- Produzir duas builds limpas e comparar os hashes.
- Validar as rotas em 412, 1280 e 1440 px.
- Validar Tab, setas, Enter, Espaço e Escape.
- Revalidar modais, tabelas sticky e alvos de toque.
- Revalidar offline, incluindo service worker.
- Conferir a sessão do lago antes e depois.
- Atualizar documentação somente com critérios realmente atendidos.

**Critério:** todos os itens de aceitação abaixo passam.

## Critérios de aceitação

- [ ] Nenhuma alteração em backend, dados, fórmulas, API, rotas ou `model_version`.
- [ ] Geometria, fontes e cores preservadas em 412, 1280 e 1440 px.
- [ ] Alvos interativos visíveis maiores ou iguais a 44 px.
- [ ] Navegação por Tab, setas, Enter, Espaço e Escape funcionando.
- [ ] Modais preservam foco inicial, retorno de foco e bloqueio durante salvamento.
- [ ] Tabelas preservam cabeçalho e primeira coluna sticky.
- [ ] Tooltip funciona por hover, foco e toque, com fechamento por Escape.
- [ ] Grupos de filtro preservam semântica, rótulos e estado ativo.
- [ ] Erros continuam exibindo `code`, `message` e `hint` quando disponíveis.
- [ ] Browser sem erros de console ou pageerror.
- [ ] Lint e typecheck passam no container.
- [ ] Duas builds limpas produzem resultado idêntico.
- [ ] Cobertura campo ↔ glossário passa sem regressão.
- [ ] Offline revalidado na página e no service worker.
- [ ] Sessão do lago igual antes e depois das comparações.
- [ ] `git diff --check` limpo.
- [ ] Nenhuma dependência nova sem lock atualizado no container.

## Estratégia de execução

- Usar sub-agentes **Hy4 Flash**, modelo `opencode-go/hy4-flash-free`.
- Usar sub-agentes para inventário, revisão de risco e validação independente.
- Não permitir que dois sub-agentes editem os mesmos arquivos em paralelo.
- Usar o CLI do shadcn somente com prévia das alterações.
- Nunca usar `--all` nem sobrescrever primitives existentes.
- Fazer commit por fase verde e seguir a convenção de push do `AGENTS.md`.
- Se o worktree precisar de app local, seguir a armadilha registrada: copiar o lake para o worktree e usar porta livre; junction no `data/` não serve.

## Entregáveis

- Novos primitives shadcn adotados apenas onde houver ganho real.
- Componentes locais consolidados sobre esses primitives.
- Dependências Radix limpas e lock atualizado quando necessário.
- Regressão visual, funcional e offline documentada.
- Aprendizados registrados em `AGENTS.md` e critérios atualizados em `PLAN.md`.
