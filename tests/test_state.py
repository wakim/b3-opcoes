"""Testes do `ingest_state` (F0.5, contrato Onda B §2).

Cobrem: round-trip save/load, JSON válido e ordenado, update incremental que preserva o que
não foi tocado, escrita atômica, estado default sem arquivo e falha explícita (JSON corrompido,
chave desconhecida, tipo errado, `updated_at` sem offset, contagem negativa).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from b3o.ingest.errors import SourceFailure
from b3o.ingest.validate import BadLine, ValidationReport
from b3o.store.state import (
    MODEL_VERSION,
    IngestState,
    SkippedLines,
    ingest_state_path,
    load_ingest_state,
    save_ingest_state,
    skipped_lines_from_report,
    update_ingest_state,
)

BRT = timezone(timedelta(hours=-3))
T1 = datetime(2026, 11, 22, 18, 30, tzinfo=BRT)
T2 = datetime(2026, 11, 23, 7, 5, tzinfo=UTC)
D18 = date(2026, 11, 18)
D19 = date(2026, 11, 19)
SHA = "a" * 64


def _state(**overrides: object) -> IngestState:
    base: dict[str, object] = {
        "etag": '"hash:0"',
        "last_modified": "Thu, 20 Nov 2026 08:12:00 GMT",
        "sha256": SHA,
        "trailer_total": 3_570_794,
        "last_session": D19,
        "counts_by_date": {D18: 16_100, D19: 16_134},
        "model_version": MODEL_VERSION,
        "skipped_lines": SkippedLines(width=1, numeric=0),
        "updated_at": T1,
    }
    base.update(overrides)
    return IngestState(**base)  # type: ignore[arg-type]


def _tmp_leftovers(data_dir: Path) -> list[Path]:
    tmp_dir = data_dir / "tmp"
    if not tmp_dir.exists():
        return []
    return sorted(path for path in tmp_dir.rglob("*") if path.is_file())


def test_round_trip_save_load(tmp_path: Path) -> None:
    state = _state()
    path = save_ingest_state(tmp_path, state)
    assert path == ingest_state_path(tmp_path)
    assert path == tmp_path / "ingest_state.json"

    loaded = load_ingest_state(tmp_path)
    assert loaded == state
    assert loaded.updated_at == T1
    assert loaded.last_session == D19
    assert loaded.counts_by_date == {D18: 16_100, D19: 16_134}
    assert loaded.model_version == 1
    assert _tmp_leftovers(tmp_path) == []


def test_json_valido_ordenado_e_no_esquema_do_contrato(tmp_path: Path) -> None:
    path = save_ingest_state(tmp_path, _state())
    text = path.read_text(encoding="utf-8")
    raw = json.loads(text)

    assert list(raw) == sorted(raw)  # chaves ordenadas: diff legível, conteúdo determinístico
    assert text.endswith("\n")
    assert set(raw) == {
        "etag",
        "last_modified",
        "sha256",
        "trailer_total",
        "last_session",
        "counts_by_date",
        "model_version",
        "skipped_lines",
        "updated_at",
    }
    assert raw["counts_by_date"] == {"2026-11-18": 16_100, "2026-11-19": 16_134}
    assert raw["skipped_lines"] == {"numeric": 0, "width": 1}
    assert raw["last_session"] == "2026-11-19"
    assert raw["updated_at"] == "2026-11-22T18:30:00-03:00"
    assert raw["model_version"] == 1


def test_sem_arquivo_devolve_estado_default(tmp_path: Path) -> None:
    state = load_ingest_state(tmp_path)
    assert state == IngestState()
    assert state.etag is None
    assert state.last_session is None
    assert state.counts_by_date == {}
    assert state.model_version == MODEL_VERSION == 1
    assert state.skipped_lines == SkippedLines()
    assert state.updated_at is None


def test_escrita_atomica_substitui_sem_deixar_tmp(tmp_path: Path) -> None:
    save_ingest_state(tmp_path, _state())
    before = ingest_state_path(tmp_path).read_text(encoding="utf-8")

    save_ingest_state(tmp_path, _state(etag='"outro"', updated_at=T2))

    assert _tmp_leftovers(tmp_path) == []
    raw = json.loads(ingest_state_path(tmp_path).read_text(encoding="utf-8"))
    assert raw["etag"] == '"outro"'
    assert raw["updated_at"] == "2026-11-23T07:05:00+00:00"
    assert before != ingest_state_path(tmp_path).read_text(encoding="utf-8")


def test_segunda_gravacao_sobrevive_ao_eacces_do_bind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No bind do Windows, replace sobre arquivo existente dá EACCES (medido 22/11/2026).

    `save_ingest_state` publica via `b3o.fsutil.replace_file`; o teste simula a recusa do bind
    e prova que o segundo `prepare` atualiza o estado (unlink + rename) sem deixar tmp.
    """
    save_ingest_state(tmp_path, _state())
    real_replace = os.replace

    def bind_replace(src: object, dst: object, *args: object, **kwargs: object) -> None:
        if Path(str(dst)).exists():
            raise PermissionError(13, "simulando bind do Windows", str(dst))
        real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", bind_replace)
    save_ingest_state(tmp_path, _state(etag='"outro"', updated_at=T2))

    assert load_ingest_state(tmp_path).etag == '"outro"'
    assert _tmp_leftovers(tmp_path) == []


