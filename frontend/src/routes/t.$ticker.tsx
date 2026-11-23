import { useQuery } from '@tanstack/react-query'
import { Link, createFileRoute } from '@tanstack/react-router'
import { apiGet, type ChainData, type Underlying } from '../lib/api'
import { brl, fmt } from '../lib/format'
import { ErrorBox, Loading } from '../lib/ui'

export const Route = createFileRoute('/t/$ticker')({
  component: TickerPage,
})

function TickerPage() {
  const { ticker } = Route.useParams()
  const overview = useQuery({
    queryKey: ['underlying', ticker],
    queryFn: ({ signal }) => apiGet<Underlying>(`/api/t/${ticker}`, signal),
  })
  const chain = useQuery({
    queryKey: ['chain', ticker],
    queryFn: ({ signal }) =>
      apiGet<ChainData>(`/api/t/${ticker}/chain`, signal),
  })

  if (overview.isLoading || !overview.data) return <Loading />
  if (overview.isError) return <ErrorBox error={overview.error} />
  const data = overview.data.data
  const summary = data.summary

  return (
    <>
      <Link className="back" to="/">
        ‹ Hoje
      </Link>
      <h1 className="screen-title">{ticker}</h1>

      <div className="metrics">
        <div className="metric">
          <span className="k">Fechamento</span>
          <div className="v">{brl(summary?.spot_brl)}</div>
        </div>
        <div className="metric">
          <span className="k">IV ATM 30d</span>
          <div className="v">{fmt(summary?.iv_atm_30d)}</div>
        </div>
        <div className="metric">
          <span className="k">VRP</span>
          <div className="v">{fmt(summary?.vrp)}</div>
        </div>
        <div className="metric">
          <span className="k">Vencimentos</span>
          <div className="v">{data.counts.term}</div>
        </div>
        <div className="metric">
          <span className="k">Séries</span>
          <div className="v">{data.counts.smile}</div>
        </div>
        <div className="metric">
          <span className="k">Cadeia (linhas)</span>
          <div className="v">{chain.data?.meta?.total ?? '—'}</div>
        </div>
      </div>

      {data.notes.map((note) => (
        <p className="note" key={note}>
          {note}
        </p>
      ))}
      {chain.isError && <ErrorBox error={chain.error} />}
    </>
  )
}
