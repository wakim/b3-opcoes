/** Contrato tipado da API do b3o — ÚNICO lugar com `fetch` no frontend.
 *
 * Escrito à mão a partir de /api/openapi.json + docs/contratos/onda-i.md e
 * onda-j.md: as rotas devolvem `dict` genérico, então a geração automática não
 * traria schema útil (ADR F5-stack). Se uma resposta mudar, muda aqui primeiro.
 *
 * Regra do projeto: o Python calcula, o frontend só formata o que vem daqui.
 */

export const API_BASE: string =
  (import.meta.env.VITE_API_BASE as string | undefined) ?? ''

export interface ApiErrorBody {
  code: string
  message: string
  hint: string
}

export interface Meta {
  total: number
  returned: number
  truncated: boolean
  /** Página atual (0-based) e próximo deslocamento; `null` na última página. */
  offset?: number
  next_offset?: number | null
  lists?: Record<string, { total: number; returned: number; truncated: boolean }>
  note?: string
}

export interface Envelope<T> {
  as_of_session: string | null
  model_version: number | null
  data: T
  meta?: Meta
}

export class ApiError extends Error {
  readonly code: string
  readonly hint: string
  readonly status: number

  constructor(status: number, body: ApiErrorBody) {
    super(body.message)
    this.name = 'ApiError'
    this.code = body.code
    this.hint = body.hint
    this.status = status
  }

  get isNotFound(): boolean {
    return this.status === 404 || this.code === 'not_found'
  }
}

async function request<T>(path: string, init: RequestInit): Promise<Envelope<T>> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { Accept: 'application/json', ...(init.headers ?? {}) },
    })
  } catch (cause) {
    throw new ApiError(0, {
      code: 'network_error',
      message: cause instanceof Error ? cause.message : 'falha de rede',
      hint: 'confira se o servidor b3o está no ar e se a origem da API está correta (VITE_API_BASE)',
    })
  }

  let body: unknown
  try {
    body = await response.json()
  } catch {
    throw new ApiError(response.status, {
      code: 'bad_response',
      message: `resposta inválida de ${path}`,
      hint: 'a rota devolveu um corpo que não é JSON; confira o log do container',
    })
  }

  if (typeof body !== 'object' || body === null) {
    throw new ApiError(response.status, {
      code: 'bad_response',
      message: `corpo inesperado de ${path}`,
      hint: 'esperado um objeto JSON (envelope ou erro)',
    })
  }
  const obj = body as Record<string, unknown>
  const error = obj.error as ApiErrorBody | undefined
  if (!response.ok || error) {
    throw new ApiError(response.status, error ?? {
      code: 'http_error',
      message: `HTTP ${response.status} em ${path}`,
      hint: 'confira a rota e os parâmetros em /api/openapi.json',
    })
  }
  if (obj.meta && (obj.meta as Meta).truncated) {
    console.warn(`[b3o] resposta truncada em ${path}:`, (obj.meta as Meta).total)
  }
  return body as Envelope<T>
}

export function apiGet<T>(path: string, signal?: AbortSignal): Promise<Envelope<T>> {
  return request<T>(path, { method: 'GET', signal })
}

/** Cálculo de estrutura (POST /api/structure) — não grava nada e não exige token. */
export function apiPost<T>(path: string, body: unknown): Promise<Envelope<T>> {
  return request<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export const APP_TOKEN_STORAGE_KEY = 'b3o:app-token'

/** Escrita do journal (F6): injeta o token salvo neste navegador, sem expô-lo. */
export function apiWrite<T>(path: string, body: unknown): Promise<Envelope<T>> {
  let token: string
  try {
    token = (typeof localStorage === 'undefined' ? '' : localStorage.getItem(APP_TOKEN_STORAGE_KEY))?.trim() ?? ''
  } catch {
    token = ''
  }
  if (!token) {
    throw new ApiError(401, {
      code: 'app_token_required',
      message: 'token do aplicativo ausente',
      hint: 'digite o token na Carteira; ele fica salvo apenas neste navegador',
    })
  }
  return request<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-App-Token': token },
    body: JSON.stringify(body),
  })
}

// ---------------------------------------------------------------------------
// Tipos de domínio (espelham os dataclasses/serializações do backend)
// ---------------------------------------------------------------------------

