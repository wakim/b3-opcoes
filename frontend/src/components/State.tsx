/** Estados explícitos: erro com code/message/hint e carregamento quieto. */
import type { ReactNode } from 'react'
import { ApiError } from '../lib/api'

export function ErrorState({ error, title }: { error: unknown; title?: string }) {
  const api = error instanceof ApiError ? error : null
  const notFound = api?.isNotFound ?? false
  const message = error instanceof Error ? error.message : String(error)

  return (
    <div
      className="grid gap-2 rounded-[6px] border border-destructive/40 bg-destructive/5 p-4"
      role="alert"
    >
      <div className="lg:text-label font-mono text-destructive">{api?.code ?? 'erro'}</div>
      <div className="lg:text-block-title lg:leading-title font-medium">
        {title ?? (notFound ? 'Não encontrado' : 'Não foi possível carregar')}
      </div>
      <div className="lg:text-body lg:leading-body">{message}</div>
      {api?.hint && <div className="lg:text-label text-muted-foreground">{api.hint}</div>}
    </div>
  )
}

export function Loading({ label = 'carregando…' }: { label?: string }): ReactNode {
  return <p className="lg:text-label text-muted-foreground" role="status">{label}</p>
}
