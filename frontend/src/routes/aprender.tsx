import { useQuery } from '@tanstack/react-query'
import { createFileRoute } from '@tanstack/react-router'
import { useState } from 'react'
import {
  apiGet,
  type GlossaryData,
  type GlossaryDetail,
} from '../lib/api'
import { ErrorBox, Loading } from '../lib/ui'

const HOUR = 3_600_000

export const Route = createFileRoute('/aprender')({
  component: Aprender,
})

function plain(markdown: string): string {
  return markdown.replace(/\*\*/g, '').replace(/`/g, '')
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

      {detail.isError && <ErrorBox error={detail.error} />}
      {detail.data && (
        <section className="card entry">
          <h2>{detail.data.data.title}</h2>
          {plain(detail.data.data.body_md)
            .split(/\n\s*\n/)
            .map((paragraph, index) => (
              <p key={index}>{paragraph}</p>
            ))}
        </section>
      )}
    </>
  )
}
