"""Helpers for figure and table scripts run by ``tarlab figures build``.

Scripts run in the dedicated figures environment (matplotlib, numpy, pandas, scipy) and
import this module as ``import tarlab_fig as tf``. The build tells each script which artifact
it makes (id, declared inputs, final width) and where to write; a script may read only its
declared inputs. Typical figure script::

    import tarlab_fig as tf
    runs = tf.load_json("results/final/o12/metrics.json")
    fig, ax = tf.figure()
    ...
    tf.save(fig)

and table script::

    tf.save_table(tf.booktabs(["Method", "Success (%)"], rows))

For a legend outside the axes use ``ax.legend(loc="lower left", bbox_to_anchor=(0, 1.02),
ncol=4)`` (constrained layout makes room) or ``fig.legend(..., loc="outside upper center")``.

Figures are drawn at their exact final width with fonts close to the paper's body text, so
they are included at natural size (``\\includegraphics{generated/<id>}``) and their text
keeps its drawn size. Matplotlib is imported lazily: the table helpers need only the standard
library.
"""

from __future__ import annotations

import csv
import json
import math
import os
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

# Okabe-Ito: distinguishable with the common colour-vision deficiencies.
COLORS = ("#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9", "#000000",
          "#F0E442")
MARKERS = ("o", "s", "^", "D", "v", "P", "X", "*")
LINESTYLES = ("-", "--", "-.", ":", (0, (5, 1)), (0, (3, 1, 1, 1, 1, 1)), (0, (1, 2)),
              (0, (8, 2)))

FONTS: dict[str, dict[str, Any]] = {
    # STIX is Times-like: NeurIPS, ICML, ICLR, CVPR, IEEE, AAAI, ... bodies.
    "serif": {"font.family": "serif", "font.serif": ["STIXGeneral"],
              "mathtext.fontset": "stix"},
    # Computer Modern: LNCS, elsarticle and other default-LaTeX bodies.
    "cm": {"font.family": "serif", "font.serif": ["cmr10"], "mathtext.fontset": "cm",
           "axes.formatter.use_mathtext": True},
    "sans": {"font.family": "sans-serif", "font.sans-serif": ["DejaVu Sans"],
             "mathtext.fontset": "dejavusans"},
}
FONT_SIZE = 8.0  # pt at final size; the minimum most venues accept is 6-7 pt


class Raw(str):
    """A table cell that is already LaTeX and must not be escaped."""


# --- the artifact being built ------------------------------------------------------------


def artifact() -> dict[str, Any]:
    """The artifact spec passed by the build (id, kind, inputs, width_in, height_in, ...)."""
    raw = os.environ.get("TARLAB_FIG_ARTIFACT")
    if not raw:
        raise RuntimeError("run this script with `tarlab figures build`, not directly")
    spec: dict[str, Any] = json.loads(raw)
    return spec


def workspace() -> Path:
    return Path(artifact()["workspace"])


def out_dir() -> Path:
    return Path(artifact()["out_dir"])


def inputs() -> list[Path]:
    """The declared inputs as absolute paths."""
    return [workspace() / p for p in artifact()["inputs"]]


def path(name: str | os.PathLike[str]) -> Path:
    """Resolve a workspace-relative path (inputs are declared relative to the workspace)."""
    p = Path(name)
    return p if p.is_absolute() else workspace() / p


def load_json(name: str | os.PathLike[str]) -> Any:
    return json.loads(path(name).read_text(encoding="utf-8"))


def load_jsonl(name: str | os.PathLike[str]) -> list[Any]:
    lines = path(name).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def load_csv(name: str | os.PathLike[str]) -> list[dict[str, str]]:
    with path(name).open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# --- statistics and number formatting ----------------------------------------------------


def mean_std(values: Iterable[float]) -> tuple[float, float]:
    """Mean and sample standard deviation (0 for a single value)."""
    xs = [float(v) for v in values]
    if not xs:
        raise ValueError("no values")
    mean = sum(xs) / len(xs)
    if len(xs) == 1:
        return mean, 0.0
    return mean, math.sqrt(sum((x - mean) ** 2 for x in xs) / (len(xs) - 1))


