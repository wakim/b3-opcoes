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
    <section className="builder" aria-labelledby="builder-title">
      <div className="block-head">
        <h2 className="block-title" id="builder-title">
          Montar estrutura
        </h2>
        <span className="block-count">
          {legs.length}/{MAX_LEGS} pernas
        </span>
      </div>
      <p className="chain-note">
        Calculadora de payoff. 100 ações = 1 contrato; a quantidade é em ações e não é multiplicada
        por 100. Nada é gravado.
      </p>

      {legs.length === 0 ? (
        <p className="chart-empty">
          Expanda uma linha da cadeia e use <strong>Comprar</strong> ou <strong>Vender</strong>, ou
          adicione a ação abaixo.
        </p>
      ) : (
        <ul className="builder__legs">
          {legs.map((leg) => (
            <li className="builder__leg" key={leg.id}>
              <span className="builder__leg-id">
                <span className="builder__leg-ticker num">{leg.ticker}</span>
                <span className="quiet">
                  {leg.kind === 'stock' ? 'ação' : 'opção'} · {SIDE_LABEL[leg.side]}
                </span>
              </span>
              <label className="builder__qty">
                <span className="quiet">ações</span>
                <input
                  type="number"
                  inputMode="numeric"
                  min={1}
                  step={1}
                  value={leg.quantity}
                  aria-label={`Quantidade de ações de ${leg.ticker}`}
                  onChange={(event) => setQuantity(leg.id, event.target.value)}
                />
              </label>
              <button
                type="button"
                className="builder__remove"
                aria-label={`Remover ${leg.ticker}`}
                onClick={() => removeLeg(leg.id)}
              >
                remover
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="builder__actions">
        <div className="builder__stock">
          <label className="builder__qty">
            <span className="quiet">ação</span>
            <select
              value={stockSide}
              aria-label="Lado da ação"
              onChange={(event) => setStockSide(event.target.value as LegSide)}
            >
              <option value="long">comprada</option>
              <option value="short">vendida</option>
            </select>
          </label>
          <label className="builder__qty">
            <span className="quiet">ações</span>
            <input
              type="number"
              inputMode="numeric"
              min={1}
              step={1}
              value={stockQuantity}
              aria-label="Quantidade de ações da ação"
              onChange={(event) => setStockQuantity(event.target.value)}
            />
          </label>
          <button
            type="button"
            className="builder__button"
            onClick={addStock}
            disabled={legs.length >= MAX_LEGS}
          >
            Adicionar ação
          </button>
        </div>
        <div className="builder__run">
          <button
            type="button"
            className="builder__button builder__button--primary"
            onClick={() => calc.mutate()}
            disabled={legs.length === 0 || calc.isPending}
          >
            {calc.isPending ? 'calculando…' : `Calcular (${legs.length})`}
          </button>
          <button
            type="button"
            className="builder__button"
            onClick={() => setLegs([])}
            disabled={legs.length === 0}
          >
            Limpar
          </button>
        </div>
      </div>

      {legs.length >= MAX_LEGS && (
        <p className="note">Limite de {MAX_LEGS} pernas atingido (o backend recusa mais).</p>
      )}

      {calc.isError && <ErrorState error={calc.error} title="Estrutura não avaliada" />}

      {result && (
        <div className="builder__result">
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

          <p className="note">
            {result.maturity ? `Vencimento ${result.maturity} · ` : ''}
            estrutura {result.structure_version} · sessão {calc.data?.as_of_session ?? '—'}
            {result.forward_brl != null && ` · forward ${brl(result.forward_brl)}`}
          </p>

          {result.capital_over && (
            <p className="notice" role="status">
              O capital comprometido passa do capital informado.
            </p>
          )}
          {result.flags.length > 0 && (
            <p className="note">flags: {result.flags.join(' · ')}</p>
          )}

          <PayoffChart
            points={result.payoff}
            breakevens={result.breakeven_brl}
            ariaLabel={`Payoff no vencimento de ${underlying}`}
          />

          {scenariosUnavailable ? (
            <p className="notice" role="status">
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
    return <p className="note">sem cenários no dia 0</p>
  }
  const spots = [...new Set(zeroDay.map((row) => row.spot_brl))].sort((a, b) => a - b)
  const shifts = [...new Set(zeroDay.map((row) => row.iv_shift_pts))].sort((a, b) => a - b)

  return (
    <div className="builder__scenarios">
      <p className="builder__scenarios-title">Cenários no dia 0 (spot e choque de IV do backend)</p>
      <div className="table-wrap">
        <table className="screen-table">
          <thead>
            <tr>
              <th>Preço</th>
              {shifts.map((shift) => (
                <th key={shift}>
                  IV {shift > 0 ? '+' : ''}
                  {fmt(shift, 0)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {spots.map((spot) => (
              <tr key={spot}>
                <td>{brl(spot)}</td>
                {shifts.map((shift) => {
                  const row = zeroDay.find(
                    (item) => item.spot_brl === spot && item.iv_shift_pts === shift,
                  )
                  return (
                    <td key={shift} className={row && row.pnl_brl < 0 ? 'quiet' : undefined}>
                      {row ? brl(row.pnl_brl) : '—'}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
