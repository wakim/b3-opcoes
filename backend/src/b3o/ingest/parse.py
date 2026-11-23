"""Parser streaming do COTAHIST (B3): latin-1, 245 bytes por linha, preços em centavos.

Regras que este arquivo respeita (AGENTS.md + `docs/contratos/onda-a.md`):

- o arquivo chega perto de 3,5 M de linhas: lê-se **linha a linha**, nunca inteiro em
  memória (o runtime tem 3,7 GiB);
- preços, strike e volume são **inteiros em centavos** (a fonte tem 2 decimais implícitos);
  nada é arredondado aqui, e nada é convertido para float;
- o ticker é campo de largura fixa e pode vir com espaços: `strip()` sempre;
- o `isin` é do **ativo-objeto**, não da série — a chave da série é `(ticker, maturity)`;
- o arquivo **não está ordenado por data**: nada aqui assume ordem;
- o estilo da opção vem do **1º token do nome resumido** (`PETRE` × `PETR`), nunca do ticker;
- linha estruturalmente impossível de mercado que a gente guarda falha de forma explícita
  (`CotahistParseError`, com o `SourceError` dentro), nunca é silenciada: a validação
  global (`validate.py`) roda antes; aqui é a rede de segurança. Linha de mercado fora do
  universo é descartada — o arquivo de 2026 tem 1 linha de 246 bytes (mercado `017`), e
  ela não pode derrubar a ingestão.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from b3o.ingest.errors import SourceError

LINE_WIDTH = 245
"""Largura de TODA linha do COTAHIST (00, 01 e 99)."""

SPOT_MARKETS = frozenset({"010", "020"})
OPTION_MARKETS = frozenset({"070", "080"})
EXERCISE_MARKETS = frozenset({"012", "013"})
KEPT_MARKETS = SPOT_MARKETS | OPTION_MARKETS | EXERCISE_MARKETS
"""`010` à vista, `020` fracionário, `070` call, `080` put, `012`/`013` exercício."""

SIDE_BY_MARKET = {"070": "C", "080": "P"}

RECORD_HEADER = "00"
RECORD_DATA = "01"
RECORD_TRAILER = "99"

NO_MATURITY = "99991231"
"""Vencimento das linhas que não são opção."""

ENCODING = "latin-1"

# Offsets validados (0 violações em 16.134 linhas de 19/11/2026 — contrato §3).
# Não usar [49:52] (lixo variável); a moeda `R$` mora em [52:54].
_REFDATE = slice(2, 10)
_BDI = slice(10, 12)
_TICKER = slice(12, 24)
_TPMERC = slice(24, 27)
_CORP_NAME = slice(27, 39)
_SPEC = slice(39, 49)
_OPEN = slice(56, 69)
_HIGH = slice(69, 82)
_LOW = slice(82, 95)
_AVG = slice(95, 108)
_CLOSE = slice(108, 121)
_BEST_BID = slice(121, 134)
_BEST_ASK = slice(134, 147)
_TRADES = slice(147, 152)
_QTY = slice(152, 170)
_VOLUME = slice(170, 188)
_STRIKE = slice(188, 201)
_STRIKE_ADJ_IND = slice(201, 202)
_MATURITY = slice(202, 210)
_QUOTE_FACTOR = slice(210, 217)
_STRIKE_PTS = slice(217, 230)
_ISIN = slice(230, 242)
_DISTRIBUTION_ID = slice(242, 245)

_MARKET_WIDTH = _TPMERC.stop - _TPMERC.start

WEEKLY = re.compile(r"(?<=\d)W\d+$")
"""Marcador semanal: `W` só é semanal **depois de dígito** (`ABEVA140W1`).

