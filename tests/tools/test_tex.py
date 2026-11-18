import os
import shutil
from pathlib import Path

import pytest

from tar_lab.tools import tex


def _exe(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return path


@pytest.fixture
def no_engines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """No TeX anywhere: empty PATH, no local installs, no overrides."""
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.delenv("TARLAB_TEXLIVE_BIN", raising=False)
    monkeypatch.delenv("TARLAB_TECTONIC", raising=False)
    monkeypatch.setattr(tex, "LOCAL_TEXLIVE", tmp_path / "texlive")
    monkeypatch.setattr(tex, "LOCAL_TECTONIC", tmp_path / "tectonic" / "bin" / "tectonic")
    return empty


def _texlive(root: Path, year: str) -> Path:
    bin_dir = root / year / "bin" / "x86_64-linux"
    _exe(bin_dir / "latexmk")
    _exe(bin_dir / "pdflatex")
    return bin_dir


def test_local_texlive_is_preferred_and_newest_year_wins(
        tmp_path: Path, no_engines: Path) -> None:
    _exe(tmp_path / "tectonic" / "bin" / "tectonic")
    _exe(no_engines / "latexmk")
    _exe(no_engines / "pdflatex")
    _texlive(tmp_path / "texlive", "2025")
    newest = _texlive(tmp_path / "texlive", "2026")
    engine = tex.select_engine("main.tex")
    assert engine.name == "texlive"
    assert engine.bin_dir == newest
    assert engine.command == [str(newest / "latexmk"), "-pdf", "-interaction=nonstopmode",
                              "-halt-on-error", "-shell-restricted", "-file-line-error",
                              "main.tex"]


def test_texlive_bin_override(tmp_path: Path, no_engines: Path,
                              monkeypatch: pytest.MonkeyPatch) -> None:
    _texlive(tmp_path / "texlive", "2026")
    custom = _texlive(tmp_path / "custom", "2024")
    monkeypatch.setenv("TARLAB_TEXLIVE_BIN", str(custom))
    assert tex.select_engine("main.tex").bin_dir == custom


def test_path_texlive_then_tectonic(tmp_path: Path, no_engines: Path) -> None:
    tectonic = _exe(tmp_path / "tectonic" / "bin" / "tectonic")
    assert tex.select_engine("main.tex").name == "tectonic"
    assert tex.build_command("main.tex") == [str(tectonic), "-X", "compile", "--keep-logs",
                                             "main.tex"]
    _exe(no_engines / "pdflatex")
    assert tex.select_engine("main.tex").name == "pdflatex-path"
    _exe(no_engines / "latexmk")
    engine = tex.select_engine("main.tex")
    assert engine.name == "texlive-path"
    assert engine.command[0] == str(no_engines / "latexmk")


def test_no_engine_build_fails_cleanly(tmp_path: Path, no_engines: Path) -> None:
    with pytest.raises(FileNotFoundError):
        tex.select_engine("main.tex")
    paper = tmp_path / "paper"
    paper.mkdir()
    result = tex.build(paper)
    assert result.pdf is None and result.returncode == 127
    assert "no TeX engine found" in result.log.read_text()


def test_build_log_names_engine(tmp_path: Path, no_engines: Path,
                                monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = tmp_path / "fake-tl" / "2026" / "bin" / "x86_64-linux"
    _exe(bin_dir / "pdflatex")
    latexmk = bin_dir / "latexmk"
    latexmk.parent.mkdir(parents=True, exist_ok=True)
    # Fake latexmk: checks pdflatex is on PATH, then writes the PDF.
    latexmk.write_text('#!/bin/sh\ncommand -v pdflatex >/dev/null || exit 3\n'
                       'echo "max_print_line=$max_print_line"\ntouch main.pdf\n')
    latexmk.chmod(0o755)
    monkeypatch.setenv("TARLAB_TEXLIVE_BIN", str(bin_dir))
    monkeypatch.setenv("PATH", f"{no_engines}{os.pathsep}/usr/bin{os.pathsep}/bin")
    paper = tmp_path / "paper"
    paper.mkdir()
    result = tex.build(paper)
    log = result.log.read_text()
    assert result.pdf == paper / "main.pdf" and result.engine == "texlive"
    assert log.startswith("[tarlab] engine: texlive")
    assert "max_print_line=10000" in log


needs_texlive = pytest.mark.skipif(
    tex.find_texlive_bin() is None and shutil.which("latexmk") is None,
    reason="needs a TeX Live install")


@needs_texlive
def test_real_build(tmp_path: Path) -> None:
    (tmp_path / "main.tex").write_text(
        "\\documentclass{article}\\begin{document}Hello.\\end{document}\n")
    result = tex.build(tmp_path)
    assert result.pdf is not None, result.log.read_text()
    assert result.engine.startswith("texlive")
