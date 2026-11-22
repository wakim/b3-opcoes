# Opções B3 — dados, agente e painel

Ferramenta pessoal de apoio à decisão em opções de ações da B3. Ingestão diária dos arquivos públicos da B3 (EOD), métricas derivadas calculadas em Python, servidor MCP para um agente de IA e PWA instalável no celular.

**O princípio:** o Python calcula, o agente interpreta, o humano decide. Nenhum número exibido ou citado é produzido por modelo de linguagem.

---

## Estado

| | |
|---|---|
| Planejamento | ✅ completo — spec v0.5, direção visual, log de decisões |
| Código | ⛔ nenhum |
| Fase atual | F0 (ingestão) — não iniciada |
| Runtime | 🐳 tudo em container (`compose.yaml`); runner no host: `podman compose` |

O trabalho começa em [`PLAN.md`](PLAN.md), tarefa `F0.0`.

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
| [`docs/prompts/f0-f1b.md`](docs/prompts/f0-f1b.md) | prompt de execução autônoma de F0 a F1b (orquestração com sub-agentes) |
| [`BACKLOG.md`](BACKLOG.md) | o que fica para depois, e por quê |

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
podman compose run --rm prepare    # baixa e deriva (1×/dia)
podman compose run --rm app b3o health   # idade e integridade do dado
podman compose run --rm app b3o backtest # o sinal funciona? (portão antes da UI)
podman compose run --rm app b3o screen   # ranking do dia
podman compose up -d app                 # app + MCP (/mcp) em 127.0.0.1:8080
tailscale serve --bg --https=443 http://127.0.0.1:8080
```

Frontend: **Vite + React + TypeScript + TanStack Router/Query** (SPA pura), em `web/`. Build no serviço `web`:

```powershell
podman compose run --rm web     # gera web/dist, que o FastAPI serve
```

Os serviços e as regras do ambiente estão na spec (§4.1) e em [`PLAN.md`](PLAN.md#ambiente).

Nada disso existe ainda — está em `PLAN.md`, fase por fase.
