/** Rótulos e formatação de posição — apresentação, nunca cálculo financeiro. */
import { brl, fmt } from '../lib/format'
import { numNeg, numPos, quiet } from '../lib/classes'
import { InfoTip } from './InfoTip'

const FLAG_LABELS: Record<string, string> = {
  near_expiry: 'vencimento próximo',
  itm: 'dentro do dinheiro',
  delta_drift: 'delta mudou',
  event_in_window: 'evento na janela',
  thesis_failed: 'tese não confirmada',
  capital_over: 'capital acima do limite',
  one_sided: 'livro de um lado só',
  mark_missing: 'marcação ausente',
  gregas_parciais: 'gregas parciais',
  unbounded_loss: 'perda ilimitada',
  payoff_unavailable: 'payoff indisponível',
  payoff_truncated: 'payoff truncado',
  close_snapshot_missing: 'snapshot do fechamento ausente',
}

const KIND_LABELS: Record<string, string> = {
  covered: 'cobertura',
  bull_call_spread: 'trava de alta',
  bear_put_spread: 'trava de baixa',
  straddle: 'straddle',
  strangle: 'strangle',
  put_caixa: 'put caixa',
  custom: 'estrutura',
}

export function positionKindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind
}

export function positionStatusLabel(status: string): string {
  if (status === 'open') return 'aberta'
  if (status === 'closed') return 'fechada'
  return status
}

export function legTypeLabel(type: string, optionSide?: string | null): string {
  if (type === 'stock') return 'ação'
  if (type === 'call' || optionSide === 'C') return 'call'
  if (type === 'put' || optionSide === 'P') return 'put'
  if (type === 'option') return 'opção'
  return type
}

export function predictionStatusLabel(status: string): string {
  if (status === 'pending') return 'pendente'
  if (status === 'hit') return 'confirmada'
  if (status === 'miss') return 'não confirmada'
  if (status === 'void') return 'sem avaliação'
  return status
}

export function formatPnlBrl(value: number | null): string {
  if (value == null) return '—'
  return `${value > 0 ? '+' : value < 0 ? '−' : ''}${brl(Math.abs(value))}`
}

export function formatPnlPct(value: number | null): string {
  if (value == null) return '—'
  const pct = value * 100
  return `${pct > 0 ? '+' : pct < 0 ? '−' : ''}${fmt(Math.abs(pct))}%`
}

export function pnlTone(value: number | null): string | undefined {
  if (value == null || value === 0) return undefined
  return value > 0 ? numPos : numNeg
}

export function PositionFlags({ flags, emptyLabel = 'sem flags' }: { flags: string[]; emptyLabel?: string }) {
  const unique = [...new Set(flags)]
  if (unique.length === 0) return <span className={`${quiet} min-w-0 lg:text-label`}>{emptyLabel}</span>
  return (
    <span className="position-flags min-w-0 lg:text-label">
      {unique.map((flag) => {
        const label = FLAG_LABELS[flag] ?? flag
        return (
          <InfoTip key={flag} text={`${label}; código ${flag}`}>
            <span aria-label={`${label}; código ${flag}`}>{label}</span>
          </InfoTip>
        )
      })}
    </span>
  )
}
