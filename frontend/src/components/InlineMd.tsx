/** Markdown inline do glossário, sem biblioteca: `**negrito**`, `` `código` `` e
 * `[texto](url)` (só o texto: a ficha não navega para fora).
 *
 * Nada vira HTML cru — cada pedaço é um nó React, então texto do verbete nunca
 * é interpretado como marcação. Marcador sem par fica como texto literal.
 */
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

const TOKEN = /\*\*([^*]+)\*\*|`([^`]+)`|\[([^\]]+)\]\([^)]+\)/g

export function InlineMd({
  text,
  codeClassName,
}: {
  text: string
  codeClassName?: string
}): ReactNode {
  const nodes: ReactNode[] = []
  let last = 0
  for (const match of text.matchAll(TOKEN)) {
    const start = match.index ?? 0
    if (start > last) nodes.push(text.slice(last, start))
    const [, bold, code, link] = match
    if (bold !== undefined) nodes.push(<strong key={start}>{bold}</strong>)
    else if (code !== undefined) nodes.push(<code key={start} className={cn('lg:text-detail', codeClassName)}>{code}</code>)
    else nodes.push(link)
    last = start + match[0].length
  }
  if (last < text.length) nodes.push(text.slice(last))
  return <>{nodes}</>
}
