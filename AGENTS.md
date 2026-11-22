# AGENTS.md — Opções B3

Guia para quem (humano ou agente) mexer neste projeto. Leia antes de escrever código.

---

## O que é

Ferramenta pessoal, de um usuário só, para apoiar decisão em opções de ações da B3.
Fonte: arquivos públicos da B3 (EOD). Interface: MCP (agente) + PWA (humano).

## Regras que não se quebram

1. **O Python calcula, o agente interpreta, o humano decide.** Nenhum número pode nascer de um LLM.
2. **EOD é o dado.** Não há preço vivo na v1; se um dia houver, nunca entra em IV, grega, forward ou estrutura.
3. **Uma camada de consulta.** MCP (montado em `/mcp`) e HTTP chamam o mesmo `QueryService`, no mesmo processo. Query SQL fora dela é bug.
4. **Nenhuma métrica sem verbete** no `docs/glossario.md`, e o teste de cobertura tem que passar.
5. **Falha explícita.** Erro com `code/message/hint`; indisponível vira `null` + flag; nunca zero silencioso.
6. **`model_version` sobe a cada mudança de fórmula.** O histórico é recalculado inteiro; o rank não pode misturar modelos.

## Ordem de leitura

`PLAN.md` (o que fazer) → `docs/spec.md` (como) → este arquivo (o que já deu errado) → `docs/decisoes.md` (o que foi decidido sem o dono e quando revisar).

---

## Armadilhas do dado (todas verificadas no arquivo de 2026)

Estas custaram uma sessão de investigação cada. Medições do COTAHIST 2026 (até 21/11). Não redescubra.

### 1. O ISIN não identifica a série
Nas linhas de opção, o campo `isin` é o **ISIN do ativo-objeto** (`BRPETRACNPR6` para todas as opções de PETR4). A chave da série é `(ticker, maturity)`.
*Medido:* 220.282 tickers de opção; 3.060.453 linhas.

### 2. O strike muda no meio da vida, no mesmo ticker
A B3 ajusta o strike quando há provento. 51.830 tickers (23,5%) têm mais de um strike em 2026; dos que têm dois valores, **98% descendentes** (`ABEVX131`: 12,04 → 12,00 → 11,94) e **698 ascendentes** (não é dividendo — é ajuste de escala).
Consequências:
- o strike é atributo **por data**, nunca da série;
- nenhuma métrica (IV, retorno, paridade) atravessa a data do ajuste;
- um desdobramento derruba o strike (SBSP em 29/04/2026: ×0,2 em 106 séries) — tratar `Δstrike < 0` como dividendo o registraria como provento de 80%;
- **não existe limiar de razão** que separe provento de escala: provento de R$ 2,86 (LOGG) dá razão 0,77 num strike baixo, e há dividendo com razão de 0,51 numa série. Uma bonificação de 10% (0,909) cairia no meio dos proventos;
- a assinatura está **entre strikes do mesmo papel e data**: provento = **Δ em R$ igual** em todos os strikes (±1 centavo); escala = **razão igual** em todos (±0,1%). Provento pequeno cabe nos dois → provento (spec §6.6);
- ascendente (`ρ > 1`) é grupamento: SIMH ×2 em 18/02/2026, AZUL ×75 em 27/03/2026.

### 3. A data em que o strike muda no arquivo não é a data ex
O COTAHIST só lista série que negociou. **70%** das mudanças de strike em 2026 (54.617 de 77.002) aparecem depois de uma lacuna: a série ficou dias sem negócio e pode ter acumulado mais de um ajuste. Só mudança **contígua** (a série negociou no pregão anterior) data e classifica o evento; a data ex é a primeira mudança contígua do papel.

### 4. O arquivo anual não está ordenado por data
Ele é ordenado **por instrumento**. Em 2026 há 822.616 "quedas" de data; o primeiro salto é de 12/02 para 02/01 na linha 590.
Consequência: **nunca** usar "parei no primeiro registro antigo" como otimização. Filtra-se por data, lendo tudo.

### 5. O campo de ticker vem preenchido com espaço à direita — `strip()` sempre
Correção medida (22/11/2026): nas 3.570.796 linhas do arquivo de 2026 **não existe um só ticker com espaço à esquerda** (`l[12:24]` é left-aligned com padding à direita: `"PETRK410    "`). O `strip()` continua **obrigatório** (espaço à direita + pode ser diferente em arquivos antigos — o campo de 12 posições é texto), mas a forma left-aligned que a nota original descrevia não se reproduz neste arquivo. `"PETR4       ".strip()` antes de comparar, sempre.

