/** Gráfico de linha em SVG inline — sem biblioteca de gráfico.
 *
 * Série principal em branco-gelo, comparador em cinza tracejado: é a regra da
 * direção para distinguir séries sem inventar cor. Só formata o que vem da API.
 *
 * O eixo x pode ser **por tempo** (`xValues`, ex.: `t` em anos): a distância
 * entre pontos passa a refletir o intervalo real, não o índice. Quando há
 * buracos de observação, `dashedGap` liga os pontos com tracejado em vez de
 * fingir uma curva contínua.
 *
 * Leitura ponto a ponto: crosshair + legenda flutuante (desktop) ou leitura
 * fixa (mobile), com mouse, toque e teclado. A tabela de valores fica na página
 * que usa o gráfico — o tooltip não substitui o dado acessível.
 */
import { useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import { num, visuallyHidden } from '../lib/classes'

export interface ChartSeries {
  label: string
  values: Array<number | null>
  dashed?: boolean
  /** `true` no índice = ponto vazado (leitura sem série líquida, não preço). */
  hollow?: boolean[]
}

export interface LineChartProps {
  labels: string[]
  series: ChartSeries[]
  ariaLabel: string
  height?: number
  formatValue?: (value: number) => string
  marks?: string[]
  compact?: boolean
  /** Marca todo ponto com IV; sem isso, um vencimento isolado some. */
  showPoints?: boolean
  /** Rodapé quieto para explicar lacunas e liquidez do recorte. */
  caption?: string
  /** Posição numérica de cada rótulo no eixo x (monotônica). Sem isso, o índice. */
  xValues?: number[]
  /** Posições de todos os vencimentos do recorte, para marcar a densidade real. */
  xTickValues?: number[]
  /** Liga observações com tracejado quando o salto em x passa deste limiar. */
  dashedGap?: number
  /** `band` desenha uma faixa "sem IV" no vão e mantém a linha sólida; `dashed` quebra o traço. */
  gapStyle?: 'dashed' | 'band'
  /** Rótulo completo de cada ponto (ex.: data com ano); o eixo segue curto. */
  tipLabels?: string[]
  /** Linha quieta extra no tooltip (ex.: séries líquidas e forward). */
  details?: Array<string | null>
}

type Point = { i: number; x: number; y: number }

export function LineChart({
  labels,
  series,
  ariaLabel,
  height = 132,
  formatValue = (v) => String(v),
  marks = [],
  compact = false,
  showPoints = true,
  caption,
  xValues,
  xTickValues = [],
  dashedGap,
  gapStyle = 'dashed',
  tipLabels,
  details = [],
}: LineChartProps) {
  const [active, setActive] = useState<number | null>(null)
  const wrapRef = useRef<HTMLDivElement>(null)

  const finite = series
    .flatMap((s) => s.values)
    .filter((v): v is number => v != null && Number.isFinite(v))

  if (labels.length < 2 || finite.length < 2) {
    return <p className="mx-0 my-[8px] text-[11.5px] text-muted-foreground lg:text-detail">sem pontos suficientes para desenhar</p>
  }

  const W = 320
  const H = compact ? Math.min(height, 96) : height
  const PAD = 6
  const PAD_X = 5
  const inner = H - 2 * PAD
  let lo = Math.min(...finite)
  let hi = Math.max(...finite)
  if (hi === lo) {
    lo -= 1
    hi += 1
  }
  const span = hi - lo

  const timeAxis = Array.isArray(xValues) && xValues.length === labels.length
  const xMin = timeAxis ? Math.min(...xValues) : 0
  const xMax = timeAxis ? Math.max(...xValues) : labels.length - 1
  const xAt = (value: number): number =>
    PAD_X + ((value - xMin) / (xMax - xMin || 1)) * (W - 2 * PAD_X)
  const x = (i: number): number => (timeAxis ? xAt(xValues[i]) : PAD_X + (i / (labels.length - 1)) * (W - 2 * PAD_X))
  const y = (v: number): number => PAD + inner - ((v - lo) / span) * inner

  function pointsOf(values: Array<number | null>): Point[] {
    return values
      .map((v, i) => (v != null && Number.isFinite(v) ? { i, x: x(i), y: y(v) } : null))
      .filter((point): point is Point => point !== null)
  }

  /** Separa o traço em trechos sólidos e trechos tracejados (salto grande). */
  function segmentsOf(values: Array<number | null>): { solid: Point[][]; gap: Point[][] } {
    const points = pointsOf(values)
    const solid: Point[][] = []
    const gap: Point[][] = []
    const connectAll = gapStyle === 'band'
    let run: Point[] = []
    for (let k = 0; k < points.length; k += 1) {
      const point = points[k]
      if (k === 0) {
        run = [point]
        continue
      }
      const prev = points[k - 1]
      const step = timeAxis ? xValues[point.i] - xValues[prev.i] : point.i - prev.i
      const broken = connectAll
        ? false
        : dashedGap == null
          ? point.i !== prev.i + 1
          : step > dashedGap
      if (broken) {
        if (run.length > 1) solid.push(run)
        if (dashedGap != null) gap.push([prev, point])
        run = [point]
      } else {
        run.push(point)
      }
    }
    if (run.length > 1) solid.push(run)
    return { solid, gap }
  }

  /** Faixas "sem IV" entre observações distantes (só no estilo `band`). */
  const bands = (() => {
    if (dashedGap == null || gapStyle !== 'band') return []
    const points = pointsOf(series[0]?.values ?? [])
    const out: Array<{ x1: number; x2: number }> = []
    for (let k = 1; k < points.length; k += 1) {
      const prev = points[k - 1]
      const point = points[k]
      const step = timeAxis ? xValues[point.i] - xValues[prev.i] : point.i - prev.i
      if (step > dashedGap) out.push({ x1: prev.x, x2: point.x })
    }
    return out
  })()

  function pathOf(segment: Point[]): string {
    return segment
      .map((point, index) => `${index === 0 ? 'M' : 'L'}${point.x.toFixed(2)} ${point.y.toFixed(2)}`)
      .join(' ')
  }

  const axisIndices = (() => {
    if (!timeAxis) {
      return [0, Math.floor((labels.length - 1) / 2), labels.length - 1]
    }
    const fractions = [0, 0.5, 1]
    const picks = fractions.map((fraction) => {
      const target = xMin + fraction * (xMax - xMin)
      let best = 0
      let bestDistance = Infinity
      labels.forEach((_, index) => {
        const distance = Math.abs(xValues[index] - target)
        if (distance < bestDistance) {
          best = index
          bestDistance = distance
        }
      })
      return best
    })
    return [...new Set(picks)]
  })()

  const axis = axisIndices
    .map((index) => labels[index])
    .filter((value, index, all) => all.indexOf(value) === index)

  const activeIndex = active != null && active < labels.length ? active : null
  const activePoint = activeIndex != null && activeIndex >= 0 && activeIndex < labels.length ? activeIndex : null

  function nearestIndex(clientX: number, rect: DOMRect): number {
    const position = ((clientX - rect.left) / rect.width) * W
    let best = 0
    let bestDistance = Infinity
    labels.forEach((_, index) => {
      const distance = Math.abs(x(index) - position)
      if (distance < bestDistance) {
        best = index
        bestDistance = distance
      }
    })
    return best
  }

  function handlePointerMove(event: PointerEvent<SVGSVGElement>) {
    const rect = event.currentTarget.getBoundingClientRect()
    setActive(nearestIndex(event.clientX, rect))
  }

  function handlePointerLeave(event: PointerEvent<SVGSVGElement>) {
    if (event.pointerType === 'mouse') setActive(null)
  }

  function handleKeyDown(event: KeyboardEvent<SVGSVGElement>) {
    const current = activeIndex ?? -1
    if (event.key === 'ArrowLeft') {
      event.preventDefault()
      setActive(Math.max(0, current - 1))
    } else if (event.key === 'ArrowRight') {
      event.preventDefault()
      setActive(Math.min(labels.length - 1, current + 1))
    } else if (event.key === 'Home') {
      event.preventDefault()
      setActive(0)
    } else if (event.key === 'End') {
      event.preventDefault()
      setActive(labels.length - 1)
    } else if (event.key === 'Escape') {
      setActive(null)
    }
  }

  const tipLabel = activePoint != null ? (tipLabels?.[activePoint] ?? labels[activePoint]) : null
  const tipPosition = activePoint != null ? (x(activePoint) / W) * 100 : 50
  const tipRows = activePoint != null
    ? series.map((s) => {
        const value = s.values[activePoint]
        const shown = value != null && Number.isFinite(value) ? formatValue(value) : '—'
        return { label: s.label, value: shown }
      })
    : []
  const tipDetail = activePoint != null ? details[activePoint] : null
  const liveText =
    activePoint != null
      ? `${tipLabel}: ${tipRows.map((row) => `${row.label} ${row.value}`).join('; ')}${tipDetail ? `. ${tipDetail}` : ''}`
      : ''

  return (
    <div className={`chart${compact ? ' chart--compact' : ''}`} ref={wrapRef}>
      <div className="chart__bounds lg:text-chart" aria-hidden="true">
        <span>máx {formatValue(hi)}</span>
        <span>mín {formatValue(lo)}</span>
      </div>
      <svg
        className="chart__svg"
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`${ariaLabel}. Use as setas para percorrer os pontos.`}
        tabIndex={0}
        onPointerMove={handlePointerMove}
        onPointerDown={handlePointerMove}
        onPointerLeave={handlePointerLeave}
        onKeyDown={handleKeyDown}
        onBlur={() => setActive(null)}
      >
        <line className="chart__grid" x1={0} x2={W} y1={PAD} y2={PAD} />
        <line className="chart__grid chart__grid--mid" x1={0} x2={W} y1={PAD + inner / 2} y2={PAD + inner / 2} />
        <line className="chart__grid" x1={0} x2={W} y1={H - PAD} y2={H - PAD} />
        {bands.map((band, index) => {
          const width = band.x2 - band.x1
          if (width < 2) return null
          return (
            <rect
              key={`band-${index}`}
              className="chart__band"
              x={band.x1}
              y={PAD}
              width={width}
              height={inner}
            />
          )
        })}
        {xTickValues
          .filter((value) => value >= xMin && value <= xMax)
          .map((value) => (
            <line
              key={`tick-${value}`}
              className="chart__tick"
              x1={xAt(value)}
              x2={xAt(value)}
              y1={H - PAD}
              y2={H - PAD + 4}
            />
          ))}
        {marks.map((mark) => {
          const index = labels.indexOf(mark)
          if (index < 0) return null
          return (
            <line
              key={mark}
              className="chart__mark"
              x1={x(index)}
              x2={x(index)}
              y1={PAD - 2}
              y2={H - PAD + 2}
            />
          )
        })}
        {activePoint != null && (
          <line
            className="chart__cross"
            x1={x(activePoint)}
            x2={x(activePoint)}
            y1={PAD - 2}
            y2={H - PAD + 2}
          />
        )}
        {series.map((s) => {
          const { solid, gap } = segmentsOf(s.values)
          const points = pointsOf(s.values)
          const lastIndex = points.length > 0 ? points[points.length - 1].i : -1
          return (
            <g key={s.label}>
              {solid.map((segment, index) => (
                <path
                  key={`solid-${index}`}
                  className={`chart__line${s.dashed ? ' chart__line--dashed' : ''}`}
                  d={pathOf(segment)}
                  fill="none"
                />
              ))}
              {gap.map((segment, index) => (
                <path
                  key={`gap-${index}`}
                  className="chart__line chart__line--gap"
                  d={pathOf(segment)}
                  fill="none"
                />
              ))}
              {points
                .filter((point) => showPoints || point.i === lastIndex || point.i === activePoint)
                .map((point) => {
                  const hollow = s.hollow?.[point.i] === true
                  const isActive = point.i === activePoint
                  return (
                    <circle
                      key={point.i}
                      className={`chart__dot${s.dashed ? ' chart__dot--muted' : ''}${hollow ? ' chart__dot--hollow' : ''}`}
                      cx={point.x}
                      cy={point.y}
                      r={isActive ? 3.2 : point.i === lastIndex ? 2.5 : 1.8}
                    />
                  )
                })}
            </g>
          )
        })}
      </svg>
      {bands.map((band, index) => {
        const width = band.x2 - band.x1
        if (width < 40) return null
        return (
          <span
            key={`tag-${index}`}
            className="chart__band-tag lg:text-chart"
            style={{ left: `${((band.x1 + band.x2) / 2 / W) * 100}%` }}
          >
            sem IV
          </span>
        )
      })}
      <div className="chart__axis lg:text-chart" aria-hidden="true">
        {axis.map((label) => (
          <span key={label}>{label}</span>
        ))}
      </div>
      {tipLabel != null && (
        <div className="chart__tip lg:text-chart lg:leading-table" style={{ left: `clamp(112px, ${tipPosition}%, calc(100% - 112px))` }} aria-hidden="true">
          <div className="chart__tip-head">{tipLabel}</div>
          {tipRows.map((row) => (
            <div className="chart__tip-row" key={row.label}>
              <span>{row.label}</span>
              <span className={num}>{row.value}</span>
            </div>
          ))}
          {tipDetail && <div className="chart__tip-note">{tipDetail}</div>}
        </div>
      )}
      <p className={visuallyHidden} aria-live="polite">
        {liveText}
      </p>
      <div className="chart__legend lg:text-chart">
        {series.map((s) => (
          <span key={s.label}>
            <i className={`chart__key${s.dashed ? ' chart__key--dashed' : ''}`} aria-hidden="true" />
            {s.label}
          </span>
        ))}
        {marks.length > 0 && <span className="chart__legend-note">tracejado vertical: ajuste</span>}
        {series.some((s) => s.hollow?.some(Boolean)) && (
          <span className="chart__legend-note">ponto vazado: ATM sem série líquida</span>
        )}
        {dashedGap != null && gapStyle === 'band' && (
          <span className="chart__legend-note">faixa: intervalo sem IV</span>
        )}
        {dashedGap != null && gapStyle === 'dashed' && (
          <span className="chart__legend-note">traço tracejado: intervalo sem IV</span>
        )}
      </div>
      {caption && <p className="chart__caption lg:text-chart">{caption}</p>}
    </div>
  )
}
