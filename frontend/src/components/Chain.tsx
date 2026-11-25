/** Cadeia `put · strike · call`, agrupada por vencimento.
 *
 * O toque numa linha abre IV, spread (R$ e %), delta, preço e flags dos dois
 * lados. `bid_brl`/`ask_brl` nulos significam "sem oferta" — nunca `0,00`.
 * Open interest não está no payload da chain: não é exibido.
 */
import { useMemo, useState } from 'react'
import { brl, fmt } from '../lib/format'
import type { ChainData, ChainRow } from '../lib/api'
import type { OpenMetric } from './metricKeys'
import { Button } from './ui/button'
import { Input } from './ui/input'

type Side = { put?: ChainRow; call?: ChainRow }
type StrikePair = Side & { strike: number }
type MaturityGroup = { maturity: string; strikes: StrikePair[] }

function group(rows: ChainRow[]): MaturityGroup[] {
  const byMaturity = new Map<string, Map<string, Side>>()
  for (const row of rows) {
    let byStrike = byMaturity.get(row.maturity)
    if (!byStrike) {
      byStrike = new Map()
      byMaturity.set(row.maturity, byStrike)
    }
    const key = row.strike_brl.toFixed(2)
    let pair = byStrike.get(key)
    if (!pair) {
      pair = {}
      byStrike.set(key, pair)
    }
    if (row.side.toUpperCase().startsWith('P')) pair.put = row
    else pair.call = row
  }
  return [...byMaturity].map(([maturity, byStrike]) => ({
    maturity,
    strikes: [...byStrike]
      .map(([key, pair]) => ({ strike: Number(key), ...pair }))
      .sort((a, b) => a.strike - b.strike),
  }))
}

function DetailRow({
  label,
  value,
  field,
  onMetric,
}: {
  label: string
  value: string
  field?: string
  onMetric?: OpenMetric
}) {
  const content = (
    <>
      <span className={field && onMetric ? 'border-b border-dotted border-input text-[color:var(--text-2)]' : 'text-[color:var(--text-2)]'}>{label}</span>
      <span className="font-mono tabular-nums">{value}</span>
    </>
  )
  if (field && onMetric) {
    return (
      <button
        type="button"
        className="flex min-h-[44px] w-full cursor-pointer items-center justify-between gap-[8px] border-b border-border bg-transparent py-[3px] text-left text-[calc(11px*var(--font-scale))] text-foreground focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-primary lg:text-detail"
        onClick={() => onMetric(field, label, value)}
      >
        {content}
      </button>
    )
  }
  return <div className="flex min-h-[28px] w-full items-center justify-between gap-[8px] border-b border-border py-[3px] text-left text-[calc(11px*var(--font-scale))] text-foreground lg:text-detail">{content}</div>
}

function SideDetail({
  title,
  row,
  onMetric,
  onAddLeg,
}: {
  title: string
  row: ChainRow | undefined
  onMetric: OpenMetric
  onAddLeg?: (row: ChainRow, side: 'long' | 'short', quantity: number) => void
}) {
  const [quantity, setQuantity] = useState('100')
  if (!row) {
    return (
      <div>
        <div className="mb-[4px] text-[calc(11px*var(--font-scale))] text-[color:var(--text-2)] lg:text-label">{title}</div>
        <div className="mt-[8px] text-[calc(10.5px*var(--font-scale))] text-muted-foreground [overflow-wrap:anywhere] lg:text-detail">sem série neste lado</div>
      </div>
    )
  }
  const book = row.bid_brl != null && row.ask_brl != null
    ? `${brl(row.bid_brl)} / ${brl(row.ask_brl)}`
    : 'sem oferta'
  const parsed = Number.parseInt(quantity, 10)
  const canAdd = Number.isInteger(parsed) && parsed > 0
  return (
    <div>
      <div className="mb-[4px] text-[calc(11px*var(--font-scale))] text-[color:var(--text-2)] lg:text-label">
        {title} <span className="font-mono text-muted-foreground">{row.ticker}</span>
      </div>
      <DetailRow label="Volatilidade implícita" value={fmt(row.iv_pct)} field="iv_pct" onMetric={onMetric} />
      <DetailRow
        label="Diferença bid/ask"
        value={`${brl(row.spread_brl)} · ${fmt(row.spread_pct, 1)}%`}
        field="spread_brl"
        onMetric={onMetric}
      />
      <DetailRow label="Delta (por R$ 1)" value={fmt(row.delta, 3)} field="delta" onMetric={onMetric} />
      <DetailRow
        label="Preço"
        value={brl(row.mid_brl ?? row.close_brl)}
        field="mid_brl"
        onMetric={onMetric}
      />
      <DetailRow label="Ofertas (bid/ask)" value={book} field="bid_brl" onMetric={onMetric} />
      <div className="mt-[8px] text-[calc(10.5px*var(--font-scale))] text-muted-foreground [overflow-wrap:anywhere] lg:text-detail">
        {row.flags.length > 0 ? row.flags.join(' · ') : 'sem flags'}
      </div>
      {onAddLeg && (
        <div className="mt-[10px] flex flex-wrap items-center gap-[8px] border-t border-border pt-[10px]">
          <label className="inline-flex items-center gap-[6px] text-[calc(11px*var(--font-scale))] text-muted-foreground lg:text-label">
            <span className="text-muted-foreground">ações</span>
            <Input
              type="number"
              inputMode="numeric"
              min={1}
              step={1}
              variant="builder"
              className="w-[96px] min-w-[96px] max-w-[96px]"
              value={quantity}
              aria-label={`Quantidade de ações para ${row.ticker}`}
              onChange={(event) => setQuantity(event.target.value)}
            />
          </label>
          <Button
            type="button"
            variant="outline"
            size="sm"
            builder="button"
            disabled={!canAdd}
            onClick={() => onAddLeg(row, 'long', parsed)}
          >
            Comprar
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            builder="button"
            disabled={!canAdd}
            onClick={() => onAddLeg(row, 'short', parsed)}
          >
            Vender
          </Button>
        </div>
      )}
    </div>
  )
}

