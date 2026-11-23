import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, createFileRoute } from '@tanstack/react-router'
import { apiGet, type ScreenData } from '../lib/api'
import { brl, fmt } from '../lib/format'
import { ErrorState, Loading } from '../components/State'
import { GlossaryMetric, Metric, MetricGrid } from '../components/Metric'
import { MetricSheet } from '../components/MetricSheet'
import type { OpenMetric, SheetTarget } from '../components/metricKeys'

export const Route = createFileRoute('/')({
  component: Hoje,
})

function Hoje() {
  const [sheet, setSheet] = useState<SheetTarget | null>(null)
  const openMetric: OpenMetric = (field, label, value) => setSheet({ field, label, value })
  const screen = useQuery({
    queryKey: ['screen', 60],
    queryFn: ({ signal }) => apiGet<ScreenData>('/api/screen?max_rows=60', signal),
  })

  if (screen.isLoading || !screen.data) return <Loading />
  if (screen.isError) return <ErrorState error={screen.error} />

  const envelope = screen.data
  const { summary, rows } = envelope.data
  const meta = envelope.meta

  return (
    <>
      <h1 className="screen-title">Hoje</h1>
      <p className="lede">Leitura descritiva da sessão. Nenhum número aqui é previsão.</p>

      <MetricGrid>
        <Metric label="Universo" value={summary.total} />
        <Metric label="Com métrica" value={summary.ok} emphasis />
        <Metric label="Excluídos" value={summary.excluded} />
        <GlossaryMetric
          field="iv_atm_30d"
          label="IV mediana"
          value={fmt(summary.iv_median)}
          onOpen={openMetric}
        />
        <GlossaryMetric
          field="vrp"
          label="VRP mediana"
          value={fmt(summary.vrp_median)}
          onOpen={openMetric}
        />
      </MetricGrid>

      {meta?.truncated && (
        <p className="notice" role="status">
          A API devolveu {meta.returned} de {meta.total} papéis — recorte alfabético.
        </p>
      )}

      <h2 className="block-title">Papéis</h2>
      <div className="table-wrap">
        <table className="screen-table">
          <thead>
            <tr>
              <th>Papel</th>
              <th>Fechamento</th>
              <th>IV 30d</th>
              <th>VRP</th>
              <th>Séries líq.</th>
              <th>Venc.</th>
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
                <td>{row.n_liquid_series}</td>
                <td>{row.n_expiries}</td>
                <td className="quiet">{row.exclusion ?? 'ok'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <MetricSheet target={sheet} onClose={() => setSheet(null)} />
    </>
  )
}
