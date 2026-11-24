import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, createFileRoute } from '@tanstack/react-router'
import { fetchScreenAll, type ScreenRow } from '../lib/api'
import { brl, dateBr, fmt, sharePct } from '../lib/format'
import { ErrorState, Loading } from '../components/State'
import { GlossaryMetric, Metric, MetricGrid } from '../components/Metric'
import { MetricModal } from '../components/MetricModal'
import { TickerSearch } from '../components/TickerSearch'
import type { ModalTarget, OpenMetric } from '../components/metricKeys'

export const Route = createFileRoute('/')({
  component: Hoje,
})

function PapersTable({ rows, withOptions }: { rows: ScreenRow[]; withOptions: boolean }) {
  if (rows.length === 0) {
    return <p className="chart-empty">nenhum papel nesta seção do recorte</p>
  }
  return (
    <div className="table-wrap">
      <table className="screen-table">
        <thead>
          <tr>
            <th>Papel</th>
            <th>Fechamento</th>
            {withOptions && (
              <>
                <th>Volatilidade implícita 30d</th>
                <th title="VRP = volatilidade implícita − realizada">Prêmio de volatilidade</th>
                <th>Séries líquidas</th>
                <th>Vencimentos</th>
              </>
            )}
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
              {withOptions && (
                <>
                  <td>{fmt(row.iv_atm_30d)}</td>
                  <td>
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
                  <td>{row.n_liquid_series}</td>
                  <td>{row.n_expiries}</td>
                </>
              )}
              <td className="quiet">{row.exclusion ?? 'ok'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Hoje() {
  const [modal, setModal] = useState<ModalTarget | null>(null)
  const openMetric: OpenMetric = (field, label, value) => setModal({ field, label, value })
  const screen = useQuery({
    queryKey: ['screen', 'all'],
    queryFn: ({ signal }) => fetchScreenAll(signal),
  })

  if (screen.isLoading || !screen.data) return <Loading />
  if (screen.isError) return <ErrorState error={screen.error} />

  const { summary, rows } = screen.data.data
  const withOptions = rows.filter((row) => row.n_expiries > 0)
  const withoutOptions = rows.filter((row) => row.n_expiries === 0)

  return (
    <>
      <h1 className="screen-title">Hoje</h1>
      <p className="lede">Leitura descritiva da sessão. Nenhum número aqui é previsão.</p>

      <TickerSearch />

      <MetricGrid>
        <Metric label="Universo" value={summary.total} />
        <Metric label="Com métrica" value={summary.ok} emphasis />
        <Metric label="Excluídos" value={summary.excluded} />
        <GlossaryMetric
          field="iv_atm_30d"
          label="Volatilidade implícita mediana"
          hint="Volatilidade implícita no dinheiro (ATM), padronizada para 30 dias."
          value={fmt(summary.iv_median)}
          onOpen={openMetric}
        />
        <GlossaryMetric
          field="vrp"
          label="Prêmio de volatilidade mediano"
          hint="VRP = volatilidade implícita − realizada."
          value={fmt(summary.vrp_median)}
          onOpen={openMetric}
        />
      </MetricGrid>

      {summary.rv_shock_count > 0 && (
        <p className="notice" role="status">
          Em {summary.rv_shock_count} de {summary.ok} papéis com métrica, um único pregão responde
          por metade ou mais da oscilação dos últimos 30 pregões
          {summary.rv_shock_dates.length > 0 && (
            <> ({summary.rv_shock_dates.map((item) => `${dateBr(item.date)} em ${item.count}`).join(', ')})</>
          )}
          . Nesses papéis (marcados com †) a volatilidade realizada e o prêmio de volatilidade
          refletem esse dia: o número é real, mas não descreve o regime típico.{' '}
          <button
            type="button"
            className="inline-link"
            onClick={() => openMetric('rv_shock_share_pct', 'Pregão dominante na RV', `${summary.rv_shock_count} papéis`)}
          >
            O que é isso
          </button>
        </p>
      )}

      <section className="papers-section">
        <h2 className="block-title">
          Com opções <span className="block-count">{withOptions.length} papéis</span>
        </h2>
        <PapersTable rows={withOptions} withOptions />
      </section>

      <section className="papers-section">
        <h2 className="block-title">
          Sem opções <span className="block-count">{withoutOptions.length} papéis</span>
        </h2>
        <PapersTable rows={withoutOptions} withOptions={false} />
      </section>

      <MetricModal target={modal} onClose={() => setModal(null)} />
    </>
  )
}
