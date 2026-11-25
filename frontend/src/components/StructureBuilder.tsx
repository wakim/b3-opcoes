/** Builder de estrutura (F5.6): calculadora de payoff, não sugestão.
 *
 * As pernas viram o corpo canônico de `POST /api/structure` (onda-j §2). Todo
 * número exibido — prêmio, breakevens, perda/ganho máximo, capital, gregas,
 * payoff e cenários — vem da resposta do backend. Nada é recalculado em TS e
 * nada é gravado (F6 é que grava).
 */
import { useState, type Dispatch, type SetStateAction } from 'react'
import { useMutation } from '@tanstack/react-query'
import { apiPost, type Structure, type StructureLegInput, type LegSide } from '../lib/api'
import { brl, fmt } from '../lib/format'
import { ErrorState } from './State'
import { Metric, MetricGrid } from './Metric'
import { PayoffChart } from './PayoffChart'
import { ScreenTable } from './ScreenTable'
import { Button } from './ui/button'
import { Input } from './ui/input'
import { NativeSelect } from './ui/native-select'
import {
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from './ui/table'

export interface BuilderLeg extends StructureLegInput {
  /** Chave local da lista; não vai para a API. */
  id: number
}

const MAX_LEGS = 60
const DEFAULT_QUANTITY = 100

const SIDE_LABEL: Record<LegSide, string> = { long: 'comprada', short: 'vendida' }

export function StructureBuilder({
  underlying,
  date,
  legs,
  setLegs,
  nextId,
}: {
  underlying: string
  date: string | null
  legs: BuilderLeg[]
  setLegs: Dispatch<SetStateAction<BuilderLeg[]>>
  nextId: () => number
}) {
  const [stockSide, setStockSide] = useState<LegSide>('long')
  const [stockQuantity, setStockQuantity] = useState(String(DEFAULT_QUANTITY))

  const calc = useMutation({
    mutationFn: () =>
      apiPost<Structure>('/api/structure', {
        underlying,
        date,
        legs: legs.map((leg): StructureLegInput => ({
          kind: leg.kind,
          ticker: leg.ticker,
          side: leg.side,
          quantity: leg.quantity,
        })),
        scenarios: true,
      }),
  })

  function addStock() {
    const quantity = Number.parseInt(stockQuantity, 10)
    if (!Number.isInteger(quantity) || quantity <= 0) return
    setLegs((prev) =>
      prev.length >= MAX_LEGS
        ? prev
        : [...prev, { id: nextId(), kind: 'stock', ticker: underlying, side: stockSide, quantity }],
    )
  }

  function setQuantity(id: number, raw: string) {
    const quantity = Number.parseInt(raw, 10)
    setLegs((prev) =>
      prev.map((leg) => (leg.id === id && Number.isInteger(quantity) && quantity > 0 ? { ...leg, quantity } : leg)),
    )
  }

  function removeLeg(id: number) {
    setLegs((prev) => prev.filter((leg) => leg.id !== id))
  }

  const result = calc.data?.data
  const unboundedLoss = result?.flags.includes('unbounded_loss') ?? false
  const unboundedGain = result?.flags.includes('unbounded_gain') ?? false
  const scenariosUnavailable = result
    ? result.scenarios === null || result.flags.includes('scenarios_unavailable')
    : false

  return (
    <section className="mt-[28px] border-t border-border pt-[4px]" aria-labelledby="builder-title">
      <div className="mt-[24px] flex flex-wrap items-center justify-between gap-[8px]">
        <h2 className="mt-0 mb-[8px] text-[calc(13px*var(--font-scale))] font-medium lg:text-block-title lg:leading-title" id="builder-title">
          Montar estrutura
        </h2>
        <span className="ml-[8px] font-mono text-[calc(11px*var(--font-scale))] font-normal text-muted-foreground lg:text-label">
          {legs.length}/{MAX_LEGS} pernas
        </span>
      </div>
      <p className="mb-[8px] text-[calc(11px*var(--font-scale))] leading-[1.45] text-muted-foreground lg:text-label lg:leading-body">
        Calculadora de payoff. 100 ações = 1 contrato; a quantidade é em ações e não é multiplicada
        por 100. Nada é gravado.
      </p>

      {legs.length === 0 ? (
        <p className="my-[8px] text-[calc(11.5px*var(--font-scale))] text-muted-foreground lg:text-detail">
          Expanda uma linha da cadeia e use <strong>Comprar</strong> ou <strong>Vender</strong>, ou
          adicione a ação abaixo.
        </p>
      ) : (
        <ul className="mb-[8px] overflow-hidden rounded-[6px] border border-border">
          {legs.map((leg, index) => (
            <li className={`flex flex-wrap items-center gap-x-[12px] gap-y-[8px] bg-card px-[10px] py-[8px]${index > 0 ? ' border-t border-border' : ''}`} key={leg.id}>
              <span className="flex min-w-[92px] flex-[1_1_120px] flex-col">
                <span className="font-mono tabular-nums text-primary">{leg.ticker}</span>
                <span className="text-muted-foreground lg:text-label">
                  {leg.kind === 'stock' ? 'ação' : 'opção'} · {SIDE_LABEL[leg.side]}
                </span>
              </span>
              <label className="inline-flex items-center gap-[6px] text-[calc(11px*var(--font-scale))] text-muted-foreground lg:text-label">
                <span className="text-muted-foreground lg:text-label">ações</span>
                <Input
                  type="number"
                  inputMode="numeric"
                  min={1}
                  step={1}
                  value={leg.quantity}
                  variant="builder"
                  className="w-[96px] min-w-[96px] max-w-[96px]"
                  aria-label={`Quantidade de ações de ${leg.ticker}`}
                  onChange={(event) => setQuantity(leg.id, event.target.value)}
                />
              </label>
              <Button
                type="button"
                variant="link"
                size="sm"
                builder="remove"
                aria-label={`Remover ${leg.ticker}`}
                onClick={() => removeLeg(leg.id)}
              >
                remover
              </Button>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-[8px] mb-[12px] grid gap-[12px] min-[720px]:grid-cols-[minmax(0,1fr)_auto] min-[720px]:items-end">
        <div className="grid grid-cols-[minmax(0,1fr)_104px] items-end gap-[8px] min-[720px]:grid-cols-[156px_104px_auto]" role="group" aria-label="Perna de ação">
          <label className="flex min-w-0 flex-col gap-[4px]">
            <span className="text-[calc(11px*var(--font-scale))] text-muted-foreground lg:text-label">Perna de ação</span>
            <NativeSelect
              variant="builder"
              value={stockSide}
              aria-label="Lado da ação"
              onChange={(event) => setStockSide(event.target.value as LegSide)}
            >
              <option value="long">comprada</option>
              <option value="short">vendida</option>
            </NativeSelect>
          </label>
          <label className="flex min-w-0 flex-col gap-[4px]">
            <span className="text-[calc(11px*var(--font-scale))] text-muted-foreground lg:text-label">Ações</span>
            <Input
              type="number"
              inputMode="numeric"
              min={1}
              step={1}
              variant="builder"
              value={stockQuantity}
              aria-label="Quantidade de ações da ação"
              onChange={(event) => setStockQuantity(event.target.value)}
            />
          </label>
          <Button
            type="button"
            variant="outline"
            builder="button"
            className="col-span-full w-full min-[720px]:col-auto min-[720px]:w-auto"
            onClick={addStock}
            disabled={legs.length >= MAX_LEGS}
          >
            Adicionar ação
          </Button>
        </div>
        <div className="flex flex-wrap items-center gap-[8px] min-[720px]:justify-end">
          <Button
            type="button"
            builder="primary"
            onClick={() => calc.mutate()}
            disabled={legs.length === 0 || calc.isPending}
          >
            {calc.isPending ? 'calculando…' : `Calcular (${legs.length})`}
          </Button>
          <Button
            type="button"
            variant="outline"
            builder="button"
            onClick={() => setLegs([])}
            disabled={legs.length === 0}
          >
            Limpar
          </Button>
        </div>
      </div>

      {legs.length >= MAX_LEGS && (
        <p className="my-[8px] text-[calc(11px*var(--font-scale))] text-muted-foreground lg:text-label">Limite de {MAX_LEGS} pernas atingido (o backend recusa mais).</p>
      )}

      {calc.isError && <ErrorState error={calc.error} title="Estrutura não avaliada" />}

      {result && (
        <div className="mt-[8px]">
          <MetricGrid>
            <Metric
              label="Prêmio líquido"
              value={brl(result.net_premium_brl)}
              emphasis
              hint="Positivo = débito pago; negativo = crédito recebido."
            />
            <Metric
              label="Breakevens"
              value={result.breakeven_brl.length > 0 ? result.breakeven_brl.map((v) => brl(v)).join(' · ') : '—'}
            />
            <Metric
              label="Perda máxima"
              value={unboundedLoss ? 'ilimitada' : brl(result.max_loss_brl)}
            />
            <Metric
              label="Ganho máximo"
              value={unboundedGain ? 'ilimitado' : brl(result.max_gain_brl)}
            />
            <Metric label="Capital comprometido" value={brl(result.capital_committed_brl)} />
            <Metric label="Delta agregado" value={fmt(result.delta, 3)} />
            <Metric label="Gamma agregado" value={fmt(result.gamma, 3)} />
            <Metric label="Theta agregado" value={brl(result.theta_brl)} />
            <Metric label="Vega agregado" value={brl(result.vega_brl)} />
          </MetricGrid>

          <p className="my-[8px] text-[calc(11px*var(--font-scale))] text-muted-foreground lg:text-label">
            {result.maturity ? `Vencimento ${result.maturity} · ` : ''}
            estrutura {result.structure_version} · sessão {calc.data?.as_of_session ?? '—'}
            {result.forward_brl != null && ` · forward ${brl(result.forward_brl)}`}
          </p>

          {result.capital_over && (
            <p className="mb-[16px] border-l-2 border-input pl-[10px] text-[calc(11.5px*var(--font-scale))] text-[color:var(--text-2)] lg:text-label" role="status">
              O capital comprometido passa do capital informado.
            </p>
          )}
          {result.flags.length > 0 && (
            <p className="my-[8px] text-[calc(11px*var(--font-scale))] text-muted-foreground lg:text-label">flags: {result.flags.join(' · ')}</p>
          )}

          <PayoffChart
            points={result.payoff}
            breakevens={result.breakeven_brl}
            ariaLabel={`Payoff no vencimento de ${underlying}`}
          />

          {scenariosUnavailable ? (
            <p className="mb-[16px] border-l-2 border-input pl-[10px] text-[calc(11.5px*var(--font-scale))] text-[color:var(--text-2)] lg:text-label" role="status">
              Cenários indisponíveis
              {result.flags.includes('no_iv') && ' (série sem volatilidade implícita)'}
              {result.flags.includes('no_rates') && ' (curva de juros ausente)'}
              {result.flags.includes('forward_unavailable') && ' (forward ausente)'}
              . O payoff no vencimento continua calculado.
            </p>
          ) : (
            <ScenariosTable scenarios={result.scenarios ?? []} />
          )}
        </div>
      )}
    </section>
  )
}

/** Tabela curta: só o dia 0 (5 spots × 3 choques de IV). Não é gráfico novo. */
function ScenariosTable({
  scenarios,
}: {
  scenarios: NonNullable<Structure['scenarios']>
}) {
  const zeroDay = scenarios.filter((row) => row.days_forward === 0)
  if (zeroDay.length === 0) {
    return <p className="my-[8px] text-[calc(11px*var(--font-scale))] text-muted-foreground lg:text-label">sem cenários no dia 0</p>
  }
  const spots = [...new Set(zeroDay.map((row) => row.spot_brl))].sort((a, b) => a - b)
  const shifts = [...new Set(zeroDay.map((row) => row.iv_shift_pts))].sort((a, b) => a - b)

  return (
    <div className="mt-[12px]">
      <p className="mt-[8px] mb-[6px] text-[calc(11px*var(--font-scale))] text-muted-foreground lg:text-label">Cenários no dia 0 (spot e choque de IV do backend)</p>
      <ScreenTable wrapperClassName="overflow-x-auto rounded-[6px] border border-border">
        <TableHeader>
            <TableRow>
              <TableHead className="px-[8px] py-[10px] font-normal text-[color:var(--text-2)] lg:text-table-head">Preço</TableHead>
              {shifts.map((shift) => (
                <TableHead key={shift} className="px-[8px] py-[10px] text-right font-normal text-[color:var(--text-2)] lg:text-table-head">
                  IV {shift > 0 ? '+' : ''}
                  {fmt(shift, 0)}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {spots.map((spot) => (
              <TableRow key={spot}>
                <TableCell className="px-[8px] py-[10px]">{brl(spot)}</TableCell>
                {shifts.map((shift) => {
                  const row = zeroDay.find(
                    (item) => item.spot_brl === spot && item.iv_shift_pts === shift,
                  )
                  return (
                    <TableCell key={shift} className={row && row.pnl_brl < 0 ? 'px-[8px] py-[10px] text-muted-foreground' : 'px-[8px] py-[10px]'}>
                      {row ? brl(row.pnl_brl) : '—'}
                    </TableCell>
                  )
                })}
              </TableRow>
            ))}
        </TableBody>
      </ScreenTable>
    </div>
  )
}
