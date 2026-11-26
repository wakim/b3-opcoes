/** Rótulos e formatação de posição — apresentação, nunca cálculo financeiro.
 *
 * Tipos, status, flags e desfechos vêm do dicionário único (`usePresentation`);
 * aqui não resta mapa local. Números e unidades não são tocados.
 */
import { brl, fmt } from '../lib/format'
import { numNeg, numPos, quiet } from '../lib/classes'
import { usePresentation } from '../lib/presentation'
import { InfoTip } from './InfoTip'

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

export function PositionFlags({ flags, emptyLabel }: { flags: string[]; emptyLabel?: string }) {
  const p = usePresentation()
  const unique = [...new Set(flags)]
  if (unique.length === 0) return <span className={`${quiet} min-w-0 lg:text-label`}>{emptyLabel ?? 'sem flags'}</span>
  return (
    <span className="position-flags min-w-0 lg:text-label">
      {unique.map((code) => {
        const copy = p.flag(code)
        const help = [copy.help, `código ${code}`].filter(Boolean).join(' · ')
        return (
          <InfoTip key={code} text={help}>
            <span aria-label={help}>{copy.label}</span>
          </InfoTip>
        )
      })}
    </span>
  )
}
