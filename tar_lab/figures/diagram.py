"""Structure and method diagrams (``[[diagram]]`` in ``figures/spec.toml``).

Two sources, one renderer:

- ``.tex``: a ``tikzpicture`` (any TikZ; libraries ``arrows.meta``, ``positioning``, ``fit``,
  ``calc``, ``backgrounds``, ``shapes.geometric``, ``shapes.misc`` are loaded).
- ``.dot``/``.gv``: a Graphviz graph. Graphviz only computes the layout (``dot -Tjson``,
  sized with 8 pt text); :func:`dot_to_tikz` turns it into a ``tikzpicture``. Labels are
  escaped; a node or edge attribute ``texlbl`` gives raw LaTeX instead. Supported: shapes
  box/rect, ellipse/oval, circle, diamond, cylinder, plaintext/none, ``style`` rounded,
  dashed, dotted, bold, filled; ``fillcolor``/``color`` as ``#RRGGBB``; clusters. Spacing
  defaults are compact (:data:`COMPACT`); attributes set in the file override them.

The picture is compiled with pdfLaTeX in a ``standalone`` wrapper whose font matches the
paper's body (Computer Modern, newtx Times, or Helvetica) at 8 pt, in a scratch directory
holding only the source and its declared inputs (``openin_any=p``), with fixed PDF metadata so
the output is byte-deterministic. The PDF has the diagram's natural size, which must not
exceed the target width: the paper includes it unscaled, so its text stays 8 pt.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ..tools.tex import build as build_tex

DOT_SUFFIXES = (".dot", ".gv")
SUFFIXES = (*DOT_SUFFIXES, ".tex")
FONT_PACKAGES = {
    "cm": "",
    "serif": "\\usepackage{newtxtext,newtxmath}",
    "sans": "\\usepackage[scaled=0.92]{helvet}\\renewcommand{\\familydefault}{\\sfdefault}",
}
LAYOUT_FONT = {"cm": "Times-Roman", "serif": "Times-Roman", "sans": "Helvetica"}
FONT_SIZE_PT = 8
# Paper-sized defaults (Graphviz's are for screens); attributes in the .dot file win.
COMPACT = ("-Gnodesep=0.12", "-Granksep=0.22", "-Gmargin=0", "-Nwidth=0.3", "-Nheight=0.22",
           "-Nmargin=0.05,0.03", "-Earrowsize=0.6")
WIDTH_TOLERANCE_IN = 0.01

WRAPPER = r"""\pdfinfoomitdate=1
\pdftrailerid{}
\pdfsuppressptexinfo=-1
\documentclass[border=1pt]{standalone}
\usepackage[T1]{fontenc}
%(fonts)s
\usepackage{tikz}
\usetikzlibrary{arrows.meta,positioning,fit,calc,backgrounds,shapes.geometric,shapes.misc}
\begin{document}
\fontsize{%(size)d}{%(leading).1f}\selectfont
\input{diagram-body}
\end{document}
"""


class DiagramError(Exception):
    pass


# --- Graphviz -> TikZ --------------------------------------------------------------------

_SPECIAL = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
            "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
            "^": r"\textasciicircum{}"}
_SHAPES = {"box": "rectangle", "rect": "rectangle", "rectangle": "rectangle",
           "square": "rectangle", "ellipse": "ellipse", "oval": "ellipse",
           "circle": "circle", "diamond": "diamond", "cylinder": "cylinder,shape border rotate=90",
           "plaintext": None, "plain": None, "none": None}


def _label(obj: dict[str, Any], key: str = "label") -> str | None:
    if "texlbl" in obj:
        return str(obj["texlbl"])
    raw = obj.get(key)
    if raw == "\\N":
        raw = obj.get("name")
    if raw is None or raw in ("\\E", ""):
        return None
    lines = re.split(r"\\n|\\l|\\r|\n", str(raw))
    return r"\\".join("".join(_SPECIAL.get(c, c) for c in line) for line in lines if line)


def _color(value: str | None, what: str) -> str | None:
    if not value or value in ("none", "transparent"):
        return None
    m = re.fullmatch(r"#([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})(?:[0-9a-fA-F]{2})?",
                     value)
    if m:
        r, g, b = (int(x, 16) for x in m.groups())
        return f"{{rgb,255:red,{r};green,{g};blue,{b}}}"
    if value == "black":
        return "black"
    if value == "white":
        return "white"
    raise DiagramError(f"{what}: colour {value!r}; use #RRGGBB, black or white")


def _style_options(obj: dict[str, Any], what: str) -> list[str]:
    styles = {s.strip() for s in str(obj.get("style", "")).split(",") if s.strip()}
    opts = []
    if "dashed" in styles:
        opts.append("dashed")
    if "dotted" in styles:
        opts.append("dotted")
    if "bold" in styles:
        opts.append("line width=1pt")
    if "rounded" in styles:
        opts.append("rounded corners=2pt")
    if color := _color(obj.get("color"), what):
        opts.append(f"draw={color}")
    if "filled" in styles:
        fill = _color(obj.get("fillcolor") or obj.get("color") or "#D3D3D3", what)
        if fill:
            opts.append(f"fill={fill}")
    if "invis" in styles:
        opts.append("draw=none")
    return opts


def _points(spec: str) -> list[tuple[float, float]]:
    return [(float(x), float(y)) for x, y in (p.split(",")[:2] for p in spec.split())]


def _xy(p: tuple[float, float]) -> str:
    return f"({p[0]:.2f},{p[1]:.2f})"


def dot_to_tikz(layout: dict[str, Any]) -> str:
    """A ``tikzpicture`` from ``dot -Tjson`` output (coordinates in big points)."""
    lines = [(r"\begin{tikzpicture}[x=1bp,y=1bp,>={Stealth[length=4pt,width=3pt]},"
             r"line width=0.5pt]")]
    objects = layout.get("objects", [])
    for obj in objects:  # clusters first: they are drawn behind nodes
        if "bb" not in obj or "nodes" not in obj or not str(obj.get("name", "")).startswith(
                "cluster"):
            continue
        x0, y0, x1, y1 = (float(v) for v in obj["bb"].split(","))
        what = f"cluster {obj.get('name')}"
        opts = _style_options(obj, what) or ["dashed", "rounded corners=2pt"]
        lines.append(f"\\draw[{','.join(opts)}] ({x0:.2f},{y0:.2f}) rectangle "
                     f"({x1:.2f},{y1:.2f});")
        if (text := _label(obj)) and "lp" in obj:
            lines.append(f"\\node[inner sep=0pt,align=center] at {_xy(_points(obj['lp'])[0])} "
                         f"{{{text}}};")
    ids: dict[int, str] = {}
    for obj in objects:
        if "pos" not in obj:
            continue
        gvid = int(obj["_gvid"])
        ids[gvid] = f"n{gvid}"
        what = f"node {obj.get('name')}"
        shape_name = str(obj.get("shape", "ellipse"))
        if shape_name not in _SHAPES:
            raise DiagramError(f"{what}: shape {shape_name!r} is not supported "
                               f"({', '.join(sorted(_SHAPES))})")
        shape = _SHAPES[shape_name]
        width, height = float(obj.get("width", 0.75)), float(obj.get("height", 0.5))
        opts = [shape or "rectangle", "align=center", "inner sep=1pt",
                f"minimum width={width:.3f}in", f"minimum height={height:.3f}in"]
        opts += ["draw"] if shape else ["draw=none"]
        opts += _style_options(obj, what)
        text = _label(obj, "label") or ""
        lines.append(f"\\node[{','.join(opts)}] (n{gvid}) at {_xy(_points(obj['pos'])[0])} "
                     f"{{{text}}};")
    for edge in layout.get("edges", []):
        what = f"edge {edge.get('tail')}->{edge.get('head')}"
        tokens = str(edge.get("pos", "")).split()
        start = end = None
        controls: list[tuple[float, float]] = []
        for tok in tokens:
            if tok.startswith("e,"):
                end = _points(tok[2:])[0]
            elif tok.startswith("s,"):
                start = _points(tok[2:])[0]
            else:
                controls += _points(tok)
        if len(controls) < 4:
            continue
        path = _xy(controls[0])
        for i in range(1, len(controls) - 2, 3):
            a, b, c = controls[i:i + 3]
            path += f" .. controls {_xy(a)} and {_xy(b)} .. {_xy(c)}"
        opts = _style_options(edge, what)
        arrow_head = str(edge.get("arrowhead", "normal")) != "none"
        if end is not None and arrow_head:
            path += f" -- {_xy(end)}"
        if start is not None:
            path = f"{_xy(start)} -- " + path
        arrows = ("<" if start is not None else "") + "-" + (">" if end is not None and
                                                             arrow_head else "")
        if arrows != "-":
            opts.insert(0, arrows)
        lines.append(f"\\draw[{','.join(opts)}] {path};")
        if (text := _label(edge)) and "lp" in edge:
            lines.append(f"\\node[inner sep=1pt,align=center,fill=white] at "
                         f"{_xy(_points(edge['lp'])[0])} {{{text}}};")
    lines.append(r"\end{tikzpicture}")
    return "\n".join(lines) + "\n"


def dot_layout(source: Path, font: str) -> dict[str, Any]:
    dot = shutil.which("dot")
    if dot is None:
        raise DiagramError("Graphviz `dot` not found (install graphviz)")
    name = LAYOUT_FONT[font]
    cmd = [dot, "-Tjson", f"-Gfontname={name}", f"-Nfontname={name}", f"-Efontname={name}",
           f"-Gfontsize={FONT_SIZE_PT}", f"-Nfontsize={FONT_SIZE_PT}",
           f"-Efontsize={FONT_SIZE_PT}", *COMPACT, str(source)]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=120)
    if proc.returncode:
        raise DiagramError(f"dot failed: {proc.stderr.strip()[-500:]}")
    layout: dict[str, Any] = json.loads(proc.stdout)
    return layout


# --- rendering ---------------------------------------------------------------------------


def pdf_width_in(pdf: Path) -> float | None:
    if not shutil.which("pdfinfo"):
        return None
    out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True,
                         check=False).stdout
    m = re.search(r"Page size:\s+([\d.]+) x ([\d.]+) pts", out)
    return float(m.group(1)) / 72 if m else None


def render(workspace: Path, source: str, inputs: list[str], out_dir: Path, ident: str,
           font: str, max_width_in: float) -> tuple[list[str], float | None]:
    """Write ``<id>.pdf`` (+ ``<id>.tikz.tex``, and ``<id>.png`` when pdftoppm exists) to
    ``out_dir``; returns the output names and the natural width in inches."""
    src = workspace / source
    suffix = src.suffix.lower()
    if suffix in DOT_SUFFIXES:
        body = dot_to_tikz(dot_layout(src, font))
    elif suffix == ".tex":
        body = src.read_text(encoding="utf-8")
    else:
        raise DiagramError(f"source must be {', '.join(SUFFIXES)}")
    with tempfile.TemporaryDirectory(prefix="tarlab-diagram-") as tmp:
        work = Path(tmp)
        for rel in inputs:
            target = work / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if (workspace / rel).is_dir():
                shutil.copytree(workspace / rel, target, dirs_exist_ok=True)
            else:
                shutil.copyfile(workspace / rel, target)
        (work / "diagram-body.tex").write_text(body, encoding="utf-8")
        (work / "diagram.tex").write_text(WRAPPER % {
            "fonts": FONT_PACKAGES[font], "size": FONT_SIZE_PT,
            "leading": FONT_SIZE_PT * 1.2}, encoding="utf-8")
        result = build_tex(work, "diagram.tex", env={
            "openin_any": "p", "openout_any": "p", "SOURCE_DATE_EPOCH": "0",
            "FORCE_SOURCE_DATE": "1"})
        log = (work / "diagram.log")
        if result.pdf is None:
            text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else \
                result.log.read_text(encoding="utf-8", errors="replace")
            errors = [ln for ln in text.splitlines() if ln.startswith("!") or ": " in ln and
                      ln.split(":")[0].endswith(".tex")]
            raise DiagramError("LaTeX failed: " + (" | ".join(errors[:3]) or text[-300:]))
        shutil.copyfile(result.pdf, out_dir / f"{ident}.pdf")
    (out_dir / f"{ident}.tikz.tex").write_text(body, encoding="utf-8")
    names = [f"{ident}.pdf", f"{ident}.tikz.tex"]
    if shutil.which("pdftoppm"):
        subprocess.run(["pdftoppm", "-r", "300", "-png", "-singlefile",
                        str(out_dir / f"{ident}.pdf"), str(out_dir / ident)], check=True,
                       capture_output=True)
        names.append(f"{ident}.png")
    width = pdf_width_in(out_dir / f"{ident}.pdf")
    if width is not None and width > max_width_in + WIDTH_TOLERANCE_IN:
        raise DiagramError(f"diagram is {width:.2f} in wide but its target is "
                           f"{max_width_in:.2f} in: lay it out narrower (e.g. rankdir=TB, "
                           "shorter labels) or use width = \"text\"")
    return names, width