export interface Health {
  last_session: string | null
  expected_last_session: string | null
  /** Pregões de atraso contra `expected_last_session`; item de erro quando a fonte falha. */
  age_sessions: number | { code?: string; message?: string } | null
  lake_sessions: unknown
  model_version: number
  ingest_model_version: number
  model_version_mismatch: boolean
  model_version_source: string
  model_version_note: string | null
  universe: unknown
  rate_source: unknown
  oi_unavailable: unknown
  earnings_coverage: unknown
  last_error: unknown
  as_of: string | null
}

export interface ScreenSummary {
  total: number
  ok: number
  excluded: number
  by_reason: Record<string, number>
  iv_median: number | null
  iv_count: number
  vrp_median: number | null
  vrp_count: number
  rate_sources: string[]
  outside_universe: string[]
  earnings_coverage_start: string | null
  /** Papéis `ok` com a flag `rv_shock_day` (ADR-035). */
  rv_shock_count: number
  rv_shock_dates: { date: string; count: number }[]
}

export interface ScreenRow {
  underlying: string
  status: 'ok' | 'excluded'
  exclusion: string | null
  as_of_session: string
  model_version: number
  spot_brl: number | null
  iv_atm_30d: number | null
  iv_rank_252: number | null
  iv_pct_252: number | null
  rv20: number | null
  rv30: number | null
  rv60: number | null
  vrp: number | null
  vrp_z_252: number | null
  rr25: number | null
  bfly25: number | null
  ts_slope_30_90: number | null
  exp_move_1s_30d: number | null
  implied_div_yield_30d: number | null
  forward_30d: number | null
  days_to_liquidate: number | null
  rate_source: string | null
  n_series: number
  n_liquid_series: number
  n_expiries: number
  earnings_next_date: string | null
  earnings_next_kind: string | null
  earnings_before_expiry: boolean
  flags: string[]
  /** Pregão que mais pesa na variância de 30 pregões; flag `rv_shock_day` a partir de 50%. */
  rv_shock_date: string | null
  rv_shock_share_pct: number | null
}

export interface ScreenData {
  session: string
  summary: ScreenSummary
  rows: ScreenRow[]
}

/** Busca todas as páginas de `/api/screen` (60 por vez) e devolve o universo inteiro.
 *
 * A API mantém o teto de 60 linhas por resposta; `meta.total` diz o tamanho do
 * universo e cada página traz `meta.next_offset`. A primeira chamada é
 * sequencial (para conhecer o total); as demais vão em paralelo.
 */
export async function fetchScreenAll(signal?: AbortSignal): Promise<Envelope<ScreenData>> {
  const limit = 60
  const first = await apiGet<ScreenData>(`/api/screen?max_rows=${limit}&offset=0`, signal)
  const total = first.meta?.total ?? first.data.rows.length
  const offsets: number[] = []
  for (let offset = limit; offset < total; offset += limit) offsets.push(offset)
  const pages = await Promise.all(
    offsets.map((offset) =>
      apiGet<ScreenData>(`/api/screen?max_rows=${limit}&offset=${offset}`, signal),
    ),
  )
  const rows = [...first.data.rows, ...pages.flatMap((page) => page.data.rows)]
  return {
    ...first,
    data: { ...first.data, rows },
    meta: {
      ...(first.meta ?? { total, returned: rows.length, truncated: false }),
      total,
      returned: rows.length,
      truncated: rows.length < total,
      offset: 0,
      next_offset: null,
    },
  }
}

export interface Term {
  maturity: string
  t: number
  iv_atm: number | null
  atm_strike_brl: number | null
  forward_brl: number | null
  forward_method: 'crossing' | 'ols' | 'none'
  exp_move_1s: number | null
  n_strikes: number
  n_liquid_strikes: number
}

export interface Smile {
  strike_brl: number
  iv_pct: number | null
  delta: number | null
  side: string
  is_liquid: boolean
}

export interface History {
  date: string
  iv_atm_30d: number | null
  rv30: number | null
  vrp: number | null
  vrp_z_252: number | null
  rr25: number | null
}

export interface Underlying {
  ticker: string
  in_universe: boolean
  summary: ScreenRow | null
  term: Term[]
  smile: Smile[]
  smile_maturity: string | null
  history: History[]
  notes: string[]
  counts: { term: number; smile: number; history: number }
}

