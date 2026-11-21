import tarfile
from pathlib import Path

import pytest

from tar_lab.cli import main
from tar_lab.tools import arxiv as ax
from tar_lab.tools.paper_check import FAIL, PASS, WARN
from tar_lab.tools.tex import find_texlive_bin


def test_recorded_inputs_and_package_files(tmp_path: Path) -> None:
    (tmp_path / "figs").mkdir()
    for name in ("main.tex", "figs/a.pdf", "figs/b-eps-converted-to.pdf", "figs/b.eps",
                 "main.aux", "main.bbl", "refs.bib", "unused.txt", "my.sty"):
        (tmp_path / name).write_text("x")
    (tmp_path / "main.fls").write_text(
        f"PWD {tmp_path}\nINPUT /usr/share/texmf/tex/latex/base/article.cls\n"
        f"INPUT ./main.tex\nINPUT {tmp_path}/figs/a.pdf\nINPUT figs/b-eps-converted-to.pdf\n"
        "INPUT ./main.aux\nINPUT ./my.sty\nINPUT ./main.bbl\nOUTPUT main.pdf\n")
    assert ax.recorded_inputs(tmp_path, "main.tex") == [
        "main.tex", "figs/a.pdf", "figs/b-eps-converted-to.pdf", "main.aux", "my.sty",
        "main.bbl"]
    files = ax.package_files(tmp_path, "main.tex", [tmp_path / "refs.bib"])
    assert files == ["figs/a.pdf", "figs/b-eps-converted-to.pdf", "figs/b.eps", "main.bbl",
                     "main.tex", "my.sty", "refs.bib"]
    pkg = tmp_path / "pkg"
    assert ax.make_package(tmp_path, files, pkg) == len(files)
    out = tmp_path / "a.tar.gz"
    ax.write_tarball(pkg, files, out)
    first = out.read_bytes()
    ax.write_tarball(pkg, files, out)
    assert out.read_bytes() == first  # deterministic
    with tarfile.open(out) as tar:
        assert tar.getnames() == files


def test_rule_check(tmp_path: Path) -> None:
    good = "\\usepackage{hyperref}\\usepackage{hyperxmp}\\input{sec/intro}"
    assert ax.rule_check(tmp_path, good, ["main.tex"], 10, []).status == PASS
    bad = ("\\usepackage{hyperxmp}\\usepackage{hyperref}\\usepackage{minted}"
           "\\input{/home/me/x}\\includegraphics{../fig.pdf}")
    check = ax.rule_check(tmp_path, bad, ["main.tex"], ax.MAX_PACKAGE_BYTES + 1, [])
    assert check.status == FAIL
    joined = "\n".join(check.details)
    for part in ("absolute path '/home/me/x'", "leaves the paper directory",
                 "hyperxmp is loaded before hyperref", "upload the _minted directory",
                 "over arXiv's 50 MiB limit"):
        assert part in joined
    warn = ("\\usepackage{cleveref}\\newtheorem{thm}{Theorem}\\doublespace")
    check = ax.rule_check(tmp_path, warn, ["main.tex", ".hidden/x.pdf", "fig 1.pdf"], 10,
                          ["notes.md"])
    assert check.status == WARN
    joined = "\n".join(check.details)
    for part in ("every \\cref gets the same name", "\\doublespacing", "hidden files",
                 "fig 1.pdf: use only", "left out (not read by the build): notes.md"):
        assert part in joined
    assert ax.rule_check(tmp_path, "\\begin{doublespace}x\\end{doublespace}", [], 1,
                         []).status == PASS


def test_find_arxiv_texlive_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TARLAB_ARXIV_TEXLIVE_BIN", str(tmp_path))
    assert ax.find_arxiv_texlive_bin() is None
    (tmp_path / "pdflatex").write_text("")
    assert ax.find_arxiv_texlive_bin() == tmp_path


@pytest.mark.skipif(find_texlive_bin() is None, reason="needs TeX Live")
def test_real_arxiv_package(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    paper = tmp_path / "paper"
    (paper / "figs").mkdir(parents=True)
    (paper / "main.tex").write_text(
        "\\documentclass{article}\n\\usepackage{graphicx}\n\\begin{document}\nHello "
        "\\cite{k}.\n\\input{figs/part}\n\\bibliographystyle{plain}\n\\bibliography{refs}\n"
        "\\end{document}\n")
    (paper / "figs" / "part.tex").write_text("Part.\n")
    (paper / "refs.bib").write_text("@misc{k, title={T}, year={2024}, author={A}}\n")
    (paper / "notes.md").write_text("private notes\n")
    out = tmp_path / "reports" / "arxiv.tar.gz"
    assert main(["tex", "arxiv", str(paper), "-o", str(out)]) == 0
    text = capsys.readouterr().out
    assert "1 unused file(s) in paper/ left out" in text
    with tarfile.open(out) as tar:
        names = tar.getnames()
        main_tex = tar.extractfile("main.tex")
        assert main_tex is not None and b"TARLAB-LAYOUT" not in main_tex.read()
    assert names == ["figs/part.tex", "main.bbl", "main.tex", "refs.bib"]
