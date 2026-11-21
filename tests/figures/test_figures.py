import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

from tar_lab.cli import main
from tar_lab.figures import audit
from tar_lab.figures import build as fb
from tar_lab.tools.paper_check import FAIL, PASS, SKIP, WARN
from tar_lab.tools.tex import find_texlive_bin

RUNTIME = Path(fb.__file__).parent / "runtime" / "tarlab_fig.py"
_spec = importlib.util.spec_from_file_location("tarlab_fig", RUNTIME)
assert _spec and _spec.loader
tf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tf)

TABLE_SCRIPT = """\
import tarlab_fig as tf
data = tf.load_json("results/final/o1/metrics.json")
rows = [[name, tf.fmt_pm(*tf.mean_std(v), digits=1)] for name, v in data.items()]
tf.save_table(tf.booktabs(["Method", "Score"], rows))
"""
FIGURE_SCRIPT = """\
import tarlab_fig as tf
data = tf.load_json("results/final/o1/metrics.json")
fig, ax = tf.figure()
ax.bar(list(data), [sum(v) / len(v) for v in data.values()])
ax.set_ylabel("Score")
tf.save(fig)
"""


def make_workspace(root: Path, figure: bool = False, script: str = TABLE_SCRIPT) -> Path:
    (root / "results" / "final" / "o1").mkdir(parents=True)
    (root / "results" / "dev").mkdir(parents=True)
    (root / "figures" / "scripts").mkdir(parents=True)
    (root / "results" / "final" / "o1" / "metrics.json").write_text(
        json.dumps({"A_base": [1.0, 2.0, 3.0], "B": [2.0, 3.0, 4.0]}))
    (root / "results" / "dev" / "other.json").write_text("{}")
    (root / "figures" / "scripts" / "table.py").write_text(script)
    spec = ('[layout]\ncolumnwidth_in = 3.3\ntextwidth_in = 7.0\n\n[[table]]\nid = "main"\n'
            'script = "figures/scripts/table.py"\ninputs = ["results/final/o1/metrics.json"]\n'
            'headline = true\n')
    if figure:
        (root / "figures" / "scripts" / "bars.py").write_text(FIGURE_SCRIPT)
        spec += ('\n[[figure]]\nid = "bars"\nscript = "figures/scripts/bars.py"\n'
                 'inputs = ["results/final/o1/metrics.json"]\n')
    (root / "figures" / "spec.toml").write_text(spec)
    (root / "paper").mkdir()
    return root


@pytest.fixture
def stdlib_python(monkeypatch: pytest.MonkeyPatch) -> None:
    """Table scripts need only the standard library: run them with this interpreter."""
    monkeypatch.setenv("TARLAB_FIGURES_PYTHON", sys.executable)


def test_booktabs_and_formatting() -> None:
    assert tf.escape("a_b & 5%") == r"a\_b \& 5\%"
    assert tf.fmt(0.1234, 1, percent=True) == "12.3"
    assert tf.fmt(None) == "--"
    assert tf.fmt_pm(1.0, 0.25, 1) == r"1.0 $\pm$ 0.2"
    mean, std = tf.mean_std([1, 2, 3])
    assert (mean, std) == (2.0, 1.0)
    tex = tf.booktabs(["Method", "x"], [["a_b", 1.234], tf.MIDRULE, [tf.bold("c"), None]],
                      groups=[("", 1), ("Group", 1)], digits=1)
    assert tex.splitlines() == [
        r"\begin{tabular}{@{}lr@{}}", r"\toprule", r" & Group \\", r"\cmidrule(lr){2-2}",
        r"Method & x \\", r"\midrule", r"a\_b & 1.2 \\", r"\midrule",
        r"\textbf{c} & -- \\", r"\bottomrule", r"\end{tabular}"]
    with pytest.raises(ValueError):
        tf.booktabs(["a", "b"], [["only one"]])
    with pytest.raises(ValueError, match="tf.Raw"):
        tf.booktabs([r"95\% CI"], [])
    assert tf.booktabs(["95% CI"], [], tight=False).splitlines()[:3] == [
        r"\begin{tabular}{l}", r"\toprule", r"95\% CI \\"]
    assert tf.fmt(92454400, 0, thousands=True) == "92,454,400"
    assert tf.interval(0.117425, 0.119421, 3, scale=1000) == "[117.425,119.421]"
    with pytest.raises(ValueError):
        tf.booktabs(["a", "b"], [], groups=[("g", 3)])


