# Glossário — as métricas do painel

Este arquivo é o **conteúdo** que a aba "Aprender" e as fichas de cada métrica exibem no site. É escrito para quem entende o mercado mas está começando nas métricas de opções.

Cada entrada tem três partes:

- **O que é** — a definição, sem jargão desnecessário.
- **Erro comum** — o que quase todo mundo interpreta errado. É a parte mais importante.
- **No painel** — o número real de um caso, para ancorar a abstração.

## Como usar

As métricas estão em **três níveis**. Não tente aprender os três de uma vez: no nível 1 você já consegue ler a tela inteira e decidir se vale olhar. O nível 2 é o que transforma número em contexto. O nível 3 é para quando você já estiver operando e quiser entender por que a estrutura se move.

No primeiro acesso, o site mostra só o nível 1. Os outros dois aparecem quando você pedir.

---

# Nível 1 — o essencial

## Preço (spot / fechamento)

**O que é** — o último preço negociado da ação no pregão. No painel é sempre o fechamento de D-1, nunca o preço de agora.

**Erro comum** — tratar o fechamento como "o preço". Em papel ilíquido, o fechamento pode ser de um negócio isolado de horas antes; o preço "justo" está entre bid e ask. E, como todo o cálculo é EOD, o número envelhece: na abertura do dia seguinte ele já está errado.

**No painel** — `PETR4 41,18 · −1,04%` é o fechamento de 19/11. A tela diz `sessão 19/11 · D-1` justamente para você não confundir com hoje.

## Prêmio, bid e ask

**O que é** — o prêmio é quanto custa a opção. `bid` é o que o mercado paga por ela; `ask` é o que o mercado pede. O **mid** é a média dos dois.

**Erro comum** — comparar o prêmio de duas opções sem olhar o vencimento. Uma put de 40 para daqui a um mês e outra para daqui a seis meses têm o mesmo strike e preços completamente diferentes — o que muda é o tempo.

**No painel** — `PETRZ400 mid 1,42 · bid 1,40 · ask 1,44`.

## Spread

**O que é** — a distância entre bid e ask. É o custo implícito de entrar e sair: você compra pelo ask e vende pelo bid.

**Erro comum** — olhar só a porcentagem. Em prêmio de R$ 0,13, um spread de 15% é R$ 0,02 — e isso é irrelevante. Em prêmio de R$ 3,00, 15% é R$ 0,45, que come metade de um movimento. **O % esconde; o R$ mostra.** Por isso o painel exibe os dois.

**No painel** — `PETRZ400 spread R$ 0,04 · 3,1%` é bom. `PETRM48 R$ 0,04 · 31%` é péssimo, apesar de o valor absoluto ser o mesmo.

## Strike e vencimento

**O que é** — strike é o preço pelo qual você pode comprar (call) ou vender (put) a ação. Vencimento é o último dia do contrato.

**Erro comum** — achar que o strike é fixo para sempre. Na B3, o strike é **ajustado quando a empresa paga provento**, e o mesmo código de negociação passa a ter outro strike no meio da vida. Em 2026 isso aconteceu em 23,5% dos tickers de opção.

**No painel** — `PETRZ400` tinha strike 40,04 em 13/11 e 40,00 em 14/11: R$ 0,04 de dividendo. É a mesma série, mas nenhuma métrica atravessa essa data. O strike também muda em desdobramento e grupamento — aí a mudança é proporcional (metade, o dobro) e não é provento.

## Volatilidade implícita (IV)

**O que é** — a volatilidade que o preço da opção está "pedindo". É a resposta à pergunta: *com que volatilidade o mercado estaria precificando esta opção, se usássemos um modelo padrão?* É expressa ao ano, em %.

**Erro comum** — achar que IV alta significa "vai subir". Não significa direção nenhuma: significa que o mercado espera **movimento**, para qualquer lado. IV alta = opção cara, e o contrário de "a ação está cara".

**No painel** — `IV 28,4` quer dizer que o mercado precifica ~28,4% de oscilação anualizada. Compare com a realizada (22,1) antes de concluir qualquer coisa.

## Expected move

**O que é** — quanto o mercado espera que a ação ande até o vencimento, em %. Sai direto do preço da trava ATM (call + put no mesmo strike), sem modelo.

**Erro comum** — ler como previsão. É uma **estimativa de dispersão**, não de direção, e o mercado erra com frequência — inclusive porque o número embute prêmio de risco.

**No painel** — `±7,0% até 18/dez` com spot 41,18 significa que o mercado precifica a ação entre ~38,3 e ~44,1 no vencimento, com ~68% de confiança (1 desvio).

## Delta

**O que é** — quanto o prêmio muda para cada R$ 1 de variação na ação. Também é a exposição equivalente: uma put com Δ −0,41 se move como 41% de uma ação vendida.

**Erro comum** — usar delta como probabilidade de exercício. **Delta não é probabilidade.** Numa put OTM, a probabilidade real de virar dinheiro é menor que o |delta|, e a diferença cresce quando o skew está carregado. Delta também não serve como substituto de probabilidade — o painel não exibe probabilidade de exercício na v1.

