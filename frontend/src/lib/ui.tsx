import type { ReactNode } from 'react'
import { ApiError } from './api'
import { note } from './classes'

export function ErrorBox({ error }: { error: Error | null }): ReactNode {
  if (!error) return null
  const api = error instanceof ApiError ? error : null
  return (
    <div className="mb-[12px] rounded-[4px] border border-destructive bg-[rgba(200,131,131,0.08)] px-[12px] py-[10px] text-foreground" role="alert">
      <div className="font-mono text-[11px] text-destructive lg:text-label">{api?.code ?? 'erro'}</div>
      <div>{error.message}</div>
      {api?.hint && <div data-note="" className={`${note} lg:text-label`}>{api.hint}</div>}
    </div>
  )
}

export function Loading(): ReactNode {
  return <p data-note="" className={`${note} lg:text-label`}>carregando…</p>
}
