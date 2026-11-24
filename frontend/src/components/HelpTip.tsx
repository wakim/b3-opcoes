/** Tooltip curto e acessível: hover, foco por teclado ou toque. */
import { useId, useState, type KeyboardEvent } from 'react'

export function HelpTip({ label, text }: { label: string; text: string }) {
  const id = useId()
  const [open, setOpen] = useState(false)

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === 'Escape') {
      event.preventDefault()
      setOpen(false)
    }
  }

  return (
    <span className="relative ml-[4px] inline-flex min-w-0 align-middle">
      <button
        type="button"
        className="inline-flex size-[44px] shrink-0 cursor-help items-center justify-center rounded-[4px] border-0 bg-transparent p-0 text-[var(--text-2)] hover:bg-secondary hover:text-primary focus-visible:bg-secondary focus-visible:text-primary focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-primary"
        aria-label={label}
        aria-describedby={id}
        aria-expanded={open}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={() => setOpen((value) => !value)}
        onKeyDown={onKeyDown}
      >
        <span aria-hidden="true">?</span>
      </button>
      <span
        className="pointer-events-none invisible absolute left-0 top-[calc(100%_+_4px)] z-30 w-[280px] max-w-[calc(100vw_-_120px)] whitespace-normal rounded-[6px] border border-input bg-popover px-[12px] py-[10px] text-[12px] font-normal leading-normal text-foreground opacity-0 lg:text-detail data-[open=true]:visible data-[open=true]:opacity-100 max-sm:fixed max-sm:left-[16px] max-sm:right-[16px] max-sm:bottom-[16px] max-sm:top-auto max-sm:w-auto max-sm:max-w-none"
        id={id}
        role="tooltip"
        data-open={open}
      >
        {text}
      </span>
    </span>
  )
}
