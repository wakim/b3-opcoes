// Utilitários compartilhados (F8 — fecho). Cada constante reproduz o valor
// EFETIVO da antiga classe global (styles.css + styles-view.css, a última
// declaração vencendo). Valores em px arbitrário: a base mobile é 13,5 px,
// utilitário em rem distorce. Variantes `lg:` continuam no elemento.
// Contextos que zeravam margem (ex.: `.block-head > .block-title`) usam a
// versão *Base e acrescentam a margem própria — nunca dois mt-* no mesmo nó.

/** Contorno de foco do legado: 2 px sólido, cor da agulha. */
export const focusInset =
  'focus-visible:outline-2 focus-visible:outline-solid focus-visible:outline-offset-[-2px] focus-visible:outline-primary'
export const focusOutset =
  'focus-visible:outline-2 focus-visible:outline-solid focus-visible:outline-offset-[2px] focus-visible:outline-primary'

/** `.note` — margem 8/0, 11 px, cinza 3. `data-note` permite ao Aprender
 * zerar a margem por estrutura sem depender do nome da classe. */
export const noteBase = 'text-[11px] text-muted-foreground'
export const note = `mx-0 my-[8px] ${noteBase}`

/** `.quiet` */
export const quiet = 'text-muted-foreground'

/** `.num` */
export const num = 'font-mono tabular-nums'
export const numPos = 'text-[color:var(--pos)]'
export const numNeg = 'text-[color:var(--neg)]'

/** `.screen-title` */
export const screenTitle = 'mx-0 mt-[4px] mb-[16px] text-[28px] font-medium tracking-[-0.02em]'

/** `.block-title` — margem 24/0/8, 13 px, peso 500. */
export const blockTitleBase = 'mx-0 mb-[8px] text-[13px] font-medium'
export const blockTitle = `mt-[24px] ${blockTitleBase}`

/** `.block-head` */
export const blockHeadBase = 'flex flex-wrap items-center justify-between gap-[8px]'
export const blockHead = `mt-[24px] ${blockHeadBase}`

/** `.block-count` */
export const blockCountBase = 'font-mono text-[11px] font-normal text-muted-foreground'
export const blockCount = `ml-[8px] ${blockCountBase}`

/** `.quiet-line` */
export const quietLine = 'mx-0 mt-0 mb-[16px] font-mono text-[11px] text-muted-foreground'

/** `.chain-note` */
export const chainNote = 'mx-0 mt-0 mb-[8px] text-[11px] leading-[1.45] text-muted-foreground'

/** `.notice` */
export const notice =
  'mx-0 mt-0 mb-[16px] border-l-2 border-solid border-l-input pl-[10px] text-[11.5px] text-[color:var(--text-2)]'

/** `.back` (as duas folhas somadas: inline-flex vence o inline-block). */
export const back = `mb-[12px] inline-flex min-h-[44px] items-center font-mono text-[12px] text-[color:var(--text-2)] ${focusInset}`

/** `.inline-link` */
export const inlineLink = `inline-flex min-h-[44px] cursor-pointer items-center border-none bg-transparent p-0 text-foreground underline underline-offset-[2px] [font:inherit] ${focusInset}`

/** `.ticker-link` */
export const tickerLink = `inline-flex min-h-[44px] items-center text-primary ${focusInset}`

/** `.visually-hidden` (clip, não clip-path: igual ao legado). */
export const visuallyHidden =
  'absolute -m-px h-px w-px overflow-hidden border-none p-0 whitespace-nowrap [clip:rect(0_0_0_0)]'

/** `.builder__field`. */
export const field = 'flex min-w-0 flex-col gap-[4px]'
/** `.builder__field-label` */
export const fieldLabel = 'text-[11px] text-muted-foreground'
