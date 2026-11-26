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
import { usePresentation } from '../lib/presentation'
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
  const p = usePresentation()
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
  const scenarioReason = [
    result?.flags.includes('no_iv') ? p.copy('iv_pct').label : null,
    result?.flags.includes('no_rates') ? p.flag('no_rates').label : null,
    result?.flags.includes('forward_unavailable') ? p.copy('forward').label : null,
  ].filter(Boolean).join(', ')
  const resultMaturity = result?.maturity
    ? `${p.copy('maturity').label} ${result.maturity} · `
    : ''
  const resultForward = result?.forward_brl != null
    ? p.copy('screen', 'term_detail_forward', {
        forward: p.copy('forward').label,
        value: brl(result.forward_brl),
      }).label
    : ''

  return (
    <section className="mt-[28px] border-t border-border pt-[4px]" aria-labelledby="builder-title">
      <div className="mt-[24px] flex flex-wrap items-center justify-between gap-[8px]">
        <h2 className="mt-0 mb-[8px] text-[calc(13px*var(--font-scale))] font-medium lg:text-block-title lg:leading-title" id="builder-title">
          {p.copy('structure', 'title').label}
        </h2>
        <span className="ml-[8px] font-mono text-[calc(11px*var(--font-scale))] font-normal text-muted-foreground lg:text-label">
          {p.copy('structure', 'count', { count: legs.length, max: MAX_LEGS }).label}
        </span>
      </div>
      <p className="mb-[8px] text-[calc(11px*var(--font-scale))] leading-[1.45] text-muted-foreground lg:text-label lg:leading-body">
        {p.copy('structure', 'intro', { quantity: p.copy('quantity_contract').label }).label}
      </p>

      {legs.length === 0 ? (
        <p className="my-[8px] text-[calc(11.5px*var(--font-scale))] text-muted-foreground lg:text-detail">
          {p.copy('structure', 'empty_prefix').label}<strong>{p.copy('action', 'buy').label}</strong>{p.copy('structure', 'empty_or').label}
          <strong>{p.copy('action', 'sell').label}</strong>{p.copy('structure', 'empty_suffix', { add_stock: p.copy('action', 'add_stock').label }).label}
        </p>
      ) : (
        <ul className="mb-[8px] overflow-hidden rounded-[6px] border border-border">
          {legs.map((leg, index) => (
            <li className={`flex flex-wrap items-center gap-x-[12px] gap-y-[8px] bg-card px-[10px] py-[8px]${index > 0 ? ' border-t border-border' : ''}`} key={leg.id}>
              <span className="flex min-w-[92px] flex-[1_1_120px] flex-col">
                <span className="font-mono tabular-nums text-primary">{leg.ticker}</span>
                <span className="text-muted-foreground lg:text-label">
                  {p.legType(leg.kind)} · {p.copy('position_side', leg.side).label}
                </span>
              </span>
              <label className="inline-flex items-center gap-[6px] text-[calc(11px*var(--font-scale))] text-muted-foreground lg:text-label">
                <span className="text-muted-foreground lg:text-label">{p.copy('form', 'actions').label}</span>
                <Input
                  type="number"
                  inputMode="numeric"
                  min={1}
                  step={1}
                  value={leg.quantity}
                  variant="builder"
                  className="w-[96px] min-w-[96px] max-w-[96px]"
                  aria-label={p.copy('structure', 'quantity_aria', { ticker: leg.ticker }).label}
                  onChange={(event) => setQuantity(leg.id, event.target.value)}
                />
              </label>
              <Button
                type="button"
                variant="link"
                size="sm"
                builder="remove"
                aria-label={p.copy('structure', 'remove_aria', { ticker: leg.ticker }).label}
                onClick={() => removeLeg(leg.id)}
              >
                {p.copy('action', 'remove').label}
              </Button>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-[8px] mb-[12px] grid gap-[12px] min-[720px]:grid-cols-[minmax(0,1fr)_auto] min-[720px]:items-end">
        <div className="grid grid-cols-[minmax(0,1fr)_104px] items-end gap-[8px] min-[720px]:grid-cols-[156px_104px_auto]" role="group" aria-label={p.copy('structure', 'stock_leg').label}>
          <label className="flex min-w-0 flex-col gap-[4px]">
            <span className="text-[calc(11px*var(--font-scale))] text-muted-foreground lg:text-label">{p.copy('structure', 'stock_leg').label}</span>
            <NativeSelect
              variant="builder"
              value={stockSide}
              aria-label={p.copy('structure', 'stock_side').label}
              onChange={(event) => setStockSide(event.target.value as LegSide)}
            >
              <option value="long">{p.copy('position_side', 'long').label}</option>
              <option value="short">{p.copy('position_side', 'short').label}</option>
            </NativeSelect>
          </label>
          <label className="flex min-w-0 flex-col gap-[4px]">
            <span className="text-[calc(11px*var(--font-scale))] text-muted-foreground lg:text-label">{p.copy('form', 'actions').label}</span>
            <Input
              type="number"
              inputMode="numeric"
              min={1}
              step={1}
              variant="builder"
              value={stockQuantity}
              aria-label={p.copy('structure', 'stock_quantity').label}
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
            {p.copy('action', 'add_stock').label}
          </Button>
        </div>
        <div className="flex flex-wrap items-center gap-[8px] min-[720px]:justify-end">
          <Button
            type="button"
            builder="primary"
            onClick={() => calc.mutate()}
            disabled={legs.length === 0 || calc.isPending}
          >
            {calc.isPending
              ? p.copy('action', 'calculating').label
              : p.copy('action', 'calculate_count', { count: legs.length }).label}
          </Button>
          <Button
            type="button"
            variant="outline"
            builder="button"
            onClick={() => setLegs([])}
            disabled={legs.length === 0}
          >
            {p.copy('action', 'clear').label}
          </Button>
        </div>
      </div>

      {legs.length >= MAX_LEGS && (
        <p className="my-[8px] text-[calc(11px*var(--font-scale))] text-muted-foreground lg:text-label">{p.copy('structure', 'limit', { max: MAX_LEGS }).label}</p>
      )}

      {calc.isError && <ErrorState error={calc.error} title={p.copy('error_title', 'structure').label} />}

      {result && (
        <div className="mt-[8px]">
          <MetricGrid>
            <Metric
              term="net_premium"
              value={brl(result.net_premium_brl)}
              emphasis
            />
            <Metric
              term="breakeven"
              variant="plural"
              value={result.breakeven_brl.length > 0 ? result.breakeven_brl.map((v) => brl(v)).join(' · ') : '—'}
            />
            <Metric
              term="max_loss"
              value={unboundedLoss ? p.copy('payoff', 'unbounded_loss').label : brl(result.max_loss_brl)}
            />
            <Metric
              term="max_gain"
              value={unboundedGain ? p.copy('payoff', 'unbounded_gain').label : brl(result.max_gain_brl)}
            />
            <Metric term="capital" value={brl(result.capital_committed_brl)} />
            <Metric term="delta" variant="aggregate" value={fmt(result.delta, 3)} />
            <Metric term="gamma" variant="aggregate" value={fmt(result.gamma, 3)} />
            <Metric term="theta" variant="aggregate" value={brl(result.theta_brl)} />
            <Metric term="vega" variant="aggregate" value={brl(result.vega_brl)} />
          </MetricGrid>

          <p className="my-[8px] text-[calc(11px*var(--font-scale))] text-muted-foreground lg:text-label">
            {p.copy('structure', 'summary', {
              maturity: resultMaturity,
              structure: p.copy('structure').label,
              version: result.structure_version,
              session: calc.data?.as_of_session ?? '—',
              forward: resultForward,
            }).label}
          </p>

          {result.capital_over && (
            <p className="mb-[16px] border-l-2 border-input pl-[10px] text-[calc(11.5px*var(--font-scale))] text-[color:var(--text-2)] lg:text-label" role="status">
              {p.copy('structure', 'capital_over').label}
            </p>
          )}
          {result.flags.length > 0 && (
            <p className="my-[8px] text-[calc(11px*var(--font-scale))] text-muted-foreground lg:text-label">
              {p.copy('flag', 'list', { items: result.flags.map((code) => p.flag(code).label).join(' · ') }).label}
            </p>
          )}

          <PayoffChart
            points={result.payoff}
            breakevens={result.breakeven_brl}
            ariaLabel={p.copy('payoff', 'maturity_aria', { ticker: underlying }).label}
          />

          {scenariosUnavailable ? (
            <p className="mb-[16px] border-l-2 border-input pl-[10px] text-[calc(11.5px*var(--font-scale))] text-[color:var(--text-2)] lg:text-label" role="status">
              {p.copy('scenarios', 'unavailable_reason', { reason: scenarioReason }).label}
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
  const p = usePresentation()
  const zeroDay = scenarios.filter((row) => row.days_forward === 0)
  if (zeroDay.length === 0) {
    return <p className="my-[8px] text-[calc(11px*var(--font-scale))] text-muted-foreground lg:text-label">{p.copy('scenarios', 'no_day0').label}</p>
  }
  const spots = [...new Set(zeroDay.map((row) => row.spot_brl))].sort((a, b) => a - b)
  const shifts = [...new Set(zeroDay.map((row) => row.iv_shift_pts))].sort((a, b) => a - b)

  return (
    <div className="mt-[12px]">
      <p className="mt-[8px] mb-[6px] text-[calc(11px*var(--font-scale))] text-muted-foreground lg:text-label">{p.copy('scenarios', 'day0').label}</p>
      <ScreenTable wrapperClassName="overflow-x-auto rounded-[6px] border border-border">
        <TableHeader>
            <TableRow>
              <TableHead className="px-[8px] py-[10px] font-normal text-[color:var(--text-2)] lg:text-table-head">{p.copy('table', 'price').label}</TableHead>
              {shifts.map((shift) => (
                <TableHead key={shift} className="px-[8px] py-[10px] text-right font-normal text-[color:var(--text-2)] lg:text-table-head">
                  {p.copy('iv_pct', 'shift', { shift: `${shift > 0 ? '+' : ''}${fmt(shift, 0)}` }).label}
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