### 6. O ticker NÃO carrega o estilo — a regra do token tem quatro casos medidos
O estilo vem do 1º token de `l[27:39]`, mas a relação token × ticker é mais sutil que "PETRE vs PETR" (medido em 22/11/2026 sobre as 3.060.453 linhas de opção):
- **puts europeias têm ticker com raiz comum** (`PETRV352` com token `PETRE`): olhar só o ticker classificaria errado;
- **semanais**: `ABEVA140W1` = raiz + mês + strike + `W\d+`; o marcador `W\d+` só existe **depois de dígito** — `W` antes de dígitos é código de mês de put (`BOVAW170` é put de novembro, não semanal);
- **BRAVA**: o ticker usa raiz `BRAV` (`BRAVD286`), mas o token é `BRAVA`/`BRAVAE` (raiz +1/+2) — daí a "exceção de raiz de 5 letras";
- **B3SA**: a raiz tem dígito no meio (`B3SAD557` → raiz `B3SA`) — extração "só letras" quebra.

Regra exata (validada: puts 1.508.144 E e **zero** A; calls 818.364 A / 729.416 E; `null`+`style_unknown` em 0,15%, só trimestrais de IBOV, fora do universo de ações):
`raiz = ticker sem W\d+$ (só se precedido de dígito), sem dígitos finais, sem o último caractere (código de mês)`; `token = 1º token de l[27:39]`; **se `token.startswith(raiz)` e `len(token) ≤ len(raiz)+2`** → `E` se o token termina em `E`, senão `A`; senão `null`.
Séries com tokens conflitantes entre datas existem (909 raízes, quase só BOVA/índice): o estilo é **por linha** no parse; a série consolida no `legs` com flag de conflito.

### 7. O arquivo de 2026 tem UMA linha de 246 bytes (UTF-8 dentro do latin-1)
Linha 2.087.780 do `COTAHIST_A2026.TXT` (medido 22/11/2026): `FR0T5L`, mercado `017`, `corp_name` = `FROTA OCEÂNICA` com `Â` codificado em **UTF-8** (`\xc3\x82`) num arquivo latin-1 — 1 byte a mais e **todos os offsets a partir de `[27:39]` deslocados**. A linha é impossível de parsear por offset fixo; nem precisa ser (mercado 017 é filtrado). Consequência: validação que trate linha torta como fatal reprova o arquivo inteiro para sempre — linha torta se **pula e se conta** (ADR-007), e as contagens vão para o `health`. Teste de regressão com os bytes reais da linha existe em `tests/test_validate.py`.

---

## Outros fatos úteis

