/** Ficha de métrica em modal: verbete do glossário + o número do dia.
 *
 * Fonte única `/api/glossary/{key}` (staleTime 1 h). Diálogo centralizado com
 * foco no fechar, Escape, clique no backdrop e trap de Tab. Não importa
 * `lib/ui.tsx` (dono paralelo).
 */
import { useQuery } from '@tanstack/react-query'
import { apiGet, type GlossaryDetail } from '../lib/api'
import { num } from '../lib/classes'
import { useCopy } from '../lib/presentation'
import { glossaryKey, presentationKeyForField, type ModalTarget } from './metricKeys'
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
  // O slug é sempre de `metricKeys.ts`; a chave semântica só decide texto.
  const semantic = target ? (target.term ?? presentationKeyForField(target.field)) : null
  const copy = useCopy(semantic ?? 'metric.generic', target?.variant ?? undefined, target?.params)
  // Com chave semântica, o rótulo resolvido vence o texto legado da superfície.
  const title = semantic ? copy.label : (target?.label ?? copy.label)
  const help = semantic ? copy.help : undefined
  const eyebrow = useCopy('glossary', 'eyebrow').label
  const loadingLabel = useCopy('glossary', 'loading').label
  const errorLabel = useCopy('glossary', 'error').label
  const missingLabel = useCopy('glossary', 'missing').label
  const panelLabel = useCopy('glossary', 'panel_label').label
  const whatLabel = useCopy('glossary', 'what').label
  const mistakeLabel = useCopy('glossary', 'mistake').label
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
  const sourceCopy = useCopy('glossary', 'source', { title: source ?? '' })

  return (
    <Dialog
      open={Boolean(target)}
      title={title}
      eyebrow={eyebrow}
      description={source && source !== title ? sourceCopy.label : undefined}
      onClose={onClose}
      initialFocus="close"
      size="wide"
    >
      {target && (
        <div className="grid gap-4">
          <div className="grid gap-1 rounded-[6px] border border-border bg-muted/30 p-4">
            <span className="lg:text-label text-muted-foreground">{panelLabel}</span>
            <span className={`${num} lg:text-metric`}>{target.value}</span>
            {help && <span className="lg:text-label text-muted-foreground">{help}</span>}
          </div>
          {entry.isError && <ErrorState error={entry.error} title={errorLabel} />}
          {entry.isLoading && <p className="lg:text-label text-muted-foreground">{loadingLabel}</p>}
          <Block title={whatLabel} text={definition} />
          <Block title={mistakeLabel} text={mistake} />
          {!key && (
            <p className="lg:text-label text-muted-foreground">
              {missingLabel}
            </p>
          )}
        </div>
      )}
    </Dialog>
  )
}
