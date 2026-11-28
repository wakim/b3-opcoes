/** Dicionário único de apresentação da F9.
 *
 * Um mapa por chave semântica — nunca por texto visível. O Expert preserva os
 * rótulos atuais; o Leigo expande a sigla antes dela e traz ajuda curta. O
 * verbete completo NÃO vive aqui: continua em `docs/glossario.md`, servido por
 * `/api/glossary/{slug}`. Nenhum número novo, previsão ou recomendação.
 */
import { useMemo } from 'react'
import { useLanguage, type LanguageMode } from './language'
import { presentationKeyForField } from '../components/metricKeys'

export type CopyParams = Record<string, string | number>

export interface PresentationCopy {
  label: string
  help?: string
  glossaryKey?: string
}

interface VariantCopy {
  expert: string
  leigo: string
  helpExpert?: string
  helpLeigo?: string
  glossaryKey?: string
}

export interface PresentationEntry extends VariantCopy {
  variants?: Record<string, VariantCopy>
}

/** Ajuda herdada da entrada base quando a variante não traz a sua. */
function v(
  expert: string,
  leigo: string,
  helpExpert?: string,
  helpLeigo?: string,
  glossaryKey?: string,
): VariantCopy {
  const out: VariantCopy = { expert, leigo }
  if (helpExpert) out.helpExpert = helpExpert
  if (helpLeigo) out.helpLeigo = helpLeigo
  if (glossaryKey) out.glossaryKey = glossaryKey
  return out
}

function e(
  expert: string,
  leigo: string,
  helpExpert: string | undefined,
  helpLeigo: string,
  glossaryKey?: string,
  variants?: Record<string, VariantCopy>,
): PresentationEntry {
  const out: PresentationEntry = v(expert, leigo, helpExpert, helpLeigo, glossaryKey)
  if (variants) out.variants = variants
  return out
}

/** Templates `{param}` são interpolados aqui, nunca espalhados pela UI. */
function interpolate(template: string, params?: CopyParams): string {
  if (!params) return template
  return template.replace(/\{(\w+)\}/g, (whole, name: string) => {
    const value = params[name]
    return value === undefined ? whole : String(value)
  })
}

function resolve(
  entry: PresentationEntry,
  mode: LanguageMode,
  variant?: string,
  params?: CopyParams,
): PresentationCopy {
  const chosen = (variant && entry.variants?.[variant]) || entry
  const label = interpolate(chosen[mode], params)
  const template = mode === 'expert'
    ? (chosen.helpExpert ?? entry.helpExpert)
    : (chosen.helpLeigo ?? entry.helpLeigo)
  const help = template ? interpolate(template, params) : undefined
  const glossaryKey = chosen.glossaryKey ?? entry.glossaryKey
  if (help && glossaryKey) return { label, help, glossaryKey }
  if (help) return { label, help }
  if (glossaryKey) return { label, glossaryKey }
  return { label }
}