export function Chain({
  data,
  maturity,
  onMetric,
  onAddLeg,
}: {
  data: ChainData
  maturity?: string | null
  onMetric: OpenMetric
  onAddLeg?: (row: ChainRow, side: 'long' | 'short', quantity: number) => void
}) {
  const [open, setOpen] = useState<string | null>(null)
  const rows = maturity ? data.rows.filter((row) => row.maturity === maturity) : data.rows
  const groups = useMemo(() => group(rows), [rows])

  if (groups.length === 0) {
    return <p className="my-[8px] text-[calc(11.5px*var(--font-scale))] text-muted-foreground lg:text-detail">nenhuma série neste recorte</p>
  }

  return (
    <div>
      {groups.map((groupRow) => (
        <section className="mb-[20px]" key={groupRow.maturity}>
          <header className="flex justify-between gap-[8px] rounded-t-[4px] border border-b-0 border-border bg-muted px-[12px] py-[8px] font-mono text-[calc(11px*var(--font-scale))] text-[color:var(--text-2)] lg:text-label">
            <span>{groupRow.maturity}</span>
            <span className="font-mono tabular-nums">{groupRow.strikes.length} strikes</span>
          </header>
          <div
            className="grid grid-cols-[1fr_1fr_1fr] gap-[8px] border border-b-0 border-border px-[12px] py-[8px] text-[calc(11px*var(--font-scale))] text-muted-foreground [&>span:last-child]:text-right [&>span:nth-child(2)]:text-center lg:text-label"
            aria-hidden="true"
          >
            <span>Put</span>
            <span>Preço de exercício</span>
            <span>Call</span>
          </div>
          {groupRow.strikes.map((pair, index) => {
            const id = `${groupRow.maturity}:${pair.strike.toFixed(2)}`
            const expanded = open === id
            return (
              <div key={id}>
                <button
                  type="button"
                  className={`grid min-h-[44px] w-full cursor-pointer grid-cols-[1fr_1fr_1fr] gap-[8px] border border-border bg-card px-[12px] py-[8px] text-left text-[calc(12px*var(--font-scale))] tabular-nums text-foreground transition-[background-color] duration-[120ms] ease-[ease] focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-primary aria-expanded:bg-muted [&>span:last-child]:text-right [&>span:nth-child(2)]:text-center lg:text-chain lg:leading-table${index > 0 ? ' border-t-0' : ''}`}
                  aria-expanded={expanded}
                  onClick={() => setOpen(expanded ? null : id)}
                >
                  <span className="font-mono tabular-nums">{fmt(pair.put?.iv_pct, 1)}</span>
                  <span className="font-mono font-medium tabular-nums">{fmt(pair.strike)}</span>
                  <span className="font-mono tabular-nums">{fmt(pair.call?.iv_pct, 1)}</span>
                </button>
                {expanded && (
                  <div className="grid grid-cols-2 gap-[12px] rounded-b-[6px] border border-t-0 border-border bg-card p-[12px]">
                    <SideDetail title="Put" row={pair.put} onMetric={onMetric} onAddLeg={onAddLeg} />
                    <SideDetail title="Call" row={pair.call} onMetric={onMetric} onAddLeg={onAddLeg} />
                  </div>
                )}
              </div>
            )
          })}
        </section>
      ))}
    </div>
  )
}
