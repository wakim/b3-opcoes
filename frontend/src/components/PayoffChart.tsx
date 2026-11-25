/** Payoff no vencimento em SVG inline — sem biblioteca de gráfico.
 *
 * Eixo x = `spot_brl`, eixo y = `pnl_brl`; os pontos vêm prontos do backend
 * (`POST /api/structure` → `payoff`). Aqui só se desenha: nenhuma fórmula.
 * Linha de zero tracejada; breakevens marcados em vertical.
 */
import { useState, type KeyboardEvent, type PointerEvent } from 'react'
import { brl, fmt } from '../lib/format'
import { num, visuallyHidden } from '../lib/classes'

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
  const [active, setActive] = useState<number | null>(null)
  if (points.length < 2) {
    return <p className="mx-0 my-[8px] text-[calc(11.5px*var(--font-scale))] text-muted-foreground lg:text-detail">payoff indisponível</p>
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
  const activeIndex = active != null && active < points.length ? active : null
  const activePoint = activeIndex != null ? points[activeIndex] : null

  function nearestIndex(clientX: number, rect: DOMRect): number {
    const position = ((clientX - rect.left) / rect.width) * W
    let best = 0
    let bestDistance = Infinity
    points.forEach((point, index) => {
      const distance = Math.abs(x(point.spot_brl) - position)
      if (distance < bestDistance) {
        best = index
        bestDistance = distance
      }
    })
    return best
  }

  function handlePointerMove(event: PointerEvent<SVGSVGElement>) {
    setActive(nearestIndex(event.clientX, event.currentTarget.getBoundingClientRect()))
  }

  function handleKeyDown(event: KeyboardEvent<SVGSVGElement>) {
    const current = activeIndex ?? -1
    if (event.key === 'ArrowLeft') {
      event.preventDefault()
      setActive(Math.max(0, current - 1))
    } else if (event.key === 'ArrowRight') {
      event.preventDefault()
      setActive(Math.min(points.length - 1, current + 1))
    } else if (event.key === 'Home') {
      event.preventDefault()
      setActive(0)
    } else if (event.key === 'End') {
      event.preventDefault()
      setActive(points.length - 1)
    } else if (event.key === 'Escape') {
      setActive(null)
    }
  }

  return (
    <div className="chart chart--payoff">
      <div className="chart__bounds lg:text-chart" aria-hidden="true">
        <span>P&amp;L máx {brl(yHi)}</span>
        <span>P&amp;L mín {brl(yLo)}</span>
      </div>
      <svg
        className="chart__svg"
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`${ariaLabel}. Use as setas para percorrer o payoff.`}
        tabIndex={0}
        onPointerMove={handlePointerMove}
        onPointerDown={handlePointerMove}
        onPointerLeave={(event) => {
          if (event.pointerType === 'mouse') setActive(null)
        }}
        onKeyDown={handleKeyDown}
        onBlur={() => setActive(null)}
      >
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
        {activePoint != null && (
          <line
            className="chart__cross"
            x1={x(activePoint.spot_brl)}
            x2={x(activePoint.spot_brl)}
            y1={PAD - 2}
            y2={H - PAD + 2}
          />
        )}
        <path className="chart__line" d={path} fill="none" />
        {activePoint != null && (
          <circle className="chart__dot" cx={x(activePoint.spot_brl)} cy={y(activePoint.pnl_brl)} r={3.2} />
        )}
      </svg>
      <div className="chart__axis lg:text-chart" aria-hidden="true">
        {axis.map((value, index) => (
          <span key={index}>{fmt(value)}</span>
        ))}
      </div>
      {activePoint != null && (
        <div
          className="chart__tip lg:text-chart lg:leading-table"
          style={{
            left: `clamp(112px, ${(x(activePoint.spot_brl) / W) * 100}%, calc(100% - 112px))`,
          }}
          aria-hidden="true"
        >
          <div className="chart__tip-head">Preço {brl(activePoint.spot_brl)}</div>
          <div className="chart__tip-row">
            <span>P&amp;L</span>
            <span className={num}>{brl(activePoint.pnl_brl)}</span>
          </div>
        </div>
      )}
      <p className={visuallyHidden} aria-live="polite">
        {activePoint != null ? `Preço ${brl(activePoint.spot_brl)}: P&L ${brl(activePoint.pnl_brl)}` : ''}
      </p>
      <div className="chart__legend lg:text-chart">
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