export const PRESENTATION = {
  'metric.generic': e(
    'Métrica', 'Métrica', 'O que é {label}', 'O que é {label}',
  ),

  help: e(
    'Ajuda', 'Ajuda',
    'Explicação curta sobre o termo.',
    'Explicação curta sobre o número.',
  ),

  summary: e(
    'Resumo', 'Resumo do recorte',
    'Contagem do que foi considerado no recorte.',
    'Quantos papéis entraram na contagem.',
    undefined,
    {
      universe: v('Universo', 'papéis acompanhados'),
      metric: v('Com métrica', 'papéis com número'),
      excluded: v('Excluídos', 'papéis sem número'),
    },
  ),

  table: e(
    'Tabela', 'Tabela',
    undefined, 'Lista de valores em colunas.',
    undefined,
    {
      date: v('Data', 'data do pregão'),
      price: v('Preço', 'preço de fechamento'),
    },
  ),

  language: e(
    'Idioma', 'Idioma', 'Alterna a leitura técnica e a leitura simples.',
    'Escolha como os termos aparecem na tela.',
    undefined,
    {
      group: v('Idioma', 'Idioma', 'Alterna a leitura técnica e a leitura simples.', 'Escolha como os termos aparecem na tela.'),
      expert: v('Expert', 'Expert', undefined, 'Termos técnicos e siglas.'),
      leigo: v('Leigo', 'Leigo', undefined, 'Termos por extenso e ajuda curta.'),
    },
  ),

  iv_atm_30d: e(
    'Volatilidade implícita 30d', 'Volatilidade implícita (IV) no dinheiro (ATM), 30 dias',
    'Volatilidade implícita no dinheiro (ATM), padronizada para 30 dias.',
    'Quanto o mercado espera que o papel oscile em 30 dias.',
    'iv-atm-30d',
    {
      median: v('Volatilidade implícita mediana', 'Volatilidade implícita (IV) mediana'),
      option: v('IV ATM 30d', 'Volatilidade implícita (IV) no dinheiro (ATM), 30 dias'),
      entry: v('Volatilidade implícita 30d na entrada', 'Volatilidade implícita (IV) no dinheiro (ATM) na entrada'),
      session: v('Volatilidade implícita 30d na sessão', 'Volatilidade implícita (IV) no dinheiro (ATM) na sessão'),
      close: v('Volatilidade implícita 30d no fechamento', 'Volatilidade implícita (IV) no dinheiro (ATM) no fechamento'),
    },
  ),

  iv_pct: e(
    'Volatilidade implícita', 'Volatilidade implícita (IV)',
    'Volatilidade que o preço da opção pede, ao ano.',
    'Quanto o preço da opção já embute de oscilação.',
    'volatilidade-implicita-iv',
    {
      column: v('IV', 'Volatilidade implícita (IV)'),
      entry: v('Volatilidade implícita na entrada', 'Volatilidade implícita (IV) na entrada'),
      entry_compact: v('IV na entrada', 'Volatilidade implícita (IV) na entrada'),
      shift: v('Deslocamento de IV {shift}', 'Mudança da volatilidade implícita (IV) {shift}'),
    },
  ),

  rv: e(
    'Volatilidade realizada', 'Volatilidade realizada (RV)',
    'Oscilação observada do papel no período.',
    'Quanto o papel oscilou de verdade no período.',
    'volatilidade-realizada-rv',
    {
      d30: v('Realizada 30d', 'Volatilidade realizada (RV) de 30 dias'),
      shock: v('Pregão dominante na RV', 'Pregão dominante na volatilidade realizada (RV)', undefined, 'Um único pregão explica boa parte da oscilação recente.'),
    },
  ),

  vrp: e(
    'Prêmio de volatilidade', 'Prêmio de volatilidade (VRP)',
    'VRP = volatilidade implícita − realizada.',
    'Diferença entre o que o mercado espera e o que aconteceu.',
    'vrp',
    {
      median: v('Prêmio de volatilidade mediano', 'Prêmio de volatilidade (VRP) mediano'),
      option: v('VRP', 'Prêmio de volatilidade (VRP)'),
    },
  ),

  iv_rank: e(
    'Posição da volatilidade (12m)', 'Posição da volatilidade implícita (IV) em 12 meses',
    'Onde a volatilidade implícita de hoje está nos últimos 252 pregões.',
    'Onde a volatilidade de hoje fica entre as recentes.',
    'iv-rank-e-percentil',
    { ruler: v('Posição da volatilidade em 12 meses', 'Posição da volatilidade implícita (IV) em 12 meses') },
  ),

  vrp_z: e(
    'Desvios-padrão do prêmio', 'Desvios-padrão do prêmio de volatilidade (VRP)',
    'Quantos desvios-padrão o prêmio de volatilidade de hoje está da própria média.',
    'Quão longe o prêmio está do seu valor habitual.',
    'z-score-do-vrp',
    { ruler: v('Desvios-padrão do prêmio de volatilidade', 'Desvios-padrão do prêmio de volatilidade (VRP)') },
  ),

  rr25: e(
    'RR25', 'Risk reversal 25Δ (RR25)',
    'Diferença de volatilidade implícita entre a call e a put de 25Δ.',
    'Quanto a proteção contra queda custa em relação à alta.',
    'skew-e-risk-reversal',
  ),
  bfly25: e(
    'BFLY25', 'Borboleta 25Δ (BFLY25)',
    'Medida da curvatura do smile de volatilidade.',
    'Quanto as opções afastadas do centro são mais caras.',
    'butterfly',
  ),
  atm: e(
    'ATM', 'No dinheiro (ATM)',
    'Strike mais próximo do preço da ação.',
    'Preço de exercício mais próximo do preço atual.',
    'iv-atm-30d',
  ),
  forward: e(
    'Forward', 'Forward (preço a termo implícito)',
    'Preço a termo implícito apurado por paridade.',
    'Preço futuro que o mercado embute nas opções.',
    'forward-implicito',
  ),
  df: e(
    'Fator de desconto (DF)', 'Fator de desconto (DF)',
    'Fator usado para trazer um valor futuro a hoje.',
    'Ajuste que transforma valor futuro em valor de hoje.',
    'fator-de-desconto-df',
  ),
  maturity: e(
    'Vencimento', 'Vencimento',
    'Data em que a opção expira.',
    'Data em que a opção deixa de valer.',
    'strike-e-vencimento',
    {
      previous: v('Vencimento anterior', 'Vencimento anterior'),
      choose: v('Escolher vencimento', 'Escolher a data de vencimento'),
      next: v('Próximo vencimento', 'Próximo vencimento'),
      plural: v('vencimentos', 'datas de vencimento'),
    },
  ),
  sessions_to_expiry: e(
    'Sessões até vencimento', 'Pregões até o vencimento',
    'Quantos pregões faltam para o vencimento da posição.',
    'Quantos pregões faltam para a data final da posição.',
    'sessoes-ate-o-vencimento',
  ),

  delta: e(
    'Delta', 'Delta (Δ)',
    'Quanto o prêmio muda para cada R$ 1 de variação da ação.',
    'Quanto o prêmio muda quando a ação sobe ou desce.',
    'delta',
    {
      per_real: v('Delta (por R$ 1)', 'Delta (Δ) por R$ 1'),
      aggregate: v('Delta agregado', 'Delta (Δ) agregado'),
      exposure: v('Delta em R$', 'Exposição em delta (Δ), em reais', 'Exposição da posição em reais, por delta.', 'Quanto a posição se move em reais com a ação.'),
      entry: v('Delta na entrada', 'Delta (Δ) na entrada'),
      ruler: v('Delta', 'Delta (Δ)'),
      column: v('Δ', 'Delta (Δ)'),
    },
  ),

  gamma: e(
    'Gamma', 'Gamma (Γ)',
    'Taxa de variação do delta.',
    'Quanto o delta muda quando a ação se move.',
    'gamma',
    {
      aggregate: v('Gamma agregado', 'Gamma (Γ) agregado'),
      column: v('Γ', 'Gamma (Γ)'),
    },
  ),

  theta: e(
    'Theta', 'Theta (Θ)',
    'Quanto o prêmio perde por dia útil, só pela passagem do tempo.',
    'Quanto o prêmio encolhe só porque o tempo passa.',
    'theta',
    {
      per_day: v('Theta (por dia)', 'Theta (Θ) por dia útil'),
      brl_day: v('Theta (R$/dia)', 'Theta (Θ) em reais por dia útil'),
      aggregate: v('Theta agregado', 'Theta (Θ) agregado'),
      column: v('Θ', 'Theta (Θ)'),
    },
  ),

  vega: e(
    'Vega', 'Vega (ν)',
    'Quanto o prêmio muda para cada ponto de volatilidade implícita.',
    'Quanto o prêmio muda quando a volatilidade muda.',
    'vega',
    {
      per_vol: v('Vega (por ponto de vol.)', 'Vega (ν) por ponto de volatilidade'),
      brl_point: v('Vega (R$/ponto)', 'Vega (ν) em reais por ponto de volatilidade'),
      aggregate: v('Vega agregado', 'Vega (ν) agregado'),
      column: v('ν', 'Vega (ν)'),
    },
  ),

  pnl: e(
    'P&L', 'Lucro/prejuízo (P&L)',
    'Resultado da posição.',
    'Quanto a posição ganhou ou perdeu.',
    'p-l-da-posicao',
    {
      pct: v('P&L %', 'Lucro/prejuízo (P&L) em percentual'),
      max: v('Ganho máximo', 'Maior ganho possível'),
      min: v('Perda máxima', 'Maior perda possível'),
      chart: v('P&L', 'Lucro/prejuízo (P&L)'),
    },
  ),

  strike: e(
    'Strike', 'Preço de exercício (strike)',
    'Preço pelo qual a opção pode ser exercida.',
    'Preço combinado para comprar ou vender a ação.',
    'strike-e-vencimento',
    {
      expanded: v('Preço de exercício', 'Preço de exercício (strike)'),
      current: v('Strike atual', 'Preço de exercício (strike) atual'),
      plural: v('strikes', 'preços de exercício (strikes)'),
    },
  ),

  spread: e(
    'Spread', 'Spread (diferença entre compra e venda)',
    'Spread percentual do fechamento; reais quando não há percentual',
    'Distância entre o preço de compra e o de venda.',
    'spread',
    {
      pct: v('Spread %', 'Spread em percentual'),
      bid_ask: v('Diferença bid/ask', 'Diferença entre oferta de compra e de venda'),
    },
  ),

  bid_ask: e(
    'Ofertas (bid/ask)', 'Ofertas de compra e venda (bid/ask)',
    'Melhor preço de compra e melhor preço de venda.',
    'Preços que o mercado está disposto a pagar e a cobrar.',
    'premio-bid-e-ask',
    { none: v('sem oferta', 'sem oferta de compra e venda') },
  ),

  spot: e(
    'Fechamento', 'Preço de fechamento (spot)',
    'Último preço negociado da ação na sessão; o painel é sempre D-1.',
    'Preço da ação no fim do pregão.',
    'preco-spot-fechamento',
    {
      close: v('Fechamento', 'Preço de fechamento (spot)'),
      form: v('Preço (spot / fechamento)', 'Preço de fechamento (spot)'),
    },
  ),

  call: e(
    'Call', 'Call (opção de compra)',
    'Direito de comprar a ação.',
    'Opção que dá o direito de comprar.',
  ),
  put: e(
    'Put', 'Put (opção de venda)',
    'Direito de vender a ação.',
    'Opção que dá o direito de vender.',
  ),
  style_european: e(
    'Europeia', 'Europeia (só exerce no vencimento)',
    'Só pode ser exercida no vencimento.',
    'Só pode ser usada na data de vencimento.',
    'estilo-americana-europeia',
  ),
  style_american: e(
    'Americana', 'Americana (pode exercer antes)',
    'Pode ser exercida antes do vencimento.',
    'Pode ser usada a qualquer tempo até o vencimento.',
    'estilo-americana-europeia',
  ),
  leg: e(
    'Perna', 'Perna (cada operação da posição)',
    'Cada operação que compõe a posição.',
    'Cada parte da posição: uma ação ou uma opção.',
    'perna-de-uma-posicao',
  ),

  payoff: e(
    'Payoff', 'Resultado no vencimento (payoff)',
    'Resultado da posição em cada preço no vencimento.',
    'Quanto a posição valeria em cada preço na data final.',
    'payoff-no-vencimento',
    {
      unavailable: v('payoff indisponível', 'resultado no vencimento indisponível'),
      chart: v('Payoff', 'Resultado no vencimento (payoff)'),
      unbounded_loss: v('ilimitada', 'sem limite'),
      unbounded_gain: v('ilimitado', 'sem limite'),
      maturity_aria: v('Payoff no vencimento de {ticker}', 'Resultado no vencimento da posição de {ticker}'),
    },
  ),

  breakeven: e(
    'Breakeven', 'Breakeven (preço de empate)',
    'Preço em que a posição não tem lucro nem prejuízo.',
    'Preço em que a posição empata.',
    'breakeven',
    { plural: v('Breakevens', 'preços de empate') },
  ),
  net_premium: e(
    'Prêmio líquido', 'Prêmio líquido da estrutura',
    'Total pago ou recebido pelas pernas.',
    'Soma do que foi pago e recebido nas pernas.',
    'premio-liquido-de-uma-estrutura',
  ),
  max_loss: e(
    'Perda máxima', 'Maior perda possível',
    'Maior perda que a estrutura pode ter.',
    'Pior resultado possível para a posição.',
    'perda-maxima-ganho-maximo-e-capital-comprometido',
  ),
  max_gain: e(
    'Ganho máximo', 'Maior ganho possível',
    'Maior ganho que a estrutura pode ter.',
    'Melhor resultado possível para a posição.',
    'perda-maxima-ganho-maximo-e-capital-comprometido',
  ),
  capital: e(
    'Capital comprometido', 'Capital comprometido',
    'Recurso necessário para montar a posição.',
    'Dinheiro necessário para montar a posição.',
    'perda-maxima-ganho-maximo-e-capital-comprometido',
  ),
  capital_limit: e(
    'Limite de capital', 'Limite de capital',
    'Teto definido para o capital da posição.',
    'Valor máximo definido para a posição.',
    'limite-de-capital',
  ),

  scenarios: e(
    'Cenários', 'Cenários (preço, volatilidade e tempo)',
    'Combinações de preço, volatilidade e dias consideradas.',
    'Situações hipotéticas usadas para ver o resultado.',
    'cenario-spot-iv-dias',
    {
      unavailable: v('cenários indisponíveis', 'cenários indisponíveis'),
      no_day0: v('sem cenário no dia 0', 'sem cenário para o dia de hoje'),
      unavailable_reason: v('cenários indisponíveis: {reason}. O payoff no vencimento continua calculado.', 'cenários indisponíveis: {reason}. O resultado no vencimento continua calculado.'),
      day0: v('Cenários no dia 0 (spot e choque de IV do backend)', 'Cenários no dia de hoje (preço e choque de volatilidade vindos do servidor)'),
    },
  ),

  structure: e(
    'Estrutura', 'Estrutura (conjunto de pernas)',
    'Conjunto de pernas montado.',
    'Conjunto de operações que formam a posição.',
    'perna-de-uma-posicao',
    {
      title: v('Montar estrutura', 'Montar uma estrutura'),
      count: v('{count}/{max} pernas', '{count} de {max} operações'),
      intro: v('Calculadora de payoff. {quantity}; a quantidade é em ações e não é multiplicada por 100. Nada é gravado.', 'Calculadora de resultado no vencimento. {quantity}; a quantidade conta ações e nada é multiplicado por 100. Nada é gravado.'),
      empty: v('Expanda uma linha da cadeia e use {buy} ou {sell}, ou {add_stock} abaixo.', 'Abra uma linha das opções e use {buy} ou {sell}, ou {add_stock} abaixo.'),
      empty_prefix: v('Expanda uma linha da cadeia e use ', 'Abra uma linha das opções e use '),
      empty_or: v(' ou ', ' ou '),
      empty_suffix: v(', ou {add_stock} abaixo.', ', ou {add_stock} abaixo.'),
      remove_aria: v('Remover {ticker}', 'Remover a operação de {ticker}'),
      quantity_aria: v('Quantidade de ações de {ticker}', 'Quantidade de ações da operação de {ticker}'),
      stock_leg: v('Perna de ação', 'Operação com a ação'),
      stock_side: v('Lado da ação', 'Comprar ou vender a ação'),
      stock_quantity: v('Quantidade de ações da ação', 'Quantidade de ações'),
      limit: v('Limite de {max} pernas atingido (o backend recusa mais).', 'Limite de {max} operações atingido; o servidor não aceita mais.'),
      capital_over: v('O capital comprometido passa do capital informado.', 'O dinheiro necessário para a posição passa do limite informado.'),
      summary: v('{maturity}{structure} {version} · sessão {session}{forward}', '{maturity}{structure} {version} · pregão {session}{forward}'),
    },
  ),
  liquid_series: e(
    'Séries líquidas', 'Séries com negócio suficiente',
    'Séries que negociaram com frequência na janela.',
    'Opções com negócio suficiente para observar.',
  ),
  volume: e(
    'Volume', 'Volume financeiro negociado',
    'Volume financeiro da sessão, em reais',
    'Total em reais negociado no pregão.',
    'volume-e-open-interest',
  ),
  oi: e(
    'OI', 'Posições em aberto (OI)',
    'Contratos ainda abertos no mercado.',
    'Operações que ainda não foram fechadas.',
    'volume-e-open-interest',
  ),
  days_to_liquidate: e(
    'Dias para liquidar', 'Dias para desmontar a posição',
    'Pregões necessários para desmontar a posição média da série.',
    'Quantos pregões seriam precisos para sair da posição.',
    'dias-para-liquidar',
  ),
  trades: e(
    'Negócios', 'Negócios realizados',
    'Quantidade de negócios na sessão.',
    'Quantas vezes a opção foi negociada.',
  ),
  liquidity: e(
    'Liquidez', 'Liquidez (facilidade de negociar)',
    'Facilidade de comprar e vender sem mudar o preço.',
    'O quanto é fácil negociar sem mudar o preço.',
    undefined,
    {
      papers_title: v('Papéis', 'Papéis'),
      series_title: v('Séries mais negociadas', 'Opções mais negociadas'),
      order_note: v('ordenado por liquidez; não é recomendação', 'ordenado por facilidade de negócio; não é recomendação'),
      empty_papers: v('nenhum papel com série líquida nesta sessão', 'nenhum papel com opções com negócio suficiente nesta sessão'),
      empty_series: v('nenhuma série líquida nesta sessão', 'nenhuma opção com negócio suficiente nesta sessão'),
      papers_count: v('{count} papéis com série líquida', '{count} papéis com opções com negócio suficiente'),
      paper_column: v('Papel', 'Papel'),
      close_column: v('Fechamento', 'Preço de fechamento'),
      liquid_series_column: v('Séries líquidas', 'Opções com negócio suficiente'),
      expiries_column: v('Vencimentos', 'Datas de vencimento'),
      iv_column: v('Volatilidade implícita 30d', 'Volatilidade implícita (IV) no dinheiro (ATM), 30 dias'),
      vrp_column: v('Prêmio de volatilidade', 'Prêmio de volatilidade (VRP)'),
      show_top: v('Mostrar top 10', 'Mostrar só os 10 primeiros'),
      show_all: v('Ver todos os {count}', 'Ver todos os {count} papéis'),
      showing: v('Mostrando {returned} de {total} séries por volume.', 'Mostrando {returned} de {total} opções, por volume.'),
      series_column: v('Série', 'Opção'),
      side_style_column: v('Lado / estilo', 'Compra ou venda / estilo'),
      region_papers: v('Papéis com mais séries líquidas', 'Papéis com mais opções com negócio suficiente'),
      region_series: v('Séries líquidas mais negociadas', 'Opções com negócio suficiente mais negociadas'),
      type_header: v('Tipo de liquidez', 'Tipo de listagem: papéis ou opções'),
      volume_header: v('{label}. {help}', '{label}. {help}'),
      spread_header: v('{label}. {help}', '{label}. {help}'),
      open_ticker: v('Abrir {ticker}', 'Abrir o papel {ticker}'),
      open_series: v('Abrir {ticker}, vencimento {maturity}', 'Abrir a opção {ticker}, com vencimento em {maturity}'),
      volume_cell: v('Volume financeiro: {value}', 'Total negociado em reais: {value}'),
      liquid: v('líquida', 'com negócio suficiente'),
    },
  ),

  decision: e(
    'Decisão', 'Decisão registrada',
    'Registro da intenção do usuário.',
    'Registro do que você decidiu fazer.',
    'decisao-registrada',
  ),
  prediction: e(
    'Previsão estruturada', 'Previsão que pode ser conferida',
    'Previsão ligada a uma decisão.',
    'O que você esperava que acontecesse.',
    'previsao-estruturada',
  ),
  horizon: e(
    'Horizonte', 'Horizonte em pregões',
    'Prazo da previsão em sessões.',
    'Quantos pregões a previsão considera.',
    'horizonte-em-sessoes',
  ),
  observed: e(
    'Observado', 'Valor observado depois',
    'Valor visto na sessão de avaliação.',
    'Valor que apareceu na data combinada.',
    'observado',
  ),
  void: e(
    'Sem dados', 'Sem dados para avaliar',
    'Previsão não avaliada por falta de métrica.',
    'Não foi possível avaliar porque faltou dado.',
    'sem-dados-void',
    {
      metric_unavailable: v('métrica ausente na sessão', 'não havia valor da métrica nesse pregão'),
      unsupported_metric: v('métrica não suportada', 'essa métrica não pode ser avaliada'),
      invalid_observation: v('observado inválido', 'o valor observado não serve para avaliar'),
      prediction_parent_missing: v('previsão sem pai válido', 'a previsão não está ligada a uma posição ou decisão'),
      reason: v('Motivo', 'Motivo da ausência de dado'),
      count: v('Sem dados', 'Quantidade sem dados'),
    },
  ),

  prediction_status_journal: e(
    'Desfecho', 'Desfecho da previsão',
    'Resultado da avaliação da previsão no diário.',
    'O que aconteceu com a previsão.',
    'desfecho-da-previsao',
    {
      pending: v('pendente', 'pendente'),
      hit: v('acerto', 'acerto'),
      miss: v('erro', 'erro'),
      void: v('sem dados', 'sem dados'),
    },
  ),
  prediction_status_position: e(
    'Desfecho', 'Desfecho da previsão',
    'Resultado da avaliação da previsão da posição.',
    'O que aconteceu com a previsão da posição.',
    'desfecho-da-previsao',
    {
      pending: v('pendente', 'ainda não avaliada'),
      hit: v('confirmada', 'aconteceu como previsto'),
      miss: v('não confirmada', 'não aconteceu como previsto'),
      void: v('sem avaliação', 'sem dado para avaliar'),
    },
  ),

  sample_n: e(
    'n', 'Amostra (n)',
    'Quantidade de previsões já avaliadas.',
    'Quantas previsões já têm resultado.',
    'amostra-n',
  ),
  personal_hit_rate: e(
    'Taxa de acerto pessoal', 'Taxa de acerto pessoal',
    'Parcela das suas previsões que se confirmou.',
    'Quantas das suas previsões se confirmaram.',
    'taxa-de-acerto-pessoal',
  ),
  calibration: e(
    'Calibração pessoal', 'Calibração pessoal',
    'Comparação entre as previsões e os resultados.',
    'Comparação das suas previsões com o que aconteceu.',
    'calibracao-pessoal',
    {
      evaluated: v('Previsões avaliadas', 'previsões com resultado para comparar'),
      hits: v('Acertos', 'previsões confirmadas'),
      misses: v('Erros', 'previsões não confirmadas'),
      voids: v('Sem dados', 'sem dado para avaliar'),
      rate: v('Taxa', 'Taxa de acerto'),
      region: v('Calibração por métrica e horizonte', 'Comparação das previsões por medida e prazo'),
      intro: v('Por métrica e horizonte.', 'A comparação é separada por medida e prazo.'),
      note: v('Não há comparação com sinal nem recomendação.', 'Estes números não são um sinal nem uma recomendação.'),
    },
  ),

  position: e(
    'Posição', 'Posição',
    'Operação aberta na carteira.',
    'Operação que você mantém em andamento.',
    undefined,
    {
      singular: v('posição aberta', 'posição em andamento'),
      open_plural: v('Posições abertas', 'posições em andamento'),
    },
  ),

  stamp_age: e(
    'Sessão', 'Sessão do painel',
    'Data da sessão exibida no painel.',
    'Dia do pregão mostrado na tela.',
    undefined,
    {
      current: v('D-1', 'último pregão', undefined, 'O painel mostra o último pregão disponível.'),
      delayed: v('D-{n} · atrasada', 'pregão atrasado em {n} sessões', undefined, 'O dado exibido está alguns pregões atrasado.'),
    },
  ),
  eod: e(
    'EOD', 'Fim de pregão (EOD)',
    'Dado de fechamento do pregão.',
    'Dado do fim do pregão, sem preço ao vivo.',
  ),
  append_only: e(
    'append-only', 'registro que só acrescenta',
    'Registros nunca são apagados ou alterados.',
    'O histórico só recebe novos registros.',
  ),

  flag: e(
    'Sinalizador', 'Sinalizador',
    'Aviso curto sobre a métrica.',
    'Aviso curto sobre o número.',
    undefined,
    {
      none: v('sem flags', 'sem alertas'),
      list: v('flags: {items}', 'alertas: {items}'),
      near_expiry: v('vencimento próximo', 'vencimento próximo'),
      itm: v('dentro do dinheiro', 'dentro do dinheiro (ITM)'),
      delta_drift: v('delta mudou', 'o delta (Δ) mudou'),
      event_in_window: v('evento na janela', 'evento previsto na janela'),
      thesis_failed: v('tese não confirmada', 'tese não confirmada'),
      capital_over: v('capital acima do limite', 'capital acima do limite'),
      one_sided: v('livro de um lado só', 'oferta de um lado só'),
      mark_missing: v('marcação ausente', 'sem marcação de preço'),
      gregas_parciais: v('gregas parciais', 'gregas incompletas'),
      unbounded_loss: v('perda ilimitada', 'perda sem teto'),
      unbounded_gain: v('ganho ilimitado', 'ganho sem teto'),
      payoff_unavailable: v('payoff indisponível', 'resultado no vencimento indisponível'),
      payoff_truncated: v('payoff truncado', 'resultado no vencimento cortado'),
      close_snapshot_missing: v('snapshot do fechamento ausente', 'falta o retrato do fechamento'),
      rv_shock_day: v('pregao dominante na RV', 'um pregão domina a volatilidade realizada'),
      iv_expired: v('iv_expired', 'vencimento no próprio dia: só valor intrínseco'),
      no_rates: v('no_rates', 'sem curva de taxa na sessão'),
      no_forward: v('no_forward', 'sem preço a termo utilizável'),
      forward_unavailable: v('forward_unavailable', 'sem preço a termo utilizável'),
      no_iv: v('no_iv', 'sem volatilidade implícita'),
      sparse_chain: v('sparse_chain', 'grade de opções esparsa'),
      no_trade_5d: v('no_trade_5d', 'sem negócio na série'),
      crossing_inconsistent_slope: v('crossing_inconsistent_slope', 'cruzamento de paridade inconsistente'),
      smile_outside_range: v('smile_outside_range', 'fora da faixa do smile'),
      no_atm_iv: v('no_atm_iv', 'sem volatilidade no dinheiro'),
      invalid_tenor: v('invalid_tenor', 'prazo inválido'),
      forward_outlier: v('forward_outlier', 'preço a termo fora do esperado'),
      leg_ambiguity: v('leg_ambiguity', 'perna com identificação ambígua'),
      inconsistent: v('inconsistent', 'série inconsistente'),
      monotonicity_violation: v('monotonicity_violation', 'volatilidade não obedece à ordem dos preços de exercício'),
      butterfly_violation: v('butterfly_violation', 'curvatura do smile inconsistente'),
      bfly_negative: v('bfly_negative', 'curvatura do smile negativa'),
      calendar_violation: v('calendar_violation', 'volatilidade menor no vencimento seguinte'),
      t_estimated: v('t_estimated', 'prazo estimado pelo calendário'),
      wide_spread: v('wide_spread', 'diferença entre compra e venda larga'),
      american_call_ex_div: v('american_call_ex_div', 'call americana com provento antes do vencimento'),
      american_call_itm: v('american_call_itm', 'call americana dentro do dinheiro'),
      scale_event: v('scale_event', 'desdobramento ou grupamento na sessão'),
      scale_adjusted: v('scale_adjusted', 'preços anteriores ajustados pelo desdobramento'),
      dividends_not_adjusted: v('dividends_not_adjusted', 'proventos não descontados dos preços'),
      no_event_coverage: v('no_event_coverage', 'sem cobertura de evento corporativo na série'),
      earnings_before_expiry: v('earnings_before_expiry', 'resultado previsto antes do vencimento'),
      illiquid_leg: v('illiquid_leg', 'perna sem negócio suficiente'),
      scenarios_unavailable: v('scenarios_unavailable', 'cenários indisponíveis'),
      zero_strike: v('zero_strike', 'preço de exercício zerado'),
      leg_mismatch: v('leg_mismatch', 'perna com preço de exercício divergente'),
      style_unknown: v('style_unknown', 'estilo da opção não informado'),
      iv_extrapolated: v('iv_extrapolated', 'volatilidade extrapolada'),
      no_short_expiry: v('no_short_expiry', 'sem vencimento curto'),
      no_expiry: v('no_expiry', 'sem vencimento'),
      invalid_variance: v('invalid_variance', 'variância inválida'),
      no_pairs: v('no_pairs', 'sem pares para a paridade'),
      no_otm_crossing: v('no_otm_crossing', 'sem cruzamento fora do dinheiro'),
      ols_few_pairs: v('ols_few_pairs', 'poucos pares para o ajuste'),
      ols_nonpositive_slope: v('ols_nonpositive_slope', 'inclinação do ajuste não positiva'),
      put_not_european: v('put_not_european', 'put não europeia'),
      invalid_quote: v('invalid_quote', 'cotação inválida'),
      duplicate_strike: v('duplicate_strike', 'preço de exercício repetido'),
      no_90d: v('no_90d', 'sem janela de 90 pregões'),
      no_30d_bracket: v('no_30d_bracket', 'sem vencimentos em volta de 30 dias úteis'),
      oi_unavailable: v('oi_unavailable', 'posições em aberto indisponíveis'),
      volume_unavailable: v('volume_unavailable', 'volume indisponível'),
      iv_inconsistent_input: v('iv_inconsistent_input', 'preço da opção inconsistente'),
    },
  ),

  exclusion: e(
    'Situação', 'Situação do papel',
    'Motivo da exclusão do recorte.',
    'Por que o papel ficou de fora.',
    undefined,
    {
      ok: v('ok', 'disponível', undefined, 'O papel tem número nesta sessão.'),
      no_options: v('no_options', 'sem opções na sessão', undefined, 'Não há linha de opção do papel nesta sessão.'),
      no_trade: v('no_trade', 'não negociou na sessão', undefined, 'O papel não negociou no mercado à vista nesta sessão.'),
      no_metrics: v('no_metrics', 'sem métrica legível', undefined, 'Há opção, mas sem valor legível nas métricas.'),
      no_rates: v('no_rates', 'sem curva de taxa', undefined, 'A sessão não tem curva de taxa; IV e forward ficam vazios.'),
      sparse_chain: v('sparse_chain', 'grade de opções esparsa', undefined, 'Faltam preços de exercício para o ATM.'),
      no_forward: v('no_forward', 'sem preço a termo', undefined, 'Nenhum vencimento tem preço a termo utilizável.'),
      no_expiry: v('no_expiry', 'sem vencimento', undefined, 'Não há vencimento disponível nesta sessão.'),
      no_session: v('no_session', 'sem sessão no dado', undefined, 'A sessão não está presente no arquivo.'),
    },
  ),

  option_side: e(
    'Lado da opção', 'Lado da opção',
    'Se a opção é de compra ou de venda.',
    'Se a opção é de compra ou de venda.',
    undefined,
    {
      C: v('Call', 'Call (opção de compra)'),
      P: v('Put', 'Put (opção de venda)'),
    },
  ),
  option_style: e(
    'Estilo', 'Estilo da opção',
    'Quando a opção pode ser exercida.',
    'Quando a opção pode ser usada.',
    'estilo-americana-europeia',
    {
      E: v('Europeia', 'Europeia (só no vencimento)'),
      A: v('Americana', 'Americana (antes do vencimento)'),
      unknown: v('—', 'estilo não informado'),
    },
  ),
  position_side: e(
    'Lado', 'Lado da operação',
    'Se a operação foi compra ou venda.',
    'Se você comprou ou vendeu.',
    undefined,
    {
      long: v('comprada', 'comprada'),
      short: v('vendida', 'vendida'),
    },
  ),

  decision_type: e(
    'Tipo de decisão', 'Tipo de decisão',
    'Que tipo de decisão foi registrada.',
    'Que decisão você tomou.',
    'decisao-registrada',
    {
      no_entry: v('Não entrei', 'não entrei'),
      entry: v('Entrada', 'entrei'),
      exit: v('Saída', 'saída'),
      hold: v('Manter', 'mantive'),
      observation: v('Observação', 'fiquei observando'),
      watch: v('Observação', 'fiquei observando'),
      other: v('Outra', 'outra decisão'),
    },
  ),

  position_kind: e(
    'Tipo de posição', 'Tipo de posição',
    'Forma da posição montada.',
    'Como a posição foi montada.',
    undefined,
    {
      covered: v('cobertura', 'cobertura (ação comprada e call vendida)'),
      bull_call_spread: v('trava de alta', 'trava de alta'),
      bear_put_spread: v('trava de baixa', 'trava de baixa'),
      straddle: v('straddle', 'straddle (call e put no mesmo preço)'),
      strangle: v('strangle', 'strangle (call e put em preços diferentes)'),
      put_caixa: v('put caixa', 'put caixa (put vendida com caixa)'),
      custom: v('estrutura', 'estrutura montada'),
    },
  ),
  position_status: e(
    'Status', 'Status da posição',
    'Se a posição está aberta ou fechada.',
    'Se a posição ainda está em andamento.',
    undefined,
    {
      open: v('aberta', 'em andamento'),
      closed: v('fechada', 'encerrada'),
    },
  ),
  leg_type: e(
    'Tipo de perna', 'Tipo de perna',
    'Se a perna é uma ação ou uma opção.',
    'Se a parte da posição é uma ação ou uma opção.',
    'perna-de-uma-posicao',
    {
      stock: v('ação', 'ação'),
      call: v('call', 'call (opção de compra)'),
      put: v('put', 'put (opção de venda)'),
      option: v('opção', 'opção'),
    },
  ),

  error: e(
    'Não foi possível carregar', 'Não foi possível carregar',
    'Falha explícita, com código e orientação.',
    'Algo não deu certo; veja o motivo abaixo.',
    undefined,
    {
      unknown: v('Não foi possível carregar', 'Não foi possível carregar'),
      bad_request: v('Não foi possível carregar', 'pedido inválido'),
      unauthorized: v('Não foi possível carregar', 'sem permissão para esta ação'),
      bad_legs: v('Não foi possível carregar', 'pernas da estrutura inválidas'),
      underlying_mismatch: v('Não foi possível carregar', 'as pernas são de papéis diferentes'),
      maturity_mismatch: v('Não foi possível carregar', 'as pernas são de vencimentos diferentes'),
      ticker_not_found: v('Não encontrado', 'papel não encontrado'),
      no_session: v('Não encontrado', 'sessão não encontrada'),
      maturity_not_found: v('Não encontrado', 'vencimento não encontrado'),
      expiry_not_found: v('Não encontrado', 'vencimento não encontrado'),
      metrics_missing: v('Não foi possível carregar', 'métricas ausentes nesta sessão'),
      not_found: v('Não encontrado', 'não encontrado'),
      position_not_found: v('Não encontrado', 'posição não encontrada'),
      decision_not_found: v('Não encontrado', 'decisão não encontrada'),
      position_closed: v('Não foi possível carregar', 'a posição já está fechada'),
      position_id_conflict: v('Não foi possível carregar', 'identificador de posição repetido'),
      decision_id_conflict: v('Não foi possível carregar', 'identificador de decisão repetido'),
      method_not_allowed: v('Não foi possível carregar', 'ação não permitida'),
      frontend_missing: v('Não foi possível carregar', 'interface não disponível'),
      glossary_missing: v('Não foi possível carregar', 'verbete indisponível'),
      lake_missing: v('Não foi possível carregar', 'arquivos de dados indisponíveis'),
      lake_unavailable: v('Saúde do lake indisponível', 'arquivos de dados indisponíveis'),
      storage_error: v('Não foi possível carregar', 'falha ao salvar'),
      bad_config: v('Não foi possível carregar', 'configuração inválida'),
      internal_error: v('Não foi possível carregar', 'erro interno'),
      network_error: v('Não foi possível carregar', 'sem conexão com o servidor'),
      bad_response: v('Não foi possível carregar', 'resposta inesperada do servidor'),
      http_error: v('Não foi possível carregar', 'falha na comunicação com o servidor'),
      token_rejected: v('Não foi possível carregar', 'token recusado'),
    },
  ),

  loading: e(
    'Carregando', 'Carregando',
    undefined, 'Buscando os dados na sessão.',
    undefined,
    {
      generic: v('carregando…', 'carregando…'),
      session: v('carregando sessão…', 'carregando sessão…'),
      search: v('buscando…', 'buscando…'),
      series: v('carregando série…', 'carregando série…'),
      chain: v('carregando cadeia…', 'carregando cadeia…'),
      wallet: v('carregando carteira…', 'carregando carteira…'),
      position: v('carregando posição…', 'carregando posição…'),
      calibration: v('carregando calibração…', 'carregando calibração…'),
      journal: v('carregando diário…', 'carregando diário…'),
      glossary: v('carregando verbete…', 'carregando verbete…'),
    },
  ),

  empty: e(
    'Vazio', 'Sem itens para mostrar',
    undefined, 'Não há dado para mostrar nesta seleção.',
    undefined,
    {
      options: v('nenhuma série neste recorte', 'nenhuma opção neste recorte'),
      chain_side: v('nenhuma série neste lado', 'nenhuma opção deste lado (compra ou venda)'),
      series: v('nenhuma série neste recorte', 'nenhuma série neste recorte'),
      papers: v('nenhum papel nesta seção do recorte', 'nenhum papel nesta seção'),
      positions: v('nenhuma posição aberta', 'nenhuma posição aberta'),
      decisions: v('ainda não há decisão registrada', 'ainda não há decisão registrada'),
      calibration: v('Ainda não há previsão avaliada. O relatório não inventa taxa sem n.', 'Ainda não há previsão avaliada; a taxa só aparece quando há resultado.'),
      glossary: v('verbete indisponível', 'verbete indisponível'),
      payoff: v('payoff indisponível', 'resultado no vencimento indisponível'),
      scenarios: v('cenários indisponíveis', 'cenários indisponíveis'),
      chain: v('nenhuma série neste recorte', 'nenhuma opção neste recorte'),
      structure: v('monte uma estrutura', 'monte uma estrutura'),
      search: v('nenhum papel para “{q}”', 'nenhum papel para “{q}”'),
    },
  ),

  notice: e(
    'Aviso', 'Aviso',
    undefined, 'Informação sobre o limite do que está sendo mostrado.',
    undefined,
    {
      truncated_chain: v('A cadeia foi cortada em {returned} de {total} linhas.', 'A cadeia mostra {returned} de {total} linhas.'),
      truncated_series: v('A série foi cortada em {returned} de {total} linhas.', 'A série mostra {returned} de {total} linhas.'),
      truncated_positions: v('A lista foi cortada em {returned} de {total} linhas.', 'A lista mostra {returned} de {total} linhas.'),
      truncated_journal: v('O diário foi cortado em {returned} de {total} linhas.', 'O diário mostra {returned} de {total} linhas.'),
      truncated_calibration: v('A calibração foi cortada em {returned} de {total} linhas.', 'A calibração mostra {returned} de {total} linhas.'),
      truncated_payoff: v('O payoff foi cortado em {returned} de {total} pontos.', 'O resultado foi cortado em {returned} de {total} pontos.'),
      token_rejected: v('token recusado', 'token recusado'),
    },
  ),

  token: e(
    'Token', 'Token de acesso',
    'Campo opcional de autenticação das escritas.',
    'Chave usada para liberar as escritas no aplicativo.',
    undefined,
    {
      label: v('Token do aplicativo', 'Token do aplicativo'),
      save: v('Salvar token', 'Salvar token'),
      unlock: v('Liberar escritas', 'Liberar escritas'),
      close: v('Token para fechar', 'Chave para encerrar'),
      change: v('Trocar token', 'Trocar chave'),
      description: v('A leitura é liberada na tailnet. O token libera apenas as escritas desta carteira.', 'A leitura é liberada na tailnet. A chave libera apenas as escritas desta carteira.'),
      description_journal: v('A leitura do diário é liberada na tailnet. O token libera apenas o registro de decisões.', 'A leitura do diário é liberada na tailnet. A chave libera só o registro das suas decisões.'),
      description_close: v('O registro grava um evento append-only e exige o token de escrita.', 'O encerramento grava um registro que só acrescenta e exige a chave de escrita.'),
      register: v('Token para registrar', 'Chave para registrar'),
      compact_description: v('O fechamento grava um evento no journal e exige o token de escrita.', 'O encerramento grava um registro e exige a chave de escrita.'),
      note: v('Fica somente neste navegador e é enviado no cabeçalho das escritas. Não aparece na tela nem no log.', 'Fica somente neste navegador e é enviado nas escritas. Não aparece na tela nem no log.'),
      required_error: v('digite o token do aplicativo', 'digite a chave do aplicativo'),
      save_error: v('não foi possível salvar o token neste navegador', 'não foi possível salvar a chave neste navegador'),
    },
  ),

  action: e(
    'Ação', 'Ação',
    undefined, 'Operação disponível na tela.',
    undefined,
    {
      buy: v('Comprar', 'Comprar'),
      sell: v('Vender', 'Vender'),
      add_stock: v('Adicionar ação', 'Adicionar ação'),
      add_option: v('Adicionar opção', 'Adicionar opção'),
      remove: v('remover', 'remover'),
      calculate: v('Calcular', 'Calcular'),
      calculate_count: v('Calcular ({count})', 'Calcular ({count})'),
      calculating: v('calculando…', 'calculando…'),
      clear: v('Limpar', 'Limpar'),
      new_position: v('Nova posição', 'Nova posição'),
      new_decision: v('Nova decisão', 'Nova decisão'),
      register_position: v('Registrar posição', 'Registrar posição'),
      registering: v('registrando…', 'registrando…'),
      register_decision: v('Registrar decisão', 'Registrar decisão'),
      close_position: v('Fechar posição', 'Encerrar posição'),
      confirm_close: v('Confirmar fechamento', 'Confirmar encerramento'),
      closing: v('Fechando…', 'Encerrando…'),
      open_position: v('Abrir posição', 'Abrir posição'),
      open_wallet: v('Abrir carteira', 'Abrir carteira'),
      cancel: v('Cancelar', 'Cancelar'),
      show_all: v('Todas', 'Todas'),
      show_top: v('Líquidas', 'Com negócio suficiente'),
      papers: v('papéis', 'papéis'),
      series: v('séries', 'séries'),
      liquid: v('Líquidas', 'Com negócio suficiente'),
      all: v('Todas', 'Todas'),
      understand: v('Entendi', 'Entendi'),
      clear_draft: v('Fechar formulário', 'Limpar o que foi preenchido'),
    },
  ),

  form: e(
    'Formulário', 'Formulário',
    'Campos usados nas fichas de posição e decisão.',
    'Campos para preencher a posição ou a decisão.',
    undefined,
    {
      metric: v('Métrica', 'Medida usada na previsão'),
      operator: v('Operador', 'Comparação'),
      value: v('Valor', 'Valor de referência'),
      evaluate_on: v('Avaliar em', 'Data para conferir'),
      thesis: v('Tese', 'Por que você decidiu isso'),
      context: v('Contexto', 'Situação na decisão'),
      notes: v('Notas', 'Anotações'),
      underlying: v('Ativo-objeto', 'Ação da operação'),
      quantity: v('Quantidade', 'Quantidade de ações'),
      entry_price: v('Preço de entrada', 'Preço pago na entrada'),
      entry_strike: v('Strike de entrada', 'Preço de exercício na entrada'),
      leg_type: v('Tipo de perna', 'Tipo de operação'),
      capital_limit: v('Limite de capital', 'Valor máximo para a posição'),
      prediction_checkbox: v('Registrar previsão', 'Quero conferir esta decisão depois'),
      session_optional: v('Sessão (opcional)', 'Data do pregão (pode ficar em branco)'),
      session_date_optional: v('Data da sessão (opcional)', 'Data do pregão (pode ficar em branco)'),
      horizon_sessions: v('Horizonte (sessões)', 'Prazo em pregões'),
      decision_type: v('Tipo de decisão', 'Que decisão você registrou'),
      ticker: v('Ticker', 'Código do papel'),
      ticker_leg: v('Ticker da perna {n}', 'Código do papel da perna {n}'),
      type_leg: v('Tipo da perna {n}', 'Tipo de operação da perna {n}'),
      side_leg: v('Lado da perna {n}', 'Compra ou venda da perna {n}'),
      quantity_leg: v('Quantidade de ações da perna {n}', 'Quantidade de ações da perna {n}'),
      actions: v('Ações', 'Quantidade de ações'),
      legs_plural: v('Pernas', 'Operações da posição'),
      optional: v('(opcional)', '(pode ficar em branco)'),
      price_optional: v('(R$, opcional)', '(em reais, pode ficar em branco)'),
      quantity_note: v('A quantidade é em ações. 100 ações = 1 contrato padrão; nada é multiplicado por 100.', 'A quantidade informada conta ações. Cada 100 ações formam 1 contrato padrão; nada é multiplicado por 100.'),
      position_data: v('Dados da posição', 'Campos da posição'),
      decision_data: v('Dados da decisão', 'Campos da decisão'),
      add_prediction: v('Adicionar previsão avaliável', 'Quero conferir este resultado depois'),
      prediction_note: v('Notas continuam texto livre. Só os campos abaixo entram na calibração.', 'As notas são texto livre; só os campos abaixo viram resultado para conferir.'),
      context_placeholder: v('contexto da decisão', 'por que você decidiu isso'),
      notes_placeholder: v('o que você decidiu e por quê', 'o que você decidiu e por quê'),
    },
  ),

  screen: e(
    'Tela', 'Tela',
    undefined, 'Nome da tela aberta.',
    undefined,
    {
      hoje: v('Hoje', 'Painel de hoje'),
      carteira: v('Carteira', 'Minhas posições'),
      diario: v('Diário', 'Diário de decisões'),
      serie: v('Série', 'Histórico da opção'),
      portfolio_note: v('Posições e marcação EOD do journal', 'Posições e preços de fechamento registrados no diário'),
      positions: v('Posições', 'Minhas posições'),
      chain_title: v('Cadeia de opções', 'Opções disponíveis'),
      iv_term_title: v('Volatilidade implícita por vencimento', 'Volatilidade implícita (IV) em cada data de vencimento'),
      history_title: v('Histórico', 'Histórico da opção'),
      last_sessions: v('Últimos pregões', 'Últimos dias de pregão'),
      counts: v('{expiries} vencimentos · {strikes} strikes · {history} dias de histórico', '{expiries} datas de vencimento · {strikes} preços de exercício · {history} dias de histórico'),
      history_days: v('dias de histórico', 'dias de histórico'),
      iv_term_aria: v('{iv} por vencimento de {ticker}', '{iv} em cada vencimento de {ticker}'),
      iv_rv_title: v('{iv} vs {rv}', '{iv} comparada com {rv}'),
      iv_rv_aria: v('{iv} e {rv} de {ticker}', '{iv} e {rv} do papel {ticker}'),
      term_caption: v('{iv} em {with_iv} de {total} vencimentos{illiquid} · {axis}', '{iv} em {with_iv} de {total} vencimentos{illiquid} · {axis}'),
      term_caption_illiquid: v(' · {count} com ATM sem séries líquidas', ' · {count} sem opções com negócio suficiente no dinheiro'),
      term_detail: v('{count} séries líquidas{forward}', '{count} opções com negócio suficiente{forward}'),
      term_detail_forward: v(' · {forward} {value}', ' · {forward} {value}'),
      term_with_iv: v('Vencimentos com {iv}', 'Vencimentos com {iv}'),
      term_note: v('{iv} é a do {strike} de |{delta}| mais próximo de 0,5; {liquid} = séries líquidas no vencimento.', '{iv} é a medida da opção de {strike} mais próxima do dinheiro; {liquid} são as opções do vencimento.'),
      term_without_iv: v('Ver os {count} vencimentos sem {iv}', 'Ver os {count} vencimentos sem {iv}'),
      chain_filter: v('Filtro da cadeia', 'Filtro das opções'),
    },
  ),

  detail: e(
    'Detalhe', 'Detalhe da posição',
    undefined, 'Campos e tabelas da ficha de posição.',
    undefined,
    {
      record: v('Registro', 'Identificação da posição'),
      opened: v('Aberta em', 'Data da entrada'),
      closed: v('Fechada em', 'Data do encerramento'),
      ticker: v('Ticker', 'Código do papel'),
      actions: v('Ações', 'Quantidade de ações'),
      entry: v('Entrada', 'Valores na entrada'),
      marks_title: v('Marcação da sessão', 'Preços da última sessão'),
      mark: v('Marca', 'Preço marcado'),
      one_sided: v('Um lado', 'Oferta de um lado só'),
      thesis_title: v('Tese vs realidade', 'O que você esperava e o que aconteceu'),
      condition: v('Condição', 'Comparação prevista'),
      eval_on: v('Avaliar em', 'Data para conferir'),
      registered_legs: v('Pernas registradas', 'Operações registradas'),
      legs_region: v('Tabela horizontal das pernas registradas', 'Tabela das operações registradas'),
      marks_region: v('Tabela horizontal da marcação da sessão', 'Tabela dos preços da última sessão'),
      no_prediction: v('Sem previsão estruturada; esta posição não entra na calibração.', 'Sem previsão registrada; esta posição não entra na comparação.'),
      entry_prices_note: v('Preços de entrada do journal; o backend calcula todos os pontos.', 'Preços de entrada vindos do diário; o servidor calcula todos os pontos.'),
      close_title: v('Fechamento', 'Encerramento'),
      close_note: v('Em branco, o backend usa a última sessão disponível.', 'Se ficar em branco, o servidor usa a última sessão disponível.'),
    },
  ),

  series: e(
    'Série', 'Série da opção',
    undefined, 'Histórico de uma opção.',
    undefined,
    {
      contract: v('Contrato', 'Identificação da opção'),
      side_style: v('Lado / estilo', 'Compra ou venda / estilo'),
      chart_aria: v('Série do gráfico', 'Linha do gráfico da opção'),
      last_point: v('Último ponto · {date}', 'Último registro · {date}'),
      history_caption: v('{count} pregões com negócio em {span} do lake · preço = último negócio do dia, não cotação{legs}', '{count} pregões com negócio em {span} no arquivo · preço = último negócio do dia, não cotação{legs}'),
      history_legs: v(' · a linha não liga pernas diferentes', ' · a linha não liga operações com preços de exercício diferentes'),
      no_trade_before: v('pregões sem negócio antes', 'dias sem negócio antes'),
      no_trade_before_one: v('{count} pregão sem negócio antes', '{count} dia sem negócio antes'),
      no_trade_before_many: v('{count} pregões sem negócio antes', '{count} dias sem negócio antes'),
      strike_value: v('{strike} {value}', '{strike} {value}'),
    },
  ),

  journal: e(
    'Diário', 'Diário de decisões',
    undefined, 'Registro das suas decisões e previsões.',
    undefined,
    {
      title: v('Diário', 'Diário de decisões'),
      intro: v('Decisões, previsões estruturadas e desfecho {eod}', 'Decisões, previsões e resultados de {eod}'),
      no_decision: v('Nenhuma decisão registrada.', 'Você ainda não registrou nenhuma decisão.'),
      no_prediction: v('Sem previsão estruturada; esta decisão não entra na calibração.', 'Sem previsão registrada; esta decisão não entra na comparação.'),
    },
  ),

  error_title: e(
    'Não foi possível carregar', 'Não foi possível carregar',
    undefined, 'Motivo da falha.',
    undefined,
    {
      wallet: v('Carteira indisponível', 'Não foi possível abrir a carteira'),
      position: v('Posição indisponível', 'Não foi possível abrir a posição'),
      close: v('Fechamento não registrado', 'Não foi possível registrar o encerramento'),
      calibration: v('Calibração indisponível', 'Não foi possível mostrar a comparação'),
      journal: v('Diário indisponível', 'Não foi possível abrir o diário'),
      search: v('Busca indisponível', 'Não foi possível buscar papéis'),
      decision: v('Decisão não registrada', 'Não foi possível registrar a decisão'),
      create_position: v('Posição não registrada', 'Não foi possível registrar a posição'),
      structure: v('Estrutura não avaliada', 'Não foi possível calcular a estrutura'),
    },
  ),

  chart: e(
    'Gráfico', 'Gráfico',
    undefined, 'Leitura visual do dado da sessão.',
    undefined,
    {
      no_iv: v('sem IV', 'sem volatilidade implícita'),
      hollow: v('ponto vazado: ATM sem série líquida', 'ponto vazio: no dinheiro (ATM) sem opção com negócio suficiente'),
      band: v('faixa: intervalo sem IV', 'faixa: intervalo sem volatilidade implícita'),
      dashed: v('traço tracejado: intervalo sem IV', 'linha tracejada: intervalo sem volatilidade implícita'),
      adjustment: v('ajuste de strike', 'mudança no preço de exercício'),
      pnl: v('P&L', 'Lucro/prejuízo (P&L)'),
      breakeven: v('breakeven', 'preço de empate'),
      axis_hint: v('eixo x proporcional ao tempo', 'eixo horizontal proporcional ao tempo'),
      no_data: v('sem pontos suficientes para desenhar', 'sem pontos suficientes para desenhar'),
      price_title: v('Fechamento do papel', 'Preço de fechamento da ação'),
      option_title: v('Histórico', 'Histórico'),
      corporate: v('provento / desdobramento', 'provento ou desdobramento'),
      earnings: v('resultado', 'divulgação de resultado'),
      jump: v('salto', 'salto de preço'),
      leg: v('troca de perna', 'mudança no preço de exercício'),
      no_trade_gap: v('tracejado: pregões sem negócio', 'linha tracejada: dias sem negócio'),
      no_trade: v('sem negócio no pregão', 'sem negócio neste dia'),
      maximum: v('máx {value}', 'valor máximo: {value}'),
      minimum: v('mín {value}', 'valor mínimo: {value}'),
      keyboard_points: v('Use as setas para percorrer os pontos.', 'Use as setas do teclado para percorrer os pontos do gráfico.'),
      keyboard_payoff: v('Use as setas para percorrer o payoff.', 'Use as setas do teclado para percorrer o resultado no vencimento.'),
      vertical_dashed: v('tracejado vertical: {label}', 'linha tracejada vertical: {label}'),
      axis_price: v('eixo x: preço do papel (R$)', 'eixo horizontal: preço da ação, em reais'),
      price_tip: v('Preço {value}', 'Preço do papel {value}'),
      mark_dividend: v('data ex de provento', 'data em que o provento deixa de contar'),
      mark_dividend_value: v('data ex de provento ({value}/ação, medido no strike)', 'data em que o provento deixa de contar ({value} por ação, medido no preço de exercício)'),
      mark_scale: v('desdobramento/grupamento ×{value} — preços anteriores ajustados', 'desdobramento ou grupamento ×{value} — preços anteriores ajustados'),
      mark_indeterminate: v('ajuste de strike não classificado (pode ser provento ou escala)', 'mudança no preço de exercício sem classificar (pode ser provento ou desdobramento)'),
      mark_earnings_estimated: v('resultado (data estimada)', 'divulgação de resultado (data estimada)'),
      mark_earnings_actual: v('resultado entregue à CVM', 'resultado informado à CVM'),
      mark_gap: v('abertura a {value}× do fechamento anterior, sem evento de strike', 'abertura a {value} vezes o fechamento anterior, sem mudança no preço de exercício'),
      shock_note: v('pregão dominante: {share} da variância de 30 pregões', 'um pregão domina a oscilação: {share} da variância dos últimos 30 pregões'),
      price_caption_count: v('{traded} de {total} pregões com negócio ({start} a {end})', '{traded} de {total} pregões com negociação (de {start} a {end})'),
      price_caption_scale: v('desdobramento/grupamento aplicado às barras anteriores ao evento', 'desdobramento ou grupamento aplicado aos preços anteriores ao evento'),
      price_caption_dividend: v('proventos não descontados', 'proventos não foram descontados dos preços'),
      price_aria: v('Fechamento de {ticker} nos últimos {total} pregões', 'Preço de fechamento de {ticker} nos últimos {total} pregões'),
      price_error: v('Fechamento indisponível', 'Não foi possível mostrar o preço de fechamento'),
      note_empty: v('prices: janela vazia', 'a janela de preços está vazia'),
      note_no_trade: v('{ticker} não negociou no mercado à vista (010) na janela', '{ticker} não teve negociação no mercado à vista nesta janela'),
      note_no_options: v('{ticker} não tem opção na janela: o B2 não detecta desdobramento nem provento deste papel, e um salto pode ser evento sem ajuste', '{ticker} não tem opções nesta janela: o sistema não detecta desdobramentos nem proventos deste papel, e uma variação brusca pode ser um evento sem ajuste'),
    },
  ),

  search: e(
    'Busca', 'Busca de papel',
    'Filtro por ticker ou nome da empresa.',
    'Encontre um papel pelo código ou pelo nome.',
    undefined,
    {
      label: v('Buscar papel', 'Buscar papel'),
      placeholder: v('ticker ou nome (ex.: PETR4)', 'código ou nome (ex.: PETR4)'),
      aria: v('Buscar papel por ticker ou nome', 'Buscar papel por código ou nome'),
      empty: v('nenhum papel para “{q}”', 'nenhum papel para “{q}”'),
      showing: v('mostrando {returned} de {total} papéis', 'mostrando {returned} de {total} papéis'),
      total_one: v('{total} papel', '{total} papel'),
      total_many: v('{total} papéis', '{total} papéis'),
      in_universe: v('no universo', 'no universo'),
      out_universe: v('fora do universo', 'fora do universo'),
      with_options: v('com opções', 'com opções'),
      without_options: v('sem opções', 'sem opções'),
      region_with_options: v('Papéis com opções', 'Papéis com negociação de opções nesta sessão'),
      region_without_options: v('Papéis sem opções', 'Papéis sem negociação de opções nesta sessão'),
    },
  ),

  glossary: e(
    'Verbete', 'Verbete do glossário',
    'Definição completa, com fonte única no glossário.',
    'Explicação completa do termo.',
    undefined,
    {
      eyebrow: v('verbete', 'verbete'),
      source: v('glossário: {title}', 'glossário: {title}'),
      panel: v('Ficha do verbete', 'Ficha de explicação'),
      panel_label: v('no painel', 'no painel'),
      what: v('O que é', 'O que significa'),
      mistake: v('Erro comum', 'Erro comum'),
      loading: v('carregando verbete…', 'carregando explicação…'),
      error: v('Verbete indisponível', 'Explicação indisponível'),
      missing: v('esta métrica ainda não tem verbete no glossário.', 'esta métrica ainda não tem explicação no glossário.'),
    },
  ),
  dialog: e(
    'Diálogo', 'Janela',
    undefined, 'Janela aberta sobre a tela.',
    undefined,
    { close: v('Fechar', 'Fechar') },
  ),

  navigation: e(
    'Navegação', 'Navegação',
    undefined, 'Caminhos para as telas do aplicativo.',
    undefined,
    { primary: v('Navegação principal', 'Menu principal do aplicativo') },
  ),

  ruler: e(
    'Régua', 'Régua de posição',
    'Faixa visual onde o número se posiciona.',
    'Mostra onde o número fica dentro de uma faixa.',
    undefined,
    {
      position: v('Posição na faixa', 'Posição na faixa'),
      unavailable: v('fora da faixa', 'sem valor na faixa'),
      range: v('de {min} a {max}', 'de {min} a {max}'),
      outside: v('fora da faixa', 'fora da faixa'),
    },
  ),

  stamp: e(
    'Carimbo', 'Carimbo da sessão',
    'Data da sessão vinda de /api/health, nunca do relógio.',
    'Dia do pregão mostrado, vindo do arquivo.',
    undefined,
    {
      loading: v('carregando sessão…', 'carregando sessão…'),
      unavailable: v('sessão indisponível', 'sessão indisponível'),
      online: v('sessão {session}', 'pregão {session}'),
      offline: v('sessão {session} · offline', 'último pregão {session} · sem rede'),
    },
  ),
  offline: e(
    'Sem rede', 'Sem rede',
    'Leituras em cache continuam disponíveis.',
    'Você está sem rede; dados já vistos continuam disponíveis.',
    undefined,
    {
      banner: v('sem rede — última sessão {session}', 'sem rede — último pregão {session}'),
    },
  ),

  learn: e(
    'Aprender', 'Aprender',
    'Verbetes do glossário em ordem por nível.',
    'Explicações dos termos usados no painel.',
    undefined,
    {
      intro: v('Toque num verbete para abrir a ficha. Vários podem ficar abertos.', 'Toque numa explicação para abrir a ficha. Várias podem ficar abertas.'),
      level: v('nível {n}', 'nível {n}'),
    },
  ),

  quantity_contract: e(
    '100 ações = 1 contrato', 'Cada contrato corresponde a 100 ações',
    'Rótulo de quantidade: a quantidade informada é em ações.',
    'A quantidade informada é contada em ações.',
  ),
  calibration_note: e(
    'A taxa usa apenas acertos e erros; “sem dados” fica separado.',
    'A taxa usa apenas acertos e erros; “sem dados” fica separado.',
    undefined,
    'A taxa de acerto só aparece quando existe resultado para comparar.',
  ),
  descriptive_note: e(
    'Leitura descritiva da sessão. Nenhum número aqui é previsão.',
    'Leitura descritiva da sessão. Nenhum número aqui é previsão.',
    undefined,
    'Os números descrevem o que aconteceu; não prevêem o que vai acontecer.',
  ),

  rv_shock: e(
    'Pregão dominante', 'Pregão dominante na volatilidade',
    'Um pregão que responde por boa parte da volatilidade recente.',
    'Um dia explica grande parte da oscilação recente.',
    'pregao-dominante-na-rv',
    {
      marker: v('†', '†', undefined, 'Marca os papéis cuja oscilação recente vem de um só pregão.'),
      banner: v(
        'Em {count} de {total} papéis com métrica, um único pregão responde por metade ou mais da oscilação dos últimos 30 pregões. Nesses papéis (marcados com †) a volatilidade realizada, o prêmio de volatilidade e os desvios-padrão do prêmio refletem esse dia: o número é real, mas não descreve o regime típico.',
        'Em {count} de {total} papéis com número, um único pregão explica metade ou mais da oscilação dos últimos 30 pregões. Nesses papéis (marcados com †) a oscilação observada, o prêmio de volatilidade e os desvios do prêmio vêm desse dia: o número é real, mas não mostra o comportamento habitual.',
      ),
      help: v(
        '{share} de tudo o que o papel oscilou nos últimos 30 pregões veio de um só dia ({date}). A volatilidade realizada, o prêmio de volatilidade e os desvios-padrão do prêmio refletem esse dia: o número é real, mas não descreve o regime típico do papel.',
        '{share} da oscilação do papel nos últimos 30 pregões veio de um só dia ({date}). A oscilação observada, o prêmio de volatilidade e os desvios do prêmio vêm desse dia: o número é real, mas não mostra o comportamento habitual do papel.',
      ),
      what: v('O que é isso', 'O que isso significa'),
    },
  ),
} satisfies Record<string, PresentationEntry>

