"""Convenções de tempo/desconto (F1.1) — referências independentes, nunca o próprio código.

Onde não há tabela publicada, o teste usa **propriedade matemática exata** (definição
de `ln DF`, linearidade, monotonicidade) e a calcula com `math` no próprio teste —
o que nunca é feito é comparar a função com ela mesma.
"""

from __future__ import annotations

import ast
import math
from datetime import date
from pathlib import Path

import pytest

from b3o.derive.conventions import (
    SESSIONS_PER_YEAR,
    TradingCalendar,
    business_days_between,
    discount_factor,
    log_df_interp,
    log_df_interp_or_none,
    norm_cdf,
    norm_pdf,
    year_fraction,
)

# Semana de 2026-11-16 (segunda) a 2026-11-20 (sexta) — datas reais de sessão do lake
# em 19/11/2026. O 20/11/2026 é feriado nacional e não tem sessão.
MONDAY = date(2026, 11, 16)
TUESDAY = date(2026, 11, 17)
WEDNESDAY = date(2026, 11, 18)
THURSDAY = date(2026, 11, 19)
FRIDAY = date(2026, 11, 20)


@pytest.fixture
def calendar() -> TradingCalendar:
    """Sessões com dois buracos de propósito: o feriado de 20/11 e o fim de semana."""
    return TradingCalendar([MONDAY, TUESDAY, WEDNESDAY, THURSDAY, date(2026, 11, 23)])


def test_calendar_deduplica_e_ordena() -> None:
    cal = TradingCalendar([THURSDAY, MONDAY, THURSDAY, TUESDAY])
    assert cal.sessions == (MONDAY, TUESDAY, THURSDAY)
    assert len(cal) == 3


def test_is_session(calendar: TradingCalendar) -> None:
    assert calendar.is_session(MONDAY)
    assert not calendar.is_session(FRIDAY)  # feriado: o dado é a verdade (P-C)
    assert not calendar.is_session(date(2026, 11, 21))  # sábado


def test_next_session(calendar: TradingCalendar) -> None:
    assert calendar.next_session(MONDAY) == TUESDAY
    assert calendar.next_session(THURSDAY) == date(2026, 11, 23)  # pula o feriado/fim de semana
    assert calendar.next_session(date(2026, 11, 23)) is None  # calendário acabou


def test_sessions_between_inclusivo(calendar: TradingCalendar) -> None:
    assert calendar.sessions_between(MONDAY, THURSDAY) == (
        MONDAY,
        TUESDAY,
        WEDNESDAY,
        THURSDAY,
    )
    assert calendar.sessions_between(FRIDAY, date(2026, 11, 22)) == ()


def test_business_days_convencao_do_contrato(calendar: TradingCalendar) -> None:
    """`T` conta as sessões depois de `refdate` e até o vencimento: vence hoje → 0."""
    assert business_days_between(calendar, MONDAY, MONDAY) == 0
    assert business_days_between(calendar, THURSDAY, THURSDAY) == 0
    assert business_days_between(calendar, THURSDAY, FRIDAY) == 0  # 20/11 não é sessão
    assert business_days_between(calendar, THURSDAY, date(2026, 11, 23)) == 1
    assert business_days_between(calendar, MONDAY, THURSDAY) == 3
    assert business_days_between(calendar, MONDAY, date(2026, 11, 23)) == 4


def test_business_days_ponta_fora_do_calendario(calendar: TradingCalendar) -> None:
    # sábado → segunda: só a segunda conta
    assert business_days_between(calendar, date(2026, 11, 15), MONDAY) == 1
    # segunda → terça de outra semana que não está no calendário: para no último conhecido
    assert business_days_between(calendar, MONDAY, date(2026, 12, 31)) == 4


def test_business_days_end_antes_de_start_levanta(calendar: TradingCalendar) -> None:
    with pytest.raises(ValueError, match="end < start"):
        business_days_between(calendar, THURSDAY, MONDAY)


def test_calendario_vazio() -> None:
    cal = TradingCalendar([])
    assert cal.next_session(MONDAY) is None
    assert cal.sessions_between(MONDAY, THURSDAY) == ()
    assert cal.business_days(MONDAY, THURSDAY) == 0


def test_year_fraction_exato() -> None:
    assert SESSIONS_PER_YEAR == 252
    assert year_fraction(0) == 0.0
    assert year_fraction(1) == 1 / 252
    assert year_fraction(252) == 1.0
    assert year_fraction(20) == 20 / 252
    with pytest.raises(ValueError, match="negativos"):
        year_fraction(-1)


def test_discount_factor_definicao() -> None:
    # DF = exp(−r/100 · t); r em % a.a. → 13,65% por 1 ano é exp(−0,1365).
    assert discount_factor(13.65, 1.0) == pytest.approx(math.exp(-0.1365), rel=1e-15)
    assert discount_factor(13.65, 0.0) == 1.0
    # base 252: 20 dias úteis são 20/252 de ano
    assert discount_factor(13.65, 20 / 252) == pytest.approx(
        math.exp(-0.1365 * 20 / 252), rel=1e-15
    )
    # juro negativo desconta para cima (DF > 1)
    assert discount_factor(-1.0, 1.0) > 1.0
    with pytest.raises(ValueError, match="t inválido"):
        discount_factor(10.0, -0.5)


