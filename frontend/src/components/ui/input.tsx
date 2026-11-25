import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"
import { focusInset } from "@/lib/classes"

export const builderFieldClass = `rounded-[4px] border-border bg-muted p-[8px] font-mono text-[12px] tabular-nums text-foreground shadow-none dark:bg-muted focus-visible:ring-0 ${focusInset} lg:text-control`

const inputVariants = cva(
  "h-[44px] w-full min-w-0 rounded-md border border-input bg-transparent px-3 py-1 lg:text-control shadow-xs transition-[color,box-shadow] outline-none selection:bg-primary selection:text-primary-foreground file:inline-flex file:h-7 file:border-0 file:bg-transparent file:font-medium file:text-foreground lg:file:text-control placeholder:text-muted-foreground disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 dark:bg-input/30",
  {
    variants: {
      variant: {
        default: "",
        builder: builderFieldClass,
      },
    },
    defaultVariants: { variant: "default" },
  }
)

function Input({
  className,
  type,
  variant = "default",
  ...props
}: React.ComponentProps<"input"> & VariantProps<typeof inputVariants>) {
  return (
    <input
      type={type}
      data-slot="input"
      className={cn(
        "focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50",
        "aria-invalid:border-destructive aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40",
        inputVariants({ variant }),
        className
      )}
      {...props}
    />
  )
}

export { Input }