def test_update_incremental_preserva_chaves_existentes(tmp_path: Path) -> None:
    save_ingest_state(tmp_path, _state())

    updated = update_ingest_state(tmp_path, updated_at=T2, counts_by_date={date(2026, 11, 20): 77})

    assert updated.etag == '"hash:0"'  # não passado → preservado
    assert updated.sha256 == SHA
    assert updated.last_modified == "Thu, 20 Nov 2026 08:12:00 GMT"
    assert updated.trailer_total == 3_570_794
    assert updated.model_version == 1
    assert updated.last_session == D19
    assert updated.updated_at == T2
    # contagens mesclam por data: reparsar 2026 não apaga o dia anterior
    assert updated.counts_by_date == {D18: 16_100, D19: 16_134, date(2026, 11, 20): 77}
    assert load_ingest_state(tmp_path) == updated


def test_update_troca_contagens_com_replace_counts(tmp_path: Path) -> None:
    save_ingest_state(tmp_path, _state())
    updated = update_ingest_state(
        tmp_path,
        updated_at=T2,
        counts_by_date={D19: 1},
        replace_counts=True,
    )
    assert updated.counts_by_date == {D19: 1}


def test_update_mescla_skipped_lines_parcial(tmp_path: Path) -> None:
    save_ingest_state(tmp_path, _state(skipped_lines=SkippedLines(width=1, numeric=2)))

    updated = update_ingest_state(tmp_path, updated_at=T2, skipped_lines={"width": 3})
    assert updated.skipped_lines == SkippedLines(width=3, numeric=2)

    replaced = update_ingest_state(tmp_path, updated_at=T2, skipped_lines=SkippedLines())
    assert replaced.skipped_lines == SkippedLines(width=0, numeric=0)


def test_update_limpa_campo_com_none(tmp_path: Path) -> None:
    save_ingest_state(tmp_path, _state())
    updated = update_ingest_state(tmp_path, updated_at=T2, etag=None)
    assert updated.etag is None
    assert load_ingest_state(tmp_path).etag is None


def test_update_em_lake_novo_cria_o_arquivo(tmp_path: Path) -> None:
    subdir = tmp_path / "lake"
    state = update_ingest_state(subdir, updated_at=T1, last_session=D19, sha256=SHA)
    assert state.etag is None
    assert state.model_version == 1
    assert load_ingest_state(subdir) == state


def test_json_corrompido_falha_explicito(tmp_path: Path) -> None:
    ingest_state_path(tmp_path).write_text("{não é json", encoding="utf-8")
    with pytest.raises(SourceFailure) as excinfo:
        load_ingest_state(tmp_path)
    assert excinfo.value.code == "bad_data"
    assert "ingest_state.json" in str(excinfo.value)


def test_chave_desconhecida_falha_explicito(tmp_path: Path) -> None:
    payload = _state().to_dict()
    payload["campo_novo"] = 1
    ingest_state_path(tmp_path).write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SourceFailure) as excinfo:
        load_ingest_state(tmp_path)
    assert excinfo.value.code == "bad_data"
    assert "campo_novo" in str(excinfo.value)


def test_contagem_negativa_no_json_falha_explicito(tmp_path: Path) -> None:
    payload = _state().to_dict()
    payload["skipped_lines"] = {"width": -1, "numeric": 0}
    ingest_state_path(tmp_path).write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(SourceFailure) as excinfo:
        load_ingest_state(tmp_path)
    assert excinfo.value.code == "bad_data"
    assert "width" in str(excinfo.value)


def test_updated_at_sem_offset_falha_explicito(tmp_path: Path) -> None:
    naive = datetime(2026, 11, 22, 18, 30)
    with pytest.raises(SourceFailure) as excinfo:
        save_ingest_state(tmp_path, _state(updated_at=naive))
    assert excinfo.value.code == "bad_data"
    assert "offset" in str(excinfo.value)
    assert not ingest_state_path(tmp_path).exists()
    assert _tmp_leftovers(tmp_path) == []


def test_sem_updated_at_falha_explicito(tmp_path: Path) -> None:
    with pytest.raises(SourceFailure) as excinfo:
        save_ingest_state(tmp_path, _state(updated_at=None))
    assert excinfo.value.code == "bad_data"
    assert "updated_at" in str(excinfo.value)


def test_estado_invalido_nao_toca_o_arquivo_bom(tmp_path: Path) -> None:
    save_ingest_state(tmp_path, _state())
    before = ingest_state_path(tmp_path).read_bytes()

    with pytest.raises(SourceFailure) as excinfo:
        save_ingest_state(tmp_path, _state(model_version=0, updated_at=T2))
    assert excinfo.value.code == "bad_data"
    assert ingest_state_path(tmp_path).read_bytes() == before
    assert _tmp_leftovers(tmp_path) == []


def test_data_dir_que_e_arquivo_falha_explicito(tmp_path: Path) -> None:
    file_path = tmp_path / "nao_e_diretorio"
    file_path.write_text("x", encoding="utf-8")

    with pytest.raises(SourceFailure) as excinfo:
        save_ingest_state(file_path, _state())
    assert excinfo.value.code == "bad_data"
    assert "não é diretório" in str(excinfo.value)

    with pytest.raises(SourceFailure) as excinfo:
        load_ingest_state(file_path)
    assert excinfo.value.code == "bad_data"


def test_skipped_lines_from_report_mapeia_o_validador() -> None:
    """ADR-007: o que a validação pulou vira contagem explícita, não silêncio."""
    report = ValidationReport(
        ok=True,
        zip_path=Path("COTAHIST_A2026.ZIP"),
        sha256=SHA,
        line_count=12,
        header_ok=True,
        trailer_total=10,
        count_match=True,
        bad_width=(BadLine(1, "width", "curta"), BadLine(2_087_780, "width", "246 bytes")),
        bad_numeric=(BadLine(7, "numeric", "open"),),
        errors=(),
    )
    assert skipped_lines_from_report(report) == SkippedLines(width=2, numeric=1)
