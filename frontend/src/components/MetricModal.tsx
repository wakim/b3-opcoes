/** Ficha de métrica em modal: verbete do glossário + o número do dia.
 *
 * Fonte única `/api/glossary/{key}` (staleTime 1 h). Diálogo centralizado com
 * foco no fechar, Escape, clique no backdrop e trap de Tab. Não importa
 * `lib/ui.tsx` (dono paralelo).
 */
import { useEffect, useRef, type KeyboardEvent as ReactKeyboardEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiGet, type GlossaryDetail } from '../lib/api'
import { glossaryKey, type ModalTarget } from './metricKeys'

/** `**O que é** — texto` → `texto`, parando no próximo rótulo em negrito. */
function section(md: string, name: string): string {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const match = md.match(new RegExp(`\\*\\*${escaped}\\*\\*\\s*—\\s*([\\s\\S]*?)(?=\\n\\s*\\*\\*|\\s*$)`))
  return match?.[1].trim() ?? ''
}

function Block({ title, text }: { title: string; text: string }) {
  if (!text) return null
  return (
    <div className="modal__block">
      <div className="modal__block-title">{title}</div>
      <p className="modal__block-text">{text}</p>
    </div>
  )
}

export function MetricModal({
  target,
  onClose,
}: {
  target: ModalTarget | null
  onClose: () => void
}) {
  const key = target ? glossaryKey(target.field) : null
  const entry = useQuery({
    queryKey: ['glossary', key],
    queryFn: ({ signal }) => apiGet<GlossaryDetail>(`/api/glossary/${key}`, signal),
    enabled: Boolean(key),
    staleTime: 3_600_000,
    retry: 1,
  })
  const dialogRef = useRef<HTMLElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!target) return
    const previous = document.activeElement as HTMLElement | null
    closeRef.current?.focus()
    return () => previous?.focus?.()
  }, [target])

  useEffect(() => {
    if (!target) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [target, onClose])

  useEffect(() => {
    if (!target) return
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previous
    }
  }, [target])

  if (!target) return null

  const onKeyDown = (event: ReactKeyboardEvent<HTMLElement>) => {
    if (event.key !== 'Tab') return
    const nodes = dialogRef.current?.querySelectorAll<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    )
    if (!nodes || nodes.length === 0) return
    const first = nodes[0]
    const last = nodes[nodes.length - 1]
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }

  const body = entry.data?.data.body_md ?? ''
  const definition = section(body, 'O que é')
  const mistake = section(body, 'Erro comum')
  const source = entry.data?.data.title

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <section
        ref={dialogRef}
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="metric-modal-title"
        onMouseDown={(event) => event.stopPropagation()}
        onKeyDown={onKeyDown}
      >
        <header className="modal__head">
          <div>
            <div className="modal__eyebrow">verbete</div>
            <h2 className="modal__title" id="metric-modal-title">{target.label}</h2>
            {source && source !== target.label && (
              <div className="modal__source">glossário: {source}</div>
            )}
          </div>
          <button ref={closeRef} type="button" className="modal__close" onClick={onClose} aria-label="Fechar">
            ×
          </button>
        </header>

        <div className="modal__body">
          <div className="modal__value">
            <span className="modal__value-label">no painel</span>
            <span className="modal__value-number">{target.value}</span>
          </div>
          {entry.isError && (
            <div className="modal__error" role="alert">
              verbete indisponível: {entry.error instanceof Error ? entry.error.message : 'erro'}
            </div>
          )}
          {entry.isLoading && <p className="state-loading">carregando verbete…</p>}
          <Block title="O que é" text={definition} />
          <Block title="Erro comum" text={mistake} />
          {!key && <p className="modal__note">esta métrica ainda não tem verbete no glossário.</p>}
        </div>
      </section>
    </div>
  )
}