- Preços no arquivo têm 2 decimais implícitos: `000000000201` = R$ 2,01. Internamente, **centavos inteiros** (`int64`); volume passa de R$ 21 M/dia e estoura `int32`.
- Mercados: `010` à vista, `020` fracionário, `070` call, `080` put, `012/013` exercício, `017` leilão, `021` lote/bloco. O parser só guarda os seis primeiros.
- O arquivo anual do ano corrente é atualizado diariamente e vem como **D-1**. `ETag`/`If-Modified-Since` devolvem 304 — usar sempre.
- **A URL de download do COTAHIST mudou (medido 22/11/2026):** o padrão antigo `arquivos.b3.com.br/dados/pregao/cotahist/…ZIP` está **morto** (404; as páginas de dados públicos e cotações foram desativadas em 31/03/2026, Comunicado Externo 01/2026-VTEC). A URL viva é `https://bvmf.bmfbovespa.com.br/InstDados/SerHist/COTAHIST_A{AAAA}.ZIP` e **exige User-Agent de browser** — sem UA responde 520/404. `ETag` (formato IIS `"hash:0"`) e `Last-Modified` presentes: GET condicional (304) funciona.
- **Offsets do layout, validados programaticamente** (16.134 linhas de 19/11/2026: zero violações de `open ≤ high`, `low ≤ open/close`, `avg ∈ [low, high]`): `date[2:10]`, `bdi[10:12]`, `ticker[12:24]`, `market[24:27]`, `corp_name[27:39]`, `spec[39:49]`, `open[56:69]`, `high[69:82]`, `low[82:95]`, `avg[95:108]`, `close[108:121]`, `bid[121:134]`, `ask[134:147]`, `trades[147:152]`, `qty[152:170]`, `volume[170:188]`, `strike[188:201]`, `strike_adj_ind[201:202]`, `maturity[202:210]`, `quote_factor[210:217]`, `strike_pts[217:230]`, `isin[230:242]`, `dist_id[242:245]`. A moeda `R$` mora em `[52:54]`; `[49:52]` é lixo variável (espaço no à vista, `0` em opções) — não usar. Trailer `99`: total de registros em `[32:42]`.
- **`volume` também segue o `FATCOT`** e é o financeiro exato: `volume ≈ qty × avg ÷ FATCOT` (nunca `qty × avg` direto, e **nunca exata**: `avg` é truncado em 2 decimais — 62% das linhas de 2026 diferem < 1¢/unidade; 5 linhas diferem 1¢). As 106 linhas de opção de 19/11 com diferença grande são IBOV com `FATCOT=100`. `volume` é a fonte da verdade; não se reconstitui.
- **`strike_adj_ind` (`l[201:202]`) é SEMPRE `'0'` em 2026** (3.570.794 linhas, zero exceções — medido 22/11). O "Validar contra `strike_adj_ind`" da spec §6.6 não tem o que validar neste arquivo: fronteira de leg é só por `strike` e `distribution_id`.
- BDI nas linhas de opção: call=`78`, put=`82`; à vista lote padrão=`02`. O lado (C/P) vem do mercado (`070`/`080`), não do BDI.
- **Open interest:** o dado **por instrumento** está no portal **BDI** (`https://arquivos.b3.com.br/bdi/tabelas`) como tabela `OpenPositionsEquities` ("Posições em aberto", derivativos de renda variável; colunas incluem `TckrSymb`, `ISIN`, `Asst`, `XprtnCd`, `SgmtNm`, `OpnIntrst`, `CvrdQty`, `UcvrdQty`, `TtlPos`, `FwdPric`). A `AnalyticalFramework2` ("Quadro analítico") é AGREGADA (68 categorias), não serve para oi por série. Chamada medida: `POST https://arquivos.b3.com.br/bdi/table/export/csv` com corpo `{"Name":"OpenPositionsEquities","Date":"YYYY-MM-DD","FinalDate":"YYYY-MM-DD","ClientId":"","Filters":null}` → CSV latin-1 com `;` (2 linhas de descrição + glossário + vazia + header); data sem dado = linha `Nenhum resultado`; **janela de ~10–12 sessões** (medido: 19/11 e 11/10 OK, 02/11 vazio). Páginas públicas antigas desativadas 31/03/2026 (Comunicado 01/2026-VTEC).
- **Curva DI:** o portal novo é `https://sistemaswebb3-derivativos.b3.com.br/referenceRatesProxy/` — `GET Search/GetProducts/{b64}`, `GET Search/GetDate/{b64}` (datas disponíveis) e `GET Search/GetDownloadFile/{b64}` (payload `{"language":"pt-br","date":"YYYY-MM-DD","id":"PRE"}`; resposta = **CSV em base64**, `;`, vírgula decimal). Produtos: `PRE` = DI x pré (primária), `APR` = ajustes DI1 (fallback), `SLP` = Selic x pré. **Histórico automático limitado a ~20 sessões** — para backfill completo, BCB SGS série 11 (Selic % a.d., `api.bcb.gov.br/dados/serie/bcdata.sgs.11/dados?formato=json&dataInicial=&dataFinal=`), anualizada `(1+t/100)**252−1`.
- **Calendário de resultados — campos exatos da CVM (confirmados 22/11/2026; a spec §6.14 deixava "a confirmar"):** índice ITR/DFP (`itr_cia_aberta_{AAAA}.zip` em `dados.cvm.gov.br/dados/CIA_ABERTA/DOC/ITR/DADOS/`, idem DFP): `CNPJ_CIA, DT_REFER, VERSAO, DENOM_CIA, CD_CVM, CATEG_DOC, ID_DOC, DT_RECEB, LINK_DOC` — **`actual` = `DT_RECEB` da `VERSAO == 1`** por `(CD_CVM, DT_REFER)`. Mapeamento ticker↔empresa: membro `fca_cia_aberta_valor_mobiliario_{AAAA}.csv` do `…/DOC/FCA/DADOS/fca_cia_aberta_{AAAA}.zip`: `Codigo_Negociacao` → `CNPJ_Companhia` (filtrar `Mercado == "Bolsa"` e `Data_Fim_Negociacao` vazio) → join por `CNPJ_CIA`.
- Endpoint de preço (não usar em métrica): `cotacao.b3.com.br/mds/api/v1/instrumentQuotation/<TICKER>`, uma série por chamada, sem bid/ask e **sem timestamp**, HTTP 200 com `BizSts.cd = "NOK"` para desconhecido.
- **O estilo da opção está no COTAHIST, escondido no nome resumido** (`l[27:39]`): o 1º token é a raiz do ticker + `E` para europeia (`PETRE`, `BOVAE`) e a raiz pura para americana (`PETR`). Medido em 2026: **todas as puts são europeias** (110.993 séries, nenhuma americana); calls têm 74.576 séries americanas (66%) e 38.564 europeias (34%) — 55%/45% em número de negócios —, e o mesmo papel tem os dois estilos. Validação: 24% das puts ITM negociaram mais de 1% abaixo do intrínseco, o que é impossível para americana. Exceção: `BRAVA`/`BRAVAE` (raiz de 5 letras). Sufixos como `/EJ` e `/AT` depois do token são marcas de ex-direito, não estilo.
- `FATCOT` (`l[210:217]`) não é sempre 1: 42.250 linhas de opção com 100, e algumas com 10.000 e 1.000.000. Preço por ação = preço ÷ fator.
- O COTAHIST anual do ano corrente vem inteiro todo dia: reconstrói-se a partição do ano, sem append.
- **Não há OpLab.** Referência externa gratuita de IV: sandbox da brapi sem token, **só PETR4**, 20 req/min; convenções diferentes (dias corridos/365, dividendo anunciado, BSM em europeias, binomial em americanas). A validação principal é interna: IV de call europeia = IV de put europeia no mesmo strike.
- **Calendário de resultados vem da CVM**, não de digitação: data de entrega da 1ª versão do ITR/DFP = data do resultado; o futuro é estimado e marcado `estimated`.
- Open interest não está no COTAHIST: vem do arquivo regulatório de posições em aberto, com 10 dias de histórico. É `null` quando falta.

