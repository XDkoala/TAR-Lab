import json
import shutil
from pathlib import Path

import pytest

from tar_lab.figures import audit
from tar_lab.figures import build as fb
from tar_lab.figures import diagram as dg
from tar_lab.tools.paper_check import PASS
from tar_lab.tools.tex import find_texlive_bin

LAYOUT = {
    "bb": "0,0,200,80",
    "objects": [
        {"_gvid": 0, "name": "cluster_wm", "bb": "10,10,120,70", "label": "World model",
         "lp": "65,62", "nodes": [1]},
        {"_gvid": 1, "name": "enc", "label": "E_t\\nencoder", "pos": "40,30", "width": "0.6",
         "height": "0.3", "shape": "box", "style": "rounded,filled", "fillcolor": "#DCE9F5"},
        {"_gvid": 2, "name": "plan", "label": "\\N", "texlbl": "$\\pi$", "pos": "170,30",
         "width": "0.4", "height": "0.3", "shape": "ellipse"},
        {"_gvid": 3, "name": "note", "label": "x & y", "pos": "170,70", "width": "0.4",
         "height": "0.2", "shape": "plaintext"},
    ],
    "edges": [
        {"tail": 1, "head": 2, "pos": "e,150,30 62,30 90,30 120,30 140,30", "label": "z_t",
         "lp": "100,36", "style": "dashed"},
        {"tail": 2, "head": 3, "pos": "170,40 170,50 170,55 170,60", "arrowhead": "none"},
    ],
}


def test_dot_to_tikz() -> None:
    tikz = dg.dot_to_tikz(LAYOUT)
    assert tikz.startswith("\\begin{tikzpicture}") and tikz.endswith("\\end{tikzpicture}\n")
    assert "\\draw[dashed,rounded corners=2pt] (10.00,10.00) rectangle (120.00,70.00);" in tikz
    assert "{World model}" in tikz
    assert ("\\node[rectangle,align=center,inner sep=1pt,minimum width=0.600in,"
            "minimum height=0.300in,draw,rounded corners=2pt,"
            "fill={rgb,255:red,220;green,233;blue,245}] (n1) at (40.00,30.00) "
            "{E\\_t\\\\encoder};") in tikz
    assert "(n2) at (170.00,30.00) {$\\pi$};" in tikz  # texlbl is raw LaTeX
    assert "draw=none] (n3) at (170.00,70.00) {x \\& y};" in tikz
    assert ("\\draw[->,dashed] (62.00,30.00) .. controls (90.00,30.00) and (120.00,30.00) .. "
            "(140.00,30.00) -- (150.00,30.00);") in tikz
    assert "{z\\_t}" in tikz
    assert ("\\draw[] (170.00,40.00) .. controls (170.00,50.00) and (170.00,55.00) .. "
            "(170.00,60.00);") in tikz


def test_dot_to_tikz_rejects_unsupported() -> None:
    bad_shape = {"objects": [{"_gvid": 0, "name": "a", "pos": "0,0", "shape": "star"}]}
    with pytest.raises(dg.DiagramError, match="shape 'star'"):
        dg.dot_to_tikz(bad_shape)
    bad_colour = {"objects": [{"_gvid": 0, "name": "a", "pos": "0,0", "shape": "box",
                               "style": "filled", "fillcolor": "lightblue"}]}
    with pytest.raises(dg.DiagramError, match="#RRGGBB"):
        dg.dot_to_tikz(bad_colour)


def test_spec_diagram_errors(tmp_path: Path) -> None:
    (tmp_path / "figures").mkdir()
    (tmp_path / "figures" / "a.svg").write_text("")
    (tmp_path / "figures" / "spec.toml").write_text(
        '[[diagram]]\nid = "a"\nsource = "figures/a.svg"\n\n[[diagram]]\nid = "b"\n'
        'script = "figures/a.svg"\n')
    errors = fb.load_spec(tmp_path).errors
    assert any("a: source must end in .dot, .gv, .tex" in e for e in errors)
    assert any("b source: expected" in e for e in errors)


@pytest.mark.skipif(find_texlive_bin() is None or shutil.which("dot") is None
                    or shutil.which("pdfinfo") is None,
                    reason="needs TeX Live, Graphviz and poppler")
def test_real_diagrams(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TARLAB_FIGURES_PYTHON", str(tmp_path / "unused"))  # not needed
    ws = tmp_path
    (ws / "figures" / "diagrams").mkdir(parents=True)
    (ws / "figures" / "diagrams" / "m.dot").write_text(
        'digraph G { rankdir=LR; node [shape=box]; a [label="Encoder"]; '
        'b [label="Predictor"]; a -> b [texlbl="$z_t$"]; }')
    (ws / "figures" / "diagrams" / "styles.tex").write_text(
        "\\tikzset{box/.style={draw,inner sep=2pt}}\n")
    (ws / "figures" / "diagrams" / "loop.tex").write_text(
        "\\input{figures/diagrams/styles}\n\\begin{tikzpicture}\\node[box] {Agent};"
        "\\end{tikzpicture}\n")
    (ws / "figures" / "diagrams" / "wide.dot").write_text(
        "digraph G { rankdir=LR; " + " -> ".join(f"n{i}" for i in range(30)) + "; }")
    (ws / "figures" / "spec.toml").write_text(
        '[layout]\ncolumnwidth_in = 3.3\ntextwidth_in = 7.0\nfont = "cm"\n\n'
        '[[diagram]]\nid = "m"\nsource = "figures/diagrams/m.dot"\n\n'
        '[[diagram]]\nid = "loop"\nsource = "figures/diagrams/loop.tex"\n'
        'inputs = ["figures/diagrams/styles.tex"]\n')
    (ws / "paper").mkdir()
    report = fb.build(ws, ws / "paper")
    assert report.ok, report.render()
    for ident in ("m", "loop"):
        pdf = ws / "paper" / "generated" / f"{ident}.pdf"
        assert pdf.is_file()
        width = dg.pdf_width_in(pdf)
        assert width is not None and 0.3 < width <= 3.3 + dg.WIDTH_TOLERANCE_IN
    assert (ws / "figures" / "out" / "m" / "m.png").is_file()
    prov = json.loads((ws / "figures" / "out" / "provenance.json").read_text())
    assert prov["artifacts"]["m"]["kind"] == "diagram"
    check = audit.artifact_check(ws, ws / "paper")
    assert check.status == PASS and "reproduced byte for byte" in check.summary

    spec = ws / "figures" / "spec.toml"
    spec.write_text(spec.read_text() + '\n[[diagram]]\nid = "wide"\n'
                    'source = "figures/diagrams/wide.dot"\n')
    report = fb.build(ws, ws / "paper", only=["wide"])
    assert not report.ok and "but its target is 3.30 in" in report.results[0].message
    spec.write_text(spec.read_text().replace(
        'source = "figures/diagrams/loop.tex"\ninputs = ["figures/diagrams/styles.tex"]\n',
        'source = "figures/diagrams/loop.tex"\n'))
    report = fb.build(ws, ws / "paper", only=["loop"])
    assert not report.ok and "LaTeX failed" in report.results[0].message
