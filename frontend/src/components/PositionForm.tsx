/** Abertura de posição: o backend resolve preços, strike, IV e delta da sessão. */
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useMutation } from '@tanstack/react-query'
import {
  apiWrite,
  ApiError,
  type LegKind,
  type LegSide,
  type PositionCreateRequest,
  type PositionDetail,
  type Envelope,
  type PositionPredictionInput,
} from '../lib/api'
import { ErrorState } from './State'
import { HelpTip } from './HelpTip'

const MAX_LEGS = 60
const DEFAULT_QUANTITY = '100'
const DRAFT_KEY = 'b3o:position-draft'

interface FormLeg {
  id: number
  kind: LegKind
  ticker: string
  side: LegSide
  quantity: string
}

interface DraftState {
  clientPositionId: string
  underlying: string
  date: string
  notes: string
  capitalLimit: string
  legs: FormLeg[]
  predictionEnabled: boolean
  metric: string
  operator: PositionPredictionInput['operator']
  predictionValue: string
  horizon: string
  evaluateOn: string
}

function defaultLeg(): FormLeg {
  return { id: 0, kind: 'option', ticker: '', side: 'long', quantity: DEFAULT_QUANTITY }
}

function loadDraft(): DraftState | null {
  try {
    const raw = sessionStorage.getItem(DRAFT_KEY)
    if (!raw) return null
    const value = JSON.parse(raw) as Record<string, unknown>
    const legs = Array.isArray(value.legs)
      ? value.legs
          .filter(
            (item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object',
          )
          .slice(0, MAX_LEGS)
          .map((item, index): FormLeg => ({
            id: index + 1,
            kind: item.kind === 'stock' ? 'stock' : 'option',
            ticker: typeof item.ticker === 'string' ? item.ticker : '',
            side: item.side === 'short' ? 'short' : 'long',
            quantity: typeof item.quantity === 'string' ? item.quantity : DEFAULT_QUANTITY,
          }))
      : []
    const operator = ['<', '<=', '>', '>='].includes(String(value.operator))
      ? (value.operator as PositionPredictionInput['operator'])
      : '>='
    return {
      clientPositionId:
        typeof value.clientPositionId === 'string' && value.clientPositionId.length <= 36
          ? value.clientPositionId
          : globalThis.crypto.randomUUID(),
      underlying: typeof value.underlying === 'string' ? value.underlying : '',
      date: typeof value.date === 'string' ? value.date : '',
      notes: typeof value.notes === 'string' ? value.notes : '',
      capitalLimit: typeof value.capitalLimit === 'string' ? value.capitalLimit : '',
      legs: legs.length > 0 ? legs : [{ ...defaultLeg(), id: 1 }],
      predictionEnabled: value.predictionEnabled === true,
      metric: typeof value.metric === 'string' ? value.metric : 'vrp',
      operator,
      predictionValue: typeof value.predictionValue === 'string' ? value.predictionValue : '',
      horizon: typeof value.horizon === 'string' ? value.horizon : '5',
      evaluateOn: typeof value.evaluateOn === 'string' ? value.evaluateOn : '',
    }
  } catch {
    return null
  }
}

function optionalDecimal(raw: string): number | null {
  const text = raw.trim().replace(',', '.')
  if (!text) return null
  const value = Number(text)
  return Number.isFinite(value) ? value : null
}

export function PositionForm({
  onCreated,
  onCancel,
  onUnauthorized,
  onBusyChange,
}: {
  onCreated: (result: Envelope<PositionDetail>) => void | Promise<void>
  onCancel: () => void
  onUnauthorized: () => void
  onBusyChange: (busy: boolean) => void
}) {
  const [initialDraft] = useState(loadDraft)
  const nextId = useRef(initialDraft ? Math.max(...initialDraft.legs.map((leg) => leg.id)) + 1 : 1)
  const [clientPositionId] = useState(
    () => initialDraft?.clientPositionId ?? globalThis.crypto.randomUUID(),
  )
  const [underlying, setUnderlying] = useState(initialDraft?.underlying ?? '')
  const [date, setDate] = useState(initialDraft?.date ?? '')
  const [notes, setNotes] = useState(initialDraft?.notes ?? '')
  const [capitalLimit, setCapitalLimit] = useState(initialDraft?.capitalLimit ?? '')
  const [legs, setLegs] = useState<FormLeg[]>(initialDraft?.legs ?? [{ ...defaultLeg(), id: 0 }])
  const [predictionEnabled, setPredictionEnabled] = useState(
    initialDraft?.predictionEnabled ?? false,
  )
  const [metric, setMetric] = useState(initialDraft?.metric ?? 'vrp')
  const [operator, setOperator] = useState<PositionPredictionInput['operator']>(
    initialDraft?.operator ?? '>=',
  )
  const [predictionValue, setPredictionValue] = useState(initialDraft?.predictionValue ?? '')
  const [horizon, setHorizon] = useState(initialDraft?.horizon ?? '5')
  const [evaluateOn, setEvaluateOn] = useState(initialDraft?.evaluateOn ?? '')
  const [validationError, setValidationError] = useState<string | null>(null)

  useEffect(() => {
    try {
      const draft: DraftState = {
        clientPositionId,
        underlying,
        date,
        notes,
        capitalLimit,
        legs,
        predictionEnabled,
        metric,
        operator,
        predictionValue,
        horizon,
        evaluateOn,
      }
      sessionStorage.setItem(DRAFT_KEY, JSON.stringify(draft))
    } catch {
      // sessionStorage indisponível não impede operar; o UUID continua estável no mount.
    }
  }, [
    clientPositionId,
    underlying,
    date,
    notes,
    capitalLimit,
    legs,
    predictionEnabled,
    metric,
    operator,
    predictionValue,
    horizon,
    evaluateOn,
  ])

  const create = useMutation({
    mutationFn: (body: PositionCreateRequest) => apiWrite<PositionDetail>('/api/positions', body),
    onMutate: () => onBusyChange(true),
    onSuccess: (result) => {
      try {
        sessionStorage.removeItem(DRAFT_KEY)
      } catch {
        // A resposta já confirma a gravação; falha ao limpar o rascunho não desfaz o evento.
      }
      return onCreated(result)
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 401) onUnauthorized()
    },
    onSettled: () => onBusyChange(false),
  })

  function discardDraft() {
    try {
      sessionStorage.removeItem(DRAFT_KEY)
    } catch {
      // O descarte explícito ainda fecha o formulário se o armazenamento falhar.
    }
    onCancel()
  }

  function addLeg(kind: LegKind) {
    if (legs.length >= MAX_LEGS) return
    const id = nextId.current
    nextId.current += 1
    setLegs((prev) => [
      ...prev,
      {
        id,
        kind,
        ticker: kind === 'stock' ? underlying.trim().toUpperCase() : '',
        side: 'long',
        quantity: DEFAULT_QUANTITY,
      },
    ])
  }

  function updateLeg(id: number, patch: Partial<FormLeg>) {
    setLegs((prev) => prev.map((leg) => (leg.id === id ? { ...leg, ...patch } : leg)))
  }

  function changeKind(id: number, kind: LegKind) {
    setLegs((prev) =>
      prev.map((leg) =>
        leg.id === id
          ? {
              ...leg,
              kind,
              ticker:
                kind === 'stock' && !leg.ticker.trim()
                  ? underlying.trim().toUpperCase()
                  : leg.ticker,
            }
          : leg,
      ),
    )
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setValidationError(null)

    const paper = underlying.trim().toUpperCase()
    if (!paper) {
      setValidationError('informe o ativo-objeto')
      return
    }
    if (legs.length === 0) {
      setValidationError('adicione ao menos uma perna')
      return
    }

    const preparedLegs = legs.map((leg) => {
      const quantity = Number(leg.quantity)
      return {
        kind: leg.kind,
        ticker: leg.ticker.trim().toUpperCase(),
        side: leg.side,
        quantity,
      }
    })
    if (preparedLegs.some((leg) => !leg.ticker)) {
      setValidationError('informe o ticker de cada perna')
      return
    }
    if (
      preparedLegs.some(
        (leg) =>
          !Number.isSafeInteger(leg.quantity) ||
          leg.quantity <= 0 ||
          leg.quantity > Number.MAX_SAFE_INTEGER,
      )
    ) {
      setValidationError('a quantidade de cada perna deve ser um inteiro positivo seguro')
      return
    }

    const limit = optionalDecimal(capitalLimit)
    if (capitalLimit.trim() && (limit == null || limit < 0)) {
      setValidationError('o limite de capital deve ser um valor não negativo')
      return
    }

    let prediction: PositionPredictionInput | null = null
    if (predictionEnabled) {
      const value = optionalDecimal(predictionValue)
      const sessions = Number(horizon)
      if (!metric.trim()) {
        setValidationError('informe a métrica da previsão')
        return
      }
      if (value == null) {
        setValidationError('informe o valor da previsão')
        return
      }
      if (!Number.isSafeInteger(sessions) || sessions <= 0 || sessions > 252) {
        setValidationError('o horizonte deve ser um inteiro entre 1 e 252 sessões')
        return
      }
      if (!evaluateOn) {
        setValidationError('informe a data de avaliação da previsão')
        return
      }
      prediction = {
        metric: metric.trim(),
        operator,
        value,
        horizon_sessions: sessions,
        evaluate_on: evaluateOn,
      }
    }

    create.mutate({
      client_position_id: clientPositionId,
      underlying: paper,
      date: date || null,
      notes: notes.trim() || null,
      capital_limit_brl: limit,
      legs: preparedLegs,
      ...(prediction ? { prediction } : {}),
    })
  }

  return (
    <>
      <header className="modal__head">
        <div>
          <div className="modal__eyebrow lg:text-label">journal append-only</div>
          <h2
            className="modal__title lg:text-modal-title lg:leading-title"
            id="position-form-title"
          >
            Nova posição
          </h2>
        </div>
        <button
          type="button"
          className="modal__close lg:text-modal-title"
          onClick={discardDraft}
          aria-label="Fechar formulário"
          disabled={create.isPending}
        >
          ×
        </button>
      </header>

      <form className="position-form" onSubmit={submit} aria-busy={create.isPending} noValidate>
        <fieldset
          className="position-form__fieldset position-form__fieldset--all"
          disabled={create.isPending}
        >
          <legend className="visually-hidden">Dados da posição</legend>
          <div className="position-form__grid">
            <label className="builder__field">
              <span className="builder__field-label lg:text-label">Ativo-objeto</span>
              <input
                className="lg:text-control"
                value={underlying}
                onChange={(event) => setUnderlying(event.target.value.toUpperCase())}
                placeholder="PETR4"
                maxLength={32}
                autoComplete="off"
                spellCheck={false}
                data-autofocus
                required
              />
            </label>
            <label className="builder__field">
              <span className="builder__field-label lg:text-label">Data da sessão (opcional)</span>
              <input
                className="lg:text-control"
                type="date"
                value={date}
                onChange={(event) => setDate(event.target.value)}
              />
            </label>
            <label className="builder__field">
              <span className="builder__field-label lg:text-label">
                Limite de capital (R$, opcional)
              </span>
              <input
                className="lg:text-control"
                type="text"
                inputMode="decimal"
                value={capitalLimit}
                onChange={(event) => setCapitalLimit(event.target.value)}
                placeholder="5000,00"
                maxLength={32}
                autoComplete="off"
              />
            </label>
            <label className="builder__field position-form__full">
              <span className="builder__field-label lg:text-label">Notas</span>
              <textarea
                className="position-form__textarea lg:text-control lg:leading-body"
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
                placeholder="contexto da decisão"
                maxLength={2000}
                rows={3}
              />
            </label>
          </div>

          <fieldset className="position-form__fieldset" disabled={create.isPending}>
            <legend className="position-form__legend lg:text-block-title lg:leading-title">
              Pernas
              <HelpTip
                label="O que é uma perna?"
                text="Perna é cada operação que compõe a posição: uma ação ou uma opção, com lado (comprada/vendida) e quantidade. Uma coberta, por exemplo, tem duas pernas: a ação comprada e a call vendida."
              />
            </legend>
            <p className="chain-note lg:text-label lg:leading-body">
              A quantidade é em ações. 100 ações = 1 contrato padrão; nada é multiplicado por 100.
            </p>
            <div className="position-form__legs">
              {legs.map((leg, index) => (
                <div className="position-form__leg" key={leg.id}>
                  <div className="position-form__leg-head">
                    <span className="quiet lg:text-label">Perna {index + 1}</span>
                    <button
                      type="button"
                      className="builder__remove lg:text-label"
                      onClick={() => setLegs((prev) => prev.filter((item) => item.id !== leg.id))}
                      disabled={legs.length === 1}
                      aria-label={`Remover perna ${index + 1}`}
                    >
                      remover
                    </button>
                  </div>
                  <div className="position-form__leg-grid">
                    <label className="builder__field">
                      <span className="builder__field-label lg:text-label">Tipo</span>
                      <select
                        className="lg:text-control"
                        value={leg.kind}
                        onChange={(event) => changeKind(leg.id, event.target.value as LegKind)}
                        aria-label={`Tipo da perna ${index + 1}`}
                      >
                        <option value="option">opção</option>
                        <option value="stock">ação</option>
                      </select>
                    </label>
                    <label className="builder__field">
                      <span className="builder__field-label lg:text-label">Ticker</span>
                      <input
                        className="lg:text-control"
                        value={leg.ticker}
                        onChange={(event) =>
                          updateLeg(leg.id, { ticker: event.target.value.toUpperCase() })
                        }
                        placeholder={
                          leg.kind === 'stock'
                            ? underlying.trim().toUpperCase() || 'PETR4'
                            : 'PETRL44'
                        }
                        maxLength={32}
                        autoComplete="off"
                        spellCheck={false}
                        aria-label={`Ticker da perna ${index + 1}`}
                        required
                      />
                    </label>
                    <label className="builder__field">
                      <span className="builder__field-label lg:text-label">Lado</span>
                      <select
                        className="lg:text-control"
                        value={leg.side}
                        onChange={(event) =>
                          updateLeg(leg.id, { side: event.target.value as LegSide })
                        }
                        aria-label={`Lado da perna ${index + 1}`}
                      >
                        <option value="long">comprada</option>
                        <option value="short">vendida</option>
                      </select>
                    </label>
                    <label className="builder__field">
                      <span className="builder__field-label lg:text-label">Ações</span>
                      <input
                        className="lg:text-control"
                        type="number"
                        inputMode="numeric"
                        min={1}
                        max={Number.MAX_SAFE_INTEGER}
                        step={1}
                        value={leg.quantity}
                        onChange={(event) => updateLeg(leg.id, { quantity: event.target.value })}
                        aria-label={`Quantidade de ações da perna ${index + 1}`}
                        required
                      />
                    </label>
                  </div>
                </div>
              ))}
            </div>
            <div className="position-form__add">
              <button
                type="button"
                className="builder__button lg:text-control"
                onClick={() => addLeg('option')}
                disabled={legs.length >= MAX_LEGS}
              >
                Adicionar opção
              </button>
              <button
                type="button"
                className="builder__button lg:text-control"
                onClick={() => addLeg('stock')}
                disabled={legs.length >= MAX_LEGS}
              >
                Adicionar ação
              </button>
              <span className="block-count lg:text-label">
                {legs.length}/{MAX_LEGS}
              </span>
            </div>
          </fieldset>

          <fieldset className="position-form__fieldset" disabled={create.isPending}>
            <legend className="position-form__legend lg:text-block-title lg:leading-title">
              Tese
            </legend>
            <label className="position-form__toggle lg:text-label">
              <input
                className="lg:text-control"
                type="checkbox"
                checked={predictionEnabled}
                onChange={(event) => setPredictionEnabled(event.target.checked)}
              />
              <span>Registrar previsão estruturada</span>
            </label>
            {predictionEnabled && (
              <div className="position-form__grid position-form__prediction">
                <label className="builder__field">
                  <span className="builder__field-label lg:text-label">Métrica</span>
                  <select
                    className="lg:text-control"
                    value={metric}
                    onChange={(event) => setMetric(event.target.value)}
                    aria-label="Métrica da previsão"
                  >
                    <option value="spot">Preço (spot / fechamento)</option>
                    <option value="iv_atm_30d">IV ATM 30d</option>
                    <option value="vrp">VRP</option>
                    <option value="rr25">RR25</option>
                  </select>
                </label>
                <label className="builder__field">
                  <span className="builder__field-label lg:text-label">Operador</span>
                  <select
                    className="lg:text-control"
                    value={operator}
                    onChange={(event) =>
                      setOperator(event.target.value as PositionPredictionInput['operator'])
                    }
                  >
                    <option value="<">&lt;</option>
                    <option value="<=">&lt;=</option>
                    <option value=">">&gt;</option>
                    <option value=">=">&gt;=</option>
                  </select>
                </label>
                <label className="builder__field">
                  <span className="builder__field-label lg:text-label">Valor</span>
                  <input
                    className="lg:text-control"
                    type="text"
                    inputMode="decimal"
                    maxLength={64}
                    value={predictionValue}
                    onChange={(event) => setPredictionValue(event.target.value)}
                    autoComplete="off"
                  />
                </label>
                <label className="builder__field">
                  <span className="builder__field-label lg:text-label">Horizonte (sessões)</span>
                  <input
                    className="lg:text-control"
                    type="number"
                    inputMode="numeric"
                    min={1}
                    max={252}
                    step={1}
                    value={horizon}
                    onChange={(event) => setHorizon(event.target.value)}
                  />
                </label>
                <label className="builder__field position-form__full">
                  <span className="builder__field-label lg:text-label">Avaliar em</span>
                  <input
                    className="lg:text-control"
                    type="date"
                    value={evaluateOn}
                    onChange={(event) => setEvaluateOn(event.target.value)}
                  />
                </label>
              </div>
            )}
          </fieldset>

          <p className="note lg:text-label">
            O backend resolve preço de entrada, strike, IV e delta da sessão. Nenhum número
            financeiro é calculado aqui.
          </p>

          {validationError && (
            <p className="notice lg:text-label" role="alert">
              {validationError}
            </p>
          )}
          {create.isError && <ErrorState error={create.error} title="Posição não registrada" />}

          <div className="position-form__actions">
            <button
              type="button"
              className="builder__button lg:text-control"
              onClick={discardDraft}
              disabled={create.isPending}
            >
              Cancelar
            </button>
            <button
              type="submit"
              className="builder__button builder__button--primary lg:text-control"
              disabled={create.isPending}
            >
              {create.isPending ? 'registrando…' : 'Registrar posição'}
            </button>
          </div>
        </fieldset>
      </form>
    </>
  )
}