---

## Convenções de código

- Python 3.12, `uv`, layout `src/b3o/`.
- **Tudo roda em container** (`compose.yaml`, spec §4.1). Nenhum comando oficial usa `uv`/Python/Node do host: `podman compose run --rm test pytest`, `podman compose run --rm prepare`. Se um comando só funciona no host, ele está errado.
- Runner: `podman compose` (o host não tem Docker). O `compose.yaml` é compose padrão — nunca usar extensão exclusiva do Podman, para que `docker compose` funcione em outra máquina.
- `uv.lock` é versionado e a imagem instala com `uv sync --frozen`: sem lock atualizado a build quebra de propósito.
- `.venv` e `node_modules` ficam em **volumes nomeados**, nunca no bind mount: no Windows o bind não preserva modo e é lento (medir antes de reclamar).
- Mudou dependência → `uv lock` **dentro do container** e commit do `uv.lock`; nunca `pip install` avulso na imagem.
- Nomes de campo em inglês, com unidade no nome: `iv_pct`, `spread_brl`, `theta_brl`, `theta_pct`.
- Tempo: `T` em **dias úteis / 252**; vencimento expira no fechamento; Θ por dia útil.
- Datas em ISO com offset nas bordas; `date` internamente.
- Nada de arredondar no cálculo — só na serialização.
- Funções puras para fórmula (recebem número, devolvem número); I/O separado.
- **Toda fórmula e o parser têm teste** (`podman compose run --rm test pytest`). Validação manual complementa, não substitui.

## Frontend

- **Vite + React + TypeScript + TanStack Router (file-based) + TanStack Query**, SPA pura, `web/` na raiz. Sem TanStack Start nem qualquer framework com servidor.
- Rotas em `web/src/routes/`, um arquivo por tela: `index` (Hoje), `carteira`, `diario`, `aprender`, `t.$ticker`, `s.$ticker.$maturity`.
- Dados via **TanStack Query** sobre `/api`; nada de `fetch` solto em componente.
- O backend é um só (FastAPI). Se o frontend ganhar lógica de servidor, a regra da `QueryService` única morre — por isso a stack não tem servidor.
- Tipos da API em **um** arquivo de contrato (`web/src/lib/api.ts`), gerado ou escrito à mão a partir de `/api/openapi.json`. Divergência entre tipo e resposta é bug de contrato.
- Gráficos em **SVG inline** (os mesmos do mockup). Não adicionar biblioteca de gráfico.
- Build oficial **sempre no container** (`node:22-slim`, serviço `web` do compose). No hospedeiro não há Node. Para `npm run dev`, usar o serviço `web-dev` com `CHOKIDAR_USEPOLLING=1` (vigilância de arquivo entre volumes no Windows é lenta).
- `npm run lint` + `tsc --noEmit` antes de considerar uma tarefa pronta.

