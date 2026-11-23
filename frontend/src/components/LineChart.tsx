/** Gráfico de linha em SVG inline — sem biblioteca de gráfico.
 *
 * Série principal em branco-gelo, comparador em cinza tracejado: é a regra da
 * direção para distinguir séries sem inventar cor. Só formata o que vem da API.
 */
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
}

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
}: LineChartProps) {
  const finite = series
    .flatMap((s) => s.values)
    .filter((v): v is number => v != null && Number.isFinite(v))

  if (labels.length < 2 || finite.length < 2) {
    return <p className="chart-empty">sem pontos suficientes para desenhar</p>
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
  const x = (i: number) => PAD_X + (i / (labels.length - 1)) * (W - 2 * PAD_X)
  const y = (v: number) => PAD + inner - ((v - lo) / span) * inner

  function pathOf(values: Array<number | null>): string {
    let d = ''
    let connected = false
    values.forEach((v, i) => {
      if (v == null || !Number.isFinite(v)) {
        connected = false
        return
      }
      d += `${connected ? ' L' : ' M'}${x(i).toFixed(2)} ${y(v).toFixed(2)}`
      connected = true
    })
    return d
  }

  function pointsOf(values: Array<number | null>): Array<{ i: number; x: number; y: number }> {
    return values
      .map((v, i) => (v != null && Number.isFinite(v) ? { i, x: x(i), y: y(v) } : null))
      .filter((point): point is { i: number; x: number; y: number } => point !== null)
  }

  const axis = [labels[0], labels[Math.floor((labels.length - 1) / 2)], labels[labels.length - 1]]
    .filter((value, index, all) => all.indexOf(value) === index)

  return (
    <div className={`chart${compact ? ' chart--compact' : ''}`}>
      <div className="chart__bounds" aria-hidden="true">
        <span>máx {formatValue(hi)}</span>
        <span>mín {formatValue(lo)}</span>
      </div>
      <svg
        className="chart__svg"
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={ariaLabel}
      >
        <line className="chart__grid" x1={0} x2={W} y1={PAD} y2={PAD} />
        <line className="chart__grid chart__grid--mid" x1={0} x2={W} y1={PAD + inner / 2} y2={PAD + inner / 2} />
        <line className="chart__grid" x1={0} x2={W} y1={H - PAD} y2={H - PAD} />
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
        {series.map((s) => {
          const points = pointsOf(s.values)
          const lastIndex = points.length > 0 ? points[points.length - 1].i : -1
          return (
            <g key={s.label}>
              <path
                className={`chart__line${s.dashed ? ' chart__line--dashed' : ''}`}
                d={pathOf(s.values)}
                fill="none"
              />
              {points
                .filter((point) => showPoints || point.i === lastIndex)
                .map((point) => {
                  const hollow = s.hollow?.[point.i] === true
                  return (
                    <circle
                      key={point.i}
                      className={`chart__dot${s.dashed ? ' chart__dot--muted' : ''}${hollow ? ' chart__dot--hollow' : ''}`}
                      cx={point.x}
                      cy={point.y}
                      r={point.i === lastIndex ? 2.5 : 1.8}
                    />
                  )
                })}
            </g>
          )
        })}
      </svg>
      <div className="chart__axis" aria-hidden="true">
        {axis.map((label) => (
          <span key={label}>{label}</span>
        ))}
      </div>
      <div className="chart__legend">
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
      </div>
      {caption && <p className="chart__caption">{caption}</p>}
    </div>
  )
}
