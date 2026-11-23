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
        .map((paragraph, index) => <p key={index}>{paragraph}</p>)}
    </>
  )
}

/** Accordion independente: cada linha abre/colapsa o seu painel sem afetar os demais. */
function EntryRow({
  entry,
  open,
  onToggle,
}: {
  entry: GlossaryEntry
  open: boolean
  onToggle: (key: string) => void
}): ReactNode {
  const panelId = useId()
  return (
    <>
      <button
        className="glossary-item"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => onToggle(entry.key)}
      >
        <span className="g-label">
          <span>{entry.title}</span>
          <span className="level">nível {entry.level}</span>
        </span>
        <span className="acc-sign" aria-hidden="true">
          {open ? '−' : '+'}
        </span>
      </button>
      <div
        id={panelId}
        className="glossary-panel"
        role="region"
        aria-label={entry.title}
        hidden={!open}
      >
        {open && <EntryDetail entryKey={entry.key} />}
      </div>
    </>
  )
}

function Aprender() {
  const [open, setOpen] = useState<Set<string>>(() => new Set())
  const list = useQuery({
    queryKey: ['glossary'],
    queryFn: ({ signal }) => apiGet<GlossaryData>('/api/glossary', signal),
    staleTime: HOUR,
  })

  if (list.isLoading || !list.data) return <Loading />
  if (list.isError) return <ErrorBox error={list.error} />

  function toggle(key: string) {
    setOpen((previous) => {
      const next = new Set(previous)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  return (
    <>
      <h1 className="screen-title">Aprender</h1>
      <p className="note">
        Toque num verbete para abrir a ficha. Vários podem ficar abertos.
      </p>

      <div className="glossary-list">
        {list.data.data.entries.map((entry) => (
          <EntryRow
            key={entry.key}
            entry={entry}
            open={open.has(entry.key)}
            onToggle={toggle}
          />
        ))}
      </div>
    </>
  )
}
