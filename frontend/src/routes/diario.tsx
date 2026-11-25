import { useCallback, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, createFileRoute } from '@tanstack/react-router'
import {
  ApiError,
  apiGet,
  fetchJournalAll,
  fetchJournalCalibrationAll,
  fetchJournalDecision,
  type CalibrationData,
  type DecisionType,
  type Envelope,
  type Health,
  type JournalDecision,
  type JournalDetailData,
  type JournalPrediction,
} from '../lib/api'
import { brl, dateBr, fmt } from '../lib/format'
import {
  blockCount,
  blockHead,
  blockTitleBase,
  chainNote,
  focusOutset,
  inlineLink,
  note,
  noteBase,
  notice,
  num,
  quiet,
  quietLine,
  screenTitle,
} from '../lib/classes'
import { DecisionForm } from '../components/DecisionForm'
import { Dialog } from '../components/Dialog'
import { GlossaryMetric, Metric, MetricGrid } from '../components/Metric'
import { MetricModal } from '../components/MetricModal'
import type { ModalTarget, OpenMetric } from '../components/metricKeys'
import { ErrorState, Loading } from '../components/State'
import { TokenGate, useAppToken } from '../components/TokenGate'
import { Button } from '../components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../components/ui/table'

export const Route = createFileRoute('/diario')({
  component: Diario,
})

const TYPE_LABELS: Record<string, string> = {
  no_entry: 'Não entrei',
  entry: 'Entrada',
  exit: 'Saída',
  hold: 'Manter',
  watch: 'Observação',
  other: 'Outra',
}

const METRIC_LABELS: Record<string, string> = {
  spot: 'Preço (spot / fechamento)',
  iv_atm_30d: 'IV ATM 30d',
  vrp: 'VRP',
  rr25: 'RR25',
}

const STATUS_LABELS: Record<string, string> = {
  pending: 'pendente',
  hit: 'acerto',
  miss: 'erro',
  void: 'sem dados',
}

const VOID_LABELS: Record<string, string> = {
  metric_unavailable: 'métrica ausente na sessão',
  unsupported_metric: 'métrica não suportada',
  invalid_observation: 'observado inválido',
  prediction_parent_missing: 'previsão sem pai válido',
}

function metricLabel(metric: string): string {
  return METRIC_LABELS[metric] ?? metric
}

function metricValue(value: number | null, unit: string | undefined): string {
  if (value == null) return '—'
  if (unit === 'brl') return brl(value)
  if (unit === 'pct_annual') return `${fmt(value)}%`
  if (unit === 'vol_points') return `${fmt(value)} p.p.`
  return fmt(value)
}

function sessionsLabel(value: number): string {
  return `${value} ${value === 1 ? 'sessão' : 'sessões'}`
}

function decisionTypeLabel(type: DecisionType | string): string {
  return TYPE_LABELS[type] ?? type
}

