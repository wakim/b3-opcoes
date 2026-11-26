/** Estados explícitos: erro com code/message/hint e carregamento quieto. */
import type { ReactNode } from 'react'
import { ApiError } from '../lib/api'
import { note } from '../lib/classes'
import { usePresentation } from '../lib/presentation'
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
  const p = usePresentation()
  const api = error instanceof ApiError ? error : null
  const notFound = api?.isNotFound ?? false
  const code = api?.code ?? 'erro'
  const friendly = p.error(code)
  const message = error instanceof Error ? error.message : String(error)

  return (
    <Alert variant={tone === 'inline' ? 'destructive-inline' : 'default'}>
      <div className={tone === 'inline' ? 'font-mono text-[calc(11px*var(--font-scale))] text-destructive lg:text-label' : 'lg:text-label font-mono text-destructive'}>{code}</div>
      {tone === 'block' && (
        <div className="lg:text-block-title lg:leading-title font-medium">
          {title ?? (notFound ? p.error('not_found').label : p.error('unknown').label)}
        </div>
      )}
      <div className={tone === 'inline' ? undefined : 'lg:text-body lg:leading-body'}>{message}</div>
      {api?.hint && <div data-note="" className={tone === 'inline' ? `${note} lg:text-label` : 'lg:text-label text-muted-foreground'}>{api.hint}</div>}
      {!api?.hint && friendly.help && <div data-note="" className={tone === 'inline' ? `${note} lg:text-label` : 'lg:text-label text-muted-foreground'}>{friendly.help}</div>}
    </Alert>
  )
}

export function ErrorState({ error, title }: { error: unknown; title?: string }) {
  return <ErrorStatus error={error} title={title} />
}

export function Loading({ label }: { label?: string }): ReactNode {
  const p = usePresentation()
  return <p className="lg:text-label text-muted-foreground" role="status">{label ?? p.copy('loading', 'generic').label}</p>
}
