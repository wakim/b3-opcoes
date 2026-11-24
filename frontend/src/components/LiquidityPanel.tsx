import { useMemo, useState, type KeyboardEvent } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { apiGet, type LiquidData, type ScreenRow } from '../lib/api'
import { brl, brlCompact, dateBr, fmt, sharePct } from '../lib/format'
import { ErrorState, Loading } from './State'

function sideLabel(side: string): string {
  const code = side.trim().toUpperCase()
  if (code.startsWith('C')) return 'Call'
  if (code.startsWith('P')) return 'Put'
  return side
}

function styleLabel(style: string | null): string {
  if (!style) return '—'
  const code = style.trim().toUpperCase()
  if (code === 'E') return 'Europeia'
  if (code === 'A') return 'Americana'
  return style
}

function spreadLabel(spreadPct: number | null, spreadBrl: number | null): string {
  return spreadPct != null ? `${fmt(spreadPct, 1)}%` : brl(spreadBrl)
}

/** Descoberta descritiva: liquidez do papel e séries mais negociadas da sessão. */
export function LiquidityPanel({ rows }: { rows: ScreenRow[] }) {
  const [tab, setTab] = useState<'papers' | 'series'>('papers')
  const [showAll, setShowAll] = useState(false)
  const navigate = useNavigate()

  const ranked = useMemo(
    () =>
      rows
        .filter((row) => row.status === 'ok' && row.n_liquid_series > 0)
        .sort(
          (a, b) =>
            b.n_liquid_series - a.n_liquid_series ||
            b.n_expiries - a.n_expiries ||
            b.n_series - a.n_series ||
            a.underlying.localeCompare(b.underlying),
        ),
    [rows],
  )
  const visiblePapers = showAll ? ranked : ranked.slice(0, 10)

  const liquid = useQuery({
    queryKey: ['liquid', 'top-volume'],
    queryFn: ({ signal }) => apiGet<LiquidData>('/api/liquid?limit=20', signal),
    enabled: tab === 'series',
    staleTime: 60_000,
  })

  const openTicker = (ticker: string) => {
    void navigate({ to: '/t/$ticker', params: { ticker } })
  }
  const openSeries = (ticker: string, maturity: string) => {
    void navigate({ to: '/s/$ticker/$maturity', params: { ticker, maturity } })
  }
  const rowKeyDown = (action: () => void) => (event: KeyboardEvent<HTMLTableRowElement>) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      action()
    }
  }

  return (
    <section className="liquidity" aria-labelledby="liquidity-title">
      <div className="block-head">
        <h2 className="block-title lg:text-block-title lg:leading-title" id="liquidity-title">
          Liquidez <span className="block-count lg:text-label">{ranked.length} papéis com série líquida</span>
        </h2>
        <div className="selector" role="group" aria-label="Tipo de liquidez">
          <button
            type="button"
            className={`${tab === 'papers' ? 'active' : ''} lg:text-control`.trim()}
            aria-pressed={tab === 'papers'}
            onClick={() => setTab('papers')}
          >
            Papéis
          </button>
          <button
            type="button"
            className={`${tab === 'series' ? 'active' : ''} lg:text-control`.trim()}
            aria-pressed={tab === 'series'}
            onClick={() => setTab('series')}
          >
            Séries mais negociadas
          </button>
        </div>
      </div>
      <p className="chain-note lg:text-label lg:leading-body">ordenado por liquidez; não é recomendação</p>

      {tab === 'papers' ? (
        ranked.length === 0 ? (
          <p className="chart-empty lg:text-detail">nenhum papel com série líquida nesta sessão</p>
        ) : (
          <>
            <div
              className="table-wrap table-wrap--scroll"
              id="liquidity-papers"
              role="region"
              aria-label="Papéis com mais séries líquidas"
              tabIndex={0}
            >
              <table className="screen-table liquidity-table lg:text-table lg:leading-table">
                <thead>
                  <tr>
                    <th className="lg:text-table-head">Papel</th>
                    <th className="lg:text-table-head">Fechamento</th>
                    <th className="lg:text-table-head">Séries líquidas</th>
                    <th className="lg:text-table-head">Vencimentos</th>
                    <th className="lg:text-table-head">Volatilidade implícita 30d</th>
                    <th className="lg:text-table-head" title="VRP = volatilidade implícita − realizada">Prêmio de volatilidade</th>
                  </tr>
                </thead>
                <tbody>
                  {visiblePapers.map((row) => (
                    <tr
                      key={row.underlying}
                      role="link"
                      tabIndex={0}
                      aria-label={`Abrir ${row.underlying}`}
                      onClick={() => openTicker(row.underlying)}
                      onKeyDown={rowKeyDown(() => openTicker(row.underlying))}
                    >
                      <td className="num lg:leading-table">{row.underlying}</td>
                      <td className="lg:leading-table">{brl(row.spot_brl)}</td>
                      <td className="lg:leading-table">{row.n_liquid_series}</td>
                      <td className="lg:leading-table">{row.n_expiries}</td>
                      <td className="lg:leading-table">{fmt(row.iv_atm_30d)}</td>
                      <td className="lg:leading-table">
                        {fmt(row.vrp)}
                        {row.vrp != null && row.flags.includes('rv_shock_day') && (
                          <span
                            className="shock-mark"
                            title={`${sharePct(row.rv_shock_share_pct)} da variância de 30 pregões veio de ${dateBr(row.rv_shock_date)}`}
                            aria-label={`realizada dominada por ${dateBr(row.rv_shock_date)}`}
                          >
                            †
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {ranked.length > 10 && (
              <button
                type="button"
                className="liquidity__more lg:text-control"
                aria-expanded={showAll}
                aria-controls="liquidity-papers"
                onClick={() => setShowAll((value) => !value)}
              >
                {showAll ? 'Mostrar top 10' : `Ver todos os ${ranked.length}`}
              </button>
            )}
          </>
        )
      ) : liquid.isLoading ? (
        <Loading label="carregando séries…" />
      ) : liquid.isError ? (
        <ErrorState error={liquid.error} />
      ) : !liquid.data ? (
        <Loading label="carregando séries…" />
      ) : liquid.data.data.rows.length === 0 ? (
        <p className="chart-empty lg:text-detail">nenhuma série líquida nesta sessão</p>
      ) : (
        <>
          {liquid.data.meta?.truncated && (
            <p className="notice lg:text-label" role="status">
              Mostrando {liquid.data.meta.returned} de {liquid.data.meta.total} séries por volume.
            </p>
          )}
          <div
            className="table-wrap table-wrap--scroll"
            role="region"
            aria-label="Séries líquidas mais negociadas"
            tabIndex={0}
          >
            <table className="screen-table liquidity-table liquidity-table--series lg:text-table lg:leading-table">
              <thead>
                <tr>
                  <th className="lg:text-table-head">Série</th>
                  <th className="lg:text-table-head">Papel</th>
                  <th className="lg:text-table-head">Lado / estilo</th>
                  <th className="lg:text-table-head">Strike</th>
                  <th className="lg:text-table-head">Vencimento</th>
                  <th className="lg:text-table-head" title="Volume financeiro da sessão, em reais">Volume</th>
                  <th className="lg:text-table-head">Negócios</th>
                  <th className="lg:text-table-head" title="Spread percentual do fechamento; reais quando não há percentual">Spread</th>
                  <th className="lg:text-table-head">Volatilidade implícita</th>
                  <th className="lg:text-table-head">Delta</th>
                  <th className="lg:text-table-head">Liquidez</th>
                </tr>
              </thead>
              <tbody>
                {liquid.data.data.rows.map((row) => (
                  <tr
                    key={`${row.ticker}:${row.maturity}`}
                    role="link"
                    tabIndex={0}
                    aria-label={`Abrir ${row.ticker}, vencimento ${row.maturity}`}
                    onClick={() => openSeries(row.ticker, row.maturity)}
                    onKeyDown={rowKeyDown(() => openSeries(row.ticker, row.maturity))}
                  >
                    <td className="num lg:leading-table">{row.ticker}</td>
                    <td className="num lg:leading-table">{row.underlying}</td>
                    <td className="lg:leading-table">{sideLabel(row.side)} / {styleLabel(row.style)}</td>
                    <td className="lg:leading-table">{brl(row.strike_brl)}</td>
                    <td className="num lg:leading-table">{dateBr(row.maturity)}</td>
                    <td className="lg:leading-table" title={`Volume financeiro: ${brl(row.volume_brl)}`}>{brlCompact(row.volume_brl)}</td>
                    <td className="lg:leading-table">{row.trades}</td>
                    <td className="lg:leading-table">{spreadLabel(row.spread_pct, row.spread_brl)}</td>
                    <td className="lg:leading-table">{fmt(row.iv_pct)}</td>
                    <td className="lg:leading-table">{fmt(row.delta, 3)}</td>
                    <td className="quiet lg:leading-table">{row.is_liquid ? 'líquida' : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  )
}
