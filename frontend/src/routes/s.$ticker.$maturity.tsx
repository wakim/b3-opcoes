import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, createFileRoute } from '@tanstack/react-router'
import { apiGet, type SeriesDetail } from '../lib/api'
import { brl, dateBr, fmt } from '../lib/format'
import { ErrorState, Loading } from '../components/State'
import { GlossaryMetric, Metric, MetricGrid } from '../components/Metric'
import { Ruler } from '../components/Ruler'
import { LineChart } from '../components/LineChart'
import { MetricModal } from '../components/MetricModal'
import { ScreenTable } from '../components/ScreenTable'
import type { ModalTarget, OpenMetric } from '../components/metricKeys'
import { usePresentation } from '../lib/presentation'
import { ToggleGroup, ToggleGroupItem } from '../components/ui/toggle-group'
import {
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../components/ui/table'

export const Route = createFileRoute('/s/$ticker/$maturity')({
  component: SeriesPage,
})

const IDC = 'bg-card px-[12px] py-[10px]'
const IDT = 'text-[calc(11px*var(--font-scale))] text-[color:var(--text-2)] lg:text-label'
const IDD = 'mt-[4px] text-[calc(12px*var(--font-scale))] text-foreground [overflow-wrap:anywhere] lg:text-detail'
const IDD_NUM = `${IDD} font-mono tabular-nums`

function SeriesPage() {
  const p = usePresentation()
  const { ticker, maturity } = Route.useParams()
  const [view, setView] = useState<'price' | 'iv'>('price')
  const [modal, setModal] = useState<ModalTarget | null>(null)
  const openMetric: OpenMetric = (field, label, value) => setModal({ field, label, value })

  const series = useQuery({
    queryKey: ['series', ticker, maturity],
    queryFn: ({ signal }) => apiGet<SeriesDetail>(`/api/s/${ticker}/${maturity}`, signal),
  })

  if (series.isError) return <ErrorState error={series.error} />
  if (series.isLoading || !series.data) return <Loading />

  const data = series.data.data
  const history = data.history
  const labels = history.map((point) => point.date.slice(5))
  // `metrics_series` só tem dia com negócio: o eixo x é o pregão do lake, para
  // que 10 negócios em 3 meses não pareçam uma curva diária contínua.
  const sessionX = history.every((point) => point.session_index != null)
    ? history.map((point) => point.session_index as number)
    : undefined
  const legChange = history.map(
    (point, index) => index > 0 && point.leg_id != null && point.leg_id !== history[index - 1].leg_id,
  )
  const legEvents = legChange.flatMap((changed, index) => (changed ? [{ index, kind: 'leg' as const }] : []))
  const sessionSpan = sessionX ? sessionX[sessionX.length - 1] - sessionX[0] + 1 : history.length
  const historyDetails = history.map((point, index) => {
    const parts: string[] = []
    if (point.strike_brl != null) parts.push(`${p.copy('strike').label} ${brl(point.strike_brl)}`)
    if (legChange[index]) parts.push(p.copy('chart', 'leg').label)
    if (sessionX && index > 0) {
      const idle = sessionX[index] - sessionX[index - 1] - 1
      if (idle > 0) parts.push(`${idle} ${idle > 1 ? 'pregões' : 'pregão'} sem negócio antes`)
    }
    return parts.length ? parts.join(' · ') : null
  })

  return (
    <>
      <Link className="mb-[12px] inline-flex min-h-[44px] items-center font-mono text-[calc(12px*var(--font-scale))] text-[color:var(--text-2)] focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-primary lg:text-label" to="/t/$ticker" params={{ ticker }}>
        <span aria-hidden="true">‹</span> {ticker}
      </Link>
      <h1 className="mt-[4px] mb-[16px] text-[calc(28px*var(--font-scale))] font-medium tracking-[-0.02em] lg:text-screen-title lg:leading-title">Série</h1>

      <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-[6px] border border-border bg-border">
        <div className={IDC}><dt className={IDT}>Contrato</dt><dd className={IDD_NUM}>{data.ticker}</dd></div>
        <div className={IDC}><dt className={IDT}>Lado / estilo</dt><dd className={IDD}>{p.side(data.side)} / {data.style ? p.style(data.style) : p.copy('option_style', 'unknown').label}</dd></div>
        <div className={IDC}><dt className={IDT}>{p.copy('strike', 'expanded').label}</dt><dd className={IDD_NUM}>{brl(data.strike_brl)}</dd></div>
        <div className={IDC}><dt className={IDT}>{p.copy('maturity').label}</dt><dd className={IDD_NUM}>{data.maturity}</dd></div>
        <div className={IDC}><dt className={IDT}>{p.copy('leg').label}</dt><dd className={IDD_NUM}>{data.leg_id}</dd></div>
      </dl>

      <h2 className="mt-[24px] mb-[8px] text-[calc(13px*var(--font-scale))] font-medium lg:text-block-title lg:leading-title">Último ponto · {data.last.date}</h2>
      <MetricGrid>
        <Metric label="Data" value={data.last.date} />
        <GlossaryMetric
          field="mid_brl"
          label="Preço"
          term={p.metric('mid_brl') ?? 'spot'}
          value={brl(data.last.mid_brl ?? data.last.close_brl)}
          onOpen={openMetric}
          emphasis
        />
        <GlossaryMetric
          field="iv_pct"
          term="iv_pct"
          value={fmt(data.last.iv_pct)}
          onOpen={openMetric}
        />
        <GlossaryMetric
          field="delta"
          term="delta"
          value={fmt(data.last.delta, 3)}
          onOpen={openMetric}
          note={
            <Ruler
              value={data.last.delta}
              min={-1}
              max={1}
              ticks={[-1, 0, 1]}
              format={(v) => fmt(v, 1)}
              describe={p.copy('delta', 'ruler').label}
            />
          }
        />
        <GlossaryMetric
          field="gamma"
          term="gamma"
          value={fmt(data.gamma, 4)}
          onOpen={openMetric}
        />
        <GlossaryMetric
          field="theta_brl"
          term="theta"
          variant="per_day"
          value={brl(data.theta_brl)}
          onOpen={openMetric}
        />
        <GlossaryMetric
          field="vega_brl"
          term="vega"
          variant="per_vol"
          value={brl(data.vega_brl)}
          onOpen={openMetric}
        />
      </MetricGrid>

      <div className="mt-[24px] flex flex-wrap items-center justify-between gap-[8px]">
        <h2 className="mt-0 mb-[8px] text-[calc(13px*var(--font-scale))] font-medium lg:text-block-title lg:leading-title">Histórico</h2>
        <ToggleGroup
          type="multiple"
          value={[view]}
          onValueChange={(values) => { if (values.length) setView(values[values.length - 1] as 'price' | 'iv') }}
          className="flex flex-wrap gap-[4px]"
          aria-label="Série do gráfico"
        >
          <ToggleGroupItem
            value="price"
            className="h-[44px] rounded-[4px] border border-border bg-card px-[12px] py-[8px] font-mono text-[calc(11.5px*var(--font-scale))] font-normal whitespace-normal tabular-nums text-[color:var(--text-2)] lg:text-control data-[state=on]:border-input data-[state=on]:bg-muted data-[state=on]:text-foreground focus-visible:ring-0 focus-visible:outline-2 focus-visible:outline-solid focus-visible:outline-offset-[-2px] focus-visible:outline-primary"
          >
            Preço
          </ToggleGroupItem>
          <ToggleGroupItem
            value="iv"
            className="h-[44px] rounded-[4px] border border-border bg-card px-[12px] py-[8px] font-mono text-[calc(11.5px*var(--font-scale))] font-normal whitespace-normal tabular-nums text-[color:var(--text-2)] lg:text-control data-[state=on]:border-input data-[state=on]:bg-muted data-[state=on]:text-foreground focus-visible:ring-0 focus-visible:outline-2 focus-visible:outline-solid focus-visible:outline-offset-[-2px] focus-visible:outline-primary"
          >
            {p.copy('iv_pct').label}
          </ToggleGroupItem>
        </ToggleGroup>
      </div>
      <LineChart
        labels={labels}
        series={[
          {
            label: view === 'price' ? 'Preço' : p.copy('iv_pct').label,
            values: history.map((point) => (view === 'price' ? point.close_brl : point.iv_pct)),
          },
        ]}
        formatValue={(v) => (view === 'price' ? brl(v) : fmt(v, 1))}
        ariaLabel={`Histórico de ${view === 'price' ? 'preço' : p.copy('iv_pct').label} de ${data.ticker}`}
        showPoints={history.length <= 30}
        xValues={sessionX}
        dashedGap={1}
        gapLabel={p.copy('chart', 'no_trade_gap').label}
        tipLabels={history.map((point) => dateBr(point.date))}
        details={historyDetails}
        breakBefore={legChange}
        events={legEvents}
        eventLabels={{ leg: p.copy('chart', 'leg').label }}
        caption={`${history.length} pregões com negócio em ${sessionSpan} do lake · preço = último negócio do dia, não cotação${legEvents.length ? ' · a linha não liga pernas diferentes' : ''}`}
      />
      {data.flags.length > 0 && <p className="my-[8px] text-[calc(11px*var(--font-scale))] text-muted-foreground lg:text-label">flags: {data.flags.map((code) => p.flag(code).label).join(' · ')}</p>}

      <h2 className="mt-[24px] mb-[8px] text-[calc(13px*var(--font-scale))] font-medium lg:text-block-title lg:leading-title">Últimos pregões</h2>
      <ScreenTable wrapperClassName="overflow-x-auto rounded-[6px] border border-border">
        <TableHeader>
            <TableRow>
              <TableHead className="px-[8px] py-[10px] font-normal text-[color:var(--text-2)] lg:text-table-head">Data</TableHead>
              <TableHead className="px-[8px] py-[10px] text-right font-normal text-[color:var(--text-2)] lg:text-table-head">{p.copy('spot', 'close').label}</TableHead>
              <TableHead className="px-[8px] py-[10px] text-right font-normal text-[color:var(--text-2)] lg:text-table-head">{p.copy('iv_pct').label}</TableHead>
              <TableHead className="px-[8px] py-[10px] text-right font-normal text-[color:var(--text-2)] lg:text-table-head">{p.copy('delta').label}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {history.slice(-8).reverse().map((point) => (
              <TableRow key={point.date}>
                <TableCell className="px-[8px] py-[10px] font-mono tabular-nums">{point.date}</TableCell>
                <TableCell className="px-[8px] py-[10px]">{brl(point.close_brl)}</TableCell>
                <TableCell className="px-[8px] py-[10px]">{fmt(point.iv_pct)}</TableCell>
                <TableCell className="px-[8px] py-[10px]">{fmt(point.delta, 3)}</TableCell>
              </TableRow>
            ))}
        </TableBody>
      </ScreenTable>

      <MetricModal target={modal} onClose={() => setModal(null)} />
    </>
  )
}
