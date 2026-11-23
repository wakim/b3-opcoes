/** Formatação de saída — nunca calcula número, só apresenta o que a API deu. */

const nf = (digits: number) =>
  new Intl.NumberFormat('pt-BR', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })

export function fmt(value: number | null | undefined, digits = 2): string {
  return value == null ? '—' : nf(digits).format(value)
}

export function brl(value: number | null | undefined): string {
  return value == null ? '—' : `R$ ${fmt(value)}`
}
