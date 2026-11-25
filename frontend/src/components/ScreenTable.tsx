import type { ComponentProps } from 'react'
import { cn } from '../lib/utils'
import { Table } from './ui/table'

type ScreenTableProps = Omit<
  ComponentProps<typeof Table>,
  'contained' | 'containerClassName'
> & {
  /** Classes do wrapper de scroll. Cada tela passa exatamente as classes atuais. */
  wrapperClassName?: string
  /** Atributos do wrapper: region/tabIndex/aria-label/id continuam no elemento de scroll. */
  wrapperProps?: Omit<ComponentProps<'div'>, 'className' | 'children'>
  /** A primeira coluna é sticky por padrão; TermTable desliga a exceção. */
  stickyFirst?: boolean
}

const noStickyFirst =
  '[&_:is(th,td):first-child]:static [&_:is(th,td):first-child]:z-auto [&_:is(th,td):first-child]:border-r-0'

/** Composição local das tabelas da tela: um só wrapper e `contained={false}`. */
export function ScreenTable({
  className,
  wrapperClassName,
  wrapperProps,
  stickyFirst = true,
  ...props
}: ScreenTableProps) {
  return (
    <div {...wrapperProps} className={wrapperClassName}>
      <Table
        contained={false}
        className={cn(
          'screen-table lg:text-table lg:leading-table',
          !stickyFirst && noStickyFirst,
          className,
        )}
        {...props}
      />
    </div>
  )
}
