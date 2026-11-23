/** A assinatura: escala horizontal com agulha para métricas posicionais.
 *
 * O número continua visível no `Metric`; a régua só responde "onde ele está".
 * A agulha é o único branco-gelo fora do gráfico — não é cor decorativa.
 */
export interface RulerProps {
  value: number | null
  min: number
  max: number
  ticks?: number[]
  format?: (value: number) => string
  describe?: string
}

export function Ruler({
  value,
  min,
  max,
  ticks,
  format = (v) => String(v),
  describe = 'Posição',
}: RulerProps) {
  const marks = ticks ?? [min, (min + max) / 2, max]
  const clamped = value != null && (value < min || value > max)
  const pct = value == null
    ? null
    : Math.max(0, Math.min(1, (value - min) / (max - min))) * 100
  const aria = value == null
    ? `${describe}: indisponível; faixa ${format(min)} a ${format(max)}`
    : `${describe}: ${format(value)}; faixa ${format(min)} a ${format(max)}${clamped ? '; fora da faixa' : ''}`

  return (
    <span className={`ruler${clamped ? ' ruler--clamped' : ''}`} role="img" aria-label={aria}>
      <span className="ruler__track">
        {marks.map((mark) => (
          <span
            key={mark}
            className="ruler__tick"
            style={{ left: `${((mark - min) / (max - min)) * 100}%` }}
          />
        ))}
        {pct != null && <span className="ruler__needle" style={{ left: `${pct}%` }} />}
      </span>
      <span className="ruler__scale">
        {marks.map((mark) => (
          <span key={mark}>{format(mark)}</span>
        ))}
      </span>
    </span>
  )
}
