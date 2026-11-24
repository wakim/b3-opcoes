// Classes dos controles do builder/cadeia (F8.3). Reproduzem o CSS legado
// `.builder__button`, `.builder__field`/`.builder__qty` e `.builder__remove`
// sobre os primitives shadcn. Os `dark:` anulam os defaults `dark:bg-input/30`
// dos primitives: a interface é sempre escura e a cor vem da paleta grafite.

const focus =
  'focus-visible:ring-0 focus-visible:outline-2 focus-visible:outline-solid focus-visible:outline-offset-[-2px] focus-visible:outline-primary'

export const builderButton = `h-[44px] rounded-[4px] border border-input bg-card px-[12px] py-[8px] text-[12px] font-normal whitespace-normal text-[color:var(--text-2)] shadow-none hover:bg-muted hover:text-foreground disabled:opacity-45 dark:border-input dark:bg-card dark:hover:bg-muted ${focus} lg:text-control`

export const builderPrimary = `h-[44px] rounded-[4px] border border-primary bg-primary px-[12px] py-[8px] text-[12px] font-normal whitespace-normal text-primary-foreground shadow-none hover:bg-primary disabled:opacity-45 ${focus} lg:text-control`

export const builderField = `rounded-[4px] border-border bg-muted p-[8px] font-mono text-[12px] tabular-nums text-foreground shadow-none dark:bg-muted ${focus} lg:text-control`

export const builderRemove = `h-[44px] rounded-none px-[4px] py-[8px] text-[11px] font-normal text-muted-foreground underline ${focus} lg:text-label`