### Tokens de design

**A direção aprovada está em `docs/direcao.md`** — paleta, tipografia, espaço, raio, movimento, regras e o que é proibido. É o contrato visual do app: componente que não segue aquilo está errado, não "com outro gosto".

Resumo: grafite frio, **zero matiz fora do gráfico**, acento = branco-gelo como única marca clara (a agulha), números mono tabulares em coluna, rótulos em caixa de frase, raio 4–6, sem pill. A assinatura é **a agulha na régua**, usada em toda métrica posicional.

Os mockups em `docs/` são referência de **estrutura e conteúdo** — o que existe em cada tela —, não alvo visual. O visual sai do **unslop**: os 9 passes são aplicados durante a construção, e o mockup não é redesenhado.

A skill está instalada globalmente (`~/.config/opencode/skills/unslop`, CC BY 4.0 — manter o crédito ao autor ao citá-la ou adaptá-la). Ela é escrita para desktop; layout e visuais precisam de adaptação ao mobile.

## Ambiente (containers)

Medido nesta máquina em 22/11/2026 — não redescubra:

- **Não há `docker` no PATH.** `docker-compose.exe` existe como shim em `WindowsApps`; `podman compose` o usa como provider externo (Docker Compose v5.8) e funciona. Comandos: `podman compose run --rm test pytest`, `podman compose build`, `podman compose up -d app`.
- Podman 6.1.4, machine Hyper-V, **8 vCPU e 3,7 GiB de RAM**, disco 100 GiB. Ingestão streaming é obrigatória; medir RSS no `health`.
- Bind mount `C:\…` → container funciona **nos dois sentidos** (testado), mas os arquivos aparecem como `nobody:nobody`, modo `666`. Consequência: nada que dependa de permissão (chave SSH, executável com bit `+x`, `.venv`, `node_modules`) pode viver no bind.
- `./data:/data` é o único bind mount de dado. Lake, `journal.db` e `raw/` ficam lá; o container roda como root, então arquivo criado no container fica legível no host.
- Imagem base `python:3.12-slim` com `uv` copiado de `ghcr.io/astral-sh/uv`. `podman` baixa de `docker.io` por padrão — sem prefixo de registry, `node:22-slim` resolve.
- **`uv lock` falha dentro do bind mount:** `failed to rename file … Input/output error (os error 5)`. O `os.replace` comum funciona (testado 20×, inclusive entre diretórios), então escrita atômica de Parquet em `/data` está segura; o problema é só o `uv`. Workaround: `cp -r` do projeto para `/tmp`, `uv lock` lá, copiar `uv.lock` de volta.
- `typer` 0.29 colapsa app com **um** comando só em comando único — `b3o health` vira "unexpected extra argument(s)". Sempre declare `@app.callback()` na raiz (ou tenha ≥ 2 comandos). `typer` 0.29 não instala `click`: `import click` quebra, use `typer._click` só se não houver alternativa.
- `podman compose …` imprime um aviso do provider (`>>>> Executing external compose provider …`) em stderr: **não é erro**. `config`, `profiles`, `env_file.required: false`, âncoras `x-*`/`<<:` e `healthcheck` foram validados no provider v5.8.
- `./data` precisa existir antes do `compose up` (o Podman não cria pasta de bind mount) — é o papel do `data/.gitkeep`.
- `TZ=America/Sao_Paulo` no container. A data de referência **nunca** vem do relógio: vem do arquivo.
- BuildKit `RUN --mount=type=cache` funciona no Podman, mas não é necessário: a imagem tem que construir do zero em < 5 min sem cache (critério do F0).

## Armazenamento

- Parquet + DuckDB, particionado por `year`, escrita atômica (`tmp/` → `os.replace`).
- `journal.db` (SQLite, append-only) é a **única** escrita do sistema, e só via HTTP.
- O lake é imutável: o MCP abre `read_only=True`.

## Ao terminar uma tarefa

- Rode o critério de aceitação da tarefa no `PLAN.md` — é ele que autoriza marcar `[x]`.
- Se aprendeu algo sobre o dado, **adicione aqui**. Uma armadilha não documentada vai ser redescoberta.
