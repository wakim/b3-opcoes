import { useQuery } from '@tanstack/react-query'
import { createFileRoute } from '@tanstack/react-router'
import { useState } from 'react'
import {
  apiGet,
  type GlossaryData,
  type GlossaryDetail,
} from '../lib/api'
import { ErrorBox, Loading, Sheet } from '../lib/ui'

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

function Aprender() {
  const [selected, setSelected] = useState<string | null>(null)
  const list = useQuery({
    queryKey: ['glossary'],
    queryFn: ({ signal }) => apiGet<GlossaryData>('/api/glossary', signal),
    staleTime: HOUR,
  })
  const detail = useQuery({
    queryKey: ['glossary', selected],
    queryFn: ({ signal }) =>
      apiGet<GlossaryDetail>(`/api/glossary/${selected}`, signal),
    enabled: selected !== null,
    staleTime: HOUR,
  })

  if (list.isLoading || !list.data) return <Loading />
  if (list.isError) return <ErrorBox error={list.error} />

  const selectedEntry = list.data.data.entries.find((e) => e.key === selected)

  return (
    <>
      <h1 className="screen-title">Aprender</h1>
      <p className="note">Toque num verbete para abrir a ficha.</p>

      <div className="glossary-list">
        {list.data.data.entries.map((entry) => (
          <button
            className="glossary-item"
            key={entry.key}
            onClick={() => setSelected(entry.key)}
          >
            <span>{entry.title}</span>
            <span className="level">nível {entry.level}</span>
          </button>
        ))}
      </div>

      {selectedEntry && (
        <Sheet
          title={selectedEntry.title}
          meta={`nível ${selectedEntry.level}`}
          onClose={() => setSelected(null)}
        >
          {detail.isLoading && <Loading />}
          {detail.isError && <ErrorBox error={detail.error} />}
          {detail.data &&
            plain(detail.data.data.body_md)
              .split(/\n\s*\n/)
              .filter((paragraph) => paragraph.trim().length > 0)
              .map((paragraph, index) => <p key={index}>{paragraph}</p>)}
        </Sheet>
      )}
    </>
  )
}
