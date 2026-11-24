/** Estados explícitos: erro com code/message/hint e carregamento quieto. */
import type { ReactNode } from 'react'
import { ApiError } from '../lib/api'

export function ErrorState({ error, title }: { error: unknown; title?: string }) {
  const api = error instanceof ApiError ? error : null
  const notFound = api?.isNotFound ?? false
  const message = error instanceof Error ? error.message : String(error)

  return (
    <div className="error-state" role="alert">
      <div className="error-state__code lg:text-label">{api?.code ?? 'erro'}</div>
      <div className="error-state__title lg:text-block-title lg:leading-title">
        {title ?? (notFound ? 'Não encontrado' : 'Não foi possível carregar')}
      </div>
      <div className="error-state__msg lg:text-body lg:leading-body">{message}</div>
      {api?.hint && <div className="error-state__hint lg:text-label">{api.hint}</div>}
    </div>
  )
}

export function Loading({ label = 'carregando…' }: { label?: string }): ReactNode {
  return <p className="state-loading lg:text-label">{label}</p>
}
