import * as React from 'react'
import { Checkbox as CheckboxPrimitive } from 'radix-ui'
import { cn } from '@/lib/utils'

function Checkbox({
  className,
  checked,
  ...props
}: React.ComponentProps<typeof CheckboxPrimitive.Root>) {
  return (
    <CheckboxPrimitive.Root
      asChild
      data-slot="checkbox"
      checked={checked}
      className={cn(
        'size-5 shrink-0 rounded-[4px] border border-input accent-primary outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50',
        className,
      )}
      {...props}
    >
      {/* Radix mantém o estado, mas asChild não repassa `checked` ao input
          visível. Sincronizar a caixa evita divergência ao retomar um rascunho. */}
      <input
        type="checkbox"
        checked={checked === 'indeterminate' ? false : checked}
        readOnly
      />
    </CheckboxPrimitive.Root>
  )
}

export { Checkbox }
