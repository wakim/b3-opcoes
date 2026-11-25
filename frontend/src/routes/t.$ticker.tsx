import { useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, createFileRoute } from '@tanstack/react-router'
import { apiGet, type ChainData, type ChainRow, type Term, type Underlying } from '../lib/api'
import { brl, dateBr, fmt, sharePct } from '../lib/format'
import { back } from '../lib/classes'
import { ErrorState, Loading } from '../components/State'
import { GlossaryMetric, MetricGrid } from '../components/Metric'
import { Ruler } from '../components/Ruler'
import { LineChart } from '../components/LineChart'
import { Chain } from '../components/Chain'
import { MetricModal } from '../components/MetricModal'
import { InfoTip } from '../components/InfoTip'
import { StructureBuilder, type BuilderLeg } from '../components/StructureBuilder'
import { ScreenTable } from '../components/ScreenTable'
import type { ModalTarget, OpenMetric } from '../components/metricKeys'
import { Button } from '../components/ui/button'
import { ToggleGroup, ToggleGroupItem } from '../components/ui/toggle-group'
import { NativeSelect } from '../components/ui/native-select'
import {
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../components/ui/table'

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

  if (overview.isError) return <ErrorState error={overview.error} />
  if (overview.isLoading || !overview.data) return <Loading />

  const data = overview.data.data
  const summary = data.summary
  const history = data.history
  const firstWithIv = term.findIndex((point) => point.iv_atm != null)
  const lastWithIv = term.reduce((acc, point, index) => (point.iv_atm != null ? index : acc), -1)
  const termWindow = firstWithIv >= 0 ? term.slice(firstWithIv, lastWithIv + 1) : []
  const illiquidAtm = withIv.filter((point) => point.n_liquid_strikes === 0).length

  return (
    <>
      <Link className={`${back} lg:text-label`} to="/">
        <span aria-hidden="true">‹</span> Hoje
      </Link>
      <h1 className="mt-[4px] mb-[16px] text-[calc(28px*var(--font-scale))] font-medium tracking-[-0.02em] lg:text-screen-title lg:leading-title">{ticker}</h1>
      <p className="mb-[16px] font-mono text-[calc(11px*var(--font-scale))] text-muted-foreground lg:text-label">
        {data.counts.term} vencimentos · {data.counts.smile}{' '}
        <InfoTip text="preços de exercício"><abbr className="underline decoration-dotted" aria-label="strikes: preços de exercício">strikes</abbr></InfoTip> · {data.counts.history} dias de histórico
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
        <p className="mb-[16px] border-l-2 border-input pl-[10px] text-[calc(11.5px*var(--font-scale))] text-[color:var(--text-2)] lg:text-label" role="status">
          {sharePct(summary.rv_shock_share_pct)} de tudo o que {ticker} oscilou nos últimos 30
          pregões veio de um só dia ({dateBr(summary.rv_shock_date)}). A volatilidade realizada, o
          prêmio de volatilidade e os desvios-padrão do prêmio refletem esse dia: o número é real,
          mas não descreve o regime típico do papel.
        </p>
      )}

      {data.notes.map((note) => (
        <p className="my-[8px] text-[calc(11px*var(--font-scale))] text-muted-foreground lg:text-label" key={note}>{note}</p>
      ))}

      <h2 className="mt-[24px] mb-[8px] text-[calc(13px*var(--font-scale))] font-medium lg:text-block-title lg:leading-title">Volatilidade implícita por vencimento</h2>
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
        <p className="my-[8px] text-[calc(11.5px*var(--font-scale))] text-muted-foreground lg:text-detail">Volatilidade implícita indisponível nos vencimentos desta sessão</p>
      )}
      {withIv.length > 0 && (
        <div className="mt-[8px]">
          <p className="mt-[12px] mb-[4px] text-[calc(11px*var(--font-scale))] text-[color:var(--text-2)] lg:text-label">
            Vencimentos com IV <span className="ml-[8px] font-mono text-[calc(11px*var(--font-scale))] font-normal text-muted-foreground lg:text-label">{withIv.length}</span>
          </p>
          <TermTable rows={withIv} />
          <p className="my-[8px] text-[calc(11px*var(--font-scale))] text-muted-foreground lg:text-label">
            IV é a do strike de |Δ| mais próximo de 0,5; Líquidas = séries líquidas no vencimento.
          </p>
        </div>
      )}
      {withoutIv.length > 0 && (
        <details className="mt-[6px]">
          <summary className="flex min-h-[44px] cursor-pointer items-center text-[calc(11.5px*var(--font-scale))] text-[color:var(--text-2)] marker:text-muted-foreground lg:text-label">Ver os {withoutIv.length} vencimentos sem IV</summary>
          <TermTable rows={withoutIv} className="mt-[4px] mb-[8px]" />
        </details>
      )}

      <h2 className="mt-[24px] mb-[8px] text-[calc(13px*var(--font-scale))] font-medium lg:text-block-title lg:leading-title">Volatilidade implícita vs realizada</h2>
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

      <h2 className="mt-[24px] mb-[8px] text-[calc(13px*var(--font-scale))] font-medium lg:text-block-title lg:leading-title">Vencimento</h2>
      {term.length > 0 && (
        <div className="mb-[8px] flex items-stretch gap-[6px]" role="group" aria-label="Vencimento">
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-[44px] w-[44px] flex-none cursor-pointer rounded-[4px] border border-border bg-card font-mono text-[calc(18px*var(--font-scale))] font-normal leading-none text-[color:var(--text-2)] focus-visible:ring-0 focus-visible:outline-2 focus-visible:outline-solid focus-visible:outline-offset-[-2px] focus-visible:outline-primary lg:text-metric"
            aria-label="Vencimento anterior"
            disabled={maturityIndex <= 0}
            onClick={() => stepMaturity(-1)}
          >
            ‹
          </Button>
          <NativeSelect
            className="min-w-0 flex-1 border-border bg-card px-[10px] py-[8px] font-mono text-[calc(12px*var(--font-scale))] tabular-nums text-foreground dark:bg-card focus-visible:ring-0 focus-visible:outline-2 focus-visible:outline-solid focus-visible:outline-offset-[-2px] focus-visible:outline-primary lg:text-control"
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
          </NativeSelect>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-[44px] w-[44px] flex-none cursor-pointer rounded-[4px] border border-border bg-card font-mono text-[calc(18px*var(--font-scale))] font-normal leading-none text-[color:var(--text-2)] focus-visible:ring-0 focus-visible:outline-2 focus-visible:outline-solid focus-visible:outline-offset-[-2px] focus-visible:outline-primary lg:text-metric"
            aria-label="Próximo vencimento"
            disabled={maturityIndex >= term.length - 1}
            onClick={() => stepMaturity(1)}
          >
            ›
          </Button>
        </div>
      )}

      <div className="mt-[24px] flex flex-wrap items-center justify-between gap-[8px]">
        <h2 className="mt-0 mb-[8px] text-[calc(13px*var(--font-scale))] font-medium lg:text-block-title lg:leading-title">Cadeia de opções</h2>
        <ToggleGroup
          type="multiple"
          value={[filter]}
          onValueChange={(values) => { if (values.length) setFilter(values[values.length - 1] as 'strict' | 'all') }}
          className="flex flex-wrap gap-[4px]"
          aria-label="Filtro da cadeia"
        >
          <ToggleGroupItem
            value="strict"
            className="h-[44px] rounded-[4px] border border-border bg-card px-[12px] py-[8px] font-mono text-[calc(11.5px*var(--font-scale))] font-normal whitespace-normal tabular-nums text-[color:var(--text-2)] lg:text-control data-[state=on]:border-input data-[state=on]:bg-muted data-[state=on]:text-foreground focus-visible:ring-0 focus-visible:outline-2 focus-visible:outline-solid focus-visible:outline-offset-[-2px] focus-visible:outline-primary"
          >
            Líquidas
          </ToggleGroupItem>
          <ToggleGroupItem
            value="all"
            className="h-[44px] rounded-[4px] border border-border bg-card px-[12px] py-[8px] font-mono text-[calc(11.5px*var(--font-scale))] font-normal whitespace-normal tabular-nums text-[color:var(--text-2)] lg:text-control data-[state=on]:border-input data-[state=on]:bg-muted data-[state=on]:text-foreground focus-visible:ring-0 focus-visible:outline-2 focus-visible:outline-solid focus-visible:outline-offset-[-2px] focus-visible:outline-primary"
          >
            Todas
          </ToggleGroupItem>
        </ToggleGroup>
      </div>
      <p className="mb-[8px] text-[calc(11px*var(--font-scale))] leading-[1.45] text-muted-foreground lg:text-label lg:leading-body">
        Put = opção de venda · Call = opção de compra · Strike = preço de exercício · números em
        volatilidade implícita ao ano.
      </p>
      {chain.isLoading && <Loading label="carregando cadeia…" />}
      {chain.isError && <ErrorState error={chain.error} />}
      {chain.data && (
        <>
          {chain.data.meta?.truncated && (
            <p className="mb-[16px] border-l-2 border-input pl-[10px] text-[calc(11.5px*var(--font-scale))] text-[color:var(--text-2)] lg:text-label" role="status">
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
function TermTable({ rows, className }: { rows: Term[]; className?: string }) {
  return (
    <ScreenTable
      wrapperClassName={`overflow-x-auto rounded-[6px] border border-border${className ? ` ${className}` : ''}`}
      stickyFirst={false}
      className="max-w-[420px]"
    >
      <TableHeader>
          <TableRow>
            <TableHead className="px-[8px] py-[10px] font-normal text-[color:var(--text-2)] lg:text-table-head">Vencimento</TableHead>
            <InfoTip text="Volatilidade implícita do strike de |Δ| mais próximo de 0,5">
              <TableHead className="px-[8px] py-[10px] text-right font-normal text-[color:var(--text-2)] lg:text-table-head" aria-label="IV. Volatilidade implícita do strike de |Δ| mais próximo de 0,5">IV</TableHead>
            </InfoTip>
            <InfoTip text="Séries líquidas no vencimento">
              <TableHead className="px-[8px] py-[10px] text-right font-normal text-[color:var(--text-2)] lg:text-table-head" aria-label="Líquidas. Séries líquidas no vencimento">Líquidas</TableHead>
            </InfoTip>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((point) => (
            <TableRow key={point.maturity}>
              <TableCell className="px-[8px] py-[10px] font-mono tabular-nums">{dateBr(point.maturity)}</TableCell>
              <TableCell className="px-[8px] py-[10px]">{fmt(point.iv_atm)}</TableCell>
              <TableCell className="px-[8px] py-[10px]">{point.n_liquid_strikes}</TableCell>
            </TableRow>
          ))}
        </TableBody>
    </ScreenTable>
  )
}