function Diario() {
  const auth = useAppToken()
  const queryClient = useQueryClient()
  const [formOpen, setFormOpen] = useState(false)
  const [formBusy, setFormBusy] = useState(false)
  const [tokenNotice, setTokenNotice] = useState<string | null>(null)
  const [writeNotice, setWriteNotice] = useState<string | null>(null)
  const [modal, setModal] = useState<ModalTarget | null>(null)
  const openMetric: OpenMetric = (field, label, value) => setModal({ field, label, value })
  const closeForm = useCallback(() => setFormOpen(false), [])

  const health = useQuery({
    queryKey: ['health'],
    queryFn: ({ signal }) => apiGet<Health>('/api/health', signal),
    staleTime: 30_000,
  })
  const journal = useQuery({
    queryKey: ['journal', 'all'],
    queryFn: ({ signal }) => fetchJournalAll(signal),
  })
  const calibration = useQuery({
    queryKey: ['journal', 'calibration', 'all'],
    queryFn: ({ signal }) => fetchJournalCalibrationAll(signal),
  })

  function handleUnauthorized() {
    const cleared = auth.forgetToken()
    setTokenNotice(
      cleared
        ? 'O token foi recusado. O rascunho continua aqui; confira o APP_TOKEN e digite novamente.'
        : 'Não foi possível limpar o token deste navegador. Limpe os dados do site e tente novamente.',
    )
  }

  async function refreshJournal() {
    await queryClient.cancelQueries({ queryKey: ['journal'] })
    await queryClient.invalidateQueries({ queryKey: ['journal'] })
  }

  async function handleCreated(result: Envelope<JournalDetailData>) {
    await refreshJournal()
    setWriteNotice(
      result.meta?.truncated
        ? 'A decisão foi gravada, mas o detalhe retornado ficou truncado. Recarregue o diário.'
        : null,
    )
    setFormBusy(false)
    setFormOpen(false)
    setTokenNotice(null)
  }

  async function handleConflict(clientDecisionId: string) {
    await refreshJournal()
    try {
      const existing = await fetchJournalDecision(clientDecisionId)
      const decision = existing.data.decision
      setWriteNotice(
        `O UUID já estava em uso. A decisão existente de ${decision.underlying} em ${dateBr(
          decision.session,
        )} foi recarregada; o rascunho atual foi preservado.`,
      )
    } catch (error) {
      if (!(error instanceof ApiError && error.isNotFound)) {
        setWriteNotice('O UUID foi recusado e o diário foi recarregado. Confira o rascunho antes de tentar novamente.')
      }
    }
  }

  function changeToken() {
    if (auth.forgetToken()) {
      setFormOpen(false)
      setTokenNotice(null)
    }
  }

  return (
    <>
      <div className="flex flex-wrap items-start justify-between gap-[12px] max-sm:block">
        <div>
          <h1 className={`${screenTitle} lg:text-screen-title lg:leading-title`}>Diário</h1>
          <p className={`${quietLine} lg:text-label`}>Decisões, previsões estruturadas e desfecho EOD</p>
        </div>
        {auth.hasToken && (
          <div className="flex flex-wrap items-center gap-[12px] max-sm:mt-[12px]">
            <Button
              id="new-decision-trigger"
              type="button"
              onClick={() => {
                setWriteNotice(null)
                setFormBusy(false)
                setFormOpen(true)
              }}
            >
              Nova decisão
            </Button>
            {auth.tokenRequired && (
              <Button type="button" variant="link" size="sm" className="h-[44px] px-0 lg:text-label" onClick={changeToken}>
                Trocar token
              </Button>
            )}
          </div>
        )}
      </div>

      {!auth.hasToken && (
        <TokenGate
          notice={tokenNotice}
          description="A leitura do diário é liberada na tailnet. O token libera apenas o registro de decisões."
          onUnlock={() => setTokenNotice(null)}
        />
      )}

      {writeNotice && (
        <p className={`${notice} lg:text-label`} role="status">
          {writeNotice}
        </p>
      )}

      <section className="mt-[24px]" aria-labelledby="journal-calibration-title">
        <div className={blockHead}>
          <h2 className={`${blockTitleBase} mt-0 lg:text-block-title lg:leading-title`} id="journal-calibration-title">
            Calibração pessoal
          </h2>
          <span className={`${blockCount} lg:text-label`}>{calibration.data?.meta?.total ?? 0}</span>
        </div>
        <p className={`${chainNote} lg:text-label lg:leading-body`}>
          Por métrica e horizonte. A taxa usa apenas acertos e erros; “sem dados” fica separado.
          Não há comparação com sinal nem recomendação.
        </p>
        {calibration.isLoading && <Loading label="carregando calibração…" />}
        {calibration.isError && (
          <ErrorState error={calibration.error} title="Calibração indisponível" />
        )}
        {calibration.data && (
          <CalibrationReport data={calibration.data.data} onMetric={openMetric} />
        )}
      </section>

      <section className="mt-[24px]" aria-labelledby="journal-list-title">
        <div className={blockHead}>
          <h2 className={`${blockTitleBase} mt-0 lg:text-block-title lg:leading-title`} id="journal-list-title">
            Decisões
          </h2>
          <span className={`${blockCount} lg:text-label`}>{journal.data?.meta?.total ?? 0}</span>
        </div>
        {journal.isLoading && <Loading label="carregando diário…" />}
        {journal.isError && <ErrorState error={journal.error} title="Diário indisponível" />}
        {journal.data?.meta?.note && (
          <p className={`${notice} lg:text-label`} role="status">
            {journal.data.meta.note}
          </p>
        )}
        {journal.data && journal.data.data.decisions.length === 0 ? (
          <div className="rounded-[6px] border border-dashed border-input bg-card px-[16px] py-[24px]">
            <p className="mx-0 mt-0 mb-[4px]">Nenhuma decisão registrada.</p>
            <p className={`${noteBase} m-0 lg:text-label`}>
              {auth.hasToken
                ? 'Use “Nova decisão” para registrar, inclusive, uma escolha de não entrar.'
                : 'Salve o token acima para registrar uma decisão.'}
            </p>
          </div>
        ) : (
          <div className="grid gap-[12px]">
            {journal.data?.data.decisions.map((decision) => (
              <DecisionCard key={decision.id} decision={decision} onMetric={openMetric} />
            ))}
          </div>
        )}
      </section>

      <MetricModal target={modal} onClose={() => setModal(null)} />

      <Dialog
        open={formOpen}
        title="Nova decisão"
        eyebrow="journal append-only"
        onClose={closeForm}
        busy={formBusy}
        showCloseButton={false}
        size="wide"
        returnFocusSelector="#new-decision-trigger"
      >
        {formOpen && (
          <>
            {!auth.hasToken && (
              <TokenGate
                compact
                title="Token para registrar"
                description="O registro grava um evento append-only e exige o token de escrita."
                autoFocus
                notice={tokenNotice}
                onUnlock={() => setTokenNotice(null)}
              />
            )}
            <DecisionForm
              defaultSession={health.data?.data.last_session ?? null}
              onCreated={handleCreated}
              onCancel={closeForm}
              onUnauthorized={handleUnauthorized}
              onConflict={handleConflict}
              onBusyChange={setFormBusy}
            />
          </>
        )}
      </Dialog>
    </>
  )
}

