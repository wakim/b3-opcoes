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
import { DecisionForm } from '../components/DecisionForm'
import { Dialog } from '../components/Dialog'
import { GlossaryMetric, Metric, MetricGrid } from '../components/Metric'
import { MetricModal } from '../components/MetricModal'
import type { ModalTarget, OpenMetric } from '../components/metricKeys'
import { ErrorState, Loading } from '../components/State'
import { TokenGate, useAppToken } from '../components/TokenGate'

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
      <div className="portfolio-head">
        <div>
          <h1 className="screen-title lg:text-screen-title lg:leading-title">Diário</h1>
          <p className="quiet-line lg:text-label">Decisões, previsões estruturadas e desfecho EOD</p>
        </div>
        {auth.hasToken && (
          <div className="portfolio-actions">
            <button
              type="button"
              className="builder__button builder__button--primary lg:text-control"
              onClick={() => {
                setWriteNotice(null)
                setFormBusy(false)
                setFormOpen(true)
              }}
            >
              Nova decisão
            </button>
            <button type="button" className="inline-link lg:text-label" onClick={changeToken}>
              Trocar token
            </button>
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
        <p className="notice lg:text-label" role="status">
          {writeNotice}
        </p>
      )}

      <section className="journal-section" aria-labelledby="journal-calibration-title">
        <div className="block-head">
          <h2 className="block-title lg:text-block-title lg:leading-title" id="journal-calibration-title">
            Calibração pessoal
          </h2>
          <span className="block-count lg:text-label">{calibration.data?.meta?.total ?? 0}</span>
        </div>
        <p className="chain-note lg:text-label lg:leading-body">
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

      <section className="journal-section" aria-labelledby="journal-list-title">
        <div className="block-head">
          <h2 className="block-title lg:text-block-title lg:leading-title" id="journal-list-title">
            Decisões
          </h2>
          <span className="block-count lg:text-label">{journal.data?.meta?.total ?? 0}</span>
        </div>
        {journal.isLoading && <Loading label="carregando diário…" />}
        {journal.isError && <ErrorState error={journal.error} title="Diário indisponível" />}
        {journal.data?.meta?.note && (
          <p className="notice lg:text-label" role="status">
            {journal.data.meta.note}
          </p>
        )}
        {journal.data && journal.data.data.decisions.length === 0 ? (
          <div className="position-empty">
            <p>Nenhuma decisão registrada.</p>
            <p className="note lg:text-label">
              {auth.hasToken
                ? 'Use “Nova decisão” para registrar, inclusive, uma escolha de não entrar.'
                : 'Salve o token acima para registrar uma decisão.'}
            </p>
          </div>
        ) : (
          <div className="position-list">
            {journal.data?.data.decisions.map((decision) => (
              <DecisionCard key={decision.id} decision={decision} onMetric={openMetric} />
            ))}
          </div>
        )}
      </section>

      <MetricModal target={modal} onClose={() => setModal(null)} />

      {formOpen && (
        <Dialog
          labelledBy="decision-form-title"
          onClose={closeForm}
          busy={formBusy}
          className="modal--form"
        >
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
        </Dialog>
      )}
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
        <p className="note lg:text-label">Ainda não há previsão avaliada. O relatório não inventa taxa sem n.</p>
      ) : (
        <div
          className="table-wrap table-wrap--scroll journal-table-wrap"
          role="region"
          aria-label="Calibração por métrica e horizonte"
          tabIndex={0}
        >
          <table className="screen-table journal-table lg:text-table lg:leading-table">
            <thead>
              <tr>
                <th scope="col" className="lg:text-table-head">Métrica</th>
                <th scope="col" className="lg:text-table-head">Horizonte</th>
                <th scope="col" className="lg:text-table-head">n</th>
                <th scope="col" className="lg:text-table-head">Acertos</th>
                <th scope="col" className="lg:text-table-head">Erros</th>
                <th scope="col" className="lg:text-table-head">Sem dados</th>
                <th scope="col" className="lg:text-table-head">Taxa</th>
                <th scope="col" className="lg:text-table-head">Motivo de void</th>
              </tr>
            </thead>
            <tbody>
              {data.groups.map((group) => (
                <tr key={`${group.metric}:${group.horizon_sessions}`}>
                  <th scope="row" className="lg:text-table-head">
                    <button
                      type="button"
                      className="inline-link lg:text-label"
                      onClick={() =>
                        onMetric(
                          group.metric,
                          metricLabel(group.metric),
                          metricValue(null, group.unit),
                        )
                      }
                    >
                      {metricLabel(group.metric)}
                    </button>
                  </th>
                  <td className="num">{sessionsLabel(group.horizon_sessions)}</td>
                  <td className="num">{group.n}</td>
                  <td className="num">{group.hit}</td>
                  <td className="num">{group.miss}</td>
                  <td className="num">{group.void}</td>
                  <td className="num">
                    {group.hit_rate_pct == null ? '—' : `${fmt(group.hit_rate_pct, 1)}%`}
                    <span className="journal-table__n lg:text-detail">n {group.n}</span>
                  </td>
                  <td>
                    {group.void_reasons.length === 0
                      ? '—'
                      : group.void_reasons
                          .map(
                            (item) =>
                              `${VOID_LABELS[item.reason] ?? item.reason}: ${item.count}`,
                          )
                          .join(' · ')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
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
    <article className="position-card journal-card">
      <header className="position-card__head">
        <div>
          <Link
            className="position-card__title lg:text-metric"
            to="/t/$ticker"
            params={{ ticker: decision.underlying }}
          >
            {decision.underlying}
          </Link>
          <span className="position-card__kind lg:text-label">
            {decisionTypeLabel(decision.decision_type)}
          </span>
        </div>
        <span className="quiet lg:text-label">{dateBr(decision.session)}</span>
      </header>
      {decision.context && <p className="journal-card__context lg:text-body lg:leading-body">{decision.context}</p>}
      {decision.notes && <p className="position-card__notes lg:text-body lg:leading-body">{decision.notes}</p>}
      {decision.position_id && (
        <p className="quiet-line lg:text-label">
          posição vinculada:{' '}
          <Link
            className="inline-link lg:text-label"
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
        <p className="note lg:text-label">Sem previsão estruturada; esta decisão não entra na calibração.</p>
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
    <div className="journal-outcome">
      <div className="journal-outcome__row lg:text-label">
        <span className="quiet lg:text-label">Previsão</span>
        <span className="num">
          <button
            type="button"
            className="inline-link lg:text-label"
            onClick={() => onMetric(prediction.metric, metricLabel(prediction.metric), threshold)}
          >
            {metricLabel(prediction.metric)}
          </button>{' '}
          {prediction.operator} {threshold} · {sessionsLabel(prediction.horizon_sessions)} · avaliar em{' '}
          {dateBr(prediction.evaluate_on)}
        </span>
      </div>
      <div className="journal-outcome__row lg:text-label">
        <span className="quiet lg:text-label">Observado</span>
        <span className="num">
          {observed}
          {prediction.evaluated_on ? ` · ${dateBr(prediction.evaluated_on)}` : ''}
        </span>
      </div>
      <div className="journal-outcome__row lg:text-label">
        <span className="quiet lg:text-label">Desfecho</span>
        <span className="num">
          <button
            type="button"
            className="inline-link lg:text-label"
            onClick={() =>
              onMetric(
                'prediction_status',
                'Desfecho da previsão',
                STATUS_LABELS[prediction.status] ?? prediction.status,
              )
            }
          >
            {STATUS_LABELS[prediction.status] ?? prediction.status}
          </button>
          {prediction.status === 'void' && prediction.void_reason && (
            <span className="journal-outcome__reason">
              {' '}
              · {VOID_LABELS[prediction.void_reason] ?? prediction.void_reason}
            </span>
          )}
        </span>
      </div>
    </div>
  )
}
