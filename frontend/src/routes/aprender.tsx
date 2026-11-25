import { useQuery } from '@tanstack/react-query'
import { createFileRoute } from '@tanstack/react-router'
import { useId, useState, type ReactNode } from 'react'
import {
  apiGet,
  type GlossaryData,
  type GlossaryDetail,
  type GlossaryEntry,
} from '../lib/api'
import { ErrorBox, Loading } from '../lib/ui'
import { note } from '../lib/classes'
import {
  Accordion,
  AccordionContent,
  AccordionHeader,
  AccordionItem,
  AccordionTrigger,
} from '../components/ui/accordion'

const HOUR = 3_600_000

export const Route = createFileRoute('/aprender')({
  component: Aprender,
})

/** Texto puro do body_md, sem biblioteca de Markdown: remove ênfase, links,
 *  marcador de blockquote e regras horizontais. Paragraph splitting fica no
 *  chamador (fecha a pendência B2 do parecer F5-A). */
function plain(markdown: string): string {
  return markdown
    .replace(/\*\*/g, '')
    .replace(/`/g, '')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .split('\n')
    .map((line) => line.replace(/^\s*>\s?/, ''))
    .filter((line) => !/^\s*-{3,}\s*$/.test(line))
    .join('\n')
}

/** Uma query por verbete aberto; o cache de 1 h (ADR-032) torna reabrir instantâneo. */
function EntryDetail({ entryKey }: { entryKey: string }): ReactNode {
  const detail = useQuery({
    queryKey: ['glossary', entryKey],
    queryFn: ({ signal }) =>
      apiGet<GlossaryDetail>(`/api/glossary/${entryKey}`, signal),
    staleTime: HOUR,
  })
  if (detail.isError) return <ErrorBox error={detail.error} />
  if (!detail.data) return <Loading />
  return (
    <>
      {plain(detail.data.data.body_md)
        .split(/\n\s*\n/)
        .filter((paragraph) => paragraph.trim().length > 0)
        .map((paragraph, index) => (
          <p key={index} className="mb-[12px] text-[var(--text-2)] last:mb-0 lg:text-body lg:leading-body">
            {paragraph}
          </p>
        ))}
    </>
  )
}

/** Accordion independente: cada linha abre/colapsa o seu painel sem afetar os demais. */
function EntryRow({
  entry,
  open,
}: {
  entry: GlossaryEntry
  open: boolean
}): ReactNode {
  const panelId = useId()
  return (
    <AccordionItem value={entry.key} className="contents">
      <AccordionHeader asChild>
        <div className="flex">
          <AccordionTrigger
            className="flex min-h-[44px] w-full justify-between gap-[10px] rounded-[4px] border border-border bg-card px-[14px] py-[12px] text-left text-foreground aria-[expanded=true]:rounded-b-none aria-[expanded=true]:bg-secondary lg:text-body lg:leading-body"
            aria-controls={panelId}
          >
            <span className="inline-flex min-w-0 items-baseline gap-[10px]">
              <span>{entry.title}</span>
              <span className="font-mono text-[calc(11px*var(--font-scale))] text-muted-foreground lg:text-label">nível {entry.level}</span>
            </span>
            <span className="shrink-0 font-mono text-[calc(16px*var(--font-scale))] leading-none text-muted-foreground lg:text-metric" aria-hidden="true">
              {open ? '−' : '+'}
            </span>
          </AccordionTrigger>
        </div>
      </AccordionHeader>
      <AccordionContent
        id={panelId}
        className="mt-[-6px] rounded-b-[4px] border-x border-b border-border bg-card p-[14px] lg:text-body lg:leading-body [&_[data-note]]:m-0"
        role="region"
        aria-label={entry.title}
        aria-labelledby={undefined}
        forceMount
        hidden={!open}
      >
        {open && <EntryDetail entryKey={entry.key} />}
      </AccordionContent>
    </AccordionItem>
  )
}

function Aprender() {
  const [open, setOpen] = useState<Set<string>>(() => new Set())
  const list = useQuery({
    queryKey: ['glossary'],
    queryFn: ({ signal }) => apiGet<GlossaryData>('/api/glossary', signal),
    staleTime: HOUR,
  })

  if (list.isError) return <ErrorBox error={list.error} />
  if (list.isLoading || !list.data) return <Loading />

  return (
    <>
      <h1 className="mt-[4px] mb-[16px] text-[calc(28px*var(--font-scale))] font-medium tracking-[-0.02em] lg:text-screen-title lg:leading-title">
        Aprender
      </h1>
      <p className={`${note} lg:text-label`}>
        Toque num verbete para abrir a ficha. Vários podem ficar abertos.
      </p>

      <Accordion
        type="multiple"
        value={[...open]}
        onValueChange={(values) => setOpen(new Set(values))}
        className="flex flex-col gap-[6px]"
      >
        {list.data.data.entries.map((entry) => (
          <EntryRow key={entry.key} entry={entry} open={open.has(entry.key)} />
        ))}
      </Accordion>
    </>
  )
}
