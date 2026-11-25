/** Registro de decisão sem posição: notas livres nunca viram previsão. */
import { useEffect, useState, type FormEvent } from 'react'
import { useMutation } from '@tanstack/react-query'
import {
  ApiError,
  apiWrite,
  type DecisionType,
  type Envelope,
  type JournalCreateRequest,
  type JournalDetailData,
  type JournalPredictionInput,
  type PredictionMetric,
} from '../lib/api'
import { ErrorState } from './State'
import { Button } from './ui/button'
import { Checkbox } from './ui/checkbox'
import { DialogClose } from './ui/dialog'
import { Input } from './ui/input'
import { NativeSelect } from './ui/native-select'
import { Textarea } from './ui/textarea'
import {
  chainNote,
  field,
  fieldLabel,
  note,
  notice,
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

const DRAFT_KEY = 'b3o:journal-draft'
const METRICS: Array<{ value: PredictionMetric; label: string; unit: string }> = [
  { value: 'spot', label: 'Preço (spot / fechamento)', unit: 'R$' },
  { value: 'iv_atm_30d', label: 'IV ATM 30d', unit: '% a.a.' },
  { value: 'vrp', label: 'VRP', unit: 'pontos de vol' },
  { value: 'rr25', label: 'RR25', unit: 'pontos de vol' },
]
const TYPES: Array<{ value: DecisionType; label: string }> = [
  { value: 'no_entry', label: 'Não entrei' },
  { value: 'entry', label: 'Entrada' },
  { value: 'exit', label: 'Saída' },
  { value: 'hold', label: 'Manter' },
  { value: 'watch', label: 'Observação' },
  { value: 'other', label: 'Outra' },
]

interface DraftState {
  clientDecisionId: string
  underlying: string
  date: string
  decisionType: DecisionType
  context: string
  notes: string
  predictionEnabled: boolean
  metric: PredictionMetric
  operator: JournalPredictionInput['operator']
  predictionValue: string
  horizon: string
  evaluateOn: string
}

function newId(): string {
  return globalThis.crypto.randomUUID()
}

function loadDraft(): DraftState | null {
  try {
    const raw = sessionStorage.getItem(DRAFT_KEY)
    if (!raw) return null
    const value = JSON.parse(raw) as Record<string, unknown>
    const decisionType = TYPES.some((item) => item.value === value.decisionType)
      ? (value.decisionType as DecisionType)
      : 'no_entry'
    const metric = METRICS.some((item) => item.value === value.metric)
      ? (value.metric as PredictionMetric)
      : 'vrp'
    const operator = ['<', '<=', '>', '>='].includes(String(value.operator))
      ? (value.operator as JournalPredictionInput['operator'])
      : '>='
    return {
      clientDecisionId:
        typeof value.clientDecisionId === 'string' && value.clientDecisionId.length <= 36
          ? value.clientDecisionId
          : newId(),
      underlying: typeof value.underlying === 'string' ? value.underlying : '',
      date: typeof value.date === 'string' ? value.date : '',
      decisionType,
      context: typeof value.context === 'string' ? value.context : '',
      notes: typeof value.notes === 'string' ? value.notes : '',
      predictionEnabled: value.predictionEnabled === true,
      metric,
      operator,
      predictionValue: typeof value.predictionValue === 'string' ? value.predictionValue : '',
      horizon: typeof value.horizon === 'string' ? value.horizon : '5',
      evaluateOn: typeof value.evaluateOn === 'string' ? value.evaluateOn : '',
    }
  } catch {
    return null
  }
}

function decimal(raw: string): number | null {
  const text = raw.trim().replace(',', '.')
  if (!text) return null
  const value = Number(text)
  return Number.isFinite(value) ? value : null
}

export function DecisionForm({
  defaultSession,
  onCreated,
  onCancel,
  onUnauthorized,
  onConflict,
  onBusyChange,
}: {
  defaultSession: string | null
  onCreated: (result: Envelope<JournalDetailData>) => void | Promise<void>
  onCancel: () => void
  onUnauthorized: () => void
  onConflict: (clientDecisionId: string) => void | Promise<void>
  onBusyChange: (busy: boolean) => void
}) {
  const [initialDraft] = useState(loadDraft)
  const [clientDecisionId] = useState(() => initialDraft?.clientDecisionId ?? newId())
  const [underlying, setUnderlying] = useState(initialDraft?.underlying ?? '')
  const [date, setDate] = useState(initialDraft?.date ?? defaultSession ?? '')
  const [decisionType, setDecisionType] = useState<DecisionType>(
    initialDraft?.decisionType ?? 'no_entry',
  )
  const [context, setContext] = useState(initialDraft?.context ?? '')
  const [notes, setNotes] = useState(initialDraft?.notes ?? '')
  const [predictionEnabled, setPredictionEnabled] = useState(
    initialDraft?.predictionEnabled ?? false,
  )
  const [metric, setMetric] = useState<PredictionMetric>(initialDraft?.metric ?? 'vrp')
  const [operator, setOperator] = useState<JournalPredictionInput['operator']>(
    initialDraft?.operator ?? '>=',
  )
  const [predictionValue, setPredictionValue] = useState(initialDraft?.predictionValue ?? '')
  const [horizon, setHorizon] = useState(initialDraft?.horizon ?? '5')
  const [evaluateOn, setEvaluateOn] = useState(initialDraft?.evaluateOn ?? '')
  const [validationError, setValidationError] = useState<string | null>(null)

  useEffect(() => {
    try {
      const draft: DraftState = {
        clientDecisionId,
        underlying,
        date,
        decisionType,
        context,
        notes,
        predictionEnabled,
        metric,
        operator,
        predictionValue,
        horizon,
        evaluateOn,
      }
      sessionStorage.setItem(DRAFT_KEY, JSON.stringify(draft))
    } catch {
      // O UUID continua estável no mount mesmo sem sessionStorage.
    }
  }, [
    clientDecisionId,
    underlying,
    date,
    decisionType,
    context,
    notes,
    predictionEnabled,
    metric,
    operator,
    predictionValue,
    horizon,
    evaluateOn,
  ])

  const create = useMutation({
    mutationFn: (body: JournalCreateRequest) => apiWrite<JournalDetailData>('/api/journal', body),
    onMutate: () => onBusyChange(true),
    onSuccess: (result) => {
      try {
        sessionStorage.removeItem(DRAFT_KEY)
      } catch {
        // A gravação já foi confirmada; falha ao limpar o rascunho não desfaz o evento.
      }
      return onCreated(result)
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 401) onUnauthorized()
      if (error instanceof ApiError && error.code === 'decision_id_conflict') {
        void onConflict(clientDecisionId)
      }
    },
    onSettled: () => onBusyChange(false),
  })

  function clearDraft() {
    try {
      sessionStorage.removeItem(DRAFT_KEY)
    } catch {
      // O descarte explícito ainda fecha o formulário.
    }
  }

  function discard() {
    clearDraft()
    onCancel()
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setValidationError(null)
    const paper = underlying.trim().toUpperCase()
    if (!paper) {
      setValidationError('informe o ativo-objeto')
      return
    }
    let prediction: JournalPredictionInput | null = null
    if (predictionEnabled) {
      const value = decimal(predictionValue)
      const sessions = Number(horizon)
      if (value == null) {
        setValidationError('informe um valor finito para a previsão')
        return
      }
      if (Number.isInteger(value) && Math.abs(value) > Number.MAX_SAFE_INTEGER) {
        setValidationError('o valor inteiro da previsão excede o limite seguro')
        return
      }
      if (!Number.isInteger(sessions) || sessions < 1 || sessions > 252) {
        setValidationError('o horizonte deve ser um inteiro entre 1 e 252 sessões')
        return
      }
      if (!evaluateOn) {
        setValidationError('informe a data de avaliação')
        return
      }
      prediction = {
        metric,
        operator,
        value,
        horizon_sessions: sessions,
        evaluate_on: evaluateOn,
      }
    }
    create.mutate({
      client_decision_id: clientDecisionId,
      underlying: paper,
      date: date || null,
      decision_type: decisionType,
      context: context.trim(),
      notes: notes.trim() || null,
      ...(prediction ? { prediction } : {}),
    })
  }

  const unit = METRICS.find((item) => item.value === metric)?.unit ?? ''

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
          <legend className={`${visuallyHidden} border-none`}>Dados da decisão</legend>
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
              <span className={`${fieldLabel} lg:text-label`}>Sessão (opcional)</span>
              <Input
                className={`${fieldControl} lg:text-control`}
                type="date"
                value={date}
                onChange={(event) => setDate(event.target.value)}
              />
            </label>
            <label className={field}>
              <span className={`${fieldLabel} lg:text-label`}>Tipo de decisão</span>
              <NativeSelect
                className={`${fieldControl} lg:text-control`}
                value={decisionType}
                onChange={(event) => setDecisionType(event.target.value as DecisionType)}
              >
                {TYPES.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </NativeSelect>
            </label>
            <label className={field}>
              <span className={`${fieldLabel} lg:text-label`}>Contexto (opcional)</span>
              <Input
                className={`${fieldControl} lg:text-control`}
                value={context}
                onChange={(event) => setContext(event.target.value)}
                maxLength={500}
                autoComplete="off"
              />
            </label>
            <label className={`${field} col-span-full`}>
              <span className={`${fieldLabel} lg:text-label`}>Notas</span>
              <Textarea
                className={`${textareaControl} lg:text-control lg:leading-body`}
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
                placeholder="o que você decidiu e por quê"
                maxLength={4000}
                rows={4}
              />
            </label>
          </div>

          <fieldset className="m-0 min-w-0 border-none p-0" disabled={create.isPending}>
            <legend className="mb-[8px] flex min-h-[44px] items-center p-0 text-[13px] font-medium lg:text-block-title lg:leading-title">
              Previsão estruturada
            </legend>
            <label className="flex min-h-[44px] cursor-pointer items-center gap-[8px] text-[12.5px] text-[color:var(--text-2)] lg:text-label">
              <Checkbox
                className={focusToggle}
                checked={predictionEnabled}
                onChange={(event) => setPredictionEnabled(event.target.checked)}
              />
              <span>Adicionar previsão avaliável</span>
            </label>
            <p className={`${chainNote} lg:text-label lg:leading-body`}>
              Notas continuam texto livre. Só os campos abaixo entram na calibração.
            </p>
            {predictionEnabled && (
              <div className="mt-[12px] grid grid-cols-[minmax(0,1fr)] gap-[12px] sm:grid-cols-2">
                <label className={`${field} col-span-full`}>
                  <span className={`${fieldLabel} lg:text-label`}>Métrica</span>
                  <NativeSelect
                    className={`${fieldControl} lg:text-control`}
                    value={metric}
                    onChange={(event) => setMetric(event.target.value as PredictionMetric)}
                  >
                    {METRICS.map((item) => (
                      <option key={item.value} value={item.value}>
                        {item.label}
                      </option>
                    ))}
                  </NativeSelect>
                </label>
                <label className={field}>
                  <span className={`${fieldLabel} lg:text-label`}>Operador</span>
                  <NativeSelect
                    className={`${fieldControl} lg:text-control`}
                    value={operator}
                    onChange={(event) =>
                      setOperator(event.target.value as JournalPredictionInput['operator'])
                    }
                  >
                    <option value="<">&lt;</option>
                    <option value="<=">&lt;=</option>
                    <option value=">">&gt;</option>
                    <option value=">=">&gt;=</option>
                  </NativeSelect>
                </label>
                <label className={field}>
                  <span className={`${fieldLabel} lg:text-label`}>
                    Valor {unit && `(${unit})`}
                  </span>
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
                <label className={field}>
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
            A data de avaliação precisa corresponder ao horizonte no calendário B3. O lote EOD usa o
            primeiro pregão posterior; métrica ausente vira “sem dados”, não erro.
          </p>
          {validationError && (
            <p className={`${notice} lg:text-label`} role="alert">
              {validationError}
            </p>
          )}
          {create.isError && <ErrorState error={create.error} title="Decisão não registrada" />}
          <div className="mt-[4px] flex flex-wrap justify-end gap-[8px] max-sm:grid max-sm:grid-cols-[minmax(0,1fr)]">
            <Button
              type="button"
              variant="outline"
              onClick={discard}
              disabled={create.isPending}
            >
              Cancelar
            </Button>
            <Button type="submit" disabled={create.isPending}>
              {create.isPending ? 'registrando…' : 'Registrar decisão'}
            </Button>
          </div>
        </fieldset>
      </form>
    </>
  )
}
