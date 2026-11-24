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

/** Cálculo de estrutura (POST /api/structure) — não grava nada (F6 é que grava). */
export function apiPost<T>(path: string, body: unknown): Promise<Envelope<T>> {
  return request<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

// ---------------------------------------------------------------------------
// Tipos de domínio (espelham os dataclasses/serializações do backend)
// ---------------------------------------------------------------------------

export interface Health {
  last_session: string | null
  expected_last_session: string | null
  age_sessions: unknown
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
