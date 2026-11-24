import { useCallback, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, createFileRoute } from '@tanstack/react-router'
import {
  apiGet,
  apiWrite,
  ApiError,
  type PositionCloseRequest,
  type PositionDetail,
} from '../lib/api'
import { brl, dateBr, fmt } from '../lib/format'
import { Dialog } from '../components/Dialog'
import { GlossaryMetric, Metric, MetricGrid } from '../components/Metric'
import { MetricModal } from '../components/MetricModal'
import type { ModalTarget, OpenMetric } from '../components/metricKeys'
import { PayoffChart } from '../components/PayoffChart'
import {
  formatPnlBrl,
  formatPnlPct,
  legTypeLabel,
  pnlTone,
  positionKindLabel,
  positionStatusLabel,
  predictionStatusLabel,
  PositionFlags,
} from '../components/PositionBits'
import { ErrorState, Loading } from '../components/State'
import { HelpTip } from '../components/HelpTip'
import { TokenGate, useAppToken } from '../components/TokenGate'

export const Route = createFileRoute('/carteira_/$positionId')({
  component: PositionDetailPage,
})

function percentPoints(value: number | null): string {
  return value == null ? '—' : `${fmt(value)}%`
}

function PositionDetailPage() {
  const { positionId } = Route.useParams()
  const auth = useAppToken()
  const queryClient = useQueryClient()
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [closeDate, setCloseDate] = useState('')
  const [tokenNotice, setTokenNotice] = useState<string | null>(null)
  const [writeNotice, setWriteNotice] = useState<string | null>(null)
  const [modal, setModal] = useState<ModalTarget | null>(null)
  const openMetric: OpenMetric = (field, label, value) => setModal({ field, label, value })
  const closeConfirm = useCallback(() => setConfirmOpen(false), [])

  const position = useQuery({
    queryKey: ['position', positionId],
    queryFn: ({ signal }) =>
      apiGet<PositionDetail>(`/api/positions/${encodeURIComponent(positionId)}`, signal),
    staleTime: 0,
    refetchOnMount: 'always',
  })

  const close = useMutation({
    mutationFn: (body: PositionCloseRequest) =>
      apiWrite<PositionDetail>(`/api/positions/${encodeURIComponent(positionId)}/close`, body),
    onSuccess: (result) => {
      queryClient.setQueryData(['position', positionId], result)
      void queryClient.invalidateQueries({ queryKey: ['positions'] })
      setWriteNotice(
        result.meta?.truncated
          ? 'O fechamento foi gravado, mas o detalhe retornado ficou truncado. A carteira será recarregada.'
          : result.meta?.note ?? null,
      )
      setConfirmOpen(false)
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 401) {
        const cleared = auth.forgetToken()
        setTokenNotice(
          cleared
            ? 'O token foi recusado. Confira o APP_TOKEN do servidor e digite novamente.'
            : 'Não foi possível limpar o token deste navegador. Limpe os dados do site e tente novamente.',
        )
      } else if (
        error instanceof ApiError &&
        (error.status === 409 || error.code === 'position_closed')
      ) {
        setConfirmOpen(false)
        void queryClient.invalidateQueries({ queryKey: ['position', positionId] })
        void queryClient.invalidateQueries({ queryKey: ['positions'] })
      }
    },
  })

  function openConfirmation() {
    setCloseDate('')
    setConfirmOpen(true)
  }

  function changeToken() {
    if (auth.forgetToken()) setTokenNotice(null)
  }

  if (position.isLoading) return <Loading label="carregando posição…" />
  if (position.isError) return <ErrorState error={position.error} title="Posição indisponível" />
  if (!position.data) return <Loading label="carregando posição…" />

  const data = position.data.data
  const isOpen = data.status === 'open'
  const payoffTruncated = Boolean(position.data.meta?.truncated || position.data.meta?.note)

  return (
    <>
      <Link className="back lg:text-label" to="/carteira">
        <span aria-hidden="true">‹</span> Carteira
      </Link>

      <div className="position-detail__head">
        <div>
          <h1 className="screen-title lg:text-screen-title lg:leading-title">{data.underlying}</h1>
          <p className="quiet-line lg:text-label">
            {positionKindLabel(data.kind)} · {positionStatusLabel(data.status)} · sessão{' '}
            {position.data.as_of_session ?? '—'}
          </p>
        </div>
      </div>

      {writeNotice && (
        <p className="notice lg:text-label" role="status">
          {writeNotice}
        </p>
      )}
      {(position.data.meta?.truncated || position.data.meta?.note) && (
        <p className="notice lg:text-label" role="status">
          {position.data.meta.note ??
            'O payoff foi truncado no transporte e não é exibido como completo. Reabra a posição para tentar novamente.'}
        </p>
      )}

      <p className="position-flags-line lg:text-label">
        <span className="quiet lg:text-label">Flags da posição</span>{' '}
        <PositionFlags flags={data.flags} />
      </p>

      <MetricGrid>
        <Metric
          label="P&L"
          value={<span className={pnlTone(data.pnl_brl)}>{formatPnlBrl(data.pnl_brl)}</span>}
          emphasis
        />
        <GlossaryMetric
          field="pnl_pct"
          label="P&L %"
          value={formatPnlPct(data.pnl_pct)}
          onOpen={openMetric}
        />
        <GlossaryMetric
          field="delta"
          label="Delta"
          value={fmt(data.delta, 3)}
          onOpen={openMetric}
        />
        <GlossaryMetric
          field="theta_brl"
          label="Theta (R$/dia)"
          value={brl(data.theta_brl)}
          onOpen={openMetric}
        />
        <GlossaryMetric
          field="vega_brl"
          label="Vega (R$/ponto)"
          value={brl(data.vega_brl)}
          onOpen={openMetric}
        />
        <GlossaryMetric
          field="days_to_expiry"
          label="Sessões até vencimento"
          value={fmt(data.days_to_expiry, 0)}
          onOpen={openMetric}
        />
        <GlossaryMetric
          field="breakeven_brl"
          label="Breakevens"
          value={
            data.breakeven_brl.length > 0
              ? data.breakeven_brl.map((value) => brl(value)).join(' · ')
              : '—'
          }
          onOpen={openMetric}
        />
        <GlossaryMetric
          field="entry_iv_atm_30d"
          label="Volatilidade implícita 30d na entrada"
          value={percentPoints(data.entry_iv_atm_30d)}
          onOpen={openMetric}
        />
        <GlossaryMetric
          field="current_iv_atm_30d"
          label={
            isOpen
              ? 'Volatilidade implícita 30d na sessão'
              : 'Volatilidade implícita 30d no fechamento'
          }
          value={percentPoints(data.current_iv_atm_30d)}
          onOpen={openMetric}
        />
      </MetricGrid>

      <section className="position-section" aria-labelledby="position-identity-title">
        <h2 className="block-title lg:text-block-title lg:leading-title" id="position-identity-title">Registro</h2>
        <dl className="identity">
          <div>
            <dt className="lg:text-label">Capital comprometido</dt>
            <dd className="num lg:text-detail">{brl(data.capital_committed_brl)}</dd>
          </div>
          <div>
            <dt className="lg:text-label">Limite de capital</dt>
            <dd className="num lg:text-detail">{brl(data.capital_limit_brl)}</dd>
          </div>
          <div>
            <dt className="lg:text-label">Aberta em</dt>
            <dd className="num lg:text-detail">{dateBr(data.opened_session)}</dd>
          </div>
          <div>
            <dt className="lg:text-label">Fechada em</dt>
            <dd className="num lg:text-detail">{dateBr(data.closed_session)}</dd>
          </div>
          <div>
            <dt className="lg:text-label">Vencimento</dt>
            <dd className="num lg:text-detail">{dateBr(data.maturity)}</dd>
          </div>
          <div>
            <dt className="lg:text-label">Status</dt>
            <dd className="lg:text-detail">{positionStatusLabel(data.status)}</dd>
          </div>
        </dl>
        {data.notes && <p className="position-detail__notes lg:text-body lg:leading-body">{data.notes}</p>}
      </section>

      <section className="position-section" aria-labelledby="position-legs-title">
        <h2 className="block-title lg:text-block-title lg:leading-title" id="position-legs-title">
          Pernas registradas
          <HelpTip
            label="O que é uma perna?"
            text="Perna é cada operação que compõe a posição: uma ação ou uma opção, com lado (comprada/vendida) e quantidade. Uma coberta, por exemplo, tem duas pernas: a ação comprada e a call vendida."
          />
        </h2>
        <div
          className="table-wrap table-wrap--position"
          role="region"
          tabIndex={0}
          aria-label="Tabela horizontal das pernas registradas"
        >
          <table className="screen-table position-table lg:text-table lg:leading-table">
            <thead>
              <tr>
                <th className="lg:text-table-head">Ticker</th>
                <th className="lg:text-table-head">Tipo</th>
                <th className="lg:text-table-head">Lado</th>
                <th className="lg:text-table-head">Ações</th>
                <th className="lg:text-table-head">Vencimento</th>
                <th className="lg:text-table-head">Entrada</th>
                <th className="lg:text-table-head">Strike</th>
                <th className="lg:text-table-head">Volatilidade implícita na entrada</th>
                <th className="lg:text-table-head">Delta entrada</th>
              </tr>
            </thead>
            <tbody>
              {data.legs.map((leg) => (
                <tr key={leg.id}>
                  <td>{leg.ticker}</td>
                  <td>{legTypeLabel(leg.type, leg.option_side)}</td>
                  <td>{leg.side === 'long' ? 'comprada' : 'vendida'}</td>
                  <td>{fmt(leg.qty, 0)}</td>
                  <td>{dateBr(leg.maturity)}</td>
                  <td>{brl(leg.entry_price_brl)}</td>
                  <td>{brl(leg.entry_strike_brl)}</td>
                  <td>{percentPoints(leg.entry_iv_pct)}</td>
                  <td>{fmt(leg.entry_delta, 3)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="position-section" aria-labelledby="position-marks-title">
        <h2 className="block-title lg:text-block-title lg:leading-title" id="position-marks-title">Marcação da sessão</h2>
        <div
          className="table-wrap table-wrap--position"
          role="region"
          tabIndex={0}
          aria-label="Tabela horizontal da marcação da sessão"
        >
          <table className="screen-table position-table position-table--marks lg:text-table lg:leading-table">
            <thead>
              <tr>
                <th className="lg:text-table-head">Ticker</th>
                <th className="lg:text-table-head">Marca</th>
                <th className="lg:text-table-head">Strike vigente</th>
                <th className="lg:text-table-head">Volatilidade implícita</th>
                <th className="lg:text-table-head">Delta</th>
                <th className="lg:text-table-head">Gamma</th>
                <th className="lg:text-table-head">Theta</th>
                <th className="lg:text-table-head">Vega</th>
                <th className="lg:text-table-head">Spread</th>
                <th className="lg:text-table-head">Spread %</th>
                <th className="lg:text-table-head">Um lado</th>
                <th className="lg:text-table-head">Flags</th>
              </tr>
            </thead>
            <tbody>
              {data.marks.map((mark) => (
                <tr key={mark.leg_id}>
                  <td>{mark.ticker}</td>
                  <td>{brl(mark.mark_brl)}</td>
                  <td>{brl(mark.strike_brl)}</td>
                  <td>{percentPoints(mark.iv_pct)}</td>
                  <td>{fmt(mark.delta, 3)}</td>
                  <td>{fmt(mark.gamma, 3)}</td>
                  <td>{brl(mark.theta_brl)}</td>
                  <td>{brl(mark.vega_brl)}</td>
                  <td>{brl(mark.spread_brl)}</td>
                  <td>{percentPoints(mark.spread_pct)}</td>
                  <td>{mark.one_sided ? 'sim' : 'não'}</td>
                  <td className="position-table__flags">
                    <PositionFlags flags={mark.flags} emptyLabel="—" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="position-section" aria-labelledby="position-thesis-title">
        <h2 className="block-title lg:text-block-title lg:leading-title" id="position-thesis-title">Tese vs realidade</h2>
        <div className="position-thesis">
          <p className="position-thesis__line lg:text-body lg:leading-body">
            {data.thesis_line ?? 'Nenhuma linha de tese retornada para esta posição.'}
          </p>
          {data.prediction ? (
            <dl className="identity">
              <div>
                <dt className="lg:text-label">Métrica</dt>
                <dd className="num lg:text-detail">{data.prediction.metric}</dd>
              </div>
              <div>
                <dt className="lg:text-label">Condição</dt>
                <dd className="num lg:text-detail">
                  {data.prediction.operator} {fmt(data.prediction.value)}
                </dd>
              </div>
              <div>
                <dt className="lg:text-label">Horizonte</dt>
                <dd className="num lg:text-detail">{fmt(data.prediction.horizon_sessions, 0)} sessões</dd>
              </div>
              <div>
                <dt className="lg:text-label">Avaliar em</dt>
                <dd className="num lg:text-detail">{dateBr(data.prediction.evaluate_on)}</dd>
              </div>
              <div>
                <dt className="lg:text-label">Status</dt>
                <dd className="lg:text-detail">{predictionStatusLabel(data.prediction.status)}</dd>
              </div>
              <div>
                <dt className="lg:text-label">Observado</dt>
                <dd className="num lg:text-detail">{fmt(data.prediction.observed)}</dd>
              </div>
            </dl>
          ) : (
            <p className="note lg:text-label">Sem previsão estruturada; esta posição não entra na calibração.</p>
          )}
        </div>
      </section>

      <section className="position-section" aria-labelledby="position-payoff-title">
        <h2 className="block-title lg:text-block-title lg:leading-title" id="position-payoff-title">Payoff no vencimento</h2>
        <p className="chain-note lg:text-label lg:leading-body">Preços de entrada do journal; o backend calcula todos os pontos.</p>
        {!payoffTruncated && (
          <PayoffChart
            points={data.payoff}
            breakevens={data.breakeven_brl}
            ariaLabel={`Payoff no vencimento da posição de ${data.underlying}`}
          />
        )}
      </section>

      {isOpen && (
        <section className="position-write" aria-labelledby="position-close-title">
          <h2 className="block-title lg:text-block-title lg:leading-title" id="position-close-title">Fechamento</h2>
          {!auth.hasToken && !confirmOpen && (
            <TokenGate compact notice={tokenNotice} onUnlock={() => setTokenNotice(null)} />
          )}
          <div className="position-detail__actions">
            <button
              type="button"
              className="builder__button builder__button--primary lg:text-control"
              onClick={openConfirmation}
              disabled={!auth.hasToken}
            >
              Fechar posição
            </button>
            {auth.hasToken && (
              <button type="button" className="inline-link lg:text-label" onClick={changeToken}>
                Trocar token
              </button>
            )}
          </div>
        </section>
      )}

      <MetricModal target={modal} onClose={() => setModal(null)} />

      {confirmOpen && (
        <Dialog
          labelledBy="close-position-title"
          describedBy="close-position-description"
          onClose={closeConfirm}
          busy={close.isPending}
          className="modal--confirm"
        >
          {!auth.hasToken && (
            <TokenGate
              compact
              autoFocus
              notice={tokenNotice}
              onUnlock={() => setTokenNotice(null)}
            />
          )}
          <header className="modal__head">
            <div>
              <div className="modal__eyebrow lg:text-label">evento append-only</div>
              <h2 className="modal__title lg:text-modal-title lg:leading-title" id="close-position-title">
                Fechar posição
              </h2>
            </div>
            <button
              type="button"
              className="modal__close lg:text-modal-title"
              onClick={closeConfirm}
              aria-label="Cancelar fechamento"
              disabled={close.isPending}
            >
              ×
            </button>
          </header>
          <p className="position-confirm__text lg:text-body lg:leading-body" id="close-position-description">
            O fechamento usa a marcação EOD e acrescenta um evento que não pode ser desfeito.
          </p>
          <label className="builder__field">
            <span className="builder__field-label lg:text-label">Data da sessão (opcional)</span>
            <input
              className="lg:text-control"
              type="date"
              value={closeDate}
              onChange={(event) => setCloseDate(event.target.value)}
              data-autofocus
              disabled={close.isPending}
            />
          </label>
          <p className="note lg:text-label">Em branco, o backend usa a última sessão disponível.</p>
          {close.isError && <ErrorState error={close.error} title="Fechamento não registrado" />}
          <div className="position-form__actions">
            <button
              type="button"
              className="builder__button lg:text-control"
              onClick={closeConfirm}
              disabled={close.isPending}
            >
              Cancelar
            </button>
            <button
              type="button"
              className="builder__button builder__button--primary lg:text-control"
              onClick={() => close.mutate(closeDate ? { date: closeDate } : {})}
              disabled={close.isPending}
            >
              {close.isPending ? 'fechando…' : 'Confirmar fechamento'}
            </button>
          </div>
        </Dialog>
      )}
    </>
  )
}