export interface ChainRow {
  ticker: string
  side: string
  style: string | null
  maturity: string
  strike_brl: number
  t: number
  bid_brl: number | null
  ask_brl: number | null
  mid_brl: number | null
  close_brl: number | null
  iv_pct: number | null
  delta: number | null
  gamma: number | null
  theta_brl: number | null
  vega_brl: number | null
  spread_brl: number | null
  spread_pct: number | null
  is_liquid: boolean
  flags: string[]
}

export interface ChainData {
  ticker: string
  rows: ChainRow[]
  notes: string[]
}

export interface LiquidRow {
  underlying: string
  ticker: string
  side: string
  style: string | null
  strike_brl: number
  maturity: string
  /** Volume financeiro do lake, já em reais — nunca reconstituído no frontend. */
  volume_brl: number
  trades: number
  spread_brl: number | null
  spread_pct: number | null
  iv_pct: number | null
  delta: number | null
  is_liquid: boolean
}

export interface LiquidData {
  session: string
  rows: LiquidRow[]
  notes: string[]
}

export interface SeriesPoint {
  date: string
  close_brl: number | null
  mid_brl: number | null
  iv_pct: number | null
  delta: number | null
}

export interface SeriesDetail {
  ticker: string
  maturity: string
  side: string
  style: string | null
  strike_brl: number
  leg_id: string
  t: number
  is_liquid: boolean
  flags: string[]
  gamma: number | null
  theta_brl: number | null
  vega_brl: number | null
  last: SeriesPoint
  history: SeriesPoint[]
  counts: { history: number }
}

export interface StrikeEvent {
  date: string
  kind: string
  delta_strike: number | null
  factor: number | null
  n_series: number
  ambiguous: boolean
}

export interface EarningsEvent {
  date: string
  ref_period: string
  kind: 'actual' | 'estimated' | string
}

export interface Events {
  ticker: string
  maturities: string[]
  strike_events: StrikeEvent[]
  earnings: EarningsEvent[]
  next_earnings: EarningsEvent | null
  notes: string[]
  counts: { maturities: number; strike_events: number; earnings: number }
}

export interface Structure {
  underlying: string
  structure_version: number
  spot_brl: number
  forward_brl: number | null
  df: number | null
  t: number | null
  maturity: string | null
  legs: Array<Record<string, unknown>>
  net_premium_brl: number
  net_premium_low_brl: number | null
  net_premium_high_brl: number | null
  breakeven_brl: number[]
  max_loss_brl: number | null
  max_gain_brl: number | null
  capital_committed_brl: number | null
  capital_input_brl: number | null
  capital_over: boolean
  delta: number
  gamma: number
  theta_brl: number
  vega_brl: number
  payoff: Array<{ spot_brl: number; pnl_brl: number }>
  scenarios: Array<{
    spot_brl: number
    iv_shift_pts: number
    days_forward: number
    pnl_brl: number
  }> | null
  flags: string[]
  notes: string[]
}

/** Perna de entrada do builder (o backend resolve ticker → papel e vencimento pelo ISIN). */
export type LegKind = 'option' | 'stock'
export type LegSide = 'long' | 'short'

export interface StructureLegInput {
  kind: LegKind
  ticker: string
  side: LegSide
  /** Número de ações; 1 contrato padrão = 100. Nunca multiplicado por 100 aqui. */
  quantity: number
}

export interface StructureRequest {
  underlying?: string
  date?: string | null
  legs: StructureLegInput[]
  capital?: number
  scenarios?: boolean
}

// ---------------------------------------------------------------------------
// F6 — posições e carteira (docs/contratos/f6.md)
// ---------------------------------------------------------------------------

export interface PositionSummary {
  id: string
  underlying: string
  kind: string
  status: 'open' | 'closed' | string
  opened_session: string
  closed_session: string | null
  capital_committed_brl: number | null
  capital_limit_brl: number | null
  pnl_brl: number | null
  pnl_pct: number | null
  delta: number | null
  delta_exposure_brl: number | null
  gamma: number | null
  theta_brl: number | null
  vega_brl: number | null
  days_to_expiry: number | null
  maturity: string | null
  breakeven_brl: number[]
  flags: string[]
  notes: string | null
}