def test_method_style(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TARLAB_FIG_ARTIFACT", json.dumps({"methods": ["W", "D"]}))
    assert tf.method_style("D") == {"color": tf.COLORS[1], "marker": tf.MARKERS[1],
                                    "linestyle": tf.LINESTYLES[1]}
    with pytest.raises(ValueError, match="not in"):
        tf.method_style("RS")


def test_load_spec_errors(tmp_path: Path) -> None:
    assert fb.load_spec(tmp_path).errors == ["figures/spec.toml not found"]
    ws = make_workspace(tmp_path)
    assert fb.load_spec(ws).errors == []
    (ws / "figures" / "spec.toml").write_text(
        '[[figure]]\nid = "bad id"\n\n[[figure]]\nid = "f"\nscript = "results/x.py"\n'
        'inputs = ["results/dev/other.json", "../etc"]\nheadline = true\nwidth = "huge"\n'
        '\n[[table]]\nid = "t"\nscript = "figures/scripts/table.py"\ninputs = []\n')
    errors = fb.load_spec(ws).errors
    assert any("id must be" in e for e in errors)
    assert any("results/x.py does not exist" in e for e in errors)
    assert any("must live under figures/" in e for e in errors)
    assert any("without '..'" in e for e in errors)
    assert any("not under results/final/" in e for e in errors)
    assert any("width must be" in e for e in errors)
    assert any("t: inputs must list" in e for e in errors)


def test_layout_probe(tmp_path: Path) -> None:
    main_tex = tmp_path / "main.tex"
    main_tex.write_text("\\documentclass{article}\n\\begin{document}\nx\n\\end{document}\n")
    assert fb.insert_layout_probe(main_tex)
    assert fb.LAYOUT_MARK in main_tex.read_text()
    assert fb.parse_layout("TARLAB-LAYOUT columnwidth=252.0pt;textwidth=516.0pt") == {
        "columnwidth_in": 3.4869, "textwidth_in": 7.1399}
    assert fb.parse_layout("nothing") is None
    probe = ("TARLAB-LAYOUT columnwidth=252.0pt;textwidth=516.0pt;rm={};sf=phv;family={};")
    assert (fb.parse_layout(probe.format("ptm", "ptm")) or {})["body_font"] == "serif"
    assert (fb.parse_layout(probe.format("lmr", "lmr")) or {})["body_font"] == "cm"
    assert (fb.parse_layout(probe.format("cmr", "phv")) or {})["body_font"] == "sans"
    (tmp_path / "x.tex").write_text("no document")
    assert not fb.insert_layout_probe(tmp_path / "x.tex")


def test_build_records_provenance_and_audit_detects_drift(tmp_path: Path,
                                                          stdlib_python: None) -> None:
    ws = make_workspace(tmp_path)
    report = fb.build(ws, ws / "paper")
    assert report.ok, report.render()
    assert report.layout_source == "spec"
    table = (ws / "paper" / "generated" / "main.tex").read_text()
    assert "% input: results/final/o1/metrics.json" in table
    assert r"A\_base & 2.0 $\pm$ 1.0 \\" in table
    prov = fb.load_provenance(ws)
    assert prov is not None and set(prov["artifacts"]) == {"main"}
    check = audit.artifact_check(ws, ws / "paper")
    assert check.status == PASS and "reproduced byte for byte" in check.summary

    generated = ws / "paper" / "generated" / "main.tex"
    generated.write_text(table.replace("2.0", "9.0"))
    (ws / "results" / "final" / "o1" / "metrics.json").write_text('{"A_base": [5.0]}')
    check = audit.artifact_check(ws, ws / "paper")
    assert check.status == FAIL
    assert any("input results/final/o1/metrics.json changed" in d for d in check.details)
    assert any("differs from the build output" in d for d in check.details)

    assert fb.build(ws, ws / "paper").ok
    assert audit.artifact_check(ws, ws / "paper").status == PASS
    spec_path = ws / "figures" / "spec.toml"
    spec_path.write_text(spec_path.read_text() + '\n[style]\nmethods = ["B", "A_base"]\n')
    check = audit.artifact_check(ws, ws / "paper")
    assert any("[style] methods changed" in d for d in check.details)
    assert fb.build(ws, ws / "paper").ok
    (ws / "figures" / "spec.toml").write_text(
        (ws / "figures" / "spec.toml").read_text().replace('id = "main"', 'id = "renamed"'))
    assert fb.build(ws, ws / "paper").ok
    assert not generated.exists() and (ws / "paper" / "generated" / "renamed.tex").is_file()


@pytest.mark.parametrize(("line", "message"), [
    ('open("results/dev/other.json").read()', "not a declared input"),
    ('open("paper/x.tex", "w").write("1")', "may only write to their output directory"),
    ('import os; os.system("true")', "may not start processes"),
])
def test_guard(tmp_path: Path, stdlib_python: None, line: str, message: str) -> None:
    ws = make_workspace(tmp_path, script=TABLE_SCRIPT + line + "\n")
    report = fb.build(ws, ws / "paper")
    assert not report.ok
    assert message in report.results[0].message
    assert not (ws / "paper" / "x.tex").exists()
    assert audit.artifact_check(ws, ws / "paper").status == FAIL


def test_build_without_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                   capsys: pytest.CaptureFixture[str]) -> None:
    ws = make_workspace(tmp_path)
    monkeypatch.setenv("TARLAB_FIGURES_PYTHON", str(tmp_path / "missing"))
    assert main(["figures", "build", "--workspace", str(ws)]) == 1
    assert "tarlab figures setup" in capsys.readouterr().out


