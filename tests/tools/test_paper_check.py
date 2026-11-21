import json
import shutil
from pathlib import Path

import pytest

from tar_lab.cli import main
from tar_lab.tools import paper_check as pc
from tar_lab.tools.tex import TexBuild, find_texlive_bin

LOG = r"""This is pdfTeX, Version 3.141592653-2.6-1.40.27 (TeX Live 2026) (preloaded format=pdflatex)
LaTeX Font Warning: Font shape `OT1/cmr/bx/sc' undefined
(Font)              using `OT1/cmr/bx/n' instead on input line 12.
LaTeX Warning: Citation `smith2020' on page 1 undefined on input line 20.
LaTeX Warning: Citation `smith2020' on page 2 undefined on input line 30.
Package natbib Warning: Citation `doe21' on page 2 undefined on input line 31.
LaTeX Warning: Reference `fig:main' on page 1 undefined on input line 22.
Package pdftex.def Error: File `missing.png' not found: using draft setting.
Overfull \hbox (0.5pt too wide) in paragraph at lines 50--51
Overfull \hbox (12.34pt too wide) in paragraph at lines 60--62
./main.tex:70: Undefined control sequence.
! LaTeX Error: File `foo.sty' not found.
"""


def test_parse_log() -> None:
    found = pc.parse_log(LOG)
    assert found.undefined_citations == ["smith2020", "doe21"]
    assert found.undefined_references == ["fig:main"]
    assert found.missing_files == ["missing.png", "foo.sty"]
    assert found.overfull == ["12.34pt too wide in paragraph at lines 60--62"]
    assert found.errors == ["./main.tex:70: Undefined control sequence.",
                            "! LaTeX Error: File `foo.sty' not found."]
    assert found.font_warnings == [
        "Font shape `OT1/cmr/bx/sc' undefined using `OT1/cmr/bx/n' instead on input line 12."]
    statuses = {c.name: c.status for c in pc.log_checks(found)}
    assert statuses == {"latex errors": "FAIL", "citations/refs": "FAIL",
                        "missing files": "FAIL", "overfull boxes": "WARN",
                        "font substitution": "WARN"}
    assert all(c.status == "PASS" for c in pc.log_checks(pc.parse_log("Output written.\n")))


PDFFONTS = """\
name                                 type              encoding         emb sub uni object ID
------------------------------------ ----------------- ---------------- --- --- --- ---------
ABCDEF+NimbusRomNo9L-Regu            Type 1            Custom           yes yes no       8  0
GHIJKL+AVeryLongFontNameThatOverflowsTheNameColumn-Bold Type 1C   Custom  yes yes no  9  0
[none]                               Type 3            Custom           yes no  no      12  0
Helvetica                            Type 1            Standard         no  no  no      14  0
"""


def test_parse_pdffonts_and_font_check() -> None:
    fonts = pc.parse_pdffonts(PDFFONTS)
    assert [f.type for f in fonts] == ["Type 1", "Type 1C", "Type 3", "Type 1"]
    assert fonts[1].name.startswith("GHIJKL+AVeryLong") and fonts[1].embedded
    assert [f.embedded for f in fonts] == [True, True, True, False]
    check = pc.font_check(fonts)
    assert check.status == "FAIL"
    assert "Helvetica (Type 1) not embedded" in check.details
    assert "[none] is Type 3" in check.details
    ok = pc.font_check(fonts[:2])
    assert ok.status == "PASS" and "NimbusRomNo9L-Regu" in ok.summary
    assert pc.parse_pdffonts("") == []


def test_pages() -> None:
    assert pc.parse_pdfinfo_pages("Title: x\nPages:          9\n") == 9
    text = "Intro\f more\f\nREFERENCES\n[1] A.\f"
    assert pc.references_page(text) == 3
    assert pc.page_check(9, 8, text).status == "FAIL"
    assert pc.page_check(8, 8, text).details == [
        "references heading found on page 3 (best effort)"]
    assert pc.page_check(None, 8, None).status == "SKIP"


BIB = """\
@string{neurips = "NeurIPS"}
@article{smith2020,
  title = {A {Nested} Title, with comma},
  author = {Smith, A.},
  year = 2020,
}
@inproceedings(doe21,
  author = "Doe, J.", booktitle = neurips
)
@misc{online, title={X}, date={2024-01-01}}
"""


def test_bib_check(tmp_path: Path) -> None:
    assert pc.parse_bib(BIB) == {"smith2020": {"title", "author", "year"},
                                 "doe21": {"author", "booktitle"},
                                 "online": {"title", "date"}}
    (tmp_path / "refs.bib").write_text(BIB)
    tex = pc.strip_comments(
        "\\citep[see][p.~2]{smith2020, doe21} \\citet{online}\\citestyle{plain}\n"
        "% \\cite{commented}\n\\nocite{*}\\bibliography{refs}\n")
    assert pc.cite_keys(tex) == ["smith2020", "doe21", "online"]
    check = pc.bib_check(tmp_path, tex)
    assert check.status == "WARN" and check.details == ["doe21: missing title, year"]
    check = pc.bib_check(tmp_path, tex + "\\cite{ghost}")
    assert check.status == "FAIL" and check.details[0].startswith("cited key ghost not in")


