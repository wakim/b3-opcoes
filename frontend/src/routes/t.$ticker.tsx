import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, createFileRoute } from '@tanstack/react-router'
import { apiGet, type ChainData, type Underlying } from '../lib/api'
import { brl, fmt } from '../lib/format'
import { ErrorState, Loading } from '../components/State'
import { GlossaryMetric, MetricGrid } from '../components/Metric'
import { Ruler } from '../components/Ruler'
import { LineChart } from '../components/LineChart'
import { Chain } from '../components/Chain'
import { MetricSheet } from '../components/MetricSheet'
import type { OpenMetric, SheetTarget } from '../components/metricKeys'

export const Route = createFileRoute('/t/$ticker')({
  component: TickerPage,
})

function TickerPage() {
  const { ticker } = Route.useParams()
  const [pickedMaturity, setPickedMaturity] = useState<string | null>(null)
  const [filter, setFilter] = useState<'strict' | 'all'>('strict')
  const [sheet, setSheet] = useState<SheetTarget | null>(null)
  const openMetric: OpenMetric = (field, label, value) => setSheet({ field, label, value })

  const overview = useQuery({
    queryKey: ['underlying', ticker],
    queryFn: ({ signal }) => apiGet<Underlying>(`/api/t/${ticker}`, signal),
  })

  const term = overview.data?.data.term ?? []
  // O primeiro vencimento da curva é o da própria sessão (iv_expired, sem
  // cadeia líquida). O smile_maturity já vem no mesmo payload e é o recorte
  // que o backend considera legível — nenhum endpoint novo.
  const activeMaturity = pickedMaturity
    ?? overview.data?.data.smile_maturity
    ?? term[0]?.maturity
    ?? null

  const chain = useQuery({
    queryKey: ['chain', ticker, activeMaturity, filter],
    queryFn: ({ signal }) => {
      const params = new URLSearchParams({
        liquidity: filter,
        delta_min: filter === 'all' ? '0' : '0.25',
        delta_max: filter === 'all' ? '1' : '0.75',
        max_rows: '60',
      })
      if (activeMaturity) params.set('expiry', activeMaturity)
      return apiGet<ChainData>(`/api/t/${ticker}/chain?${params.toString()}`, signal)
    },
    enabled: Boolean(ticker),
  })

  if (overview.isLoading || !overview.data) return <Loading />
  if (overview.isError) return <ErrorState error={overview.error} />

  const data = overview.data.data
  const summary = data.summary
  const history = data.history
  const withIv = term.filter((point) => point.iv_atm != null)
  const firstWithIv = term.findIndex((point) => point.iv_atm != null)
  const lastWithIv = term.reduce((acc, point, index) => (point.iv_atm != null ? index : acc), -1)
  const termWindow = firstWithIv >= 0 ? term.slice(firstWithIv, lastWithIv + 1) : []
  const illiquidAtm = withIv.filter((point) => point.n_liquid_strikes === 0).length

  return (
    <>
      <Link className="back" to="/">
        <span aria-hidden="true">‹</span> Hoje
      </Link>
      <h1 className="screen-title">{ticker}</h1>
      <p className="quiet-line">
        {data.counts.term} vencimentos · {data.counts.smile} strikes · {data.counts.history} dias de histórico
      </p>

      <MetricGrid>
        <GlossaryMetric
          field="spot_brl"
          label="Fechamento"
          value={brl(summary?.spot_brl)}
          onOpen={openMetric}
        />
        <GlossaryMetric
          field="iv_atm_30d"
          label="IV ATM 30d"
          value={fmt(summary?.iv_atm_30d)}
          onOpen={openMetric}
          emphasis
        />
        <GlossaryMetric
          field="vrp"
          label="VRP"
          value={fmt(summary?.vrp)}
          onOpen={openMetric}
        />
        <GlossaryMetric
          field="iv_rank_252"
          label="IV rank"
          value={fmt(summary?.iv_rank_252, 0)}
          onOpen={openMetric}
          note={
            <Ruler
              value={summary?.iv_rank_252 ?? null}
              min={0}
              max={100}
              ticks={[0, 50, 100]}
              format={(v) => fmt(v, 0)}
              describe="IV rank"
            />
          }
        />
        <GlossaryMetric
          field="vrp_z_252"
          label="z-score do VRP"
          value={fmt(summary?.vrp_z_252)}
          onOpen={openMetric}
          note={
            <Ruler
              value={summary?.vrp_z_252 ?? null}
              min={-3}
              max={3}
              ticks={[-3, 0, 3]}
              format={(v) => fmt(v, 1)}
              describe="z-score do VRP"
            />
          }
        />
        <GlossaryMetric
          field="days_to_liquidate"
          label="Dias para liquidar"
          value={fmt(summary?.days_to_liquidate)}
          onOpen={openMetric}
          note={
            <Ruler
              value={summary?.days_to_liquidate ?? null}
              min={0}
              max={10}
              ticks={[0, 5, 10]}
              format={(v) => fmt(v, 0)}
              describe="Dias para liquidar"
            />
          }
        />
      </MetricGrid>

      {data.notes.map((note) => (
        <p className="note" key={note}>{note}</p>
      ))}

      <h2 className="block-title">Estrutura a termo</h2>
      {withIv.length >= 2 ? (
        <LineChart
          labels={termWindow.map((point) => point.maturity.slice(5))}
          series={[{
            label: 'IV ATM',
            values: termWindow.map((point) => point.iv_atm),
            hollow: termWindow.map((point) => point.n_liquid_strikes === 0),
          }]}
          formatValue={(v) => fmt(v, 1)}
          ariaLabel={`Estrutura a termo de ${ticker}: IV por vencimento`}
          caption={`IV em ${withIv.length} de ${term.length} vencimentos${illiquidAtm > 0 ? ` · ${illiquidAtm} com ATM sem série líquida` : ''}`}
        />
      ) : (
        <p className="chart-empty">IV indisponível nos vencimentos desta sessão</p>
      )}

      <h2 className="block-title">IV vs realizada</h2>
      <LineChart
        compact
        labels={history.map((point) => point.date.slice(5))}
        series={[
          { label: 'IV 30d', values: history.map((point) => point.iv_atm_30d) },
          { label: 'RV30', values: history.map((point) => point.rv30), dashed: true },
        ]}
        formatValue={(v) => fmt(v, 1)}
        ariaLabel={`Volatilidade implícita e realizada de ${ticker}`}
        showPoints={false}
      />

      <h2 className="block-title">Vencimento</h2>
      <div className="selector selector--scroll" role="group" aria-label="Vencimento">
        {term.map((point) => (
          <button
            key={point.maturity}
            type="button"
            className={activeMaturity === point.maturity ? 'active' : undefined}
            aria-pressed={activeMaturity === point.maturity}
            onClick={() => setPickedMaturity(point.maturity)}
          >
            {point.maturity.slice(5)}
          </button>
        ))}
      </div>

      <div className="block-head">
        <h2 className="block-title">Cadeia</h2>
        <div className="selector" role="group" aria-label="Filtro da cadeia">
          <button
            type="button"
            className={filter === 'strict' ? 'active' : undefined}
            aria-pressed={filter === 'strict'}
            onClick={() => setFilter('strict')}
          >
            Líquidas
          </button>
          <button
            type="button"
            className={filter === 'all' ? 'active' : undefined}
            aria-pressed={filter === 'all'}
            onClick={() => setFilter('all')}
          >
            Todas
          </button>
        </div>
      </div>
      {chain.isLoading && <Loading label="carregando cadeia…" />}
      {chain.isError && <ErrorState error={chain.error} />}
      {chain.data && (
        <>
          {chain.data.meta?.truncated && (
            <p className="notice" role="status">
              A cadeia foi cortada em {chain.data.meta.returned} de {chain.data.meta.total} linhas.
            </p>
          )}
          <Chain data={chain.data.data} maturity={activeMaturity} onMetric={openMetric} />
        </>
      )}

      <MetricSheet target={sheet} onClose={() => setSheet(null)} />
    </>
  )
}