**No painel** — `Δ −0,41`: se a ação cair R$ 1,00, o prêmio sobe ~R$ 0,41 (na primeira ordem).

---

# Nível 2 — o que dá contexto

## IV ATM 30d

**O que é** — a IV da opção no dinheiro, padronizada para 30 dias. É o "termômetro" do papel, comparável entre semanas e entre papéis diferentes.

**Erro comum** — comparar IV de vencimentos diferentes. A IV do vencimento mais próximo sobe e desce com o calendário, criando serra dente de serra. Por isso o painel **interpola** para 30 dias em vez de pegar o vencimento mais próximo.

**No painel** — `IV ATM 30d 28,4` é comparável com a de ontem, com a de outro papel e com a sua própria média de 12 meses.

## IV rank e percentil

**O que é** — onde a IV de hoje está dentro da faixa dos últimos 12 meses. `p82` significa que a IV atual é maior que 82% dos dias do período.

**Erro comum** — tratar como sinal. **IV rank é contexto, não gatilho.** Se o papel mudou de regime (entrou num ciclo de resultados piores, mudou de controlador), o "p85" pode ser o novo normal. Por isso o gatilho do funil é o z-score do VRP, e o rank fica só como leitura.

**No painel** — `28,4 · p82 · 12m`: prêmio historicamente caro para este papel. Não diz se vai continuar caro.

## Volatilidade realizada (RV)

**O que é** — quanto a ação **de fato** oscilou, anualizada. É o passado medido, em contraste com a IV, que é expectativa.

**Erro comum** — calcular de fechamento a fechamento ignorando o gap de abertura. No Brasil o gap noturno é grande, e ele carrega a maior parte da informação. O painel usa Yang-Zhang, que separa overnight de intraday.

**No painel** — `RV20 22,1 · RV60 24,7`: o papel está mais calmo nas últimas 4 semanas do que nos últimos 3 meses.

## VRP

**O que é** — o prêmio de risco de volatilidade: `IV − RV`. Quanto o mercado cobra acima do que o papel costuma entregar.

**Erro comum** — comparar IV de 30 dias com RV de 20 ou de 5. Tenores diferentes produzem VRP fabricado. **O tenor tem que ser casado.**

**No painel** — `VRP +6,3`: o mercado cobra 6,3 pontos de vol acima do realizado. Historicamente, prêmio positivo persistente é a norma — vender vol "funciona" na média e quebra nas caudas.

## z-score do VRP

**O que é** — quantos desvios-padrão o VRP de hoje está da sua própria média de 252 sessões. Normaliza por regime: +1,9 significa "caro **para este papel**, no seu próprio histórico".

**Erro comum** — usar valor absoluto de VRP como comparação entre papéis. Um papel estruturalmente mais volátil tem VRP maior sempre. O z-score resolve isso.

**No painel** — `z +1,9` é o que coloca a PETR4 no topo do funil hoje.

## Skew e risk reversal

**O que é** — o smile não é simétrico: puts OTM costumam ser mais caras que calls OTM. O **risk reversal** mede isso: `IV(call 25Δ) − IV(put 25Δ)`.

**Erro comum** — chamar de "skew" o nível de IV. Skew é **inclinação**. RR negativo = puts mais caras (proteção demandada); RR positivo = calls mais caras (corrida por upside).

**No painel** — `RR 25Δ −4,2`: puts 4,2 pontos de vol mais caras que calls. RR negativo é o normal em ação brasileira.

## Butterfly

**O que é** — a **curvatura** do smile: `½·(IV25c + IV25p) − IV ATM`. Mede o quanto as asas estão levantadas em relação ao centro.

**Erro comum** — confundir com skew. RR e butterfly são ortogonais: um mede inclinação, o outro convexidade. Smile pode estar muito inclinado e pouco curvo, ou o contrário.

**No painel** — `BFLY +2,1`: as asas estão 2,1 pontos acima do centro — formato normal. Butterfly negativo costuma ser cotação inconsistente, não oportunidade.

## Estrutura a termo

**O que é** — a IV por vencimento. Normalmente sobe com o prazo (contango). Quando o curto prazo fica mais caro que o longo, o mercado está com medo de algo próximo.

**Erro comum** — olhar um vencimento só. Um evento (resultado, decisão de dividendos) aparece como "degrau" na estrutura a termo e desaparece depois.

**No painel** — `28,4 (nov) / 30,1 (dez) / 31,8 (jan)`: contango normal, sem evento concentrado no curto prazo.

## Volume e open interest

**O que é** — volume é o que foi negociado no dia; open interest (OI) é quantos contratos estão abertos. Volume mostra atividade, OI mostra posição acumulada.

**Erro comum** — concluir liquidez a partir de volume de um dia. Um dia de volume alto pode ser um único negócio grande num book vazio. O que importa é a combinação: negócios recorrentes + spread estreito + OI.

**No painel** — `OI 12.400 · vol 830`: série com posição acumulada relevante e giro diário saudável.

## Dias para liquidar

