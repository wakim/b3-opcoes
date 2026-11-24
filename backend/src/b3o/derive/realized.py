"""Vol realizada Yang-Zhang sobre OHLC do subjacente (F1.6, spec §6.5) — puro.

Fórmula: Yang, D. & Zhang, Q. (2000), *Drift-Independent Volatility Estimation
Based on High, Low, Open, and Close Prices*, Journal of Business 73(3), 477-491,
equações (1)–(6):

    σ²_YZ = σ²_o + k·σ²_c + (1 − k)·σ²_rs
    k     = 0,34 / (1,34 + (N+1)/(N−1))

    o_i = ln(O_i/C_{i−1})            σ²_o  = variância amostral (N−1) de o_i
    c_i = ln(C_i/O_i)                σ²_c  = variância amostral (N−1) de c_i
    u_i = ln(H_i/O_i)                σ²_rs = (1/N)·Σ[u_i·(u_i − c_i) + d_i·(d_i − c_i)]
    d_i = ln(L_i/O_i)

Atenção ao duplo uso do símbolo: no paper, ``c`` é o retorno **open-to-close** e
``u`` também é usado para o retorno close-to-close em parte da literatura. Trocar
os dois é o erro clássico da implementação; aqui os campos têm nome explícito
(``overnight``/``intraday``/``high_log``/``low_log``/``close_log``) e a forma
usada é a equivalente em preços crus:

    σ²_rs = (1/N)·Σ[(h_i − c_i)(h_i − o_i) + (l_i − c_i)(l_i − o_i)]
    h_i = ln(H_i/C_{i−1})   l_i = ln(L_i/C_{i−1})   c_i = ln(C_i/C_{i−1})

porque ``h_i − o_i = u_i`` e ``h_i − c_i = ln(H_i/C_i)`` (identidade algébrica, não
aproximação). As duas parcelas σ²_c e σ²_rs estimam a MESMA quantidade (variância
open-to-close) e ``k`` é o peso ótimo entre elas; é por isso que os pesos somam
``k + (1−k) = 1`` e o total estima σ²_overnight + σ²_open-to-close = σ²_diária.
Anualização: ``σ_aa = √(σ²_YZ · 252)`` e ``value`` em **% a.a.**.

Duas armadilhas do dado que este módulo respeita:

- **escala (desdobramento/grupamento) mata a RV.** Um 1:3 sem ajuste gera um
  retorno overnight de ln(1/3) ≈ −110%, que contamina a janela inteira por
  meses (spec §6.5). O ajuste vem do B2 via ``corporate_events`` e chega aqui
  já resolvido em ``OhlcBar.adj_factor`` — este módulo **não** classifica evento
  nenhum e **não** lê I/O.
- **gap de provento.** O retorno overnight do dia ex carrega o dividendo, que é
  risco de outra natureza (spec §6.5). Quem sabe a data ex é o pipeline (B2);
  aqui ela entra como ``ex_div_dates`` e a observação contaminada sai da janela.

Este módulo é puro: math/dataclasses/typing apenas. Zero I/O, zero numpy, zero
scipy/pandas (ADR-004).
"""

from __future__ import annotations

import math
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date

SESSIONS_PER_YEAR = 252
"""Base de anualização (AGENTS.md: ``T`` em dias úteis / 252)."""

WINDOWS: tuple[int, ...] = (20, 30, 60)
"""Janelas publicadas (``rv20``/``rv30``/``rv60``, spec §5.3 e §6.5)."""

MIN_WINDOW = 3
"""Menor janela com sentido: σ² amostral precisa de N ≥ 2 retornos (window − 1)."""

INSUFFICIENT_HISTORY = "insufficient_history"
"""Menos barras (ou menos observações limpas) do que a janela pedida."""

INVALID_PRICE = "invalid_price"
"""Barra com preço ≤ 0 — não há log que salve; nunca zero silencioso (P-N)."""

NEGATIVE_VARIANCE = "negative_variance"
"""σ²_YZ < −``VARIANCE_EPSILON``: só acontece com OHLC inconsistente
(high < max(O,C), low > min(O,C))."""

EX_DIV_EXCLUDED = "ex_div_excluded"
"""Ao menos uma observação saiu da janela por cair em data ex de provento."""

DEFAULT_GAP_THRESHOLD = 0.40
"""``|r|`` overnight acima disto sem evento é `unexplained_gap` (spec §6.5)."""

VARIANCE_EPSILON = 1e-12
"""σ² entre ``−``isto e zero é ruído de ponto flutuante de série parada, não erro.

Sem o guarda, uma série legítima de vol ~0 pode sair ``None`` + ``negative_variance``
só porque o termo de Rogers-Satchell somou −1e−17. Acima disto é OHLC inconsistente
de verdade e o resultado é ``None`` com flag.
"""


