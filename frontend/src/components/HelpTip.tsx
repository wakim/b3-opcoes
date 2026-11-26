/** Tooltip curto e acessível: hover, foco por teclado ou toque.
 *
 * F9: `text` explícito continua valendo; na ausência dele a ajuda curta vem do
 * dicionário pela chave semântica. Nenhuma lógica de perfil vive aqui.
 */
import { useEffect, useId, useRef, useState, type FocusEvent, type PointerEvent } from 'react'
import { useCopy, type CopyParams, type PresentationKey } from '../lib/presentation'

export function HelpTip({
  label,
  text,
  term,
  variant,
  params,
}: {
  label?: string
  text?: string
  term?: PresentationKey
  variant?: string
  params?: CopyParams
}) {
  const copy = useCopy(term ?? 'metric.generic', variant, { ...params, label: label ?? '' })
  const resolvedText = text ?? copy.help ?? ''
  const ariaLabel = label ?? copy.label ?? 'Ajuda'
  const id = useId()
  const [open, setOpen] = useState(false)
  const ignoreClickUntil = useRef(0)
  const ignoreMouseLeaveUntil = useRef(0)

  function openTip() {
    setOpen(true)
  }

  function close() {
    setOpen(false)
  }

  useEffect(() => {
    if (!open) return
    // O Radix Dialog escuta Escape na captura do documento. A janela vem antes
    // e impede que a dica feche o diálogo que a contém.
    const onEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      event.stopImmediatePropagation()
      close()
    }
    window.addEventListener('keydown', onEscape, true)
    return () => window.removeEventListener('keydown', onEscape, true)
  }, [open])

  function onPointerDown(event: PointerEvent<HTMLButtonElement>) {
    if (event.pointerType === 'mouse') return
    // O navegador emula mouse/click depois do toque. Sem prevenir o pointerdown
    // e descartar esse clique, o primeiro toque abre e o clique sintético fecha.
    event.preventDefault()
    ignoreClickUntil.current = Date.now() + 700
    ignoreMouseLeaveUntil.current = Date.now() + 1000
    if (open) close()
    else openTip()
  }

  function onFocus(event: FocusEvent<HTMLButtonElement>) {
    // Toque não é foco por teclado: abrir no focus e alternar no clique fecharia
    // a dica imediatamente.
    if (!event.currentTarget.matches(':focus-visible')) return
    setOpen(true)
  }

  function onClick() {
    if (Date.now() < ignoreClickUntil.current) return
    if (open) close()
    else openTip()
  }

  return (
    <span className="relative ml-[4px] inline-flex min-w-0 align-middle">
      <button
        type="button"
        className="inline-flex size-[44px] shrink-0 cursor-help items-center justify-center rounded-[4px] border-0 bg-transparent p-0 text-[var(--text-2)] hover:bg-secondary hover:text-primary focus-visible:bg-secondary focus-visible:text-primary focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-primary"
        aria-label={ariaLabel}
        aria-describedby={id}
        aria-expanded={open}
        onMouseEnter={openTip}
        onMouseLeave={() => {
          if (Date.now() >= ignoreMouseLeaveUntil.current) close()
        }}
        onFocus={onFocus}
        onBlur={close}
        onPointerDown={onPointerDown}
        onClick={onClick}
      >
        <span aria-hidden="true">?</span>
      </button>
      <span
        className="pointer-events-none invisible absolute left-0 top-[calc(100%_+_4px)] z-30 w-[280px] max-w-[calc(100vw_-_120px)] whitespace-normal rounded-[6px] border border-input bg-popover px-[12px] py-[10px] text-[calc(12px*var(--font-scale))] font-normal leading-normal text-foreground opacity-0 lg:text-detail data-[open=true]:visible data-[open=true]:opacity-100 max-sm:fixed max-sm:left-[16px] max-sm:right-[16px] max-sm:bottom-[16px] max-sm:top-auto max-sm:w-auto max-sm:max-w-none"
        id={id}
        role="tooltip"
        data-open={open}
      >
        {resolvedText}
      </span>
    </span>
  )
}
