import { useQuery } from '@tanstack/react-query'
import { Link, createFileRoute } from '@tanstack/react-router'
import { apiGet, type SeriesDetail } from '../lib/api'
import { brl, fmt } from '../lib/format'
import { ErrorBox, Loading } from '../lib/ui'

export const Route = createFileRoute('/s/$ticker/$maturity')({
  component: SeriesPage,
})

function SeriesPage() {
  const { ticker, maturity } = Route.useParams()
  const series = useQuery({
    queryKey: ['series', ticker, maturity],
    queryFn: ({ signal }) =>
      apiGet<SeriesDetail>(`/api/s/${ticker}/${maturity}`, signal),
  })

  if (series.isLoading || !series.data) return <Loading />
  if (series.isError) return <ErrorBox error={series.error} />
  const data = series.data.data

  return (
    <>
      <Link className="back" to="/t/$ticker" params={{ ticker }}>
        ‹ {ticker}
      </Link>
      <h1 className="screen-title">Série</h1>

      <div className="metrics">
        <div className="metric">
          <span className="k">Contrato</span>
          <div className="v">{data.ticker}</div>
        </div>
        <div className="metric">
          <span className="k">Lado / estilo</span>
          <div className="v">
            {data.side} / {data.style ?? '—'}
          </div>
        </div>
        <div className="metric">
          <span className="k">Strike</span>
          <div className="v">{brl(data.strike_brl)}</div>
        </div>
        <div className="metric">
          <span className="k">Vencimento</span>
          <div className="v">{data.maturity}</div>
        </div>
      </div>

      <p className="section-label">Último ponto do histórico</p>
      <div className="metrics">
        <div className="metric">
          <span className="k">Data</span>
          <div className="v">{data.last.date}</div>
        </div>
        <div className="metric">
          <span className="k">Preço</span>
          <div className="v">{brl(data.last.close_brl)}</div>
        </div>
        <div className="metric">
          <span className="k">IV</span>
          <div className="v">{fmt(data.last.iv_pct)}</div>
        </div>
        <div className="metric">
          <span className="k">Delta</span>
          <div className="v">{fmt(data.last.delta)}</div>
        </div>
      </div>
      <p className="note">
        {data.history.length} ponto(s) no histórico da resposta. Gráfico e marcas
        de ajuste entram na F5-B.
      </p>
    </>
  )
}
