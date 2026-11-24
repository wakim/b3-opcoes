/** Formatação de saída — nunca calcula número, só apresenta o que a API deu. */

const nf = (digits: number) =>
  new Intl.NumberFormat('pt-BR', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })

export function fmt(value: number | null | undefined, digits = 2): string {
  return value == null ? '—' : nf(digits).format(value)
}

/** `2026-10-26` → `26/10/2026` (só apresentação). */
export function dateBr(iso: string | null | undefined): string {
  if (!iso) return '—'
  const [year, month, day] = iso.split('-')
  return `${day}/${month}/${year}`
}

/** Fração da variância com piso: 49,97% não pode aparecer como "50%" sem a flag. */
export function sharePct(value: number | null | undefined): string {
  return value == null ? '—' : `${Math.floor(value)}%`
}

export function brl(value: number | null | undefined): string {
  return value == null ? '—' : `R$ ${fmt(value)}`
}