def mean_ci(values: Iterable[float], level: float = 0.95) -> tuple[float, float]:
    """Mean and the half-width of its Student-t confidence interval (0 for one value)."""
    xs = [float(v) for v in values]
    mean, std = mean_std(xs)
    if len(xs) == 1:
        return mean, 0.0
    from scipy import stats

    return mean, float(stats.t.ppf(0.5 + level / 2, len(xs) - 1)) * std / math.sqrt(len(xs))


def fmt(value: float | None, digits: int = 2, percent: bool = False, scale: float = 1.0,
        thousands: bool = False) -> str:
    """Fixed-point ``value * scale`` (``percent`` = scale 100; e.g. ``scale=1000`` for seconds
    shown as ms); ``thousands`` adds ``,`` separators; ``--`` for missing values."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "--"
    x = value * (100 if percent else scale)
    return f"{x:,.{digits}f}" if thousands else f"{x:.{digits}f}"


def fmt_pm(mean: float, spread: float, digits: int = 2, percent: bool = False,
           scale: float = 1.0) -> Raw:
    """``mean $\\pm$ spread`` with the same number of decimals."""
    return Raw(f"{fmt(mean, digits, percent, scale)} $\\pm$ "
               f"{fmt(spread, digits, percent, scale)}")


def interval(lo: float, hi: float, digits: int = 2, percent: bool = False,
             scale: float = 1.0) -> str:
    """``[lo,hi]`` with the same number of decimals."""
    return f"[{fmt(lo, digits, percent, scale)},{fmt(hi, digits, percent, scale)}]"


# --- tables ------------------------------------------------------------------------------

_LATEX_SPECIAL = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
                  "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
                  "^": r"\textasciicircum{}"}

MIDRULE = "midrule"


def escape(text: str) -> str:
    return "".join(_LATEX_SPECIAL.get(ch, ch) for ch in text)


def _cell(value: Any, digits: int) -> str:
    if isinstance(value, Raw):
        return str(value)
    if isinstance(value, str) and "\\" in value:
        raise ValueError(f"cell {value!r} looks like LaTeX; wrap it in tf.Raw(...) (plain "
                         "strings are escaped, so '\\%' would print a backslash)")
    if value is None:
        return "--"
    if isinstance(value, bool):
        return escape(str(value))
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return fmt(value, digits)
    return escape(str(value))


def bold(value: Any, digits: int = 2) -> Raw:
    return Raw(f"\\textbf{{{_cell(value, digits)}}}")


def booktabs(header: Sequence[Any], rows: Sequence[Sequence[Any] | str], *,
             align: str | None = None, groups: Sequence[tuple[str, int]] | None = None,
             digits: int = 2, tight: bool = True) -> str:
    """A ``tabular`` with booktabs rules (needs ``\\usepackage{booktabs}``).

    ``rows`` holds cell lists or :data:`MIDRULE`; floats are printed with ``digits`` decimals.
    Header and body strings are escaped (``"95% CI"`` prints as written); LaTeX such as
    ``$N=128$`` or ``\\textbf`` must be wrapped in :class:`Raw` (see :func:`bold`,
    :func:`fmt_pm`); a plain string with a backslash raises. ``align`` defaults to one ``l``
    then ``r`` columns; ``tight`` drops the outer column padding (``@{}...@{}``).
    ``groups`` adds a spanning header row, e.g. ``[("", 1), ("Dataset A", 2)]``. The caption
    and label belong in the paper's ``table`` environment, around ``\\input``.
    """
    ncols = len(header)
    for row in rows:
        if not isinstance(row, str) and len(row) != ncols:
            raise ValueError(f"row has {len(row)} cells, header has {ncols}: {row!r}")
    if groups is not None and sum(span for _, span in groups) != ncols:
        raise ValueError("group spans must add up to the number of columns")
    align = align or "l" + "r" * (ncols - 1)
    if tight and not align.startswith("@{}"):
        align = f"@{{}}{align}@{{}}"
    lines = [f"\\begin{{tabular}}{{{align}}}", "\\toprule"]
    if groups:
        cells, rules, col = [], [], 1
        for label, span in groups:
            cells.append(f"\\multicolumn{{{span}}}{{c}}{{{escape(label)}}}" if span > 1
                         else escape(label))
            if label:
                rules.append(f"\\cmidrule(lr){{{col}-{col + span - 1}}}")
            col += span
        lines.append(" & ".join(cells) + r" \\")
        if rules:
            lines.append(" ".join(rules))
    lines.append(" & ".join(_cell(h, digits) for h in header) + r" \\")
    lines.append("\\midrule")
    for row in rows:
        if isinstance(row, str):
            if row != MIDRULE:
                raise ValueError(f"unknown row marker {row!r}")
            lines.append("\\midrule")
        else:
            lines.append(" & ".join(_cell(c, digits) for c in row) + r" \\")
    lines += ["\\bottomrule", "\\end{tabular}"]
    return "\n".join(lines) + "\n"


def save_table(tex: str) -> Path:
    """Write the table fragment to ``<out>/<id>.tex`` with a do-not-edit header."""
    spec = artifact()
    header = [f"% Generated by `tarlab figures build` ({spec['id']}); do not edit by hand.",
              *(f"% input: {p}" for p in spec["inputs"])]
    out = out_dir() / f"{spec['id']}.tex"
    out.write_text("\n".join(header) + "\n" + tex, encoding="utf-8")
    return out


# --- figures -----------------------------------------------------------------------------


def method_style(name: str) -> dict[str, Any]:
    """Colour, marker and line style for a method, stable across every figure: the order
    comes from ``[style] methods = [...]`` in ``figures/spec.toml``. Use as
    ``ax.plot(x, y, label=name, **tf.method_style(name))``."""
    methods = list(artifact().get("methods") or [])
    if name not in methods:
        raise ValueError(f"method {name!r} is not in [style] methods = {methods} in "
                         "figures/spec.toml; list every method once so all figures agree")
    i = methods.index(name)
    return {"color": COLORS[i % len(COLORS)], "marker": MARKERS[i % len(MARKERS)],
            "linestyle": LINESTYLES[i % len(LINESTYLES)]}


def style(font: str | None = None, size: float = FONT_SIZE) -> None:
    """Apply the publication rcParams (fonts embedded as TrueType: no Type 3)."""
    import matplotlib

    matplotlib.use("Agg", force=True)
    from cycler import cycler

    font = font or artifact().get("font", "serif")
    if font not in FONTS:
        raise ValueError(f"font must be one of {sorted(FONTS)}, not {font!r}")
    matplotlib.rcParams.update({
        **FONTS[font],
        "font.size": size, "axes.labelsize": size, "axes.titlesize": size,
        "xtick.labelsize": size - 1, "ytick.labelsize": size - 1,
        "legend.fontsize": size - 1, "legend.frameon": False,
        "axes.linewidth": 0.6, "lines.linewidth": 1.2, "lines.markersize": 3.5,
        "xtick.major.width": 0.6, "ytick.major.width": 0.6,
        "xtick.major.size": 2.5, "ytick.major.size": 2.5,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": False, "grid.linewidth": 0.4, "grid.alpha": 0.4,
        "axes.prop_cycle": cycler(color=COLORS) + cycler(marker=MARKERS),
        "errorbar.capsize": 2.0,
        "pdf.fonttype": 42, "ps.fonttype": 42, "svg.fonttype": "path",
        "svg.hashsalt": "tarlab", "figure.dpi": 100, "savefig.dpi": 300,
        "figure.constrained_layout.use": True,
    })


def figure(nrows: int = 1, ncols: int = 1, *, height_in: float | None = None,
           **subplots: Any) -> tuple[Any, Any]:
    """A figure at the artifact's final width; height from the spec, else width * 0.62/ncols
    per row (capped by the spec)."""
    import matplotlib.pyplot as plt

    style()
    spec = artifact()
    width = float(spec["width_in"])
    height = height_in or spec.get("height_in") or min(
        width * 0.62 / max(ncols, 1) * nrows + 0.3, 8.0)
    return plt.subplots(nrows, ncols, figsize=(width, float(height)), **subplots)


def save(fig: Any) -> list[Path]:
    """Write ``<id>.pdf`` (for the paper) and ``<id>.png`` (300 dpi preview at final size).

    The figure keeps the size it was created with: no tight bounding box, so its width is
    the width the build was asked for."""
    spec = artifact()
    base = out_dir() / spec["id"]
    pdf, png = base.with_suffix(".pdf"), base.with_suffix(".png")
    fig.savefig(pdf, metadata={"CreationDate": None, "ModDate": None})
    fig.savefig(png, dpi=300, metadata={"Software": None})
    return [pdf, png]
