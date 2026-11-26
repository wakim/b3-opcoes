/** Rótulo em caixa de frase + número mono alinhado à direita.
 *
 * Sem sparkline, sem ícone decorativo e sem "card de estatística". Quando há
 * verbete, o rótulo inteiro é o alvo de toque (≥ 44 px) e abre a ficha.
 *
 * F9: `term`/`variant`/`params` são opcionais. `label` explícito continua sendo
 * override. A dica `O que é ${label}` não é mais sintetizada no componente:
 * ela é template da entrada `metric.generic` do dicionário.
 */
import type { ReactNode } from 'react'
import { useCopy, type CopyParams, type PresentationKey } from '../lib/presentation'
import { presentationKeyForField, type OpenMetric } from './metricKeys'
import { InfoTip } from './InfoTip'

export interface MetricProps {
  label?: string
  value: ReactNode
  note?: ReactNode
  onOpen?: () => void
  emphasis?: boolean
  /** Tooltip do rótulo quando o nome completo não cabe (ex.: sigla). */
  hint?: string
  term?: PresentationKey
  variant?: string
  params?: CopyParams
}

export function Metric({
  label,
  value,
  note,
  onOpen,
  emphasis = false,
  hint,
  term,
  variant,
  params,
}: MetricProps) {
  const resolved = useCopy(term ?? 'metric.generic', variant, params)
  const displayLabel = label ?? resolved.label
  const generic = useCopy('metric.generic', undefined, { ...params, label: displayLabel })
  // Com term, a ajuda é a da chave semântica; sem term, a genérica com o rótulo.
  const help = hint ?? (term ? resolved.help : generic.help)

  const classes = [
    'block min-h-[44px] bg-card px-[12px] py-[10px] text-left text-foreground',
    onOpen
      ? 'cursor-pointer transition-[background-color] duration-[120ms] ease-[ease] hover:bg-secondary focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-primary'
      : '',
  ].filter(Boolean).join(' ')

  const content = (
    <>
      <span className={`mb-[4px] block text-[calc(11px*var(--font-scale))] text-[var(--text-2)] lg:text-label ${onOpen ? 'border-b border-dotted border-input' : ''}`}>{displayLabel}</span>
      <span className={`block text-right font-mono tabular-nums text-foreground lg:leading-title ${emphasis ? 'text-[calc(22px*var(--font-scale))] lg:text-hero' : 'text-[calc(16px*var(--font-scale))] lg:text-metric'}`}>{value}</span>
      {note && <span className="mt-[8px] block lg:text-label">{note}</span>}
    </>
  )

  if (onOpen) {
    const button = (
      <button
        type="button"
        className={classes}
        onClick={onOpen}
      >
        {content}
      </button>
    )
    return help ? <InfoTip text={help}>{button}</InfoTip> : button
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
  term,
  variant,
  params,
}: {
  field: string
  label?: string
  value: string
  onOpen: OpenMetric
  note?: ReactNode
  emphasis?: boolean
  hint?: string
  term?: PresentationKey
  variant?: string
  params?: CopyParams
}) {
  const inferred = term ?? presentationKeyForField(field)
  const copy = useCopy(inferred ?? 'metric.generic', variant, params)
  const displayLabel = label ?? copy.label

  return (
    <Metric
      label={displayLabel}
      value={value}
      note={note}
      emphasis={emphasis}
      hint={hint}
      term={inferred ?? undefined}
      variant={variant}
      params={params}
      onOpen={() => onOpen(field, displayLabel, value, inferred ?? undefined, variant, params)}
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
