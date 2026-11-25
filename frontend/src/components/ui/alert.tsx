import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"

const alertVariants = cva("", {
  variants: {
    variant: {
      default:
        "grid gap-2 rounded-[6px] border border-destructive/40 bg-destructive/5 p-4",
      "destructive-inline":
        "mb-[12px] rounded-[4px] border border-destructive bg-[rgba(200,131,131,0.08)] px-[12px] py-[10px] text-foreground",
    },
  },
  defaultVariants: {
    variant: "default",
  },
})

function Alert({
  className,
  variant,
  ...props
}: React.ComponentProps<"div"> & VariantProps<typeof alertVariants>) {
  return (
    <div
      data-slot="alert"
      role="alert"
      className={cn(alertVariants({ variant }), className)}
      {...props}
    />
  )
}

function AlertTitle({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="alert-title" className={cn("font-medium", className)} {...props} />
}

function AlertDescription({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="alert-description" className={className} {...props} />
}

export { Alert, AlertDescription, AlertTitle }
