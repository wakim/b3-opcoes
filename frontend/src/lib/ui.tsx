import { useEffect, useId, useRef, type ReactNode } from 'react'
import { ApiError } from './api'

export function ErrorBox({ error }: { error: Error | null }): ReactNode {
  if (!error) return null
  const api = error instanceof ApiError ? error : null
  return (
    <div className="error" role="alert">
      <div className="code">{api?.code ?? 'erro'}</div>
      <div>{error.message}</div>
      {api?.hint && <div className="note">{api.hint}</div>}
    </div>
  )
}

export function Loading(): ReactNode {
  return <p className="note">carregando…</p>
}

/** Painel inferior fixo (bottom sheet). Sem fetch e sem tipos de API —
 *  reutilizável na F5-B. Fecha por botão, backdrop e Escape; trava o scroll do
 *  body enquanto aberto e devolve o foco a quem abriu ao fechar. */
export function Sheet({
  title,
  meta,
  onClose,
  children,
}: {
  title: string
  meta: string
  onClose: () => void
  children: ReactNode
}): ReactNode {
  const closeRef = useRef<HTMLButtonElement>(null)
  const onCloseRef = useRef(onClose)
  const titleId = useId()

  // Mantém o handler mais recente sem reexecutar o efeito de montagem quando o
  // chamador passa uma closure nova a cada render.
  useEffect(() => {
    onCloseRef.current = onClose
  }, [onClose])

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    closeRef.current?.focus()

    function handleKey(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        onCloseRef.current()
      } else if (event.key === 'Tab') {
        // O botão "fechar" é o único elemento focável do sheet.
        event.preventDefault()
        closeRef.current?.focus()
      }
    }
    document.addEventListener('keydown', handleKey)
    return () => {
      document.removeEventListener('keydown', handleKey)
      document.body.style.overflow = previousOverflow
      opener?.focus()
    }
  }, [])

  return (
    <div className="sheet">
      <div className="sheet-backdrop" onClick={onClose} aria-hidden="true" />
      <div
        className="sheet-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <div className="sheet-head">
          <div className="sheet-heading">
            <h2 className="sheet-title" id={titleId}>
              {title}
            </h2>
            <span className="sheet-meta">{meta}</span>
          </div>
          <button ref={closeRef} className="sheet-close" onClick={onClose}>
            fechar
          </button>
        </div>
        <div className="sheet-body">{children}</div>
      </div>
    </div>
  )
}