**O que é** — `OI / volume médio de 20 dias`. Quantos pregões seriam necessários para desmontar a posição média ao ritmo atual.

**Erro comum** — achar que OI alto significa liquidez. Papel com OI enorme e volume quase zero demora semanas para sair — e você sai pelo preço que o mercado quiser.

**No painel** — `2,4` é confortável. Acima de ~10, a estrutura deixa de ser desmontável no prazo do vencimento.

---

## Taxa histórica do sinal

**O que é** — quantas vezes, nos últimos anos, a mesma classe de sinal (ex.: `premium_rich`) foi seguida de volatilidade realizada abaixo da implícita nas 20 sessões seguintes. É o backtest do próprio funil.

**Erro comum** — ler 70% de acerto como "70% de lucro". A taxa diz se a vol entregue confirmou o preço, não quanto a estrutura teria ganho — e a perda dos 30% restantes pode ser bem maior que o ganho médio dos 70%. Olhe o pior caso (P10) junto.

**No painel** — `premium_rich · 68% · n 41 · P10 −5,2`: em 41 ocorrências, 68% entregaram vol abaixo da IV; nos piores 10%, a vol veio 5,2 pontos acima.

---

# Nível 3 — para quem já opera

## Theta

**O que é** — quanto o prêmio perde por dia só pela passagem do tempo, mantendo o resto constante.

**Erro comum** — olhar theta em R$ sem escala. R$ 0,008/dia é muito para uma opção de R$ 0,10 e pouco para uma de R$ 3,00. O painel mostra **em % do prêmio**.

**No painel** — `Θ R$ 0,008 · −0,56%/dia`: em 10 dias úteis, ~5,5% do prêmio evapora se nada mais mudar. E o decaimento **acelera** na última semana.

## Vega

**O que é** — quanto o prêmio muda para cada 1 ponto de volatilidade implícita.

**Erro comum** — achar que vega alto é bom. Vega é exposição: positivo se você comprou prêmio, negativo se vendeu. Quem vende coberta está vendendo vega, e sofre quando a IV sobe depois de entrar.

**No painel** — `ν R$ 0,05 por vol pt`: um ponto de IV move o prêmio em R$ 0,05. Na trava 40/38 o vega líquido é **negativo** (~−R$ 0,02), porque a ponta vendida fica mais perto do dinheiro.

## Gamma

**O que é** — a taxa de variação do delta. É o que faz a posição "acelerar" quando o papel se aproxima do strike.

**Erro comum** — ignorar gamma no vencimento. Perto do vencimento, ATM, o delta deixa de ser estável: ele vira 0 ou 1 em horas. Posição vendida ATM na semana do vencimento é a forma mais rápida de perder o controle.

**No painel** — gamma máximo no strike ATM; cresce à medida que o vencimento chega.

## Forward implícito

**O que é** — o preço a termo que o mercado está usando, extraído da paridade put-call. É o "spot do vencimento" segundo o mercado, já com juros e dividendos embutidos.

**Erro comum** — achar que forward e spot são a mesma coisa. A diferença é juro menos dividendo esperado no período — e é justamente ela que revela expectativa de provento.

**No painel** — usado internamente para calcular IV sem depender de um chute de dividendo.

## Dividendo implícito

**O que é** — `r − ln(F/S)/T`: a expectativa de dividendo que o mercado está precificando no prazo do vencimento.

**Erro comum** — usar o dividendo anunciado como se fosse o esperado. O que move a opção é o que o mercado **acredita**, e isso inclui proventos ainda não anunciados.

**No painel** — comparado com o Δstrike (que dá o dividendo **realizado**), mostra quando o mercado espera mais provento do que já aconteceu.

## Estilo: americana × europeia

**O que é** — a opção **americana** pode ser exercida a qualquer momento até o vencimento; a **europeia**, só no vencimento. Na B3 o estilo é definido por série: no nome do papel, `PETRE` indica europeia e `PETR`, americana.

**Erro comum** — supor que todas as opções de ação são do mesmo estilo. Na B3, **todas as puts de ação são europeias**, e as calls existem nos dois estilos — em 2026, cerca de dois terços das séries de call eram americanas. Numa call americana ITM com ex-dividendo antes do vencimento, o exercício antecipado tem valor, e a IV calculada por modelo europeu sai contaminada.

**No painel** — essas séries recebem a flag `american_call_ex_div` e ficam fora do smile e do forward.

---

# As 5 armadilhas que mais custam dinheiro

1. **Delta não é probabilidade.** Delta é exposição; a distância entre os dois cresce com o skew.
2. **Spread em % esconde.** Em prêmio pequeno, o % é grande e o custo é irrelevante; em prêmio grande, o % é pequeno e o custo é enorme.
3. **IV alta não é direção.** É expectativa de movimento, para os dois lados.
4. **IV rank não é sinal.** Compara com o passado do papel, que pode ter mudado de regime.
5. **Vender prêmio funciona na média e quebra na cauda.** O VRP positivo é a regra; o tamanho da posição é o que decide se você sobrevive a uma exceção.
