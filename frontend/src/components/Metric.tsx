/** Rótulo em caixa de frase + número mono alinhado à direita.
 *
 * Sem sparkline, sem ícone decorativo e sem "card de estatística". Quando há
 * verbete, o rótulo inteiro é o alvo de toque (≥ 44 px) e abre a ficha.
 */
import type { ReactNode } from 'react'
import type { OpenMetric } from './metricKeys'

export interface MetricProps {
  label: string
  value: ReactNode
  note?: ReactNode
  onOpen?: () => void
  emphasis?: boolean
}

export function Metric({ label, value, note, onOpen, emphasis = false }: MetricProps) {
  const classes = [
    'metric',
    emphasis ? 'metric--emphasis' : '',
    onOpen ? 'metric--tappable' : '',
  ].filter(Boolean).join(' ')

  const content = (
    <>
      <span className="metric__label">{label}</span>
      <span className="metric__value">{value}</span>
      {note && <span className="metric__note">{note}</span>}
    </>
  )

  if (onOpen) {
    return (
      <button
        type="button"
        className={classes}
        onClick={onOpen}
        title={`O que é ${label}`}
      >
        {content}
      </button>
    )
  }
  return <div className={classes}>{content}</div>
}

/** Atalho para métricas que têm verbete: o valor formatado vai na ficha. */
export function GlossaryMetric({
  field,
  label,
  value,
  onOpen,
  note,
  emphasis,
}: {
  field: string
  label: string
  value: string
  onOpen: OpenMetric
  note?: ReactNode
  emphasis?: boolean
}) {
  return (
    <Metric
      label={label}
      value={value}
      note={note}
      emphasis={emphasis}
      onOpen={() => onOpen(field, label, value)}
    />
  )
}

export function MetricGrid({ children }: { children: ReactNode }) {
  return <div className="metric-grid">{children}</div>
}
