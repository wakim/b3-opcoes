/** Fechamento do papel por pregão do lake, com marcas de evento.
 *
 * Nada é calculado aqui: o ajuste de desdobramento/grupamento e a detecção de
 * salto vêm do `QueryService.prices`. A tela só posiciona marcas por data e
 * descreve cada uma no tooltip. Proventos não são descontados do preço.
 */
import { useQuery } from '@tanstack/react-query'
import { fetchPricesAll, type PriceMark } from '../lib/api'
import { brl, dateBr, fmt, sharePct } from '../lib/format'
import { usePresentation } from '../lib/presentation'
import { LineChart, type ChartEvent, type ChartEventKind } from './LineChart'
import { ErrorState, Loading } from './State'

const DAYS = 250

function shortDate(iso: string): string {
  return `${iso.slice(8, 10)}/${iso.slice(5, 7)}/${iso.slice(2, 4)}`
}

function markKind(mark: PriceMark): ChartEventKind {
  if (mark.kind === 'earnings') return 'earnings'
  if (mark.kind === 'gap') return 'jump'
  return 'corporate'
}

function markText(mark: PriceMark): string {
  switch (mark.kind) {
    case 'dividend':
      return `data ex de provento${mark.value != null ? ` (${brl(mark.value)}/ação, medido no strike)` : ''}`
    case 'scale':
      return `desdobramento/grupamento ×${fmt(mark.value, 4)} — preços anteriores ajustados`
    case 'indeterminate':
      return 'ajuste de strike não classificado (pode ser provento ou escala)'
    case 'earnings':
      return mark.detail === 'estimated' ? 'resultado (data estimada)' : 'resultado entregue à CVM'
    case 'gap':
      return `abertura a ${fmt(mark.value, 2)}× do fechamento anterior, sem evento de strike`
    default:
      return mark.kind
  }
}

export function PriceChart({
  ticker,
  shockDate,
  shockSharePct,
}: {
  ticker: string
  shockDate?: string | null
  shockSharePct?: number | null
}) {
  const p = usePresentation()
  const prices = useQuery({
    queryKey: ['prices', ticker, DAYS],
    queryFn: ({ signal }) => fetchPricesAll(ticker, DAYS, signal),
    staleTime: 10 * 60 * 1000,
  })

  if (prices.isError) return <ErrorState error={prices.error} title="Fechamento indisponível" />
  if (prices.isLoading || !prices.data) return <Loading />

  const { points, marks, flags, notes } = prices.data.data
  const indexOf = new Map(points.map((point, index) => [point.date, index]))
  const events: ChartEvent[] = []
  const notesByIndex = new Map<number, string[]>()
  const addNote = (index: number, text: string) => {
    notesByIndex.set(index, [...(notesByIndex.get(index) ?? []), text])
  }
  for (const mark of marks) {
    const index = indexOf.get(mark.date)
    if (index == null) continue
    events.push({ index, kind: markKind(mark) })
    addNote(index, markText(mark))
  }
  const shockIndex = shockDate ? indexOf.get(shockDate) : undefined
  if (shockIndex != null) {
    if (!events.some((event) => event.index === shockIndex && event.kind === 'jump')) {
      events.push({ index: shockIndex, kind: 'jump' })
    }
    addNote(shockIndex, `pregão dominante: ${sharePct(shockSharePct)} da variância de 30 pregões`)
  }
  points.forEach((point, index) => {
    if (!point.traded) addNote(index, p.copy('chart', 'no_trade').label)
  })

  const traded = points.filter((point) => point.traded).length
  const captionParts = [
    `${traded} de ${points.length} pregões com negócio (${dateBr(points[0]?.date)} a ${dateBr(points[points.length - 1]?.date)})`,
    flags.includes('scale_adjusted')
      ? 'desdobramento/grupamento aplicado às barras anteriores ao evento'
      : null,
    'proventos não descontados',
  ].filter(Boolean)

  return (
    <>
      <LineChart
        labels={points.map((point) => shortDate(point.date))}
        tipLabels={points.map((point) => dateBr(point.date))}
        details={points.map((_, index) => notesByIndex.get(index)?.join(' · ') ?? null)}
        series={[{ label: p.copy('spot', 'close').label, values: points.map((point) => point.close_brl) }]}
        formatValue={(v) => brl(v)}
        ariaLabel={`Fechamento de ${ticker} nos últimos ${points.length} pregões`}
        showPoints={false}
        dashedGap={1}
        gapLabel={p.copy('chart', 'no_trade_gap').label}
        events={events}
        eventLabels={{
          corporate: p.copy('chart', 'corporate').label,
          earnings: p.copy('chart', 'earnings').label,
          jump: p.copy('chart', 'jump').label,
        }}
        caption={captionParts.join(' · ')}
      />
      {notes.map((note) => (
        <p className="my-[8px] text-[calc(11px*var(--font-scale))] text-muted-foreground lg:text-label" key={note}>{note}</p>
      ))}
    </>
  )
}