export interface PositionTotals {
  open_positions: number
  capital_committed_brl: number | null
  pnl_brl: number | null
  pnl_pct: number | null
  delta: number | null
  delta_exposure_brl: number | null
  gamma: number | null
  theta_brl: number | null
  vega_brl: number | null
  flags: string[]
}

export interface PositionsData {
  session: string
  positions: PositionSummary[]
  totals: PositionTotals
}

/** Lê todas as páginas da Carteira (60 por vez); totais continuam vindo do backend. */
export async function fetchPositionsAll(signal?: AbortSignal): Promise<Envelope<PositionsData>> {
  const limit = 60
  const byId = new Map<string, PositionSummary>()
  let duplicate = false
  let offset = 0
  let first: Envelope<PositionsData> | null = null
  for (let page = 0; page < 1000; page += 1) {
    const current = await apiGet<PositionsData>(
      `/api/positions?max_rows=${limit}&offset=${offset}`,
      signal,
    )
    first ??= current
    for (const position of current.data.positions) {
      if (byId.has(position.id)) duplicate = true
      byId.set(position.id, position)
    }
    const next = current.meta?.next_offset
    if (next == null || next <= offset) break
    offset = next
  }
  if (!first) throw new Error('Carteira vazia sem envelope')
  const positions = [...byId.values()]
  const total = first.meta?.total ?? positions.length
  const inconsistent = duplicate || positions.length !== total
  return {
    ...first,
    data: { ...first.data, positions },
    meta: {
      ...(first.meta ?? { total, returned: positions.length, truncated: false }),
      total,
      returned: positions.length,
      truncated: positions.length < total || inconsistent,
      note: inconsistent
        ? 'A carteira mudou durante a paginação; a lista pode estar incompleta. Recarregue para tentar novamente.'
        : first.meta?.note,
      offset: 0,
      next_offset: null,
    },
  }
}

export interface PositionLeg {
  id: string
  ticker: string
  side: LegSide | string
  /** O journal consolida `stock`, `call` ou `put`; contratos antigos dizem `option`. */
  type: string
  option_side?: 'C' | 'P' | string | null
  maturity: string | null
  qty: number
  entry_price_brl: number
  entry_strike_brl: number | null
  current_strike_brl: number | null
  entry_iv_pct: number | null
  entry_delta: number | null
}

export interface PositionMark {
  leg_id: string
  ticker: string
  mark_brl: number | null
  strike_brl: number | null
  iv_pct: number | null
  delta: number | null
  gamma: number | null
  theta_brl: number | null
  vega_brl: number | null
  spread_brl: number | null
  spread_pct: number | null
  one_sided: boolean
  flags: string[]
}

export interface PositionPrediction {
  metric: string
  operator: '<' | '<=' | '>' | '>=' | string
  value: number
  horizon_sessions: number
  evaluate_on: string
  status: 'pending' | 'hit' | 'miss' | 'void' | string
  observed: number | null
}

export interface PositionDetail extends PositionSummary {
  legs: PositionLeg[]
  marks: PositionMark[]
  breakeven_brl: number[]
  entry_iv_atm_30d: number | null
  current_iv_atm_30d: number | null
  prediction: PositionPrediction | null
  thesis_line: string | null
  payoff: Array<{ spot_brl: number; pnl_brl: number }>
}

export type PositionLegInput = StructureLegInput

export interface PositionPredictionInput {
  metric: string
  operator: '<' | '<=' | '>' | '>='
  value: number
  horizon_sessions: number
  evaluate_on: string
}

export interface PositionCreateRequest {
  /** UUID4 estável no rascunho: repetir o POST não duplica a posição. */
  client_position_id?: string
  underlying: string
  date?: string | null
  notes?: string | null
  capital_limit_brl?: number | null
  legs: PositionLegInput[]
  prediction?: PositionPredictionInput | null
}

export interface PositionCloseRequest {
  date?: string | null
}

export interface TickerRow {
  ticker: string
  corp_name: string
  in_universe: boolean
  has_options: boolean
}
export interface TickerData {
  q: string
  rows: TickerRow[]
}

export interface GlossaryEntry {
  key: string
  title: string
  level: number
}

export interface GlossaryDetail extends GlossaryEntry {
  body_md: string
}

export interface GlossaryData {
  entries: GlossaryEntry[]
}
