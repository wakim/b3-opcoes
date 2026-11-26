/** A assinatura: escala horizontal com agulha para métricas posicionais.
 *
 * O número continua visível no `Metric`; a régua só responde "onde ele está".
 * A agulha é o único branco-gelo fora do gráfico — não é cor decorativa.
 * Os textos de acessibilidade vêm do dicionário; a geometria não muda.
 */
import { usePresentation } from '../lib/presentation'

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
  describe,
}: RulerProps) {
  const p = usePresentation()
  const marks = ticks ?? [min, (min + max) / 2, max]
  const clamped = value != null && (value < min || value > max)
  const pct = value == null
    ? null
    : Math.max(0, Math.min(1, (value - min) / (max - min))) * 100
  const subject = describe ?? p.copy('ruler', 'position').label
  const range = `${p.copy('ruler', 'range').label} ${format(min)} a ${format(max)}`
  const aria = value == null
    ? `${subject}: ${p.copy('ruler', 'unavailable').label}; ${range}`
    : `${subject}: ${format(value)}; ${range}${clamped ? `; ${p.copy('ruler', 'outside').label}` : ''}`

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
      <span className="ruler__scale lg:text-chart">
        {marks.map((mark) => (
          <span key={mark}>{format(mark)}</span>
        ))}
      </span>
    </span>
  )
}
