/** Ficha de métrica em modal: verbete do glossário + o número do dia.
 *
 * Fonte única `/api/glossary/{key}` (staleTime 1 h). Diálogo centralizado com
 * foco no fechar, Escape, clique no backdrop e trap de Tab. Não importa
 * `lib/ui.tsx` (dono paralelo).
 */
import { useQuery } from '@tanstack/react-query'
import { apiGet, type GlossaryDetail } from '../lib/api'
import { num } from '../lib/classes'
import { glossaryKey, type ModalTarget } from './metricKeys'
import { ErrorState } from './State'
import { InlineMd } from './InlineMd'
import { Dialog } from './Dialog'

/** `**O que é** — texto` → `texto`, parando no próximo rótulo em negrito. */
function section(md: string, name: string): string {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const match = md.match(new RegExp(`\\*\\*${escaped}\\*\\*\\s*—\\s*([\\s\\S]*?)(?=\\n\\s*\\*\\*|\\s*$)`))
  return match?.[1].trim() ?? ''
}

function Block({ title, text }: { title: string; text: string }) {
  if (!text) return null
  return (
    <div className="lg:text-modal-body lg:leading-body grid gap-1">
      <div className="lg:text-label text-muted-foreground">{title}</div>
      <p className="lg:text-modal-body lg:leading-body">
        <InlineMd text={text} />
      </p>
    </div>
  )
}

export function MetricModal({
  target,
  onClose,
}: {
  target: ModalTarget | null
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
  const body = entry.data?.data.body_md ?? ''
  const definition = section(body, 'O que é')
  const mistake = section(body, 'Erro comum')
  const source = entry.data?.data.title

  return (
    <Dialog
      open={Boolean(target)}
      title={target?.label ?? ''}
      eyebrow="verbete"
      description={source && source !== target?.label ? `glossário: ${source}` : undefined}
      onClose={onClose}
      initialFocus="close"
      size="wide"
    >
      {target && (
        <div className="grid gap-4">
          <div className="grid gap-1 rounded-[6px] border border-border bg-muted/30 p-4">
            <span className="lg:text-label text-muted-foreground">no painel</span>
            <span className={`${num} lg:text-metric`}>{target.value}</span>
          </div>
          {entry.isError && <ErrorState error={entry.error} title="Verbeto indisponível" />}
          {entry.isLoading && <p className="lg:text-label text-muted-foreground">carregando verbete…</p>}
          <Block title="O que é" text={definition} />
          <Block title="Erro comum" text={mistake} />
          {!key && (
            <p className="lg:text-label text-muted-foreground">
              esta métrica ainda não tem verbete no glossário.
            </p>
          )}
        </div>
      )}
    </Dialog>
  )
}
