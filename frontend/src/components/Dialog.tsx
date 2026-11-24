/** Diálogo acessível reutilizado pelo formulário e pela confirmação de fechamento. */
import { useEffect, useRef, type KeyboardEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

const FOCUSABLE =
  'button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"]):not(:disabled)'

export function Dialog({
  labelledBy,
  describedBy,
  onClose,
  busy = false,
  children,
  className = '',
}: {
  labelledBy: string
  describedBy?: string
  onClose: () => void
  busy?: boolean
  children: ReactNode
  className?: string
}) {
  const dialogRef = useRef<HTMLElement>(null)
  const onCloseRef = useRef(onClose)
  const busyRef = useRef(busy)

  useEffect(() => {
    onCloseRef.current = onClose
    busyRef.current = busy
  }, [onClose, busy])

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'

    const focusables = () =>
      Array.from(dialogRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []).filter(
        (node) =>
          dialogRef.current?.contains(node) &&
          !node.matches(':disabled') &&
          node.getClientRects().length > 0,
      )
    const preferred = dialogRef.current?.querySelector<HTMLElement>('[data-autofocus]')
    const initialFocus = preferred && !preferred.matches(':disabled') ? preferred : focusables()[0]
    initialFocus?.focus()

    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape' && !busyRef.current) {
        event.preventDefault()
        onCloseRef.current()
      }
    }
    window.addEventListener('keydown', onKeyDown)

    return () => {
      window.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = previousOverflow
      if (previous?.isConnected) previous.focus()
    }
  }, [])

  function trapFocus(event: KeyboardEvent<HTMLElement>) {
    if (event.key !== 'Tab') return
    const nodes = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []).filter(
      (node) =>
        dialogRef.current?.contains(node) &&
        !node.matches(':disabled') &&
        node.getClientRects().length > 0,
    )
    if (nodes.length === 0) {
      event.preventDefault()
      return
    }
    const first = nodes[0]
    const last = nodes[nodes.length - 1]
    if (!dialogRef.current?.contains(document.activeElement)) {
      event.preventDefault()
      const target = event.shiftKey ? last : first
      target.focus()
    } else if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }

  return createPortal(
    <div
      className="modal-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) onClose()
      }}
    >
      <section
        ref={dialogRef}
        className={`modal lg:text-modal-body lg:leading-body ${className}`.trim()}
        role="dialog"
        aria-modal="true"
        aria-busy={busy}
        aria-labelledby={labelledBy}
        aria-describedby={describedBy}
        onKeyDown={trapFocus}
      >
        {children}
      </section>
    </div>,
    document.body,
  )
}