def test_handwritten_tables_and_resolution(tmp_path: Path) -> None:
    (tmp_path / "generated").mkdir()
    (tmp_path / "generated" / "t.tex").write_text("\\begin{tabular}{l}1.5\\end{tabular}")
    (tmp_path / "main.tex").write_text(
        "\\begin{tabular}{l}Notation & x\\end{tabular}\n"
        "% \\begin{tabular}{l}9.9\\end{tabular}\n"
        "\\begin{tabular}{lr}A & 0.53 \\\\ B & 12.25\\end{tabular}\n")
    check = audit.handwritten_table_check(tmp_path)
    assert check.status == WARN
    assert check.details == ["main.tex: tabular with 2 decimal number(s), e.g. 0.53, 12.25"]
    listing = (
        "page   num  type   width height color comp bpc  enc interp  object ID x-ppi y-ppi "
        "size ratio\n" + "-" * 90 + "\n"
        "   1     0 image     640   480  rgb     3   8  image  no         9  0   150   150 "
        "20.0K 2.2%\n"
        "   1     1 smask     640   480  gray    1   8  image  no         9  0   150   150 "
        "1K 0.1%\n"
        "   2     2 image    3000  2000  rgb     3   8  jpeg   no        12  0   600   600 "
        "300K 1%\n")
    check = audit.resolution_check(listing)
    assert check.status == WARN and check.details == ["page 1: 150x150 ppi"]
    assert audit.resolution_check(None).status == SKIP
    assert audit.resolution_check(listing.split("\n   1")[0]).status == PASS


def test_size_check(tmp_path: Path, stdlib_python: None) -> None:
    ws = tmp_path
    (ws / "figures" / "out").mkdir(parents=True)
    (ws / "figures" / "out" / "provenance.json").write_text(json.dumps({"artifacts": {
        "curve": {"kind": "figure", "width": "column", "width_in": 3.5},
        "wide": {"kind": "figure", "width": "text", "width_in": 7.14}}}))
    layout: dict[str, object] = {"columnwidth_in": 3.3, "textwidth_in": 7.14}
    tex = ("\\includegraphics{generated/curve}\\includegraphics[width=\\textwidth]"
           "{generated/wide}\\includegraphics[width=0.5\\linewidth]{generated/curve.pdf}"
           "\\includegraphics[scale=0.5]{other}")
    check = audit.size_check(ws, tex, layout)
    assert check.status == WARN
    assert check.details == [
        ("curve: drawn 3.50 in wide but the paper's column is 3.30 in; rerun `tarlab figures "
        "build`"),
        ("curve: included with [width=0.5\\linewidth]; generated figures are drawn at final "
        "size, include them without scaling")]
    layout["columnwidth_in"] = 3.5
    assert audit.size_check(ws, "\\includegraphics{generated/curve}", layout).status == PASS
    layout["body_font"] = "cm"
    check = audit.size_check(ws, "", layout)
    assert check.status == WARN and "drawn in the None font but the body" in check.details[0]


@pytest.mark.skipif(fb.figures_python() is None or find_texlive_bin() is None
                    or shutil.which("pdffonts") is None,
                    reason="needs the figures environment, TeX Live and poppler")
def test_real_figures_and_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ws = make_workspace(tmp_path, figure=True)
    spec = ws / "figures" / "spec.toml"
    spec.write_text(spec.read_text().replace(
        "[layout]\ncolumnwidth_in = 3.3\ntextwidth_in = 7.0\n", ""))
    (ws / "paper" / "main.tex").write_text(
        "\\documentclass[twocolumn]{article}\n\\usepackage{graphicx,booktabs}\n"
        "\\begin{document}\nText.\n\\begin{figure}[t]\\includegraphics{generated/bars}"
        "\\caption{Bars.}\\end{figure}\n\\begin{table}[t]\\caption{Main.}"
        "\\input{generated/main}\\end{table}\n\\end{document}\n")
    assert main(["figures", "build", "--workspace", str(ws)]) == 0
    out = capsys.readouterr().out
    assert "font cm (measured from paper/main.tex)" in out
    assert main(["tex", "check", str(ws / "paper"), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    statuses = {c["name"]: c["status"] for c in report["checks"]}
    assert statuses["fonts"] == PASS
    assert statuses["generated artifacts"] == PASS
    assert statuses["figure sizes"] == PASS
    assert statuses["hand-written tables"] == PASS
    assert main(["tex", "render", str(ws / "paper")]) == 1  # not built yet
    assert main(["tex", "build", str(ws / "paper")]) == 0
    assert main(["tex", "render", str(ws / "paper")]) == 0
    assert (ws / "reports" / "paper_pages" / "page-1.png").is_file()