`W` antes de dígito é código de mês de put (`BOVAW170` é put de novembro).
"""

_HINT_TORTA = (
    "linha torta: rode a validação do COTAHIST (validate.py) antes do parse; "
    "largura/numérico errado indica download truncado ou layout novo da B3"
)


class CotahistParseError(Exception):
    """Falha de parse que carrega o erro compartilhado (`SourceError`) em `.error`.

    `SourceError` (`b3o.ingest.errors`) é um dataclass de valor — `frozen`, com
    `code/message/hint` e `to_dict()` —, **não** uma exceção, e não pode ser levantado.
    Para falhar de forma explícita sem recriar nem editar o erro compartilhado, o parser
    levanta esta exceção com o `SourceError` dentro; `code`, `message` e `hint` também
    aparecem no próprio `str()` do erro, para o log não perder a dica.
    """

    def __init__(self, error: SourceError) -> None:
        super().__init__(f"{error.message} (hint: {error.hint})")
        self.error = error

    @property
    def code(self) -> str:
        """Código do erro (`bad_data`, `source_unavailable`)."""
        return self.error.code

    @property
    def message(self) -> str:
        """O que aconteceu, em uma linha."""
        return self.error.message

    @property
    def hint(self) -> str:
        """O que fazer a respeito."""
        return self.error.hint


@dataclass(frozen=True, slots=True)
class CotahistRow:
    """Uma linha `01` do COTAHIST já tipada. Preços e volumes em centavos (int64)."""

    refdate: date
    bdi: str
    ticker: str  # strip() sempre (campo right-aligned)
    tpmerc: str
    corp_name: str  # strip(); o 1º token define o estilo da opção
    spec: str  # strip()
    open: int  # centavos
    high: int  # centavos
    low: int  # centavos
    avg: int  # centavos
    close: int  # centavos
    best_bid: int  # centavos
    best_ask: int  # centavos
    trades: int
    qty: int  # unidades negociadas
    volume: int  # centavos; JÁ vem ÷FATCOT na fonte, guardar como está
    strike: int  # centavos; 0 quando não é opção
    strike_adj_ind: str  # 1 char, como está na fonte
    maturity: date | None  # None quando "99991231"
    quote_factor: int  # FATCOT: preço por ação = preço ÷ fator
    strike_pts: int
    isin_underlying: str  # ISIN do ATIVO-OBJETO, nunca da série
    distribution_id: str
    style: str | None  # "A" | "E" | None (= style_unknown)
    side: str | None  # "C" (070) | "P" (080) | None


def _series_root(ticker: str) -> str | None:
    """Raiz da série: ticker sem o marcador semanal, sem os dígitos finais e sem o mês."""
    t = WEEKLY.sub("", ticker)
    letters = t.rstrip("0123456789")
    if len(letters) > 1:
        return letters[:-1]
    return letters or None


def style_of(ticker: str, corp_name: str) -> str | None:
    """Estilo da opção medido no nome resumido: `"E"`, `"A"` ou `None` (style_unknown).

    `corp_name` (l[27:39]) traz a raiz do ticker + `E` para europeia (`PETRE`) e a raiz
    pura para americana (`PETR`). O ticker sozinho classifica errado: puts europeias usam
    a raiz da call (`PETRV352` com token `PETRE`). Regra validada no arquivo de 2026
    inteiro (3.060.453 linhas de opção) — não "melhorar" sem medir de novo.
    """
    parts = corp_name.split()
    token = parts[0] if parts else ""
    root = _series_root(ticker)
    if not token or not root or not token.startswith(root):
        return None
    if len(token) > len(root) + 2:
        return None
    return "E" if token.endswith("E") else "A"


def _number(line: str, field: slice, name: str) -> int:
    """Campo numérico inteiro (zero-padded na fonte) → int. Torto vira erro explícito."""
    raw = line[field].strip()
    if not raw.isdigit():
        raise CotahistParseError(
            SourceError(
                code="bad_data",
                message=f"campo {name} não numérico: {line[field]!r}",
                hint=_HINT_TORTA,
            )
        )
    return int(raw)


def _day(line: str, field: slice, name: str) -> date:
    """Campo de data `AAAAMMDD` → `date`. Torto vira erro explícito."""
    raw = line[field]
    try:
        return date(int(raw[0:4]), int(raw[4:6]), int(raw[6:8]))
    except (ValueError, TypeError) as exc:
        raise CotahistParseError(
            SourceError(
                code="bad_data",
                message=f"campo {name} não é data AAAAMMDD válida: {raw!r}",
                hint=_HINT_TORTA,
            )
        ) from exc


def parse_line(line: str) -> CotahistRow | None:
    """Interpreta uma linha do COTAHIST. `None` para header (`00`) e trailer (`99`).

    Levanta `CotahistParseError` (com `SourceError` dentro) em linha `01` estruturalmente
    impossível — largura diferente de 245, numérico torto, data inválida: é rede de
    segurança, não caminho normal (a validação global do A1 roda antes no `prepare`).
    """
    if len(line) != LINE_WIDTH:
        raise CotahistParseError(
            SourceError(
                code="bad_data",
                message=f"linha com {len(line)} bytes, esperado {LINE_WIDTH}: {line[:40]!r}",
                hint=_HINT_TORTA,
            )
        )

    record = line[0:2]
    if record in (RECORD_HEADER, RECORD_TRAILER):
        return None
    if record != RECORD_DATA:
        raise CotahistParseError(
            SourceError(
                code="bad_data",
                message=f"tipo de registro desconhecido: {record!r}",
                hint=_HINT_TORTA,
            )
        )

    tpmerc = line[_TPMERC]
    maturity_raw = line[_MATURITY]

    return CotahistRow(
        refdate=_day(line, _REFDATE, "refdate"),
        bdi=line[_BDI],
        ticker=line[_TICKER].strip(),
        tpmerc=tpmerc,
        corp_name=line[_CORP_NAME].strip(),
        spec=line[_SPEC].strip(),
        open=_number(line, _OPEN, "open"),
        high=_number(line, _HIGH, "high"),
        low=_number(line, _LOW, "low"),
        avg=_number(line, _AVG, "avg"),
        close=_number(line, _CLOSE, "close"),
        best_bid=_number(line, _BEST_BID, "best_bid"),
        best_ask=_number(line, _BEST_ASK, "best_ask"),
        trades=_number(line, _TRADES, "trades"),
        qty=_number(line, _QTY, "qty"),
        volume=_number(line, _VOLUME, "volume"),
        strike=_number(line, _STRIKE, "strike"),
        strike_adj_ind=line[_STRIKE_ADJ_IND],
        maturity=None if maturity_raw == NO_MATURITY else _day(line, _MATURITY, "maturity"),
        quote_factor=_number(line, _QUOTE_FACTOR, "quote_factor"),
        strike_pts=_number(line, _STRIKE_PTS, "strike_pts"),
        isin_underlying=line[_ISIN],
        distribution_id=line[_DISTRIBUTION_ID],
        style=style_of(line[_TICKER].strip(), line[_CORP_NAME].strip()),
        side=SIDE_BY_MARKET.get(tpmerc),
    )


def _member_name(archive: zipfile.ZipFile, zip_path: Path) -> str:
    """Nome do membro de dados: o `.TXT` (o ZIP do COTAHIST tem membro único)."""
    members = [name for name in archive.namelist() if not name.endswith("/")]
    if not members:
        raise CotahistParseError(
            SourceError(
                code="bad_data",
                message=f"{zip_path.name} não tem membro de dados",
                hint="baixe o COTAHIST de novo (b3o prepare); o ZIP da B3 tem 1 arquivo .TXT",
            )
        )
    texts = [name for name in members if name.lower().endswith(".txt")]
    return (texts or members)[0]


def iter_cotahist(
    zip_path: Path,
    *,
    markets: frozenset[str] = KEPT_MARKETS,
) -> Iterator[CotahistRow]:
    """Gera as linhas `01` do ZIP do COTAHIST, uma por vez, filtrando por mercado.

    Streaming de verdade: o `TextIOWrapper` lê o membro em blocos e devolve linha a linha,
    então a memória não cresce com o arquivo. `markets` filtra por `tpmerc` (l[24:27]).

    Mercado fora do universo é descartado **antes** da checagem de largura: o arquivo de
    2026 tem exatamente 1 linha de 246 bytes (medido em 22/11/2026) — `FR0T5L`, mercado
    `017`, nome `FROTA OCEÂNICA` com um `Â` em UTF-8 (`C3 82`) no meio — e ela não pode
    derrubar a ingestão do universo. Já qualquer linha torta de mercado que a gente
    guarda (010/020/070/080/012/013) falha explícita, com o número da linha.
    """
    try:
        archive = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise CotahistParseError(
            SourceError(
                code="source_unavailable",
                message=f"não abriu o COTAHIST {zip_path.name}: {exc}",
                hint="confira o download e o cache em data/raw/ (b3o prepare)",
            )
        ) from exc

    with archive:
        member = _member_name(archive, zip_path)
        with archive.open(member) as binary:
            text = io.TextIOWrapper(binary, encoding=ENCODING, newline="")
            for line_no, raw in enumerate(text, start=1):
                line = raw.rstrip("\r\n")
                market = line[_TPMERC]
                # Só pula quando o campo de mercado está inteiro (3 chars): linha
                # truncada antes dele cai no parse e falha, não desaparece calada.
                if len(market) == _MARKET_WIDTH and market not in markets:
                    continue
                try:
                    row = parse_line(line)
                except CotahistParseError as exc:
                    # o número da linha é o que torna a falha acionável
                    raise CotahistParseError(
                        SourceError(
                            code=exc.code,
                            message=f"{member}:{line_no}: {exc.message}",
                            hint=exc.hint,
                        )
                    ) from exc
                if row is not None and row.tpmerc in markets:
                    yield row
