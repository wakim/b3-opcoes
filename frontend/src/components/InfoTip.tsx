import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
  type ReactElement,
} from 'react'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from './ui/tooltip'

/** Ajuda curta por hover, foco e toque. O painel é portal para não alterar a
 * geometria do gatilho; Escape fecha só a dica, mesmo dentro de um diálogo. */
export function InfoTip({
  text,
  children,
  side = 'bottom',
  align = 'start',
}: {
  text: string
  children: ReactElement
  side?: 'top' | 'right' | 'bottom' | 'left'
  align?: 'start' | 'center' | 'end'
}) {
  const [open, setOpen] = useState(false)
  const suppressCloseUntil = useRef(0)
  const preserveTouchOpen = useRef(false)
  const closeTimer = useRef<number | null>(null)

  useEffect(
    () => () => {
      if (closeTimer.current != null) window.clearTimeout(closeTimer.current)
    },
    [],
  )

  function close() {
    suppressCloseUntil.current = 0
    if (closeTimer.current != null) window.clearTimeout(closeTimer.current)
    closeTimer.current = null
    setOpen(false)
  }

  function onPointerDown(event: PointerEvent) {
    if (event.pointerType !== 'touch') return
    preserveTouchOpen.current = !open
    if (open) {
      close()
      return
    }
    suppressCloseUntil.current = Date.now() + 500
    setOpen(true)
    closeTimer.current = window.setTimeout(close, 5000)
  }

  function onKeyDown(event: KeyboardEvent) {
    if (event.key !== 'Escape') return
    event.preventDefault()
    event.stopPropagation()
    close()
  }

  return (
    <TooltipProvider delayDuration={0} skipDelayDuration={0}>
      <Tooltip
        open={open}
        onOpenChange={(nextOpen) => {
          if (!nextOpen && Date.now() < suppressCloseUntil.current) return
          setOpen(nextOpen)
        }}
      >
        <TooltipTrigger asChild onPointerDownCapture={onPointerDown} onKeyDown={onKeyDown} onClickCapture={() => {
          if (preserveTouchOpen.current) {
            preserveTouchOpen.current = false
            return
          }
          if (open) close()
        }}>
          {children}
        </TooltipTrigger>
        <TooltipContent
          side={side}
          align={align}
          onEscapeKeyDown={(event) => {
            event.preventDefault()
            event.stopPropagation()
            close()
          }}
        >
          {text}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
