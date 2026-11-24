/** Campo da API → slug do verbete em `docs/glossario.md`.
 *
 * O slug é o que `b3o.api.glossary.slugify` produz (minúsculas, sem acento,
 * pontuação → `-`). Se a resposta de `/api/glossary/{key}` der 404, a chave
 * está errada: corrija aqui, não no componente. Nenhum número nasce aqui.
 */
export type OpenMetric = (field: string, label: string, value: string) => void

export interface ModalTarget {
  field: string
  label: string
  value: string
}

const KEYS: Record<string, string> = {
  // Nível 1 — preço e prêmio
  spot_brl: 'preco-spot-fechamento',
  close_brl: 'premio-bid-e-ask',
  mid_brl: 'premio-bid-e-ask',
  bid_brl: 'premio-bid-e-ask',
  ask_brl: 'premio-bid-e-ask',
  spread_brl: 'spread',
  spread_pct: 'spread',
  strike_brl: 'strike-e-vencimento',
  entry_strike_brl: 'strike-e-vencimento',
  current_strike_brl: 'strike-e-vencimento',
  maturity: 'strike-e-vencimento',
  iv_pct: 'volatilidade-implicita-iv',
  entry_iv_pct: 'volatilidade-implicita-iv',
  entry_iv_atm_30d: 'iv-atm-30d',
  current_iv_atm_30d: 'iv-atm-30d',
  exp_move_1s: 'expected-move',
  exp_move_1s_30d: 'expected-move',
  delta: 'delta',
  net_premium_brl: 'premio-liquido-de-uma-estrutura',
  payoff: 'payoff-no-vencimento',
  breakeven_brl: 'breakeven',
  legs: 'perna-de-uma-posicao',

  // Nível 2 — contexto
  iv_atm_30d: 'iv-atm-30d',
  iv_atm: 'iv-atm-30d',
  iv_rank_252: 'iv-rank-e-percentil',
  iv_pct_252: 'iv-rank-e-percentil',
  rv20: 'volatilidade-realizada-rv',
  rv30: 'volatilidade-realizada-rv',
  rv60: 'volatilidade-realizada-rv',
  rv_shock_share_pct: 'pregao-dominante-na-rv',
  rv_shock_date: 'pregao-dominante-na-rv',
  vrp: 'vrp',
  vrp_z_252: 'z-score-do-vrp',
  rr25: 'skew-e-risk-reversal',
  bfly25: 'butterfly',
  ts_slope_30_90: 'estrutura-a-termo',
  max_loss_brl: 'perda-maxima-ganho-maximo-e-capital-comprometido',
  max_gain_brl: 'perda-maxima-ganho-maximo-e-capital-comprometido',
  capital_committed_brl: 'perda-maxima-ganho-maximo-e-capital-comprometido',
  capital_limit_brl: 'limite-de-capital',
  pnl_brl: 'p-l-da-posicao',
  pnl_pct: 'p-l-da-posicao',
  delta_exposure_brl: 'exposicao-delta-em-reais',
  days_to_expiry: 'sessoes-ate-o-vencimento',
  scenarios: 'cenario-spot-iv-dias',
  volume: 'volume-e-open-interest',
  days_to_liquidate: 'dias-para-liquidar',

  // Nível 3 — gregas e modelo
  theta_brl: 'theta',
  vega_brl: 'vega',
  gamma: 'gamma',
  forward_brl: 'forward-implicito',
  forward_30d: 'forward-implicito',
  df: 'fator-de-desconto-df',
  implied_div_yield_30d: 'dividendo-implicito',
  style: 'estilo-americana-europeia',
}

export function glossaryKey(field: string): string | null {
  return KEYS[field] ?? null
}