def test_anonymity_check() -> None:
    clean = "\\author{Anonymous Authors}\n\\section{Intro} see anonymous.4open.science\n"
    assert pc.anonymity_check(clean, "Anonymous Authors\nIntro").status == "PASS"
    leaky = ("\\author{Jane Doe \\\\ Example University \\\\ \\texttt{jane@example.edu}}\n"
             "Code: https://github.com/janedoe/repo\n\\section*{Acknowledgments}\n")
    check = pc.anonymity_check(leaky, "Jane Doe\n\nAcknowledgments\nThanks.")
    assert check.status == "WARN"
    joined = "\n".join(check.details)
    assert "author not anonymous in source and PDF: Jane Doe" in joined
    assert "email in source: jane@example.edu" in joined
    assert "GitHub link presented as the authors' own in source: github.com/janedoe" in joined
    assert "acknowledgements section in source" in joined
    assert "acknowledgements heading in PDF" in joined
    assert "source only" in pc.anonymity_check(leaky, None).summary


def test_anonymity_ignores_cited_and_third_party_links() -> None:
    tex = ("\\author{Anonymous}\nWe build on github.com/facebookresearch/dino and the planner "
           "of \\cite{icem}. Our code is available at https://github.com/janedoe/wm.\n")
    pdf = ("Anonymous\nWe build on github.com/facebookresearch/dino. Our code is available at "
           "github.com/janedoe/wm.\nReferences\n[1] iCEM. github.com/martius-lab/iCEM. "
           "contact: m@tue.mpg.de\n")
    bib = "@misc{icem, url = {https://github.com/martius-lab/iCEM}, note = {m@tue.mpg.de}}"
    check = pc.anonymity_check(tex, pdf, bib)
    assert check.status == "WARN"
    assert check.details == [
        "GitHub link presented as the authors' own in source: github.com/janedoe",
        "GitHub link presented as the authors' own in PDF: github.com/janedoe"]
    assert "3 cited or third-party" in check.summary
    third_party_only = tex.replace("Our code is available at https://github.com/janedoe/wm.",
                                   "")
    assert pc.anonymity_check(third_party_only, None, bib).status == "PASS"


def test_copy_sources_excludes_build_products(tmp_path: Path) -> None:
    src = tmp_path / "paper"
    (src / "figs").mkdir(parents=True)
    for name in ("main.tex", "refs.bib", "main.pdf", "main.aux", "main.log", "build.log",
                 "main.synctex.gz", "main.fdb_latexmk", "figs/plot.pdf", "figs/x.aux"):
        (src / name).write_text("x")
    pc.copy_sources(src, tmp_path / "copy")
    copied = sorted(str(p.relative_to(tmp_path / "copy"))
                    for p in (tmp_path / "copy").rglob("*") if p.is_file())
    assert copied == ["figs/plot.pdf", "main.tex", "refs.bib"]


def test_check_without_engine_or_poppler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                         capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "main.tex").write_text("\\documentclass{article}\\begin{document}x"
                                       "\\cite{a}\\end{document}\n")

    def fake_build(paper_dir: Path, main: str = "main.tex", timeout: float = 600,
                   **_: object) -> TexBuild:
        (paper_dir / "main.log").write_text("LaTeX Warning: Citation `a' on page 1 undefined\n")
        (paper_dir / "main.pdf").write_text("%PDF")
        (paper_dir / "build.log").write_text("ok\n")
        return TexBuild(paper_dir / "main.pdf", paper_dir / "build.log", 0, "fake")

    monkeypatch.setattr(pc, "build", fake_build)
    monkeypatch.setattr(pc.shutil, "which", lambda name: None)  # no poppler tools
    report = pc.check_paper(tmp_path, page_limit=8, anonymous=True)
    statuses = {c.name: c.status for c in report.checks}
    assert statuses["build"] == "PASS"
    assert statuses["citations/refs"] == "FAIL"
    assert statuses["fonts"] == "SKIP" and statuses["pages"] == "SKIP"
    assert statuses["bibliography"] == "FAIL"
    assert not report.ok
    assert not (tmp_path / "main.pdf").exists()  # the source tree is untouched
    assert main(["tex", "check", str(tmp_path), "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and data["engine"] == "fake"
    assert main(["tex", "check", str(tmp_path / "nope")]) == 1
    assert "FAIL" in capsys.readouterr().out


needs_tex = pytest.mark.skipif(
    find_texlive_bin() is None and shutil.which("latexmk") is None,
    reason="needs a TeX Live install")


@needs_tex
def test_real_check(tmp_path: Path) -> None:
    (tmp_path / "refs.bib").write_text("@article{k, title={T}, author={A}, journal={J}, "
                                       "year=2020}\n")
    (tmp_path / "main.tex").write_text(
        "\\documentclass{article}\\begin{document}See~\\cite{k}."
        "\\bibliographystyle{plain}\\bibliography{refs}\\end{document}\n")
    report = pc.check_paper(tmp_path, page_limit=2)
    assert report.ok, report.render()
    statuses = {c.name: c.status for c in report.checks}
    assert statuses["build"] == statuses["citations/refs"] == statuses["bibliography"] == "PASS"
