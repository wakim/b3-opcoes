# Sessão — F5-D: Tailscale + instalação no celular (PWA em HTTPS)

Você é o implementador desta sessão. Trabalhe sozinho, sem pedir confirmação a cada passo.
Este documento é o contrato: o que fazer, em que ordem, como provar, quando parar, como registrar.

**Pasta:** `C:\Users\wakim\Projects\b3-opcoes` (ou worktree próprio, se houver sessão paralela) ·
**Runner:** `podman compose` · **App:** `127.0.0.1:${B3_APP_PORT}` · **Tailscale:** instalado no host.

> Esta sessão é de **exposição/instalação**, não de cálculo. Pode tocar `frontend/` (ícones/metas
> iOS), `backend/src/b3o/api/app.py` **só** no allowlist de Host do MCP e a documentação. **Não**
> toque em `metrics/`, `ingest/`, `store/`, `derive/`, `model_version`, SQL, journal, builder ou na
> ficha do Aprender.

---

## 0. Estado de partida (não redescubra)

- **F5-C fechado e na `main`** (últimos commits: `6c12568` e `cce1ef5`; antes `f89d9ca`, `f36bb17`).
  Entregues: builder de estrutura inline, PWA offline (`vite-plugin-pwa@2`, precache do shell,
  `NetworkFirst` em `/api`, sonda `NetworkOnly`), busca de papel, cobertura campo↔glossário
  (`npm run coverage` = 15 campos/44 no mapa/31 verbetes), carimbo derivado de `age_sessions`.
- **UI da Ticker (ajustes F5-C+)**: gráfico de termo com eixo x por tempo, faixa “sem IV”, tooltips
  (hover/toque/teclado) nos três gráficos, tabela de **6 vencimentos com IV** + 22 sem IV em
  `<details>`, seletor `‹ / select / ›`, gráficos em largura total. Nada disso deve regredir.
- **Lake:** última sessão **2026-11-23**; `model_version 2`. Hoje mostra 289/157/132; os números de
  19/11 (163/126/116) só com `?date=2026-11-19` na API.
- **Portas:** 8080 = `realestate-api-1` (outro projeto), 8081 = `b3-opcoes-app-1`, 8082 =
  `f5-b-app-1`. Use **8083** (ou outra livre) e não pare container alheio.
- **Tailscale (medido em 23/11/2026, não redescubra):** `tailscale 1.104.7`, host
  **`desktop-vlp5tqv`**, tailnet **`tail5745cb.ts.net`**, conta `wakim.jraige@`.
  **Já existe um Funnel ligado** em `https://desktop-vlp5tqv.tail5745cb.ts.net/` →
  `http://127.0.0.1:8080` (o projeto `realestate`). **Não sobrescreva esse handler sem o dono
  autorizar** (motivo 4 do §8).
- **PWA já pronto:** manifest “Opções B3”, `standalone`, `start_url: "/"`, `#101418`, ícones 192/512
  (`frontend/public/icons/`), `registerType: autoUpdate`, SW registrado em `main.tsx`, offline real
  provado (shell + banner + carimbo) no F5-C. Falta o **ícone 180 para iOS** e a instalação física.
- **MCP:** `TransportSecuritySettings.allowed_hosts` em `backend/src/b3o/api/app.py` lista só
  `127.0.0.1`, `localhost` e `host.containers.internal` (com `:*`). Um Host `*.ts.net` devolve
  **421** no `/mcp`.

---

## 1. Objetivo

Abrir o painel no **celular** por HTTPS no tailnet, instalar como PWA (standalone) e provar o ciclo
online → offline → online **no aparelho**. É o F5.9; não é redesenho de tela.

---

## 2. Exposição (decidir antes de mexer)

1. **Preferência: `tailscale serve` no tailnet, nunca `funnel` para o b3o.** O app não tem
   autenticação de leitura (o `APP_TOKEN` cobre escritas, que ainda não existem); Funnel é público.
2. O Funnel atual ocupa a **443/`/`** e aponta para o `realestate` na 8080. Tente um **HTTPS em
   porta separada** (8443), sem tocar na 443:
   ```powershell
   # app no ar em 8083
   tailscale serve --bg --https=8443 http://127.0.0.1:8083
   tailscale serve status
   ```
   A URL fica `https://desktop-vlp5tqv.tail5745cb.ts.net:8443` (HTTPS válido, contexto seguro).
3. **Se a 8443 não for aceita** ou o Serve não coexistir com o Funnel: **PARE e pergunte** (motivo
   4). As opções são (a) trocar o Funnel 443 para o b3o e realocar o `realestate`, (b) usar outra
   máquina/tailnet, (c) manter only-tailnet na 443 desligando o Funnel — nenhuma é default.
4. **Não** use `tailscale funnel --https=443 http://127.0.0.1:8083` sem autorização explícita.
5. Confirme que o celular está no mesmo tailnet (app Tailscale ligado). Se a conta/login do celular
   não existir, isso é decisão do dono (motivo 1).

## 3. Host do MCP

- Adicione o hostname do tailnet ao allowlist, de preferência por **env** (sem hardcode):
  `B3_MCP_ALLOWED_HOSTS=desktop-vlp5tqv.tail5745cb.ts.net,desktop-vlp5tqv.tail5745cb.ts.net:*`
  (mantenha os atuais). O `QueryService` não muda; é só superfície de transporte.
- Rebuild da imagem e `up -d app`.
- Prove: `mcp_smoke.py` via `https://desktop-vlp5tqv.tail5745cb.ts.net:8443/mcp` de um cliente no
  tailnet (ou do próprio host com `B3_MCP_URL`); um Host não listado continua 421.
