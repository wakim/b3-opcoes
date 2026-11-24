/** Payoff no vencimento em SVG inline — sem biblioteca de gráfico.
 *
 * Eixo x = `spot_brl`, eixo y = `pnl_brl`; os pontos vêm prontos do backend
 * (`POST /api/structure` → `payoff`). Aqui só se desenha: nenhuma fórmula.
 * Linha de zero tracejada; breakevens marcados em vertical.
 */
import { brl, fmt } from '../lib/format'

export interface PayoffPoint {
  spot_brl: number
  pnl_brl: number
}

export function PayoffChart({
  points,
  breakevens = [],
  ariaLabel,
}: {
  points: PayoffPoint[]
  breakevens?: number[]
  ariaLabel: string
}) {
  if (points.length < 2) {
    return <p className="chart-empty">payoff indisponível</p>
  }

  const W = 320
  const H = 168
  const PAD = 8
  const PAD_X = 4
  const inner = H - 2 * PAD

  const spots = points.map((point) => point.spot_brl)
  const pnls = points.map((point) => point.pnl_brl)
  const xLo = Math.min(...spots)
  const xHi = Math.max(...spots)
  let yLo = Math.min(0, ...pnls)
  let yHi = Math.max(0, ...pnls)
  if (yHi === yLo) {
    yLo -= 1
    yHi += 1
  }
  const xSpan = xHi - xLo || 1
  const ySpan = yHi - yLo

  const x = (v: number) => PAD_X + ((v - xLo) / xSpan) * (W - 2 * PAD_X)
  const y = (v: number) => PAD + inner - ((v - yLo) / ySpan) * inner

  const path = points
    .map((point, index) => `${index === 0 ? 'M' : 'L'}${x(point.spot_brl).toFixed(2)} ${y(point.pnl_brl).toFixed(2)}`)
    .join(' ')

  const axis = [xLo, xLo + xSpan / 2, xHi]

  return (
    <div className="chart chart--payoff">
      <div className="chart__bounds" aria-hidden="true">
        <span>P&amp;L máx {brl(yHi)}</span>
        <span>P&amp;L mín {brl(yLo)}</span>
      </div>
      <svg className="chart__svg" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={ariaLabel}>
        <line className="chart__grid" x1={0} x2={W} y1={PAD} y2={PAD} />
        <line className="chart__grid" x1={0} x2={W} y1={H - PAD} y2={H - PAD} />
        <line
          className="chart__grid chart__grid--mid"
          x1={0}
          x2={W}
          y1={y(0)}
          y2={y(0)}
        />
        {breakevens
          .filter((value) => value >= xLo && value <= xHi)
          .map((value) => (
            <line
              key={value}
              className="chart__mark"
              x1={x(value)}
              x2={x(value)}
              y1={PAD - 2}
              y2={H - PAD + 2}
            />
          ))}
        <path className="chart__line" d={path} fill="none" />
      </svg>
      <div className="chart__axis" aria-hidden="true">
        {axis.map((value, index) => (
          <span key={index}>{fmt(value)}</span>
        ))}
      </div>
      <div className="chart__legend">
        <span>
          <i className="chart__key" aria-hidden="true" />
          P&amp;L no vencimento
        </span>
        <span className="chart__legend-note">eixo x: preço do papel (R$)</span>
        {breakevens.length > 0 && (
          <span className="chart__legend-note">tracejado vertical: breakeven</span>
        )}
      </div>
    </div>
  )
}