@dataclass(frozen=True, slots=True)
class OhlcBar:
    """Uma sessão do subjacente, em **centavos** (mercado `010`)."""

    refdate: date
    """Sessão da barra — a data de referência vem do arquivo, nunca do relógio."""

    open: int
    high: int
    low: int
    close: int
    """OHLC em centavos (``000000000201`` = R$ 2,01 no COTAHIST)."""

    adj_factor: float = 1.0
    """Multiplicador **já resolvido** pelo chamador para ESTA barra.

    É o número pelo qual os quatro preços da barra são multiplicados antes de
    qualquer log — o fator acumulado de ``corporate_events.event_factor_series``.
    Numa série contínua, ``f_i/f_{i−1}`` é o que corrige o salto de escala: um
    desdobramento 1:3 dá ``adj_factor = 1/3`` nas barras **anteriores** ao evento
    (preço antigo × 1/3 = preço novo) e 1,0 da data em diante.
    """


@dataclass(frozen=True, slots=True)
class VolResult:
    """Vol realizada anualizada, com as flags do que faltou (nunca zero mudo)."""

    value: float | None
    """Vol em **% a.a.** (``30.0`` = 30% a.a.), base 252; ``None`` + flag se não dá."""

    flags: tuple[str, ...]
    """Motivos de ausência/ressalva: ``insufficient_history``, ``invalid_price``, …"""

    n_bars: int
    """Barras tocadas pela estimativa.

    No caso limpo é a própria ``window``. Quando há observação excluída por data
    ex, a janela não encolhe — ela puxa barras mais para trás —, então este número
    pode passar de ``window`` (é o intervalo de barras que a conta atravessou).
    """


_Logs = tuple[float, float, float, float]
"""``(ln O, ln H, ln L, ln C)`` de uma barra já ajustada por ``adj_factor``."""


@dataclass(frozen=True, slots=True)
class _Observation:
    """Retornos e faixas de uma barra contra o fechamento anterior (já ajustados)."""

    refdate: date
    overnight: float
    """``ln(O_i/C_{i−1})`` — o ``o_i`` do paper."""
    intraday: float
    """``ln(C_i/O_i)`` — o ``c_i`` do paper (open-to-close)."""
    high_log: float
    """``ln(H_i/C_{i−1})`` — a base de ``u_i`` (``u_i = high_log − overnight``)."""
    low_log: float
    """``ln(L_i/C_{i−1})`` — a base de ``d_i`` (``d_i = low_log − overnight``)."""
    close_log: float
    """``ln(C_i/C_{i−1})`` — close-to-close: NÃO é o ``c`` do paper."""


def yang_zhang_vol(
    ohlc: Sequence[OhlcBar],
    window: int,
    *,
    ex_div_dates: Collection[date] = frozenset(),
) -> VolResult:
    """Vol realizada Yang-Zhang das **últimas** ``window`` barras, em % a.a.

    Anualização: ``σ_aa = √(σ²_YZ · 252)``; ``value = 100 · σ_aa`` — logo
    ``30.0`` significa 30% ao ano. ``N`` é o número de retornos usados
    (``window − 1`` quando há histórico suficiente): cada barra precisa do
    fechamento da anterior, então uma janela de 20 barras dá 19 observações.

    ``ohlc`` é a série do **subjacente** (mercado `010`), em centavos. A função
    ordena por ``refdate`` e, se a mesma sessão aparecer duas vezes, fica com a
    **última** (partição reescrita / linha duplicada): duplicata criaria um
    retorno zero artificial e encolheria a janela de fato.

    Com menos de ``window`` barras → ``None`` + ``insufficient_history``; preço
    ≤ 0 em barra que a conta tocaria → ``None`` + ``invalid_price``. Nunca devolve
    zero para "não sei" (vol zero de série parada é resultado legítimo, não
    ausência).

    ``ex_div_dates`` são as datas **ex** de provento (``dividend_dates`` do B2): a
    observação cujo dia é ex fica de fora — o gap do dividendo é risco de outra
    natureza e inflaria a RV (spec §6.5). A janela **não** encolhe: puxa uma
    barra mais para trás, e a flag ``ex_div_excluded`` registra o corte. Sem
    barra sobrando, ``insufficient_history``.

    Medido no lake de 2026 (22/11/2026, 295 proventos com spot no dia): a data
    contígua do B2 é a sessão cujo overnight carrega o gap — desvio médio de
    0,81% contra o gap implícito no dia do evento, 1,59% em ``d−1`` e 1,77% em
    ``d+1`` (209 de 295 dentro de 1 ponto percentual, contra 126 e 133). Quem
    monta ``ex_div_dates`` deve usar a data do evento, não a vizinha.
    """
    if window < MIN_WINDOW:
        raise ValueError(
            f"janela mínima é {MIN_WINDOW} barras (σ² amostral precisa de N ≥ 2); "
            f"recebido window={window}"
        )

    bars = _sorted_bars(ohlc)
    if len(bars) < window:
        return VolResult(None, (INSUFFICIENT_HISTORY,), len(bars))

    logs = [_bar_logs(bar) for bar in bars]
    ex_dates = frozenset(ex_div_dates)
    needed = window - 1

    samples: list[tuple[date, _Logs, _Logs]] = []
    index = len(bars) - 1
    span_last = index
    span_first = index
    excluded = False
    while index >= 1 and len(samples) < needed:
        if bars[index].refdate in ex_dates:
            excluded = True
            index -= 1
            continue
        previous, current = logs[index - 1], logs[index]
        if previous is None or current is None:
            return VolResult(None, (INVALID_PRICE,), len(bars))
        if not samples:
            span_last = index
        samples.append((bars[index].refdate, previous, current))
        span_first = index
        index -= 1

    if len(samples) < needed:
        return VolResult(None, (INSUFFICIENT_HISTORY,), len(bars))

    observations = [
        _observation(day, previous, current) for day, previous, current in reversed(samples)
    ]

    variance = _yang_zhang_variance(observations)
    if variance < -VARIANCE_EPSILON:
        return VolResult(None, (NEGATIVE_VARIANCE,), len(bars))
    variance = max(variance, 0.0)

    value = math.sqrt(variance * SESSIONS_PER_YEAR) * 100.0
    flags = (EX_DIV_EXCLUDED,) if excluded else ()
    return VolResult(value, flags, span_last - span_first + 2)