export type PresentationKey = keyof typeof PRESENTATION

function copyOf(
  key: PresentationKey,
  mode: LanguageMode,
  variant?: string,
  params?: CopyParams,
): PresentationCopy {
  return resolve(PRESENTATION[key], mode, variant, params)
}

/** Código desconhecido continua explícito: o rótulo é o próprio código. */
function copyOrFallback(
  key: PresentationKey,
  variant: string | undefined,
  mode: LanguageMode,
  fallback: string,
): PresentationCopy {
  const entry = PRESENTATION[key]
  if (variant && entry.variants?.[variant]) return resolve(entry, mode, variant)
  return { label: fallback }
}

export interface PresentationApi {
  copy(key: PresentationKey, variant?: string, params?: CopyParams): PresentationCopy
  flag(code: string): PresentationCopy
  exclusion(code: string): PresentationCopy
  side(code: string): string
  style(code: string): string
  decisionType(code: string): PresentationCopy
  predictionStatus(code: string, context: 'journal' | 'position'): PresentationCopy
  voidReason(code: string): PresentationCopy
  positionKind(code: string): PresentationCopy
  positionStatus(code: string): string
  legType(type: string, optionSide?: string | null): string
  metric(field: string): PresentationKey | null
  /** Rótulo único de métrica: resolve chave + variante sem mapa local na tela.
   *  Código desconhecido continua visível como contrato. */
  metricLabel(field: string): string
  error(code: string): PresentationCopy
}

