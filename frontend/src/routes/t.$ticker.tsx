import { useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, createFileRoute } from '@tanstack/react-router'
import { apiGet, type ChainData, type ChainRow, type Term, type Underlying } from '../lib/api'
import { brl, dateBr, fmt, sharePct } from '../lib/format'
import { ErrorState, Loading } from '../components/State'
import { GlossaryMetric, MetricGrid } from '../components/Metric'
import { Ruler } from '../components/Ruler'
import { LineChart } from '../components/LineChart'
import { Chain } from '../components/Chain'
import { MetricModal } from '../components/MetricModal'
import { StructureBuilder, type BuilderLeg } from '../components/StructureBuilder'
import type { ModalTarget, OpenMetric } from '../components/metricKeys'

export const Route = createFileRoute('/t/$ticker')({
  component: TickerPage,
})

function TickerPage() {
  const { ticker } = Route.useParams()
  const [pickedMaturity, setPickedMaturity] = useState<string | null>(null)
  const [filter, setFilter] = useState<'strict' | 'all'>('strict')
  const [modal, setModal] = useState<ModalTarget | null>(null)
  const [legs, setLegs] = useState<BuilderLeg[]>([])
  const legId = useRef(0)
  const nextId = () => {
    legId.current += 1
    return legId.current
  }
  const openMetric: OpenMetric = (field, label, value) => setModal({ field, label, value })
  const addLeg = (row: ChainRow, side: 'long' | 'short', quantity: number) => {
    setLegs((prev) =>
      prev.length >= 60
        ? prev
        : [...prev, { id: nextId(), kind: 'option', ticker: row.ticker, side, quantity }],
    )
  }

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
  const maturityIndex = activeMaturity ? term.findIndex((point) => point.maturity === activeMaturity) : -1
  const withIv = term.filter((point) => point.iv_atm != null)
  const withoutIv = term.filter((point) => point.iv_atm == null)

  function stepMaturity(delta: number) {
    const next = term[maturityIndex + delta]
    if (next) setPickedMaturity(next.maturity)
  }

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
  const firstWithIv = term.findIndex((point) => point.iv_atm != null)
  const lastWithIv = term.reduce((acc, point, index) => (point.iv_atm != null ? index : acc), -1)
  const termWindow = firstWithIv >= 0 ? term.slice(firstWithIv, lastWithIv + 1) : []
  const illiquidAtm = withIv.filter((point) => point.n_liquid_strikes === 0).length

  return (
    <>
      <Link className="back lg:text-label" to="/">
        <span aria-hidden="true">‹</span> Hoje
      </Link>
      <h1 className="screen-title lg:text-screen-title lg:leading-title">{ticker}</h1>
      <p className="quiet-line lg:text-label">
        {data.counts.term} vencimentos · {data.counts.smile}{' '}
        <abbr title="preços de exercício">strikes</abbr> · {data.counts.history} dias de histórico
      </p>

      <MetricGrid>
        <GlossaryMetric
          field="spot_brl"
          label="Fechamento"
          hint="Último preço negociado da ação na sessão; o painel é sempre D-1."
          value={brl(summary?.spot_brl)}
          onOpen={openMetric}
        />
        <GlossaryMetric
          field="iv_atm_30d"
          label="Volatilidade implícita 30d"
          hint="Volatilidade implícita no dinheiro (ATM), padronizada para 30 dias."
          value={fmt(summary?.iv_atm_30d)}
          onOpen={openMetric}
          emphasis
        />
        <GlossaryMetric
          field="vrp"
          label="Prêmio de volatilidade"
          hint="VRP = volatilidade implícita − realizada."
          value={fmt(summary?.vrp)}
          onOpen={openMetric}
        />
        <GlossaryMetric
          field="iv_rank_252"
          label="Posição da volatilidade (12m)"
          hint="Onde a volatilidade implícita de hoje está nos últimos 252 pregões (0 = mínimo, 100 = máximo)."
          value={fmt(summary?.iv_rank_252, 0)}
          onOpen={openMetric}
          note={
            <Ruler
              value={summary?.iv_rank_252 ?? null}
              min={0}
              max={100}
              ticks={[0, 50, 100]}
              format={(v) => fmt(v, 0)}
              describe="Posição da volatilidade em 12 meses"
            />
          }
        />
        <GlossaryMetric
          field="vrp_z_252"
          label="Desvios-padrão do prêmio"
          hint="Quantos desvios-padrão o prêmio de volatilidade de hoje está da própria média de 252 sessões."
          value={fmt(summary?.vrp_z_252)}
          onOpen={openMetric}
          note={
            <Ruler
              value={summary?.vrp_z_252 ?? null}
              min={-3}
              max={3}
              ticks={[-3, 0, 3]}
              format={(v) => fmt(v, 1)}
              describe="Desvios-padrão do prêmio de volatilidade"
            />
          }
        />
        <GlossaryMetric
          field="days_to_liquidate"
          label="Dias para liquidar"
          hint="Quantos pregões seriam necessários para desmontar a posição média da série ao ritmo atual."
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
        <GlossaryMetric
          field="rv_shock_share_pct"
          label="Pregão dominante na realizada"
          hint="O pregão que mais pesa na oscilação dos últimos 30 pregões e quanto da variância ele explica sozinho."
          value={
            summary?.rv_shock_date
              ? `${dateBr(summary.rv_shock_date)} · ${sharePct(summary.rv_shock_share_pct)}`
              : '—'
          }
          onOpen={openMetric}
        />
      </MetricGrid>

      {summary?.flags.includes('rv_shock_day') && (
        <p className="notice lg:text-label" role="status">
          {sharePct(summary.rv_shock_share_pct)} de tudo o que {ticker} oscilou nos últimos 30
          pregões veio de um só dia ({dateBr(summary.rv_shock_date)}). A volatilidade realizada, o
          prêmio de volatilidade e os desvios-padrão do prêmio refletem esse dia: o número é real,
          mas não descreve o regime típico do papel.
        </p>
      )}

      {data.notes.map((note) => (
        <p className="note lg:text-label" key={note}>{note}</p>
      ))}

      <h2 className="block-title lg:text-block-title lg:leading-title">Volatilidade implícita por vencimento</h2>
      {withIv.length >= 2 ? (
        <LineChart
          labels={termWindow.map((point) => point.maturity.slice(5))}
          xValues={termWindow.map((point) => point.t)}
          xTickValues={term.map((point) => point.t)}
          dashedGap={0.12}
          gapStyle="band"
          tipLabels={termWindow.map((point) => dateBr(point.maturity))}
          details={termWindow.map((point) =>
            `${point.n_liquid_strikes} ${point.n_liquid_strikes === 1 ? 'série líquida' : 'séries líquidas'}${
              point.forward_brl != null ? ` · forward ${brl(point.forward_brl)}` : ''
            }`,
          )}
          series={[{
            label: 'Volatilidade implícita',
            values: termWindow.map((point) => point.iv_atm),
            hollow: termWindow.map((point) => point.n_liquid_strikes === 0),
          }]}
          formatValue={(v) => fmt(v, 1)}
          ariaLabel={`Volatilidade implícita por vencimento de ${ticker}`}
          caption={`Volatilidade implícita em ${withIv.length} de ${term.length} vencimentos${illiquidAtm > 0 ? ` · ${illiquidAtm} com ATM sem série líquida` : ''} · eixo x proporcional ao tempo`}
        />
      ) : (
        <p className="chart-empty lg:text-detail">Volatilidade implícita indisponível nos vencimentos desta sessão</p>
      )}
      {withIv.length > 0 && (
        <div className="term-table-block">
          <p className="term-table__title lg:text-label">
            Vencimentos com IV <span className="block-count lg:text-label">{withIv.length}</span>
          </p>
          <TermTable rows={withIv} />
          <p className="note lg:text-label">
            IV é a do strike de |Δ| mais próximo de 0,5; Líquidas = séries líquidas no vencimento.
          </p>
        </div>
      )}
      {withoutIv.length > 0 && (
        <details className="chart-data">
          <summary className="lg:text-label">Ver os {withoutIv.length} vencimentos sem IV</summary>
          <TermTable rows={withoutIv} />
        </details>
      )}

      <h2 className="block-title lg:text-block-title lg:leading-title">Volatilidade implícita vs realizada</h2>
      <LineChart
        compact
        labels={history.map((point) => point.date.slice(5))}
        tipLabels={history.map((point) => dateBr(point.date))}
        details={history.map((point) => (point.vrp != null ? `VRP ${fmt(point.vrp)}` : null))}
        series={[
          { label: 'Volatilidade implícita', values: history.map((point) => point.iv_atm_30d) },
          { label: 'Realizada 30d', values: history.map((point) => point.rv30), dashed: true },
        ]}
        formatValue={(v) => fmt(v, 1)}
        ariaLabel={`Volatilidade implícita e realizada de ${ticker}`}
        showPoints={false}
      />

      <h2 className="block-title lg:text-block-title lg:leading-title">Vencimento</h2>
      {term.length > 0 && (
        <div className="maturity-picker" role="group" aria-label="Vencimento">
          <button
            type="button"
            className="maturity-picker__step lg:text-metric"
            aria-label="Vencimento anterior"
            disabled={maturityIndex <= 0}
            onClick={() => stepMaturity(-1)}
          >
            ‹
          </button>
          <select
            className="maturity-picker__select lg:text-control"
            aria-label="Escolher vencimento"
            value={activeMaturity ?? ''}
            onChange={(event) => setPickedMaturity(event.target.value)}
          >
            {term.map((point) => (
              <option key={point.maturity} value={point.maturity}>
                {dateBr(point.maturity)}
                {point.iv_atm == null ? ' · sem IV' : ` · IV ${fmt(point.iv_atm, 1)}`}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="maturity-picker__step lg:text-metric"
            aria-label="Próximo vencimento"
            disabled={maturityIndex >= term.length - 1}
            onClick={() => stepMaturity(1)}
          >
            ›
          </button>
        </div>
      )}

      <div className="block-head">
        <h2 className="block-title lg:text-block-title lg:leading-title">Cadeia de opções</h2>
        <div className="selector" role="group" aria-label="Filtro da cadeia">
          <button
            type="button"
            className={`${filter === 'strict' ? 'active' : ''} lg:text-control`.trim()}
            aria-pressed={filter === 'strict'}
            onClick={() => setFilter('strict')}
          >
            Líquidas
          </button>
          <button
            type="button"
            className={`${filter === 'all' ? 'active' : ''} lg:text-control`.trim()}
            aria-pressed={filter === 'all'}
            onClick={() => setFilter('all')}
          >
            Todas
          </button>
        </div>
      </div>
      <p className="chain-note lg:text-label lg:leading-body">
        Put = opção de venda · Call = opção de compra · Strike = preço de exercício · números em
        volatilidade implícita ao ano.
      </p>
      {chain.isLoading && <Loading label="carregando cadeia…" />}
      {chain.isError && <ErrorState error={chain.error} />}
      {chain.data && (
        <>
          {chain.data.meta?.truncated && (
            <p className="notice lg:text-label" role="status">
              A cadeia foi cortada em {chain.data.meta.returned} de {chain.data.meta.total} linhas.
            </p>
          )}
          <Chain data={chain.data.data} maturity={activeMaturity} onMetric={openMetric} onAddLeg={addLeg} />
        </>
      )}

      <StructureBuilder
        underlying={ticker}
        date={overview.data.as_of_session}
        legs={legs}
        setLegs={setLegs}
        nextId={nextId}
      />

      <MetricModal target={modal} onClose={() => setModal(null)} />
    </>
  )
}

/** Tabela curta dos vencimentos (IV do ATM e séries líquidas) — sem forward. */
function TermTable({ rows }: { rows: Term[] }) {
  return (
    <div className="table-wrap">
      <table className="screen-table term-table lg:text-table lg:leading-table">
        <thead>
          <tr>
            <th className="lg:text-table-head">Vencimento</th>
            <th className="lg:text-table-head" title="Volatilidade implícita do strike de |Δ| mais próximo de 0,5">IV</th>
            <th className="lg:text-table-head" title="Séries líquidas no vencimento">Líquidas</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((point) => (
            <tr key={point.maturity}>
              <td className="num">{dateBr(point.maturity)}</td>
              <td>{fmt(point.iv_atm)}</td>
              <td>{point.n_liquid_strikes}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