def flag_unexplained(
    returns: Sequence[tuple[date, float]],
    event_dates: Collection[date],
    *,
    threshold: float = DEFAULT_GAP_THRESHOLD,
) -> list[date]:
    """Datas de retorno overnight grande que **nenhum evento** explica.

    ``returns`` é ``(data_da_sessão, retorno_overnight)`` — ``r_d = ln(O_d/C_{d−1})``,
    o mesmo ``o_i`` do Yang-Zhang (log, não simples). ``event_dates`` são as datas
    dos eventos de strike do B2, **provento e escala**: ``unexplained`` é sobre o
    gap, não sobre a classe (o B2 já decidiu a classe; aqui não se reclassifica).

    Casamento **exato** de data. A data do evento do B2 é a primeira sessão com
    mudança contígua de strike e é ela que carrega o gap — medido no lake de 2026
    (22/11/2026): o overnight do dia do evento bate com o gap implícito no strike
    com desvio médio de 0,81%, contra 1,59% em ``d−1`` e 1,77% em ``d+1``; a
    assimetria esperada com mudança não contígua some justamente porque o B2 só
    aceita série que negociou no pregão anterior (AGENTS.md, armadilha 3). Aceitar
    ``d−1`` esconderia um gap real na véspera de um evento — por isso não.

    Devolve as datas em ordem crescente, sem repetição. ``|r| > threshold``
    estrito (a spec diz "> 40%"); quem transforma isto em flag de ``health`` é o
    C1 — aqui é só a função pura.

    Cuidado medido (2026, mercado `010`): dos 428 overnight com ``|r| > 40%`` em
    312 tickers, só 3 têm evento do B2 no dia (SIMH3 ×2, SBSP3 ×0,2, ORVR3
    indeterminado); 67 têm fechamento anterior ≤ 10 centavos e caem na definição
    por um tick (``2 ¢ → 3 ¢`` = +40,5%), e o resto é majoritariamente
    desdobramento de BDR/unit sem opção — ou preço furado de papel ilíquido. O
    limiar sozinho vira ruído; o C1 decide o piso de preço, aqui não se inventa um.
    """
    events = frozenset(event_dates)
    flagged = {
        day for day, value in returns if abs(value) > threshold and day not in events
    }
    return sorted(flagged)


SHOCK_SHARE_MIN = 0.5
"""Fração da variância da janela vinda de **um** pregão a partir da qual o painel
avisa (`rv_shock_day`). Com retornos normais i.i.d. em 29 pregões o maior dia
responde, em média, por ~20%; metade é cauda extrema. É aviso de leitura, não
limiar de sinal (ADR-035)."""

SHOCK_MIN_RETURNS = 10
"""Abaixo disto a "fração do maior dia" é ruído de amostra: sai `None`."""


