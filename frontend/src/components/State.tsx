/** Estados explícitos: erro com code/message/hint e carregamento quieto. */
import type { ReactNode } from 'react'
import { ApiError } from '../lib/api'
import { note } from '../lib/classes'
import { Alert } from './ui/alert'

export function ErrorStatus({
  error,
  title,
  tone = 'block',
}: {
  error: unknown
  title?: string
  tone?: 'block' | 'inline'
}) {
  const api = error instanceof ApiError ? error : null
  const notFound = api?.isNotFound ?? false
  const message = error instanceof Error ? error.message : String(error)

  return (
    <Alert variant={tone === 'inline' ? 'destructive-inline' : 'default'}>
      <div className={tone === 'inline' ? 'font-mono text-[11px] text-destructive lg:text-label' : 'lg:text-label font-mono text-destructive'}>{api?.code ?? 'erro'}</div>
      {tone === 'block' && (
        <div className="lg:text-block-title lg:leading-title font-medium">
          {title ?? (notFound ? 'Não encontrado' : 'Não foi possível carregar')}
        </div>
      )}
      <div className={tone === 'inline' ? undefined : 'lg:text-body lg:leading-body'}>{message}</div>
      {api?.hint && <div data-note="" className={tone === 'inline' ? `${note} lg:text-label` : 'lg:text-label text-muted-foreground'}>{api.hint}</div>}
    </Alert>
  )
}

export function ErrorState({ error, title }: { error: unknown; title?: string }) {
  return <ErrorStatus error={error} title={title} />
}

export function Loading({ label = 'carregando…' }: { label?: string }): ReactNode {
  return <p className="lg:text-label text-muted-foreground" role="status">{label}</p>
}
