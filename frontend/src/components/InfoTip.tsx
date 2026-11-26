import {
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
import { useCopy, type CopyParams, type PresentationKey } from '../lib/presentation'

/** Ajuda curta por hover, foco e toque. O painel é portal para não alterar a
 * geometria do gatilho; Escape fecha só a dica, mesmo dentro de um diálogo.
 *
 * F9: `text` explícito continua valendo; sem ele, a ajuda vem do dicionário
 * pela chave semântica. Comportamento de toque/foco/Escape permanece intacto.
 */
export function InfoTip({
  text,
  children,
  side = 'bottom',
  align = 'start',
  term,
  variant,
  params,
}: {
  text?: string
  children: ReactElement
  side?: 'top' | 'right' | 'bottom' | 'left'
  align?: 'start' | 'center' | 'end'
  term?: PresentationKey
  variant?: string
  params?: CopyParams
}) {
  const copy = useCopy(term ?? 'metric.generic', variant, term ? params : { ...params, label: '' })
  const resolvedText = text ?? copy.help ?? ''
  const [open, setOpen] = useState(false)
  const suppressCloseUntil = useRef(0)
  const preserveTouchOpen = useRef(false)

  function close() {
    suppressCloseUntil.current = 0
    setOpen(false)
  }

  function onPointerDown(event: PointerEvent) {
    if (event.pointerType === 'mouse') return
    preserveTouchOpen.current = !open
    if (open) {
      close()
      return
    }
    suppressCloseUntil.current = Date.now() + 500
    setOpen(true)
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
          {resolvedText}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
