# Opções B3 — dados, agente e painel

Ferramenta pessoal de apoio à decisão em opções de ações da B3. Ingestão diária dos arquivos públicos da B3 (EOD), métricas derivadas calculadas em Python, servidor MCP para um agente de IA e PWA instalável no celular.

**O princípio:** o Python calcula, o agente interpreta, o humano decide. Nenhum número exibido ou citado é produzido por modelo de linguagem.

---

## Estado

| | |
|---|---|
| Planejamento | ✅ completo — spec v0.5, direção visual, log de decisões (ADR-001…028) |
| Código | 🟢 **F0–F4 completos** — lake 2024–2026 (722 sessões), `model_version=2`, `b3o screen` descritivo, API FastAPI + MCP em `/mcp` (health, screen, underlying, chain, series, events, structure_eval) e `b3o struct` (payoff, breakevens, capital, gregas e cenários da estrutura dada). F1b: o sinal **não passou** (ADR-023); nada preditivo é construído. |
| Fase atual | **F5 (API+PWA)**, conforme o dono — F4 fechado com parecer em `docs/relatorios/2026-11-23-revisao-f4.md` |
| Runtime | 🐳 tudo em container (`compose.yaml`); runner no host: `podman compose` |
| Repositório | `git@github.com:wakim/b3-opcoes.git` (SSH) — push por pacote |

O trabalho segue o [`PLAN.md`](PLAN.md), fase por fase. O caminho oficial de execução é `podman compose`; a única coisa que precisa existir no host é Podman e `git`.

---

## Mapa dos documentos

| Arquivo | Para quê |
|---|---|
| [`PLAN.md`](PLAN.md) | **comece aqui**: fases, tarefas, estimativas, critérios de aceitação |
| [`docs/spec.md`](docs/spec.md) | fonte da verdade técnica: schema, fórmulas, tools, rotas, convenções |
| [`docs/arquitetura.html`](docs/arquitetura.html) | porquê das decisões, com diagramas (abrir no navegador) |
| [`docs/glossario.md`](docs/glossario.md) | conteúdo da aba "Aprender": 3 níveis, "erro comum" por métrica |
| [`docs/ui-mobile.html`](docs/ui-mobile.html) | mockup navegável do PWA |
| [`docs/ui-mockup.html`](docs/ui-mockup.html) | mockup desktop (tabela larga da cadeia) |
| [`AGENTS.md`](AGENTS.md) | regras e armadilhas do dado, para quem (ou o que) mexer no código |
| [`docs/decisoes.md`](docs/decisoes.md) | **log de decisões**: o que foi decidido sem o dono, com padrão adotado, motivo e ponto de revisão |
| [`docs/prompts/`](docs/prompts) | prompts de execução autônoma por fase (`f0-f1b`, `f3`, `f4`, validações) |
| [`BACKLOG.md`](BACKLOG.md) | o que fica para depois, e por quê |

---

## Estrutura

```
backend/     Python: pyproject.toml, uv.lock, config.yaml, src/b3o/, scripts/
frontend/    Node/Vite (entra no F5.0) — SPA servida estática pelo FastAPI
data/        lake Parquet + journal (fora do git)
docs/        spec, decisões, contratos, glossário, relatórios
compose.yaml · Dockerfile · PLAN.md · AGENTS.md · README.md
```

Backend e frontend são projetos separados, cada um com seu lock e seu build; o que os
liga é o `compose.yaml` (e, em produção, o mesmo processo FastAPI servindo `/api` e `/mcp`).

---

## A ideia em uma tela

```
B3 (COTAHIST + OI + curva DI) + CVM (datas de resultado)
        │  prepare (1×/dia, condicional)
        ▼
Parquet + DuckDB  ──►  QueryService  ──┬──►  MCP  ──►  agente
                                       └──►  HTTP ──►  PWA (celular)
```

Duas portas, **uma** camada de consulta. Se as duas divergirem, a ferramenta perde a razão de existir.

---

## Como vai rodar

Tudo em container. O host precisa de **Podman com `podman compose`** (aqui) ou Docker (`docker compose` funciona igual, o `compose.yaml` é padrão) e `git`. Nada de Python/Node instalados no host no caminho oficial.

```powershell
podman compose run --rm prepare                          # baixa e deriva (1×/dia)
podman compose run --rm test b3o health                  # estado do lake e da versão do cálculo
podman compose run --rm test b3o screen --date 2026-11-19 # painel descritivo (sem classe de sinal)
podman compose run --rm test b3o metrics --date 2026-11-19
podman compose run --rm test b3o struct --date 2026-11-19 `
  --underlying PETR4 --leg "option:PETRL44:short:100" --leg "stock:PETR4:long:100" --capital 5000
podman compose up -d app                                 # HTTP + MCP (/mcp) em 127.0.0.1:8080
# se a 8080 do host estiver ocupada por outro projeto:
$env:B3_APP_PORT="8081"; podman compose up -d app
# prova do MCP/HTTP (container → app):
podman compose run --rm -e B3_MCP_URL=http://host.containers.internal:8081/mcp `
  -e B3_HTTP_URL=http://host.containers.internal:8081 test python scripts/mcp_smoke.py
podman compose run --rm -e B3_MCP_URL=http://host.containers.internal:8081/mcp `
  -e B3_HTTP_URL=http://host.containers.internal:8081 test python scripts/structure_smoke.py
tailscale serve --bg --https=443 http://127.0.0.1:8080
```

`b3o struct` (e `POST /api/structure` / tool `structure_eval`) **avalia a estrutura dada** —
payoff, breakevens, prêmio líquido, perda/ganho máximo, capital comprometido, gregas e 45
cenários. É cálculo, não gravação: não vira posição nem journal, e **não sugere, compara ou
ordena** estruturas (ADR-028). `quantity` é em **ações** (1 contrato = 100), não em contratos.

`b3o backtest` continua existindo como **registro histórico do portão** (F1b): o sinal de
reversão do VRP **não passou** (ADR-023) e **não** é exposto no MCP nem usado como insumo de
decisão.

Frontend: **Vite + React + TypeScript + TanStack Router/Query** (SPA pura), em `frontend/`. Build no serviço `web`:

```powershell
podman compose run --rm web     # gera frontend/dist, que o FastAPI serve
```

Os serviços e as regras do ambiente estão na spec (§4.1) e em [`PLAN.md`](PLAN.md#ambiente).
