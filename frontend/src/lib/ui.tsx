import type { ReactNode } from 'react'
import { ApiError } from './api'

export function ErrorBox({ error }: { error: Error | null }): ReactNode {
  if (!error) return null
  const api = error instanceof ApiError ? error : null
  return (
    <div className="error" role="alert">
      <div className="code lg:text-label">{api?.code ?? 'erro'}</div>
      <div>{error.message}</div>
      {api?.hint && <div className="note lg:text-label">{api.hint}</div>}
    </div>
  )
}

export function Loading(): ReactNode {
  return <p className="note lg:text-label">carregando…</p>
}
