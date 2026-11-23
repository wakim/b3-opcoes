/** Ficha de métrica em contexto: verbete do glossário + o número do dia.
 *
 * Fonte única `/api/glossary/{key}` (staleTime 1 h). Não é a ficha do Aprender:
 * implementação independente, sem importar `lib/ui.tsx` (dono paralelo).
 */
import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { apiGet, type GlossaryDetail } from '../lib/api'
import { glossaryKey, type SheetTarget } from './metricKeys'

/** `**O que é** — texto` → `texto`, parando no próximo rótulo em negrito. */
function section(md: string, name: string): string {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const match = md.match(new RegExp(`\\*\\*${escaped}\\*\\*\\s*—\\s*([\\s\\S]*?)(?=\\n\\s*\\*\\*|\\s*$)`))
  return match?.[1].trim() ?? ''
}

function Block({ title, text }: { title: string; text: string }) {
  if (!text) return null
  return (
    <div className="sheet__block">
      <div className="sheet__block-title">{title}</div>
      <p className="sheet__block-text">{text}</p>
    </div>
  )
}

export function MetricSheet({
  target,
  onClose,
}: {
  target: SheetTarget | null
  onClose: () => void
}) {
  const key = target ? glossaryKey(target.field) : null
  const entry = useQuery({
    queryKey: ['glossary', key],
    queryFn: ({ signal }) => apiGet<GlossaryDetail>(`/api/glossary/${key}`, signal),
    enabled: Boolean(key),
    staleTime: 3_600_000,
    retry: 1,
  })

  useEffect(() => {
    if (!target) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [target, onClose])

  useEffect(() => {
    if (!target) return
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previous
    }
  }, [target])

  if (!target) return null
  const body = entry.data?.data.body_md ?? ''
  const definition = section(body, 'O que é')
  const mistake = section(body, 'Erro comum')

  return (
    <div className="sheet-backdrop" onMouseDown={onClose}>
      <section
        className="sheet"
        role="dialog"
        aria-modal="true"
        aria-label={target.label}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="sheet__head">
          <div>
            <div className="sheet__eyebrow">verbete</div>
            <h2 className="sheet__title">{entry.data?.data.title ?? target.label}</h2>
          </div>
          <button type="button" className="sheet__close" onClick={onClose} aria-label="Fechar">
            ×
          </button>
        </header>

        <div className="sheet__body">
          <div className="sheet__value">
            <span className="sheet__value-label">{target.label}</span>
            <span className="sheet__value-number">{target.value}</span>
          </div>
          {entry.isError && (
            <div className="sheet__error" role="alert">
              verbete indisponível: {entry.error instanceof Error ? entry.error.message : 'erro'}
            </div>
          )}
          {entry.isLoading && <p className="state-loading">carregando verbete…</p>}
          <Block title="O que é" text={definition} />
          <Block title="Erro comum" text={mistake} />
          {!key && (
            <p className="sheet__note">esta métrica ainda não tem verbete no glossário.</p>
          )}
        </div>
      </section>
    </div>
  )
}