function CalibrationReport({
  data,
  onMetric,
}: {
  data: CalibrationData
  onMetric: OpenMetric
}) {
  const totals = data.totals
  return (
    <>
      <MetricGrid>
        <GlossaryMetric
          field="sample_n"
          label="Previsões avaliadas"
          value={fmt(totals.n, 0)}
          onOpen={onMetric}
        />
        <Metric label="Acertos" value={fmt(totals.hit, 0)} />
        <Metric label="Erros" value={fmt(totals.miss, 0)} />
        <GlossaryMetric
          field="void_count"
          label="Sem dados"
          value={fmt(totals.void, 0)}
          onOpen={onMetric}
        />
        <GlossaryMetric
          field="personal_hit_rate"
          label="Taxa de acerto pessoal"
          value={totals.hit_rate_pct == null ? '—' : `${fmt(totals.hit_rate_pct, 1)}%`}
          note={`n ${totals.n}`}
          onOpen={onMetric}
        />
      </MetricGrid>
      {data.groups.length === 0 ? (
        <p className={`${note} lg:text-label`}>Ainda não há previsão avaliada. O relatório não inventa taxa sem n.</p>
      ) : (
        <div
          className="table-wrap table-wrap--scroll"
          role="region"
          aria-label="Calibração por métrica e horizonte"
          tabIndex={0}
        >
          <Table contained={false} className="screen-table min-w-[760px] lg:text-table lg:leading-table">
            <TableHeader>
              <TableRow>
                <TableHead scope="col" className="font-normal lg:text-table-head">Métrica</TableHead>
                <TableHead scope="col" className="font-normal lg:text-table-head">Horizonte</TableHead>
                <TableHead scope="col" className="font-normal lg:text-table-head">n</TableHead>
                <TableHead scope="col" className="font-normal lg:text-table-head">Acertos</TableHead>
                <TableHead scope="col" className="font-normal lg:text-table-head">Erros</TableHead>
                <TableHead scope="col" className="font-normal lg:text-table-head">Sem dados</TableHead>
                <TableHead scope="col" className="font-normal lg:text-table-head">Taxa</TableHead>
                <TableHead scope="col" className="min-w-[220px] font-normal lg:text-table-head">Motivo de void</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.groups.map((group) => (
                <TableRow key={`${group.metric}:${group.horizon_sessions}`}>
                  <TableHead scope="row" className="px-2 py-2.5 font-normal lg:text-table-head">
                    <Button
                      type="button"
                      variant="link"
                      size="sm"
                      className="h-[44px] px-0 lg:text-label"
                      onClick={() =>
                        onMetric(
                          group.metric,
                          metricLabel(group.metric),
                          metricValue(null, group.unit),
                        )
                      }
                    >
                      {metricLabel(group.metric)}
                    </Button>
                  </TableHead>
                  <TableCell className={`${num} px-2 py-2.5`}>{sessionsLabel(group.horizon_sessions)}</TableCell>
                  <TableCell className={`${num} px-2 py-2.5`}>{group.n}</TableCell>
                  <TableCell className={`${num} px-2 py-2.5`}>{group.hit}</TableCell>
                  <TableCell className={`${num} px-2 py-2.5`}>{group.miss}</TableCell>
                  <TableCell className={`${num} px-2 py-2.5`}>{group.void}</TableCell>
                  <TableCell className={`${num} px-2 py-2.5`}>
                    {group.hit_rate_pct == null ? '—' : `${fmt(group.hit_rate_pct, 1)}%`}
                    <span className="mt-[2px] block text-[10.5px] text-muted-foreground lg:text-detail">n {group.n}</span>
                  </TableCell>
                  <TableCell className="min-w-[220px] px-2 py-2.5 text-left">
                    {group.void_reasons.length === 0
                      ? '—'
                      : group.void_reasons
                          .map(
                            (item) =>
                              `${VOID_LABELS[item.reason] ?? item.reason}: ${item.count}`,
                          )
                          .join(' · ')}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </>
  )
}

function DecisionCard({
  decision,
  onMetric,
}: {
  decision: JournalDecision
  onMetric: OpenMetric
}) {
  const prediction = decision.prediction
  return (
    <article className="min-w-0 rounded-[6px] border border-border bg-card p-[16px] max-sm:p-[12px]">
      <header className="flex flex-wrap items-baseline justify-between gap-x-[12px] gap-y-[4px]">
        <div>
          <Link
            className={`inline-flex min-h-[44px] items-center font-mono text-[20px] font-medium tabular-nums text-foreground ${focusOutset} lg:text-metric`}
            to="/t/$ticker"
            params={{ ticker: decision.underlying }}
          >
            {decision.underlying}
          </Link>
          <span className="ml-[12px] text-[12px] text-[color:var(--text-2)] lg:text-label">
            {decisionTypeLabel(decision.decision_type)}
          </span>
        </div>
        <span className={`${quiet} lg:text-label`}>{dateBr(decision.session)}</span>
      </header>
      {decision.context && <p className="m-0 pt-[8px] text-[12.5px] leading-[1.5] text-[color:var(--text-2)] [overflow-wrap:anywhere] lg:text-body lg:leading-body">{decision.context}</p>}
      {decision.notes && <p className="m-0 border-t border-t-border py-[12px] text-[12.5px] leading-[1.55] whitespace-pre-wrap text-[color:var(--text-2)] [overflow-wrap:anywhere] lg:text-body lg:leading-body">{decision.notes}</p>}
      {decision.position_id && (
        <p className={`${quietLine} lg:text-label`}>
          posição vinculada:{' '}
          <Link
            className={`${inlineLink} lg:text-label`}
            to="/carteira/$positionId"
            params={{ positionId: decision.position_id }}
          >
            abrir na Carteira
          </Link>
        </p>
      )}
      {prediction ? (
        <PredictionOutcome prediction={prediction} onMetric={onMetric} />
      ) : (
        <p className={`${note} lg:text-label`}>Sem previsão estruturada; esta decisão não entra na calibração.</p>
      )}
    </article>
  )
}

function PredictionOutcome({
  prediction,
  onMetric,
}: {
  prediction: JournalPrediction
  onMetric: OpenMetric
}) {
  const threshold = metricValue(prediction.value, prediction.unit)
  const observed = metricValue(prediction.observed, prediction.unit)
  return (
    <div className="mt-[8px] border-t border-t-border">
      <div className="grid grid-cols-[minmax(92px,0.35fr)_minmax(0,1fr)] gap-[12px] border-b border-b-border py-[8px] text-[11.5px] max-sm:grid-cols-[minmax(0,1fr)] max-sm:gap-[2px] lg:text-label">
        <span className={`${quiet} lg:text-label`}>Previsão</span>
        <span className={`${num} min-w-0 text-right whitespace-normal [overflow-wrap:anywhere]`}>
          <Button
            type="button"
            variant="link"
            size="sm"
            className="h-[44px] px-0 lg:text-label"
            onClick={() => onMetric(prediction.metric, metricLabel(prediction.metric), threshold)}
          >
            {metricLabel(prediction.metric)}
          </Button>{' '}
          {prediction.operator} {threshold} · {sessionsLabel(prediction.horizon_sessions)} · avaliar em{' '}
          {dateBr(prediction.evaluate_on)}
        </span>
      </div>
      <div className="grid grid-cols-[minmax(92px,0.35fr)_minmax(0,1fr)] gap-[12px] border-b border-b-border py-[8px] text-[11.5px] max-sm:grid-cols-[minmax(0,1fr)] max-sm:gap-[2px] lg:text-label">
        <span className={`${quiet} lg:text-label`}>Observado</span>
        <span className={`${num} min-w-0 text-right whitespace-normal [overflow-wrap:anywhere]`}>
          {observed}
          {prediction.evaluated_on ? ` · ${dateBr(prediction.evaluated_on)}` : ''}
        </span>
      </div>
      <div className="grid grid-cols-[minmax(92px,0.35fr)_minmax(0,1fr)] gap-[12px] border-b border-b-border py-[8px] text-[11.5px] max-sm:grid-cols-[minmax(0,1fr)] max-sm:gap-[2px] lg:text-label">
        <span className={`${quiet} lg:text-label`}>Desfecho</span>
        <span className={`${num} min-w-0 text-right whitespace-normal [overflow-wrap:anywhere]`}>
          <Button
            type="button"
            variant="link"
            size="sm"
            className="h-[44px] px-0 lg:text-label"
            onClick={() =>
              onMetric(
                'prediction_status',
                'Desfecho da previsão',
                STATUS_LABELS[prediction.status] ?? prediction.status,
              )
            }
          >
            {STATUS_LABELS[prediction.status] ?? prediction.status}
          </Button>
          {prediction.status === 'void' && prediction.void_reason && (
            <span className="text-[color:var(--text-2)]">
              {' '}
              · {VOID_LABELS[prediction.void_reason] ?? prediction.void_reason}
            </span>
          )}
        </span>
      </div>
    </div>
  )
}
