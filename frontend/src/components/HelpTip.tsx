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
    <span className="help-tip">
      <button
        type="button"
        className="help-tip__trigger"
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
      <span className="help-tip__text lg:text-detail" id={id} role="tooltip" data-open={open}>
        {text}
      </span>
    </span>
  )
}
