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
  /** Tooltip do rótulo quando o nome completo não cabe (ex.: sigla). */
  hint?: string
}

export function Metric({ label, value, note, onOpen, emphasis = false, hint }: MetricProps) {
  const classes = [
    'block min-h-[44px] bg-card px-[12px] py-[10px] text-left text-foreground',
    onOpen
      ? 'cursor-pointer transition-[background-color] duration-[120ms] ease-[ease] hover:bg-secondary focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-primary'
      : '',
  ].filter(Boolean).join(' ')

  const content = (
    <>
      <span className={`mb-[4px] block text-[11px] text-[var(--text-2)] lg:text-label ${onOpen ? 'border-b border-dotted border-input' : ''}`}>{label}</span>
      <span className={`block text-right font-mono tabular-nums text-foreground lg:leading-title ${emphasis ? 'text-[22px] lg:text-hero' : 'text-[16px] lg:text-metric'}`}>{value}</span>
      {note && <span className="mt-[8px] block lg:text-label">{note}</span>}
    </>
  )

  if (onOpen) {
    return (
      <button
        type="button"
        className={classes}
        onClick={onOpen}
        title={hint ?? `O que é ${label}`}
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
  hint,
}: {
  field: string
  label: string
  value: string
  onOpen: OpenMetric
  note?: ReactNode
  emphasis?: boolean
  hint?: string
}) {
  return (
    <Metric
      label={label}
      value={value}
      note={note}
      emphasis={emphasis}
      hint={hint}
      onOpen={() => onOpen(field, label, value)}
    />
  )
}

/** `inCard`: dentro do cartão de posição a margem inferior é 12 px (era o
 * seletor `.position-card .metric-grid` do legado). */
export function MetricGrid({ children, inCard = false }: { children: ReactNode; inCard?: boolean }) {
  return (
    <div className={`${inCard ? 'mb-[12px]' : 'mb-[16px]'} grid grid-cols-[repeat(2,minmax(0,1fr))] gap-[1px] overflow-hidden rounded-[6px] border border-border bg-border sm:grid-cols-[repeat(3,minmax(0,1fr))]`}>
      {children}
    </div>
  )
}
