"""fsutil: replace_file com o fallback EACCES do bind do Windows."""

import os
from pathlib import Path

import pytest

from b3o.fsutil import replace_file, write_json_atomic


def test_replace_para_destino_novo_e_atomico(tmp_path: Path) -> None:
    src = tmp_path / "a.json"
    dst = tmp_path / "b.json"
    src.write_text("x", encoding="utf-8")
    replace_file(src, dst)
    assert dst.read_text(encoding="utf-8") == "x"
    assert not src.exists()


def test_replace_com_fallback_eacc_es(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """dst existe e os.replace recusa (EACCES): unlink + rename — sem silêncio."""
    src = tmp_path / "a.json"
    dst = tmp_path / "b.json"
    src.write_text("novo", encoding="utf-8")
    dst.write_text("velho", encoding="utf-8")

    real_replace = os.replace

    def replace_eacc_es(a: object, b: object) -> None:
        if Path(str(b)) == dst and dst.exists():
            raise PermissionError(13, "Permission denied (bind)")
        real_replace(a, b)

    monkeypatch.setattr(os, "replace", replace_eacc_es)
    replace_file(src, dst)
    assert dst.read_text(encoding="utf-8") == "novo"
    assert not src.exists()


def test_erro_de_so_diferente_propaga(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Erro que não é EACCES propaga — falha explícita, não fallback às cegas."""
    src = tmp_path / "a.json"
    dst = tmp_path / "b.json"
    src.write_text("novo", encoding="utf-8")
    dst.write_text("velho", encoding="utf-8")

    def replace_isaderr(a: object, b: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", replace_isaderr)
    with pytest.raises(OSError, match="No space"):
        replace_file(src, dst)
    # dst intacto: nada foi apagado
    assert dst.read_text(encoding="utf-8") == "velho"


def test_write_json_atomic_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    write_json_atomic(path, '{"k": 1}')
    write_json_atomic(path, '{"k": 2}')  # reescrita (segundo prepare)
    assert path.read_text(encoding="utf-8") == '{"k": 2}'
    assert not list(tmp_path.glob("*.tmp"))