- Se o agente não for usar o MCP pelo tailnet nesta sessão, registre a mudança e o teste mesmo assim
  (o contrato do F5 diz que o host `*.ts.net` entra aqui).

## 4. iOS (o que falta no PWA)

- Gere/adicione `frontend/public/icons/apple-touch-icon.png` **180×180** (mesmo desenho grafite +
  agulha; reaproveite o gerador `frontend/scripts/make-icons.mjs`, sem gradiente/glow).
- Em `frontend/index.html`, adicione:
  - `<link rel="apple-touch-icon" href="/icons/apple-touch-icon.png" />`
  - `<meta name="apple-mobile-web-app-capable" content="yes" />`
  - `<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />`
  - `<meta name="apple-mobile-web-app-title" content="Opções B3" />`
- O `manifest` já tem 192/512, `display: standalone`, `start_url` e `theme_color`; não mude o nome.
- `npm run build` no container, rebuild da imagem do app. Duas builds com hashes idênticos.

## 5. Instalação e teste no aparelho

1. No celular, com Tailscale ligado, abra `https://desktop-vlp5tqv.tail5745cb.ts.net:8443`.
2. **iPhone (Safari):** Compartilhar → “Adicionar à Tela de Início”; abrir pelo ícone; conferir
   standalone (sem barra do Safari), ícone e carimbo `sessão 2026-11-23 · D-1`.
3. **Android (Chrome):** menu → “Instalar app”/“Adicionar à tela inicial”; abrir pelo ícone.
4. **Offline real:** com o app aberto e carregado, ative o modo avião (ou desligue Wi-Fi/dados),
   feche e reabra pelo ícone. Exigir: shell servido pelo SW, banner “sem rede — última sessão
   2026-11-23”, carimbo “offline” **sem** `D-n`, e a tabela/gráficos em cache quando possível.
   Religar a rede e recarregar: banner some, carimbo volta a `D-1`, zero erro de console.
5. **Atualização:** se o ícone abrir a versão antiga, feche/abra ou limpe os dados do site; conferir
   que o SW ativo é o build novo (`index-*.js` atual). Não “conserte” isso com cache-busting manual.
6. **Segurança:** confirme que o endereço só responde no tailnet (desligue o Tailscale do celular e
   veja que não abre; Funnel não deve estar servindo o b3o).

> O agente não tem o celular. Prepare a URL, prove no host/outro nó do tailnet e **peça ao dono** a
> instalação e as capturas. Sem o aparelho, o critério “instalado no celular” fica pendente — não
> marque como provado.

## 6. Regressão que não pode quebrar

- `npm run lint`, `npm run typecheck`, duas builds idênticas; `npm run coverage` verde.
- `mcp_smoke.py` **53/53** e `structure_smoke.py` **93/93** (via HTTP local e, se possível, via
  HTTPS do tailnet); `model_version 2`.
- Hoje 289/157/132 e aviso 113/157; Ticker com 7 métricas; gráficos em largura total, faixa “sem IV”,
  tooltips, tabela 6+22 e seletor `‹ / select / ›`; carimbo derivado de `age_sessions`.
- Nenhum arquivo em `data/` alterado por GET; nenhum container alheio parado; 8080/8081/8082
  intactos.
- `GET /api/health` responde no HTTPS do tailnet com o mesmo JSON do local.

## 7. Como provar (saída colada)

1. `tailscale status`, `tailscale serve status` e `tailscale funnel status` (mostrando que o Funnel
   8080 não foi tocado, se a opção 8443 for usada).
2. `curl -I https://desktop-vlp5tqv.tail5745cb.ts.net:8443` e `curl -s .../api/health`.
3. `mcp_smoke.py` pelo URL do tailnet (allowlist) — 53/53.
4. Capturas do celular: tela inicial com o ícone, app aberto em standalone com o carimbo, banner
   offline e o retorno online.
5. `npm run lint`/`typecheck`/`build` (hashes iguais), `npm run coverage`, `data/` sem diff.
6. Zero erros de console no ciclo online→offline→online; o único aceitável é o `Failed to load
   resource` das chamadas que o SW tenta enquanto o servidor/rede está fora.

## 8. Só quatro motivos para parar e perguntar

1. dinheiro, conta, credencial ou login do Tailscale/celular;
2. critério que falha e cuja correção muda a spec (fórmula, convenção, schema publicado);
3. dado que não existe na fonte depois de todos os fallbacks;
4. decisão que apaga ou reescreve trabalho do dono — **inclui substituir o Funnel atual da 443**
   que aponta para o `realestate`.

Qualquer outro caso: default mais simples e reversível, ADR curto se couber, siga.

## 9. Ao terminar

- `docs/relatorios/2026-11-24-revisao-f5d.md`: veredito, URL/porta usada, o que ficou no Funnel,
  provas do celular (com as capturas), offline, MCP e regressão.
- `PLAN.md`: marcar **F5.9** e a aceitação “instalado no celular pelo domínio `*.ts.net` (HTTPS)”
  **somente** se o dono confirmar a instalação; F5-D inteiro. Não marcar F6.
- `AGENTS.md`: só armadilhas novas (Funnel 8080 × b3o, allowlist `*.ts.net`, ícone 180 do iOS,
  Serve × Funnel, SW preso na versão antiga).
- `README.md`: o comando `tailscale serve` da porta escolhida e o passo de instalação no celular.
- Commit por pacote verde + `git push` (após `pull --rebase`, na `main`).
- Mensagem final curta: o que foi entregue, a URL, as fotos, o que ficou pendente (se o celular não
  estava disponível) e o próximo passo (F6 — posições/carteira).
