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
import { Button } from './ui/button'
import { Checkbox } from './ui/checkbox'
import { DialogClose } from './ui/dialog'
import { Input } from './ui/input'
import { NativeSelect } from './ui/native-select'
import { Textarea } from './ui/textarea'
import {
  blockCountBase,
  chainNote,
  field,
  fieldLabel,
  note,
  notice,
  quiet,
  visuallyHidden,
} from '../lib/classes'

// F8: o contorno de foco legado (`.builder__field input/select:focus-visible`, offset -2 px)
// perde para o `outline-none` do primitive no estilo (fica `none`), mas cor e offset valem —
// por isso aqui NÃO entra `outline-solid` (focusInset desenharia um contorno que não existe hoje).
const fieldControl =
  'min-h-[44px] font-mono text-[12px] tabular-nums text-foreground focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-primary'
// `.position-form__textarea`: padding, borda, fundo e min-height legados perdem para o primitive.
const textareaControl =
  'resize-y text-[13px] leading-[1.5] text-foreground focus-visible:outline-2 focus-visible:outline-offset-[2px] focus-visible:outline-primary'
// `.position-form__toggle input`: tamanho e accent já vêm do Checkbox (size-5, accent-primary).
const focusToggle =
  'focus-visible:outline-2 focus-visible:outline-offset-[2px] focus-visible:outline-primary'

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

  function clearDraft() {
    try {
      sessionStorage.removeItem(DRAFT_KEY)
    } catch {
      // O descarte explícito ainda fecha o formulário se o armazenamento falhar.
    }
  }

  function discardDraft() {
    clearDraft()
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
      <DialogClose asChild>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="absolute top-4 right-4"
          onClick={clearDraft}
          aria-label="Fechar formulário"
          disabled={create.isPending}
        >
          <span aria-hidden="true">×</span>
        </Button>
      </DialogClose>

      <form className="grid gap-[20px]" onSubmit={submit} aria-busy={create.isPending} noValidate>
        <fieldset
          className="contents m-0 min-w-0 border-none p-0"
          disabled={create.isPending}
        >
          <legend className={visuallyHidden}>Dados da posição</legend>
          <div className="grid grid-cols-[minmax(0,1fr)] gap-[12px] sm:grid-cols-2">
            <label className={field}>
              <span className={`${fieldLabel} lg:text-label`}>Ativo-objeto</span>
              <Input
                className={`${fieldControl} lg:text-control`}
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
            <label className={field}>
              <span className={`${fieldLabel} lg:text-label`}>Data da sessão (opcional)</span>
              <Input
                className={`${fieldControl} lg:text-control`}
                type="date"
                value={date}
                onChange={(event) => setDate(event.target.value)}
              />
            </label>
            <label className={field}>
              <span className={`${fieldLabel} lg:text-label`}>
                Limite de capital (R$, opcional)
              </span>
              <Input
                className={`${fieldControl} lg:text-control`}
                type="text"
                inputMode="decimal"
                value={capitalLimit}
                onChange={(event) => setCapitalLimit(event.target.value)}
                placeholder="5000,00"
                maxLength={32}
                autoComplete="off"
              />
            </label>
            <label className={`${field} col-span-full`}>
              <span className={`${fieldLabel} lg:text-label`}>Notas</span>
              <Textarea
                className={`${textareaControl} lg:text-control lg:leading-body`}
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
                placeholder="contexto da decisão"
                maxLength={2000}
                rows={3}
              />
            </label>
          </div>

          <fieldset className="m-0 min-w-0 border-none p-0" disabled={create.isPending}>
            <legend className="mb-[8px] flex min-h-[44px] items-center p-0 text-[13px] font-medium lg:text-block-title lg:leading-title">
              Pernas
              <HelpTip
                label="O que é uma perna?"
                text="Perna é cada operação que compõe a posição: uma ação ou uma opção, com lado (comprada/vendida) e quantidade. Uma coberta, por exemplo, tem duas pernas: a ação comprada e a call vendida."
              />
            </legend>
            <p className={`${chainNote} lg:text-label lg:leading-body`}>
              A quantidade é em ações. 100 ações = 1 contrato padrão; nada é multiplicado por 100.
            </p>
            <div className="grid gap-[8px]">
              {legs.map((leg, index) => (
                <div
                  className="rounded-[6px] border border-border bg-card p-[12px]"
                  key={leg.id}
                >
                  <div className="mb-[8px] flex items-center justify-between gap-[8px]">
                    <span className={`${quiet} lg:text-label`}>Perna {index + 1}</span>
                    <Button
                      type="button"
                      variant="link"
                      size="sm"
                      className="h-[44px] px-0 lg:text-label"
                      onClick={() => setLegs((prev) => prev.filter((item) => item.id !== leg.id))}
                      disabled={legs.length === 1}
                      aria-label={`Remover perna ${index + 1}`}
                    >
                      remover
                    </Button>
                  </div>
                  <div className="grid grid-cols-[minmax(0,1fr)] gap-[8px] min-[640px]:grid-cols-[repeat(2,minmax(0,1fr))] min-[720px]:grid-cols-[120px_minmax(140px,1fr)_132px_104px]">
                    <label className={field}>
                      <span className={`${fieldLabel} lg:text-label`}>Tipo</span>
                      <NativeSelect
                        className={`${fieldControl} lg:text-control`}
                        value={leg.kind}
                        onChange={(event) => changeKind(leg.id, event.target.value as LegKind)}
                        aria-label={`Tipo da perna ${index + 1}`}
                      >
                        <option value="option">opção</option>
                        <option value="stock">ação</option>
                      </NativeSelect>
                    </label>
                    <label className={field}>
                      <span className={`${fieldLabel} lg:text-label`}>Ticker</span>
                      <Input
                        className={`${fieldControl} lg:text-control`}
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
                    <label className={field}>
                      <span className={`${fieldLabel} lg:text-label`}>Lado</span>
                      <NativeSelect
                        className={`${fieldControl} lg:text-control`}
                        value={leg.side}
                        onChange={(event) =>
                          updateLeg(leg.id, { side: event.target.value as LegSide })
                        }
                        aria-label={`Lado da perna ${index + 1}`}
                      >
                        <option value="long">comprada</option>
                        <option value="short">vendida</option>
                      </NativeSelect>
                    </label>
                    <label className={field}>
                      <span className={`${fieldLabel} lg:text-label`}>Ações</span>
                      <Input
                        className={`${fieldControl} lg:text-control`}
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
            <div className="mt-[8px] flex flex-wrap items-center gap-[8px]">
              <Button
                type="button"
                variant="outline"
                onClick={() => addLeg('option')}
                disabled={legs.length >= MAX_LEGS}
              >
                Adicionar opção
              </Button>
              <Button
                type="button"
                variant="outline"
                onClick={() => addLeg('stock')}
                disabled={legs.length >= MAX_LEGS}
              >
                Adicionar ação
              </Button>
              <span className={`${blockCountBase} ml-auto lg:text-label`}>
                {legs.length}/{MAX_LEGS}
              </span>
            </div>
          </fieldset>

          <fieldset className="m-0 min-w-0 border-none p-0" disabled={create.isPending}>
            <legend className="mb-[8px] flex min-h-[44px] items-center p-0 text-[13px] font-medium lg:text-block-title lg:leading-title">
              Tese
            </legend>
            <label className="flex min-h-[44px] cursor-pointer items-center gap-[8px] text-[12.5px] text-[color:var(--text-2)] lg:text-label">
              <Checkbox
                className={focusToggle}
                checked={predictionEnabled}
                onCheckedChange={(checked) => setPredictionEnabled(checked === true)}
                disabled={create.isPending}
              />
              <span>Registrar previsão estruturada</span>
            </label>
            {predictionEnabled && (
              <div className="mt-[12px] grid grid-cols-[minmax(0,1fr)] gap-[12px] sm:grid-cols-2">
                <label className={field}>
                  <span className={`${fieldLabel} lg:text-label`}>Métrica</span>
                  <NativeSelect
                    className={`${fieldControl} lg:text-control`}
                    value={metric}
                    onChange={(event) => setMetric(event.target.value)}
                    aria-label="Métrica da previsão"
                  >
                    <option value="spot">Preço (spot / fechamento)</option>
                    <option value="iv_atm_30d">IV ATM 30d</option>
                    <option value="vrp">VRP</option>
                    <option value="rr25">RR25</option>
                  </NativeSelect>
                </label>
                <label className={field}>
                  <span className={`${fieldLabel} lg:text-label`}>Operador</span>
                  <NativeSelect
                    className={`${fieldControl} lg:text-control`}
                    value={operator}
                    onChange={(event) =>
                      setOperator(event.target.value as PositionPredictionInput['operator'])
                    }
                  >
                    <option value="<">&lt;</option>
                    <option value="<=">&lt;=</option>
                    <option value=">">&gt;</option>
                    <option value=">=">&gt;=</option>
                  </NativeSelect>
                </label>
                <label className={field}>
                  <span className={`${fieldLabel} lg:text-label`}>Valor</span>
                  <Input
                    className={`${fieldControl} lg:text-control`}
                    type="text"
                    inputMode="decimal"
                    maxLength={64}
                    value={predictionValue}
                    onChange={(event) => setPredictionValue(event.target.value)}
                    autoComplete="off"
                  />
                </label>
                <label className={field}>
                  <span className={`${fieldLabel} lg:text-label`}>Horizonte (sessões)</span>
                  <Input
                    className={`${fieldControl} lg:text-control`}
                    type="number"
                    inputMode="numeric"
                    min={1}
                    max={252}
                    step={1}
                    value={horizon}
                    onChange={(event) => setHorizon(event.target.value)}
                  />
                </label>
                <label className={`${field} col-span-full`}>
                  <span className={`${fieldLabel} lg:text-label`}>Avaliar em</span>
                  <Input
                    className={`${fieldControl} lg:text-control`}
                    type="date"
                    value={evaluateOn}
                    onChange={(event) => setEvaluateOn(event.target.value)}
                  />
                </label>
              </div>
            )}
          </fieldset>

          <p className={`${note} lg:text-label`}>
            O backend resolve preço de entrada, strike, IV e delta da sessão. Nenhum número
            financeiro é calculado aqui.
          </p>

          {validationError && (
            <p className={`${notice} lg:text-label`} role="alert">
              {validationError}
            </p>
          )}
          {create.isError && <ErrorState error={create.error} title="Posição não registrada" />}

          <div className="mt-[4px] flex flex-wrap justify-end gap-[8px] max-sm:grid max-sm:grid-cols-[minmax(0,1fr)]">
            <Button
              type="button"
              variant="outline"
              onClick={discardDraft}
              disabled={create.isPending}
            >
              Cancelar
            </Button>
            <Button type="submit" disabled={create.isPending}>
              {create.isPending ? 'registrando…' : 'Registrar posição'}
            </Button>
          </div>
        </fieldset>
      </form>
    </>
  )
}
