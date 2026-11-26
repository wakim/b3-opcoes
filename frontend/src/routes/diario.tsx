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
import { ScreenTable } from '../components/ScreenTable'
import type { ModalTarget, OpenMetric } from '../components/metricKeys'
import { usePresentation } from '../lib/presentation'
import { ErrorState, Loading } from '../components/State'
import { TokenGate, useAppToken } from '../components/TokenGate'
import { Button } from '../components/ui/button'
import {
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../components/ui/table'

export const Route = createFileRoute('/diario')({
  component: Diario,
})

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

function Diario() {
  const p = usePresentation()
  const auth = useAppToken()
  const queryClient = useQueryClient()
  const [formOpen, setFormOpen] = useState(false)
  const [formBusy, setFormBusy] = useState(false)
  const [tokenNotice, setTokenNotice] = useState<string | null>(null)
  const [writeNotice, setWriteNotice] = useState<string | null>(null)
  const [modal, setModal] = useState<ModalTarget | null>(null)
  const openMetric: OpenMetric = (field, label, value, term, variant, params) =>
    setModal({ field, label, value, term, variant, params })
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
          <h1 className={`${screenTitle} lg:text-screen-title lg:leading-title`}>{p.copy('journal', 'title').label}</h1>
          <p className={`${quietLine} lg:text-label`}>{p.copy('journal', 'intro', { eod: p.copy('eod').label }).label}</p>
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
              {p.copy('action', 'new_decision').label}
            </Button>
            {auth.tokenRequired && (
              <Button type="button" variant="link" size="sm" className="h-[44px] min-w-[44px] px-0 lg:text-label" onClick={changeToken}>
                {p.copy('token', 'change').label}
              </Button>
            )}
          </div>
        )}
      </div>

      {!auth.hasToken && (
        <TokenGate
          notice={tokenNotice}
          description={p.copy('token', 'description_journal').label}
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
            {p.copy('calibration').label}
          </h2>
          <span className={`${blockCount} lg:text-label`}>{calibration.data?.meta?.total ?? 0}</span>
        </div>
        <p className={`${chainNote} lg:text-label lg:leading-body`}>
          {p.copy('calibration', 'intro').label} {p.copy('calibration_note').label} {p.copy('calibration', 'note').label}
        </p>
        {calibration.isLoading && <Loading label={p.copy('loading', 'calibration').label} />}
        {calibration.isError && (
          <ErrorState error={calibration.error} title={p.copy('error_title', 'calibration').label} />
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
        {journal.isLoading && <Loading label={p.copy('loading', 'journal').label} />}
        {journal.isError && <ErrorState error={journal.error} title={p.copy('error_title', 'journal').label} />}
        {journal.data?.meta?.note && (
          <p className={`${notice} lg:text-label`} role="status">
            {journal.data.meta.note}
          </p>
        )}
        {journal.data && journal.data.data.decisions.length === 0 ? (
          <div className="rounded-[6px] border border-dashed border-input bg-card px-[16px] py-[24px]">
            <p className="mx-0 mt-0 mb-[4px]">{p.copy('journal', 'no_decision').label}</p>
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
        title={p.copy('action', 'new_decision').label}
        eyebrow={p.copy('append_only').label}
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
                title={p.copy('token', 'register').label}
                description={p.copy('token', 'description_close').label}
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
  const p = usePresentation()
  const totals = data.totals
  return (
    <>
      <MetricGrid>
        <GlossaryMetric
          field="sample_n"
          label={p.copy('calibration', 'evaluated').label}
          term={p.metric('sample_n') ?? undefined}
          value={fmt(totals.n, 0)}
          onOpen={onMetric}
        />
        <Metric label={p.copy('calibration', 'hits').label} value={fmt(totals.hit, 0)} />
        <Metric label={p.copy('calibration', 'misses').label} value={fmt(totals.miss, 0)} />
        <GlossaryMetric
          field="void_count"
          term="void"
          value={fmt(totals.void, 0)}
          onOpen={onMetric}
        />
        <GlossaryMetric
          field="personal_hit_rate"
          term="personal_hit_rate"
          value={totals.hit_rate_pct == null ? '—' : `${fmt(totals.hit_rate_pct, 1)}%`}
          note={`n ${totals.n}`}
          onOpen={onMetric}
        />
      </MetricGrid>
      {data.groups.length === 0 ? (
        <p className={`${note} lg:text-label`}>{p.copy('empty', 'calibration').label}</p>
      ) : (
        <ScreenTable
          wrapperClassName="table-wrap table-wrap--scroll"
          wrapperProps={{
            role: 'region',
            'aria-label': p.copy('calibration', 'region').label,
            tabIndex: 0,
          }}
          className="min-w-[760px]"
        >
          <TableHeader>
              <TableRow>
                <TableHead scope="col" className="font-normal lg:text-table-head">{p.copy('metric.generic').label}</TableHead>
                <TableHead scope="col" className="font-normal lg:text-table-head">{p.copy('horizon').label}</TableHead>
                <TableHead scope="col" className="font-normal lg:text-table-head">{p.copy('sample_n').label}</TableHead>
                <TableHead scope="col" className="font-normal lg:text-table-head">{p.copy('calibration', 'hits').label}</TableHead>
                <TableHead scope="col" className="font-normal lg:text-table-head">{p.copy('calibration', 'misses').label}</TableHead>
                <TableHead scope="col" className="font-normal lg:text-table-head">{p.copy('void', 'count').label}</TableHead>
                <TableHead scope="col" className="font-normal lg:text-table-head">{p.copy('calibration', 'rate').label}</TableHead>
                <TableHead scope="col" className="min-w-[220px] font-normal lg:text-table-head">{p.copy('void', 'reason').label}</TableHead>
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
                      className="h-[44px] min-w-[44px] px-0 lg:text-label"
                      onClick={() =>
                        onMetric(
                          group.metric,
                          p.metricLabel(group.metric),
                          metricValue(null, group.unit),
                          p.metric(group.metric) ?? undefined,
                          group.metric === 'spot' ? 'form' : group.metric === 'iv_atm_30d' || group.metric === 'vrp' ? 'option' : undefined,
                        )
                      }
                    >
                      {p.metricLabel(group.metric)}
                    </Button>
                  </TableHead>
                  <TableCell className={`${num} px-2 py-2.5`}>{sessionsLabel(group.horizon_sessions)}</TableCell>
                  <TableCell className={`${num} px-2 py-2.5`}>{group.n}</TableCell>
                  <TableCell className={`${num} px-2 py-2.5`}>{group.hit}</TableCell>
                  <TableCell className={`${num} px-2 py-2.5`}>{group.miss}</TableCell>
                  <TableCell className={`${num} px-2 py-2.5`}>{group.void}</TableCell>
                  <TableCell className={`${num} px-2 py-2.5`}>
                    {group.hit_rate_pct == null ? '—' : `${fmt(group.hit_rate_pct, 1)}%`}
                    <span className="mt-[2px] block text-[calc(10.5px*var(--font-scale))] text-muted-foreground lg:text-detail">n {group.n}</span>
                  </TableCell>
                  <TableCell className="min-w-[220px] px-2 py-2.5 text-left">
                    {group.void_reasons.length === 0
                      ? '—'
                      : group.void_reasons
                          .map(
                            (item) =>
                              `${p.voidReason(item.reason).label}: ${item.count}`,
                          )
                          .join(' · ')}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
        </ScreenTable>
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
  const p = usePresentation()
  const prediction = decision.prediction
  return (
    <article className="min-w-0 rounded-[6px] border border-border bg-card p-[16px] max-sm:p-[12px]">
      <header className="flex flex-wrap items-baseline justify-between gap-x-[12px] gap-y-[4px]">
        <div>
          <Link
            className={`inline-flex min-h-[44px] items-center font-mono text-[calc(20px*var(--font-scale))] font-medium tabular-nums text-foreground ${focusOutset} lg:text-metric`}
            to="/t/$ticker"
            params={{ ticker: decision.underlying }}
          >
            {decision.underlying}
          </Link>
          <span className="ml-[12px] text-[calc(12px*var(--font-scale))] text-[color:var(--text-2)] lg:text-label">
            {p.decisionType(decision.decision_type).label}
          </span>
        </div>
        <span className={`${quiet} lg:text-label`}>{dateBr(decision.session)}</span>
      </header>
      {decision.context && <p className="m-0 pt-[8px] text-[calc(12.5px*var(--font-scale))] leading-[1.5] text-[color:var(--text-2)] [overflow-wrap:anywhere] lg:text-body lg:leading-body">{decision.context}</p>}
      {decision.notes && <p className="m-0 border-t border-t-border py-[12px] text-[calc(12.5px*var(--font-scale))] leading-[1.55] whitespace-pre-wrap text-[color:var(--text-2)] [overflow-wrap:anywhere] lg:text-body lg:leading-body">{decision.notes}</p>}
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
        <p className={`${note} lg:text-label`}>{p.copy('journal', 'no_prediction').label}</p>
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
  const p = usePresentation()
  const threshold = metricValue(prediction.value, prediction.unit)
  const observed = metricValue(prediction.observed, prediction.unit)
  return (
    <div className="mt-[8px] border-t border-t-border">
      <div className="grid grid-cols-[minmax(92px,0.35fr)_minmax(0,1fr)] gap-[12px] border-b border-b-border py-[8px] text-[calc(11.5px*var(--font-scale))] max-sm:grid-cols-[minmax(0,1fr)] max-sm:gap-[2px] lg:text-label">
        <span className={`${quiet} lg:text-label`}>{p.copy('prediction').label}</span>
        <span className={`${num} min-w-0 text-right whitespace-normal [overflow-wrap:anywhere]`}>
          <Button
            type="button"
            variant="link"
            size="sm"
            className="h-[44px] min-w-[44px] px-0 lg:text-label"
            onClick={() => onMetric(
              prediction.metric,
              p.metricLabel(prediction.metric),
              threshold,
              p.metric(prediction.metric) ?? undefined,
              prediction.metric === 'spot' ? 'form' : prediction.metric === 'iv_atm_30d' || prediction.metric === 'vrp' ? 'option' : undefined,
            )}
          >
            {p.metricLabel(prediction.metric)}
          </Button>{' '}
          {prediction.operator} {threshold} · {sessionsLabel(prediction.horizon_sessions)} · avaliar em{' '}
          {dateBr(prediction.evaluate_on)}
        </span>
      </div>
      <div className="grid grid-cols-[minmax(92px,0.35fr)_minmax(0,1fr)] gap-[12px] border-b border-b-border py-[8px] text-[calc(11.5px*var(--font-scale))] max-sm:grid-cols-[minmax(0,1fr)] max-sm:gap-[2px] lg:text-label">
        <span className={`${quiet} lg:text-label`}>{p.copy('observed').label}</span>
        <span className={`${num} min-w-0 text-right whitespace-normal [overflow-wrap:anywhere]`}>
          {observed}
          {prediction.evaluated_on ? ` · ${dateBr(prediction.evaluated_on)}` : ''}
        </span>
      </div>
      <div className="grid grid-cols-[minmax(92px,0.35fr)_minmax(0,1fr)] gap-[12px] border-b border-b-border py-[8px] text-[calc(11.5px*var(--font-scale))] max-sm:grid-cols-[minmax(0,1fr)] max-sm:gap-[2px] lg:text-label">
        <span className={`${quiet} lg:text-label`}>{p.copy('prediction_status_journal').label}</span>
        <span className={`${num} min-w-0 text-right whitespace-normal [overflow-wrap:anywhere]`}>
          <Button
            type="button"
            variant="link"
            size="sm"
            className="h-[44px] min-w-[44px] px-0 lg:text-label"
            onClick={() =>
              onMetric(
                'prediction_status',
                p.copy('prediction_status_journal').label,
                p.predictionStatus(prediction.status, 'journal').label,
                'prediction_status_journal',
                prediction.status,
              )
            }
          >
            {p.predictionStatus(prediction.status, 'journal').label}
          </Button>
          {prediction.status === 'void' && prediction.void_reason && (
            <span className="text-[color:var(--text-2)]">
              {' '}
              · {p.voidReason(prediction.void_reason).label}
            </span>
          )}
        </span>
      </div>
    </div>
  )
}
