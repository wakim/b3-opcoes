/** Estados explícitos: erro com code/message/hint e carregamento quieto. */
import type { ReactNode } from 'react'
import { ApiError } from '../lib/api'

export function ErrorState({ error, title }: { error: unknown; title?: string }) {
  const api = error instanceof ApiError ? error : null
  const notFound = api?.isNotFound ?? false
  const message = error instanceof Error ? error.message : String(error)

  return (
    <div className="error-state" role="alert">
      <div className="error-state__code">{api?.code ?? 'erro'}</div>
      <div className="error-state__title">
        {title ?? (notFound ? 'Não encontrado' : 'Não foi possível carregar')}
      </div>
      <div className="error-state__msg">{message}</div>
      {api?.hint && <div className="error-state__hint">{api.hint}</div>}
    </div>
  )
}

export function Loading({ label = 'carregando…' }: { label?: string }): ReactNode {
  return <p className="state-loading">{label}</p>
}
