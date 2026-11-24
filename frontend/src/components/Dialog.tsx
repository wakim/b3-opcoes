/** Diálogo acessível sobre o primitive shadcn/Radix. */
import { useRef, type ReactNode } from 'react'
import {
  Dialog as DialogRoot,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { cn } from '@/lib/utils'

export function Dialog({
  title,
  description,
  eyebrow,
  open = true,
  onClose,
  busy = false,
  children,
  className,
  initialFocus = 'autofocus',
  showCloseButton = true,
  size = 'default',
  returnFocusSelector,
}: {
  open?: boolean
  title: string
  description?: string
  eyebrow?: string
  onClose: () => void
  busy?: boolean
  children: ReactNode
  className?: string
  initialFocus?: 'autofocus' | 'close'
  showCloseButton?: boolean
  size?: 'default' | 'wide'
  returnFocusSelector?: string
}) {
  const previousFocus = useRef<HTMLElement | null>(null)

  return (
    <DialogRoot
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen && !busy) onClose()
      }}
      modal
    >
      <DialogContent
        aria-busy={busy}
        showCloseButton={showCloseButton && !busy}
        className={cn(
          'max-h-[90dvh] overflow-y-auto',
          size === 'wide' ? 'sm:max-w-3xl' : 'sm:max-w-lg',
          className,
        )}
        onEscapeKeyDown={(event) => {
          if (busy) event.preventDefault()
        }}
        onPointerDownOutside={(event) => {
          if (busy) event.preventDefault()
        }}
        onInteractOutside={(event) => {
          if (busy) event.preventDefault()
        }}
        onCloseAutoFocus={(event) => {
          const target =
            (returnFocusSelector
              ? document.querySelector<HTMLElement>(returnFocusSelector)
              : null) ?? previousFocus.current
          if (target?.isConnected) {
            event.preventDefault()
            target.focus()
          }
        }}
        onOpenAutoFocus={(event) => {
          event.preventDefault()
          previousFocus.current = document.activeElement as HTMLElement | null
          const content = event.currentTarget as HTMLElement | null
          const preferred = content?.querySelector<HTMLElement>('[data-autofocus]')
          const fallback = content?.querySelector<HTMLElement>(
            'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [href], [tabindex]:not([tabindex="-1"])',
          )
          const target =
            initialFocus === 'close'
              ? content?.querySelector<HTMLElement>('[data-slot="dialog-close"]')
              : preferred ?? fallback
          target?.focus()
        }}
      >
        <DialogHeader>
          {eyebrow && (
            <div className="lg:text-label text-muted-foreground">{eyebrow}</div>
          )}
          <DialogTitle>{title}</DialogTitle>
          {description && <DialogDescription>{description}</DialogDescription>}
        </DialogHeader>
        {children}
      </DialogContent>
    </DialogRoot>
  )
}
