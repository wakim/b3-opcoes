"use client"

import * as React from "react"
import { type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"
import { ToggleGroup as ToggleGroupPrimitive } from "radix-ui"
import { buttonVariants } from "@/components/ui/button"

function ToggleGroup({
  className,
  onKeyDown,
  ...props
}: React.ComponentProps<typeof ToggleGroupPrimitive.Root>) {
  function handleKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    onKeyDown?.(event)
    if (event.defaultPrevented || (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight')) return
    const items = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('button[data-slot="toggle-group-item"]:not(:disabled)')]
    if (items.length === 0) return
    const current = items.indexOf(document.activeElement as HTMLButtonElement)
    const delta = event.key === 'ArrowRight' ? 1 : -1
    const nextIndex = current < 0 ? (delta > 0 ? 0 : items.length - 1) : (current + delta + items.length) % items.length
    event.preventDefault()
    items[nextIndex].focus()
    items[nextIndex].click()
  }

  return (
    <ToggleGroupPrimitive.Root
      data-slot="toggle-group"
      role="group"
      tabIndex={-1}
      className={cn("flex w-fit items-center gap-[4px]", className)}
      onKeyDownCapture={handleKeyDown}
      {...props}
    />
  )
}

function ToggleGroupItem({
  className,
  variant = "ghost",
  size = "sm",
  tabIndex = 0,
  ...props
}: React.ComponentProps<typeof ToggleGroupPrimitive.Item> &
  Pick<VariantProps<typeof buttonVariants>, "variant" | "size">) {
  return (
    <ToggleGroupPrimitive.Item
      data-slot="toggle-group-item"
      tabIndex={tabIndex}
      className={cn(buttonVariants({ variant, size }), className)}
      {...props}
    />
  )
}

export { ToggleGroup, ToggleGroupItem }
