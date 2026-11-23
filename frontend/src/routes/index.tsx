import { useQuery } from '@tanstack/react-query'
import { Link, createFileRoute } from '@tanstack/react-router'
import { apiGet, type ScreenData } from '../lib/api'
import { brl, fmt } from '../lib/format'
import { ErrorBox, Loading } from '../lib/ui'

export const Route = createFileRoute('/')({
  component: Hoje,
})

function Hoje() {
  const screen = useQuery({
    queryKey: ['screen'],
    queryFn: ({ signal }) => apiGet<ScreenData>('/api/screen', signal),
  })

  if (screen.isLoading || !screen.data) return <Loading />
  if (screen.isError) return <ErrorBox error={screen.error} />
  const { summary, rows } = screen.data.data

  return (
    <>
      <h1 className="screen-title">Hoje</h1>
      <div className="metrics">
        <div className="metric">
          <span className="k">Universo</span>
          <div className="v">{summary.total}</div>
        </div>
        <div className="metric">
          <span className="k">Com métrica</span>
          <div className="v">{summary.ok}</div>
        </div>
        <div className="metric">
          <span className="k">Excluídos</span>
          <div className="v">{summary.excluded}</div>
        </div>
        <div className="metric">
          <span className="k">IV mediana</span>
          <div className="v">{fmt(summary.iv_median)}</div>
        </div>
        <div className="metric">
          <span className="k">VRP mediana</span>
          <div className="v">{fmt(summary.vrp_median)}</div>
        </div>
      </div>

      <p className="section-label">Papéis (ordem alfabética)</p>
      <table className="rows">
        <thead>
          <tr>
            <th>Papel</th>
            <th>Fechamento</th>
            <th>IV 30d</th>
            <th>VRP</th>
            <th>Situação</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.underlying}>
              <td>
                {row.status === 'ok' ? (
                  <Link className="ticker-link" to="/t/$ticker" params={{ ticker: row.underlying }}>
                    {row.underlying}
                  </Link>
                ) : (
                  row.underlying
                )}
              </td>
              <td>{brl(row.spot_brl)}</td>
              <td>{fmt(row.iv_atm_30d)}</td>
              <td>{fmt(row.vrp)}</td>
              <td>{row.exclusion ?? 'ok'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="note">
        Recorte alfabético (API devolve no máximo 60 linhas). Leitura descritiva:
        sem classe de sinal, nota ou recomendação.
      </p>
    </>
  )
}
