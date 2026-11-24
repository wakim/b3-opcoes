# Backlog

Ideias fora da v1. Nada aqui é compromisso; a ordem não é prioridade.

## Dados

- **Opções de índice e ETF** (IBOV, BOVA11). Índice é europeu e liquidado em dinheiro — exige ramo separado de precificação, não é só mais uma linha.
- **Open interest histórico** além dos 10 dias gratuitos (verificar se algum arquivo regulatório retroativo resolve).
- **Boletim BVBG.086** como fonte diária menor que o COTAHIST anual (101 MB/dia hoje).
- **Data de resultado anunciada** (IPE da CVM, PDF não estruturado) para substituir a estimativa `estimated` da v1.
- **brapi Pro** (R$ 1.399,90/ano) como referência contínua de IV e gregas — só se a validação gratuita do F1 não bastar.
- **Preço vivo do subjacente** (`instrumentQuotation`) como contexto — removido da v1: sem timestamp, sem contrato, valor marginal para decisão EOD.
- **Intraday de 1 minuto** (`DailyFluctuationHistory`) ingerido no batch, para medir decaimento de prêmio e o fechamento atípico.
- **"VIX brasileiro"** a partir das opções de IBOV (ATM, 30d) — projeto dentro do projeto.

## Produto

- **Rolagem de posição** (fecha e abre em um comando, preservando o histórico).
- **Exercício e atribuição**: marcar posição exercida no vencimento.
- **Alertas no celular** (Web Push) quando uma posição bate flag.
- **Comparação entre papéis**: IV rank relativo, dispersão implícita.
- **Wheel** (put caixa → covered call) como estratégia guiada.
- **Exportar** o briefing do dia em PDF/Markdown.
- **Modo offline completo**: analisar o dia anterior sem rede, com todo o detalhe (não só o shell).

## Agente

- **Segundo agente refutador** com as mesmas tools, instruído a derrubar a tese. Substitui o `counter_evidence` determinístico removido na spec v0.4.
- **Briefing como recurso MCP** (`b3://briefing/{date}`) para leitura única.
- **Avaliação contínua**: comparar as respostas do agente com o desfecho e ajustar o prompt.

## Quant (removido da v1 na spec 0.4)

- **Densidade neutra ao risco e PoP** — segunda derivada numa grade esparsa é ruído; só voltar com ajuste paramétrico do smile (SVI) e grade densa.
- **Prêmio de exercício antecipado (BAW)** — relevante só para calls americanas com ex-dividendo, que já ficam fora do smile por flag.
- **`historical_analog` com condição livre** — exige uma linguagem de condição; a v1 tem `signal_backtest` com as classes fixas.

## Infra

- **Deploy público** atrás de Caddy/HTTPS, com Basic auth (padrão do projeto Imoveis).
- **Backup** do volume PostgreSQL `b3o-postgres` e do `ingest_state`.
- **Métricas de uso**: quantas telas abertas, quantas previsões avaliadas.

## Ideias que eu **não** faria (registradas para não voltarem)

- **Max pain** — depende de OI por strike, e o OI daqui tem 10 dias e é fino no Brasil. Vira folclore com cara de número.
- **GEX / posicionamento de dealer** — não sabemos quem está do outro lado; interpretação seria especulação.
- **Margem calculada pela B3** — não está no dado, e aproximação errada é pior que ausência.
- **Otimizar thresholds do funil** antes de 30 dias de diário — seria ajustar ruído.