@dataclass(frozen=True, slots=True)
class DominantDay:
    """O pregão que mais pesa na variância realizada da janela."""

    refdate: date | None
    """Pregão do maior retorno² (fechamento-a-fechamento); `None` sem amostra."""
    share: float | None
    """`r²_max / Σr²` em fração (0–1), momento **não centrado**; `None` sem amostra."""
    n: int
    """Retornos usados (barras da janela − 1, menos datas ex de provento)."""


def dominant_day(
    ohlc: Sequence[OhlcBar],
    window: int,
    *,
    ex_div_dates: Collection[date] = frozenset(),
) -> DominantDay:
    """Quanto da variância das **últimas** ``window`` barras vem de um só pregão.

    Usa retornos **fechamento-a-fechamento** com o mesmo ``adj_factor`` de escala
    da RV e sem as datas ex de provento (mesma exclusão do Yang-Zhang, sem puxar
    barra para trás): a pergunta é "um movimento de preço domina a janela?", e
    não depende de como o Yang-Zhang decompõe abertura e intradiário.
    Momento não centrado (`Σr²`): a média diária é desprezível na janela e
    centrar faria a fração depender do sinal dos outros dias.
    Medido em 19/11/2026 (ADR-035): B3SA3 com 26/10 respondendo por ~0,9 da
    variância de 30 pregões.
    """
    bars = _sorted_bars(ohlc)[-window:]
    returns: list[tuple[date, float]] = []
    for previous, current in zip(bars, bars[1:], strict=False):
        if current.refdate in ex_div_dates:
            continue
        before = previous.close * previous.adj_factor
        after = current.close * current.adj_factor
        if before <= 0 or after <= 0:
            continue
        returns.append((current.refdate, math.log(after / before)))
    if len(returns) < SHOCK_MIN_RETURNS:
        return DominantDay(None, None, len(returns))
    total = math.fsum(value * value for _, value in returns)
    if total <= VARIANCE_EPSILON:
        return DominantDay(None, None, len(returns))
    day, value = max(returns, key=lambda item: item[1] * item[1])
    return DominantDay(day, value * value / total, len(returns))


def _sorted_bars(ohlc: Sequence[OhlcBar]) -> list[OhlcBar]:
    """Barras em ordem cronológica, uma por sessão (duplicata: a última vence)."""
    ordered = sorted(ohlc, key=lambda bar: bar.refdate)
    if len(ordered) < 2:
        return ordered
    deduped = [ordered[0]]
    for bar in ordered[1:]:
        if bar.refdate == deduped[-1].refdate:
            deduped[-1] = bar
        else:
            deduped.append(bar)
    return deduped


def _bar_logs(bar: OhlcBar) -> _Logs | None:
    """``(ln O, ln H, ln L, ln C)`` da barra ajustada; ``None`` se preço ≤ 0."""
    factor = bar.adj_factor
    prices = (bar.open * factor, bar.high * factor, bar.low * factor, bar.close * factor)
    if any(price <= 0 for price in prices):
        return None
    return (math.log(prices[0]), math.log(prices[1]), math.log(prices[2]), math.log(prices[3]))


def _observation(refdate: date, previous: _Logs, current: _Logs) -> _Observation:
    """Retornos da barra contra o fechamento anterior, já em log."""
    open_i, high_i, low_i, close_i = current
    close_prev = previous[3]
    return _Observation(
        refdate=refdate,
        overnight=open_i - close_prev,
        intraday=close_i - open_i,
        high_log=high_i - close_prev,
        low_log=low_i - close_prev,
        close_log=close_i - close_prev,
    )


def _yang_zhang_variance(observations: Sequence[_Observation]) -> float:
    """σ²_YZ diária (não anualizada) — equação (5) do paper."""
    overnight = [obs.overnight for obs in observations]
    intraday = [obs.intraday for obs in observations]
    n = len(observations)
    k = 0.34 / (1.34 + (n + 1) / (n - 1))
    variance_o = _sample_variance(overnight)
    variance_c = _sample_variance(intraday)
    variance_rs = math.fsum(
        (obs.high_log - obs.close_log) * (obs.high_log - obs.overnight)
        + (obs.low_log - obs.close_log) * (obs.low_log - obs.overnight)
        for obs in observations
    ) / n
    return variance_o + k * variance_c + (1.0 - k) * variance_rs


def _mean(values: Sequence[float]) -> float:
    """Média com ``fsum`` (a janela é curta, mas somar em ordem é grátis)."""
    return math.fsum(values) / len(values)


def _sample_variance(values: Sequence[float]) -> float:
    """Variância amostral (divisor ``n − 1``), como no paper."""
    mean = _mean(values)
    return math.fsum((value - mean) ** 2 for value in values) / (len(values) - 1)
