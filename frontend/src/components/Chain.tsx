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
      <span className="chain-detail__key">{label}</span>
      <span className="num">{value}</span>
    </>
  )
  if (field && onMetric) {
    return (
      <button
        type="button"
        className="chain-detail__row chain-detail__row--button"
        onClick={() => onMetric(field, label, value)}
      >
        {content}
      </button>
    )
  }
  return <div className="chain-detail__row">{content}</div>
}

function SideDetail({
  title,
  row,
  onMetric,
}: {
  title: string
  row: ChainRow | undefined
  onMetric: OpenMetric
}) {
  if (!row) {
    return (
      <div className="chain-detail__side">
        <div className="chain-detail__label">{title}</div>
        <div className="chain-detail__empty">sem série neste lado</div>
      </div>
    )
  }
  const book = row.bid_brl != null && row.ask_brl != null
    ? `${brl(row.bid_brl)} / ${brl(row.ask_brl)}`
    : 'sem oferta'
  return (
    <div className="chain-detail__side">
      <div className="chain-detail__label">
        {title} <span className="chain-detail__ticker">{row.ticker}</span>
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
      <div className="chain-detail__flags">
        {row.flags.length > 0 ? row.flags.join(' · ') : 'sem flags'}
      </div>
    </div>
  )
}

export function Chain({
  data,
  maturity,
  onMetric,
}: {
  data: ChainData
  maturity?: string | null
  onMetric: OpenMetric
}) {
  const [open, setOpen] = useState<string | null>(null)
  const rows = maturity ? data.rows.filter((row) => row.maturity === maturity) : data.rows
  const groups = useMemo(() => group(rows), [rows])

  if (groups.length === 0) {
    return <p className="chain-empty">nenhuma série neste recorte</p>
  }

  return (
    <div className="chain">
      {groups.map((groupRow) => (
        <section className="chain-group" key={groupRow.maturity}>
          <header className="chain-group__head">
            <span>{groupRow.maturity}</span>
            <span className="num">{groupRow.strikes.length} strikes</span>
          </header>
          <div className="chain-head" aria-hidden="true">
            <span>Put</span>
            <span>Preço de exercício</span>
            <span>Call</span>
          </div>
          {groupRow.strikes.map((pair) => {
            const id = `${groupRow.maturity}:${pair.strike.toFixed(2)}`
            const expanded = open === id
            return (
              <div className="chain-item" key={id}>
                <button
                  type="button"
                  className="chain-row"
                  aria-expanded={expanded}
                  onClick={() => setOpen(expanded ? null : id)}
                >
                  <span className="num">{fmt(pair.put?.iv_pct, 1)}</span>
                  <span className="num chain-row__strike">{fmt(pair.strike)}</span>
                  <span className="num">{fmt(pair.call?.iv_pct, 1)}</span>
                </button>
                {expanded && (
                  <div className="chain-detail">
                    <SideDetail title="Put" row={pair.put} onMetric={onMetric} />
                    <SideDetail title="Call" row={pair.call} onMetric={onMetric} />
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