CURVE = [(1, 13.65), (6, 13.65), (21, 14.00)]


def _log_df(du: int, rate_pct: float) -> float:
    """Definição do nó: ln DF = −r/100 · du/252 (calculada aqui, à mão)."""
    return -(rate_pct / 100.0) * (du / 252.0)


def test_log_df_nos_da_curva() -> None:
    for du, rate in CURVE:
        assert log_df_interp(du, CURVE) == pytest.approx(_log_df(du, rate), rel=1e-15)


def test_log_df_interpola_linear_em_du_no_log() -> None:
    """Entre dois nós, ln DF é a média ponderada pela distância em dias úteis."""
    between = _log_df(6, 13.65) + (13 - 6) / (21 - 6) * (_log_df(21, 14.00) - _log_df(6, 13.65))
    assert log_df_interp(13, CURVE) == pytest.approx(between, rel=1e-15)
    # e o DF correspondente é exp(ln DF) — a interpolação é no log, não na taxa
    assert math.exp(log_df_interp(13, CURVE)) == pytest.approx(math.exp(between), rel=1e-15)


def test_log_df_abaixo_do_primeiro_no_usa_o_primeiro() -> None:
    assert log_df_interp(0, CURVE) == pytest.approx(_log_df(1, 13.65), rel=1e-15)
    assert log_df_interp(-3, CURVE) == pytest.approx(_log_df(1, 13.65), rel=1e-15)


def test_log_df_acima_do_ultimo_no_nao_extrapola() -> None:
    with pytest.raises(ValueError, match="acima do último nó"):
        log_df_interp(22, CURVE)
    assert log_df_interp_or_none(22, CURVE) is None
    assert log_df_interp_or_none(21, CURVE) == pytest.approx(_log_df(21, 14.00), rel=1e-15)


def test_log_df_curva_invalida() -> None:
    with pytest.raises(ValueError, match="vazia"):
        log_df_interp(10, [])
    with pytest.raises(ValueError, match="du <= 0"):
        log_df_interp(10, [(0, 13.0), (21, 14.0)])
    with pytest.raises(ValueError, match="dois nós"):
        log_df_interp(10, [(1, 13.0), (1, 13.5)])
    assert log_df_interp(10, [(1, 13.0), (1, 13.0), (21, 14.0)]) == pytest.approx(
        _log_df(1, 13.0) + (10 - 1) / 20 * (_log_df(21, 14.0) - _log_df(1, 13.0)), rel=1e-15
    )


def test_monotonicidade_do_df() -> None:
    """Taxa positiva → DF decrescente em du (propriedade exata da exponencial)."""
    values = [math.exp(log_df_interp(du, CURVE)) for du in range(1, 22)]
    assert all(left > right for left, right in zip(values, values[1:], strict=False))


def test_norm_cdf_valores_de_tabela() -> None:
    """Tabela normal padrão (Abramowitz & Stegun, cap. 26; qualquer tabela publicada)."""
    assert norm_cdf(0.0) == 0.5
    assert norm_cdf(1.0) == pytest.approx(0.8413447460685429, abs=1e-12)
    assert norm_cdf(-1.0) == pytest.approx(0.15865525393145707, abs=1e-12)
    assert norm_cdf(1.96) == pytest.approx(0.9750021048517795, abs=1e-12)
    assert norm_cdf(2.0) == pytest.approx(0.9772498680518208, abs=1e-12)
    assert norm_cdf(0.1) == pytest.approx(0.5398278372770294, abs=1e-12)


def test_norm_pdf_valores_de_tabela() -> None:
    assert norm_pdf(0.0) == pytest.approx(0.3989422804014327, abs=1e-15)
    assert norm_pdf(1.0) == pytest.approx(0.24197072451914337, abs=1e-15)
    assert norm_pdf(-1.0) == pytest.approx(norm_pdf(1.0), rel=1e-15)  # paridade


DERIVE_DIR = Path(__file__).resolve().parents[1] / "src" / "b3o" / "derive"
OWN_MODULES = ("conventions.py", "forward.py", "iv.py", "greeks.py")
FORBIDDEN = {
    "pathlib",
    "os",
    "io",
    "duckdb",
    "pyarrow",
    "httpx",
    "sqlite3",
    "json",
    "csv",
    "requests",
    "scipy",
    "pandas",
}


def test_modulos_do_d1_sao_puros() -> None:
    """`derive/` recebe número e devolve número: zero I/O e zero dependência proibida."""
    offenders: list[str] = []
    for name in OWN_MODULES:
        tree = ast.parse((DERIVE_DIR / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [(node.module or "").split(".")[0]]
            else:
                continue
            if FORBIDDEN & set(modules):
                offenders.append(f"{name}: {modules}")
    assert not offenders, f"módulo de derive/ com I/O ou dependência proibida: {offenders}"
