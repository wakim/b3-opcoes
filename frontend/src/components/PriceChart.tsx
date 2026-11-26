/** Fechamento do papel por pregão do lake, com marcas de evento.
 *
 * Nada é calculado aqui: o ajuste de desdobramento/grupamento e a detecção de
 * salto vêm do `QueryService.prices`. A tela só posiciona marcas por data e
 * descreve cada uma no tooltip. Proventos não são descontados do preço.
 */
import { useQuery } from '@tanstack/react-query'
import { fetchPricesAll, type PriceMark } from '../lib/api'
import { brl, dateBr, fmt, sharePct } from '../lib/format'
import { usePresentation, type PresentationApi } from '../lib/presentation'
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

function markText(p: PresentationApi, mark: PriceMark): string {
  switch (mark.kind) {
    case 'dividend':
      return mark.value != null
        ? p.copy('chart', 'mark_dividend_value', { value: brl(mark.value) }).label
        : p.copy('chart', 'mark_dividend').label
    case 'scale':
      return p.copy('chart', 'mark_scale', { value: fmt(mark.value, 4) }).label
    case 'indeterminate':
      return p.copy('chart', 'mark_indeterminate').label
    case 'earnings':
      return p.copy('chart', mark.detail === 'estimated' ? 'mark_earnings_estimated' : 'mark_earnings_actual').label
    case 'gap':
      return p.copy('chart', 'mark_gap', { value: fmt(mark.value, 2) }).label
    default:
      // Nota desconhecida do backend continua literal e explícita.
      return mark.kind
  }
}

function noteText(p: PresentationApi, note: string, ticker: string): string {
  if (!note.trim()) return p.copy('chart', 'note_empty').label
  if (note.includes('não negociou no mercado à vista')) {
    return p.copy('chart', 'note_no_trade', { ticker }).label
  }
  if (note.includes('não tem opção na janela')) {
    return p.copy('chart', 'note_no_options', { ticker }).label
  }
  return note
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

  if (prices.isError) return <ErrorState error={prices.error} title={p.copy('chart', 'price_error').label} />
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
    addNote(index, markText(p, mark))
  }
  const shockIndex = shockDate ? indexOf.get(shockDate) : undefined
  if (shockIndex != null) {
    if (!events.some((event) => event.index === shockIndex && event.kind === 'jump')) {
      events.push({ index: shockIndex, kind: 'jump' })
    }
    addNote(shockIndex, p.copy('chart', 'shock_note', { share: sharePct(shockSharePct) }).label)
  }
  points.forEach((point, index) => {
    if (!point.traded) addNote(index, p.copy('chart', 'no_trade').label)
  })

  const traded = points.filter((point) => point.traded).length
  const captionParts = [
    p.copy('chart', 'price_caption_count', {
      traded,
      total: points.length,
      start: dateBr(points[0]?.date),
      end: dateBr(points[points.length - 1]?.date),
    }).label,
    flags.includes('scale_adjusted') ? p.copy('chart', 'price_caption_scale').label : null,
    p.copy('chart', 'price_caption_dividend').label,
  ].filter(Boolean)

  return (
    <>
      <LineChart
        labels={points.map((point) => shortDate(point.date))}
        tipLabels={points.map((point) => dateBr(point.date))}
        details={points.map((_, index) => notesByIndex.get(index)?.join(' · ') ?? null)}
        series={[{ label: p.copy('spot', 'close').label, values: points.map((point) => point.close_brl) }]}
        formatValue={(v) => brl(v)}
        ariaLabel={p.copy('chart', 'price_aria', { ticker, total: points.length }).label}
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
        <p className="my-[8px] text-[calc(11px*var(--font-scale))] text-muted-foreground lg:text-label" key={note}>{noteText(p, note, ticker)}</p>
      ))}
    </>
  )
}