export function usePresentation(): PresentationApi {
  const { mode } = useLanguage()
  return useMemo<PresentationApi>(() => ({
    copy: (key, variant, params) => copyOf(key, mode, variant, params),
    flag: (code) => copyOrFallback('flag', code, mode, code),
    exclusion: (code) => copyOrFallback('exclusion', code, mode, code),
    side: (code) => copyOrFallback('option_side', code, mode, code).label,
    style: (code) => copyOrFallback('option_style', code, mode, code).label,
    decisionType: (code) => copyOrFallback('decision_type', code, mode, code),
    predictionStatus: (code, context) => copyOrFallback(
      context === 'position' ? 'prediction_status_position' : 'prediction_status_journal',
      code,
      mode,
      code,
    ),
    voidReason: (code) => copyOrFallback('void', code, mode, code),
    positionKind: (code) => copyOrFallback('position_kind', code, mode, code),
    positionStatus: (code) => copyOrFallback('position_status', code, mode, code).label,
    legType: (type, optionSide) => {
      const code = optionSide === 'C' ? 'call' : optionSide === 'P' ? 'put' : type
      return copyOrFallback('leg_type', code, mode, type).label
    },
    metric: (field) => presentationKeyForField(field),
    metricLabel: (field) => {
      const key = presentationKeyForField(field)
      if (!key) return field
      // Mesmas variantes dos formulários: spot→form e IV/VRP→option.
      const variant = field === 'spot'
        ? 'form'
        : field === 'iv_atm_30d' || field === 'vrp'
          ? 'option'
          : undefined
      return copyOf(key, mode, variant).label
    },
    error: (code) => copyOrFallback('error', code, mode, code),
  }), [mode])
}

export function useCopy(
  key: PresentationKey,
  variant?: string,
  params?: CopyParams,
): PresentationCopy {
  const { mode } = useLanguage()
  return useMemo(() => copyOf(key, mode, variant, params), [key, mode, variant, params])
}
