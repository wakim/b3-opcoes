import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, createFileRoute } from '@tanstack/react-router'
import { apiGet, type SeriesDetail } from '../lib/api'
import { brl, fmt } from '../lib/format'
import { ErrorState, Loading } from '../components/State'
import { GlossaryMetric, Metric, MetricGrid } from '../components/Metric'
import { Ruler } from '../components/Ruler'
import { LineChart } from '../components/LineChart'
import { MetricModal } from '../components/MetricModal'
import type { ModalTarget, OpenMetric } from '../components/metricKeys'

export const Route = createFileRoute('/s/$ticker/$maturity')({
  component: SeriesPage,
})

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

function SeriesPage() {
  const { ticker, maturity } = Route.useParams()
  const [view, setView] = useState<'price' | 'iv'>('price')
  const [modal, setModal] = useState<ModalTarget | null>(null)
  const openMetric: OpenMetric = (field, label, value) => setModal({ field, label, value })

  const series = useQuery({
    queryKey: ['series', ticker, maturity],
    queryFn: ({ signal }) => apiGet<SeriesDetail>(`/api/s/${ticker}/${maturity}`, signal),
  })

  if (series.isLoading || !series.data) return <Loading />
  if (series.isError) return <ErrorState error={series.error} />

  const data = series.data.data
  const history = data.history
  const labels = history.map((point) => point.date.slice(5))

  return (
    <>
      <Link className="back lg:text-label" to="/t/$ticker" params={{ ticker }}>
        <span aria-hidden="true">‹</span> {ticker}
      </Link>
      <h1 className="screen-title lg:text-screen-title lg:leading-title">Série</h1>

      <dl className="identity">
        <div><dt className="lg:text-label">Contrato</dt><dd className="num lg:text-detail">{data.ticker}</dd></div>
        <div><dt className="lg:text-label">Lado / estilo</dt><dd className="lg:text-detail">{sideLabel(data.side)} / {styleLabel(data.style)}</dd></div>
        <div><dt className="lg:text-label">Preço de exercício</dt><dd className="num lg:text-detail">{brl(data.strike_brl)}</dd></div>
        <div><dt className="lg:text-label">Vencimento</dt><dd className="num lg:text-detail">{data.maturity}</dd></div>
        <div><dt className="lg:text-label">Perna</dt><dd className="num lg:text-detail">{data.leg_id}</dd></div>
      </dl>

      <h2 className="block-title lg:text-block-title lg:leading-title">Último ponto · {data.last.date}</h2>
      <MetricGrid>
        <Metric label="Data" value={data.last.date} />
        <GlossaryMetric
          field="mid_brl"
          label="Preço"
          hint="Preço usado na volatilidade implícita: o fechamento do negócio, não o meio do book."
          value={brl(data.last.mid_brl ?? data.last.close_brl)}
          onOpen={openMetric}
          emphasis
        />
        <GlossaryMetric
          field="iv_pct"
          label="Volatilidade implícita"
          hint="Volatilidade que o preço da opção está pedindo, ao ano."
          value={fmt(data.last.iv_pct)}
          onOpen={openMetric}
        />
        <GlossaryMetric
          field="delta"
          label="Delta"
          hint="Quanto o prêmio muda para cada R$ 1 de variação da ação."
          value={fmt(data.last.delta, 3)}
          onOpen={openMetric}
          note={
            <Ruler
              value={data.last.delta}
              min={-1}
              max={1}
              ticks={[-1, 0, 1]}
              format={(v) => fmt(v, 1)}
              describe="Delta"
            />
          }
        />
        <GlossaryMetric
          field="gamma"
          label="Gamma"
          hint="Taxa de variação do delta."
          value={fmt(data.gamma, 4)}
          onOpen={openMetric}
        />
        <GlossaryMetric
          field="theta_brl"
          label="Theta (por dia)"
          hint="Quanto o prêmio perde por dia útil, só pela passagem do tempo."
          value={brl(data.theta_brl)}
          onOpen={openMetric}
        />
        <GlossaryMetric
          field="vega_brl"
          label="Vega (por ponto de vol.)"
          hint="Quanto o prêmio muda para cada 1 ponto de volatilidade implícita."
          value={brl(data.vega_brl)}
          onOpen={openMetric}
        />
      </MetricGrid>

      <div className="block-head">
        <h2 className="block-title lg:text-block-title lg:leading-title">Histórico</h2>
        <div className="selector" role="group" aria-label="Série do gráfico">
          <button
            type="button"
            className={`${view === 'price' ? 'active' : ''} lg:text-control`.trim()}
            aria-pressed={view === 'price'}
            onClick={() => setView('price')}
          >
            Preço
          </button>
          <button
            type="button"
            className={`${view === 'iv' ? 'active' : ''} lg:text-control`.trim()}
            aria-pressed={view === 'iv'}
            onClick={() => setView('iv')}
          >
            Volatilidade
          </button>
        </div>
      </div>
      <LineChart
        labels={labels}
        series={[
          {
            label: view === 'price' ? 'Preço' : 'Volatilidade implícita',
            values: history.map((point) => (view === 'price' ? point.close_brl : point.iv_pct)),
          },
        ]}
        formatValue={(v) => (view === 'price' ? brl(v) : fmt(v, 1))}
        ariaLabel={`Histórico de ${view === 'price' ? 'preço' : 'volatilidade implícita'} de ${data.ticker}`}
        showPoints={false}
      />
      {data.flags.length > 0 && <p className="note lg:text-label">flags: {data.flags.join(' · ')}</p>}

      <h2 className="block-title lg:text-block-title lg:leading-title">Últimos pregões</h2>
      <div className="table-wrap">
        <table className="screen-table lg:text-table lg:leading-table">
          <thead>
            <tr>
              <th className="lg:text-table-head">Data</th>
              <th className="lg:text-table-head">Fechamento</th>
              <th className="lg:text-table-head">Volatilidade implícita</th>
              <th className="lg:text-table-head">Delta</th>
            </tr>
          </thead>
          <tbody>
            {history.slice(-8).reverse().map((point) => (
              <tr key={point.date}>
                <td className="num">{point.date}</td>
                <td>{brl(point.close_brl)}</td>
                <td>{fmt(point.iv_pct)}</td>
                <td>{fmt(point.delta, 3)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <MetricModal target={modal} onClose={() => setModal(null)} />
    </>
  )
}
