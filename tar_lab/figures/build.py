"""Paper figures and tables generated from data (``tarlab figures ...``).

A workspace declares its artifacts in ``figures/spec.toml``::

    [layout]                 # optional: measured from paper/main.tex when omitted
    font = "serif"           # serif (Times-like), cm (Computer Modern) or sans; default:
                             # the paper's body font

    [style]
    methods = ["A", "B"]     # fixed order: tf.method_style(name) gives each the same look

    [[figure]]
    id = "success_curve"
    script = "figures/scripts/success_curve.py"
    inputs = ["results/final/o12/metrics.json"]   # files or directories (all files under it)
    width = "column"         # column, text, or a number of inches
    height_in = 2.0          # optional
    headline = true          # inputs must come from the sealed final evaluation
    claim = "Planner B reaches the goal more often than A at every budget."

    [[table]]
    id = "main_results"
    script = "figures/scripts/main_results.py"
    inputs = ["results/final/o12/metrics.json"]
    headline = true

    [[diagram]]              # structure/method figure, see tar_lab.figures.diagram
    id = "method_overview"
    source = "figures/diagrams/method.dot"   # Graphviz (.dot/.gv) or a tikzpicture (.tex)
    width = "column"         # maximum width; the diagram keeps its natural size
    claim = "The planner queries the frozen world model only through the cost head."

``build`` runs every script with the dedicated figures interpreter under a guard that lets
it read only its inputs (:mod:`tar_lab.figures.runtime.tarlab_runner`), writes the outputs
to ``figures/out/<id>/``, copies the paper files (``<id>.pdf``, ``<id>.tex``) into
``paper/generated/`` and records the hashes of script, inputs and outputs in
``figures/out/provenance.json``. Output is byte-deterministic, so ``tex check`` can rebuild
and compare.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..tools.tex import LOCAL_TOOLS
from ..tools.tex import build as build_tex
from .diagram import SUFFIXES, DiagramError
from .diagram import render as render_diagram

PACKAGE_DIR = Path(__file__).resolve().parent
RUNNER = PACKAGE_DIR / "runtime" / "tarlab_runner.py"
LOCK = PACKAGE_DIR / "requirements.lock"
LOCAL_ENV = LOCAL_TOOLS / "figures" / "venv"
SPEC = Path("figures") / "spec.toml"
OUT = Path("figures") / "out"
GENERATED = "generated"  # under the paper directory
PROVENANCE = "provenance.json"
LAYOUT_CACHE = "layout.json"
FINAL_RESULTS = Path("results") / "final"
DEFAULT_LAYOUT = {"columnwidth_in": 3.5, "textwidth_in": 7.0}
FONTS = ("serif", "cm", "sans")
TIMEOUT = 300.0
PT_PER_IN = 72.27
LAYOUT_MARK = "TARLAB-LAYOUT"


# --- environment -------------------------------------------------------------------------


def figures_python() -> Path | None:
    """The figures interpreter (``TARLAB_FIGURES_PYTHON`` overrides the local venv)."""
    override = os.environ.get("TARLAB_FIGURES_PYTHON")
    candidate = Path(override) if override else LOCAL_ENV / "bin" / "python"
    return candidate if candidate.is_file() and os.access(candidate, os.X_OK) else None


def setup(env_dir: Path = LOCAL_ENV, python: str = "3.11") -> int:
    """Create the figures venv from the hash-pinned lock with uv."""
    uv = shutil.which("uv")
    if uv is None:
        print("uv not found on PATH (https://docs.astral.sh/uv/)", file=sys.stderr)
        return 127
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env_dir.parent.mkdir(parents=True, exist_ok=True)
    for cmd in ([uv, "venv", "--allow-existing", "--python", python, str(env_dir)],
                [uv, "pip", "sync", "--python", str(env_dir / "bin" / "python"), str(LOCK)]):
        proc = subprocess.run(cmd, env=env, check=False)
        if proc.returncode:
            return proc.returncode
    return 0


# --- spec --------------------------------------------------------------------------------


@dataclass
class Artifact:
    id: str
    kind: str  # "figure" or "table"
    script: str
    inputs: list[str]
    width: str | float = "column"
    height_in: float | None = None
    headline: bool = False
    claim: str = ""

    def outputs(self) -> list[str]:
        """Files the build must produce (diagrams add a PNG preview when pdftoppm exists)."""
        if self.kind == "figure":
            return [f"{self.id}.pdf", f"{self.id}.png"]
        if self.kind == "diagram":
            return [f"{self.id}.pdf", f"{self.id}.tikz.tex"]
        return [f"{self.id}.tex"]

    def paper_outputs(self) -> list[str]:
        return [f"{self.id}.tex"] if self.kind == "table" else [f"{self.id}.pdf"]


@dataclass
class Spec:
    artifacts: list[Artifact]
    layout: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)


def _relative(workspace: Path, value: Any, what: str, errors: list[str]) -> str | None:
    if not isinstance(value, str) or not value:
        errors.append(f"{what}: expected a workspace-relative path")
        return None
    p = Path(value)
    if p.is_absolute() or ".." in p.parts:
        errors.append(f"{what}: {value!r} must be relative to the workspace, without '..'")
        return None
    if not (workspace / p).exists():
        errors.append(f"{what}: {value} does not exist")
    return p.as_posix()


def load_spec(workspace: Path) -> Spec:
    path = workspace / SPEC
    if not path.is_file():
        return Spec([], errors=[f"{SPEC} not found"])
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        return Spec([], errors=[f"{SPEC}: {exc}"])
    errors: list[str] = []
    layout = raw.get("layout", {})
    if not isinstance(layout, dict):
        errors.append("[layout] must be a table")
        layout = {}
    if layout.get("font", "serif") not in FONTS:
        errors.append(f"[layout] font must be one of {', '.join(FONTS)}")
    style = raw.get("style", {})
    methods = style.get("methods", []) if isinstance(style, dict) else None
    if not isinstance(methods, list) or not all(isinstance(m, str) for m in methods) or len(
            set(methods)) != len(methods):
        errors.append("[style] methods must be a list of distinct names")
        methods = []
    artifacts: list[Artifact] = []
    seen: set[str] = set()
    for kind in ("figure", "table", "diagram"):
        for i, item in enumerate(raw.get(kind, [])):
            where = f"[[{kind}]] #{i + 1}"
            ident = item.get("id")
            if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9][\w-]*", ident):
                errors.append(f"{where}: id must be letters, digits, '_' or '-'")
                continue
            where = f"{kind} {ident}"
            if ident in seen:
                errors.append(f"{where}: duplicate id")
            seen.add(ident)
            key = "source" if kind == "diagram" else "script"
            script = _relative(workspace, item.get(key), f"{where} {key}", errors)
            if script and (not script.startswith("figures/") or script.startswith(
                    OUT.as_posix() + "/")):
                errors.append(f"{where}: {key} must live under figures/ (not figures/out/)")
            if kind == "diagram" and script and not script.lower().endswith(SUFFIXES):
                errors.append(f"{where}: source must end in {', '.join(SUFFIXES)}")
            raw_inputs = item.get("inputs", [])
            if not isinstance(raw_inputs, list) or (not raw_inputs and kind != "diagram"):
                errors.append(f"{where}: inputs must list the data files it reads")
                raw_inputs = []
            inputs = [p for j, v in enumerate(raw_inputs)
                      if (p := _relative(workspace, v, f"{where} inputs[{j}]", errors))]
            headline = bool(item.get("headline", False))
            if headline:
                for p in inputs:
                    if not p.startswith(FINAL_RESULTS.as_posix() + "/"):
                        errors.append(f"{where}: headline artifact reads {p}, which is not "
                                      f"under {FINAL_RESULTS}/ (the sealed final evaluation)")
            width = item.get("width", "column")
            if not (width in ("column", "text") or (
                    isinstance(width, int | float) and not isinstance(width, bool)
                    and 0.5 <= width <= 20)):
                errors.append(f"{where}: width must be 'column', 'text' or inches")
            height = item.get("height_in")
            if height is not None and not (isinstance(height, int | float) and 0.3 <= height
                                           <= 20):
                errors.append(f"{where}: height_in must be a number of inches")
            artifacts.append(Artifact(ident, kind, script or "", inputs, width,
                                      float(height) if height else None, headline,
                                      str(item.get("claim", ""))))
    if not artifacts and not errors:
        errors.append(f"{SPEC} declares no [[figure]] or [[table]]")
    return Spec(artifacts, layout, errors, methods)


# --- hashing -----------------------------------------------------------------------------


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def path_sha256(path: Path) -> str:
    """A file's hash, or for a directory the hash of its sorted (path, hash) list."""
    if path.is_file():
        return file_sha256(path)
    h = hashlib.sha256()
    for p in sorted(q for q in path.rglob("*") if q.is_file()):
        h.update(f"{p.relative_to(path).as_posix()}\0{file_sha256(p)}\n".encode())
    return h.hexdigest()


# --- layout ------------------------------------------------------------------------------


def insert_layout_probe(main: Path) -> bool:
    """Make ``main`` print its column and text widths to the log; False if not possible."""
    text = main.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"\\begin\s*\{document\}", text)
    if not m:
        return False
    probe = (f"\\typeout{{{LAYOUT_MARK} columnwidth=\\the\\columnwidth;"
             f"textwidth=\\the\\textwidth;rm=\\rmdefault;sf=\\sfdefault;"
             f"family=\\familydefault;}}")
    main.write_text(text[:m.end()] + probe + text[m.end():], encoding="utf-8")
    return True


_CM_FAMILIES = ("cmr", "lmr", "cmss", "lmss")


def body_font(rm: str, sf: str, family: str) -> str:
    """The figure font closest to the body text: ``cm`` for Computer/Latin Modern, ``sans``
    for sans-serif bodies, else ``serif`` (Times-like, as most venue classes use)."""
    if family == sf and family != rm:
        return "sans"
    return "cm" if rm.lower() in _CM_FAMILIES else "serif"


def parse_layout(log_text: str) -> dict[str, Any] | None:
    m = re.search(LAYOUT_MARK + r" columnwidth=([\d.]+)pt;textwidth=([\d.]+)pt"
                  r"(?:;rm=([^;]*);sf=([^;]*);family=([^;]*);)?", log_text)
    if not m:
        return None
    layout: dict[str, Any] = {"columnwidth_in": round(float(m.group(1)) / PT_PER_IN, 4),
                              "textwidth_in": round(float(m.group(2)) / PT_PER_IN, 4)}
    if m.group(3) is not None:
        layout["rmdefault"] = m.group(3).strip()
        layout["body_font"] = body_font(m.group(3).strip(), m.group(4).strip(),
                                        m.group(5).strip())
    return layout


def _preamble_sha(main: Path) -> str:
    text = main.read_text(encoding="utf-8", errors="replace")
    preamble = re.split(r"\\begin\s*\{document\}", text)[0]
    return hashlib.sha256(("layout-v2\0" + preamble).encode()).hexdigest()


def measure_layout(paper_dir: Path, main: str = "main.tex") -> dict[str, Any] | None:
    """Build a copy of the paper with a probe and read \\columnwidth and \\textwidth."""
    from ..tools.paper_check import copy_sources

    with tempfile.TemporaryDirectory(prefix="tarlab-layout-") as tmp:
        work = Path(tmp) / "paper"
        copy_sources(paper_dir, work, main)
        if not insert_layout_probe(work / main):
            return None
        build_tex(work, main)
        log = work / f"{Path(main).stem}.log"
        return parse_layout(log.read_text(encoding="utf-8", errors="replace")) if \
            log.is_file() else None


def resolve_layout(workspace: Path, spec: Spec, paper_dir: Path | None,
                   main: str = "main.tex") -> tuple[dict[str, Any], str]:
    """Widths in inches and where they came from (spec, measured paper, or default)."""
    if "columnwidth_in" in spec.layout and "textwidth_in" in spec.layout:
        return ({"columnwidth_in": float(spec.layout["columnwidth_in"]),
                 "textwidth_in": float(spec.layout["textwidth_in"])}, "spec")
    if paper_dir is not None and (paper_dir / main).is_file():
        cache = workspace / OUT / LAYOUT_CACHE
        key = _preamble_sha(paper_dir / main)
        if cache.is_file():
            cached = json.loads(cache.read_text(encoding="utf-8"))
            if cached.get("preamble_sha256") == key:
                return cached["layout"], f"measured from {paper_dir.name}/{main}"
        measured = measure_layout(paper_dir, main)
        if measured:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"preamble_sha256": key, "layout": measured},
                                        indent=2) + "\n", encoding="utf-8")
            return measured, f"measured from {paper_dir.name}/{main}"
    return dict(DEFAULT_LAYOUT), "default (no paper to measure; set [layout] in the spec)"


def figure_font(spec: Spec, layout: dict[str, Any]) -> str:
    """``[layout] font`` if set, else the measured body font, else ``serif``."""
    return str(spec.layout.get("font") or layout.get("body_font") or "serif")


def width_in(artifact: Artifact, layout: dict[str, Any]) -> float:
    if artifact.width == "column":
        return layout["columnwidth_in"]
    if artifact.width == "text":
        return layout["textwidth_in"]
    return float(artifact.width)


# --- build -------------------------------------------------------------------------------


@dataclass
class Result:
    id: str
    ok: bool
    message: str = ""
    outputs: dict[str, str] = field(default_factory=dict)  # name -> sha256


@dataclass
class BuildReport:
    results: list[Result]
    layout: dict[str, Any]
    layout_source: str
    errors: list[str] = field(default_factory=list)
    font: str = "serif"

    @property
    def ok(self) -> bool:
        return not self.errors and all(r.ok for r in self.results)

    def render(self) -> str:
        lines = [(f"Layout: column {self.layout['columnwidth_in']:.3f} in, text "
                 f"{self.layout['textwidth_in']:.3f} in, font {self.font} "
                 f"({self.layout_source})")]
        lines += [f"ERROR {e}" for e in self.errors]
        for r in self.results:
            lines.append(f"{'OK   ' if r.ok else 'FAIL '} {r.id}"
                         + (f": {r.message}" if r.message else ""))
        return "\n".join(lines)


def _run_artifact(python: Path, workspace: Path, artifact: Artifact, out_dir: Path,
                  layout: dict[str, Any], font: str, methods: list[str]) -> Result:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    payload = {"id": artifact.id, "kind": artifact.kind, "workspace": str(workspace),
               "out_dir": str(out_dir), "inputs": artifact.inputs,
               "code_dirs": [Path(artifact.script).parent.as_posix()],
               "width_in": width_in(artifact, layout), "height_in": artifact.height_in,
               "font": font, "methods": methods, "claim": artifact.claim}
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update({"TARLAB_FIG_ARTIFACT": json.dumps(payload), "PYTHONHASHSEED": "0",
                "PYTHONDONTWRITEBYTECODE": "1", "SOURCE_DATE_EPOCH": "0",
                "MPLBACKEND": "Agg",
                "MPLCONFIGDIR": str(Path(os.environ.get(
                    "XDG_CACHE_HOME", Path.home() / ".cache")) / "tar-lab" / "matplotlib")})
    try:
        proc = subprocess.run([str(python), str(RUNNER), str(workspace / artifact.script)],
                              cwd=workspace, env=env, capture_output=True, text=True,
                              timeout=TIMEOUT, check=False, errors="replace")
    except subprocess.TimeoutExpired:
        return Result(artifact.id, False, f"timed out after {TIMEOUT:.0f}s")
    (out_dir / "script.log").write_text(proc.stdout + proc.stderr, encoding="utf-8")
    if proc.returncode:
        tail = (proc.stderr.strip().splitlines() or ["(no output)"])[-1]
        return Result(artifact.id, False, f"script failed: {tail}")
    missing = [n for n in artifact.outputs() if not (out_dir / n).is_file()]
    if missing:
        helper = "tf.save(fig)" if artifact.kind == "figure" else "tf.save_table(...)"
        return Result(artifact.id, False, f"script did not write {', '.join(missing)} "
                      f"(call {helper})")
    return Result(artifact.id, True,
                  outputs={n: file_sha256(out_dir / n) for n in artifact.outputs()})


def _run_diagram(workspace: Path, artifact: Artifact, out_dir: Path,
                 layout: dict[str, Any], font: str) -> Result:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    try:
        names, width = render_diagram(workspace, artifact.script, artifact.inputs, out_dir,
                                      artifact.id, font, width_in(artifact, layout))
    except DiagramError as exc:
        return Result(artifact.id, False, str(exc))
    note = f"{width:.2f} in wide" if width is not None else ""
    return Result(artifact.id, True, note, {n: file_sha256(out_dir / n) for n in names})


def build(workspace: Path, paper_dir: Path | None = None, only: list[str] | None = None,
          out_root: Path | None = None, layout: dict[str, Any] | None = None,
          main: str = "main.tex") -> BuildReport:
    """Build the artifacts; with ``out_root`` (a scratch directory) nothing in the workspace
    or paper is written, which is how ``tex check`` rebuilds for comparison."""
    workspace = workspace.resolve()
    spec = load_spec(workspace)
    scratch = out_root is not None
    if layout is not None:
        source = "given"
    else:
        layout, source = resolve_layout(workspace, spec, paper_dir, main)
    report = BuildReport([], layout, source, list(spec.errors), figure_font(spec, layout))
    python = figures_python()
    selected = [a for a in spec.artifacts if not only or a.id in only]
    if python is None and any(a.kind != "diagram" for a in selected):
        report.errors.append("figures environment not found: run `tarlab figures setup`")
    if report.errors:
        return report
    unknown = sorted(set(only or []) - {a.id for a in spec.artifacts})
    if unknown:
        report.errors.append(f"unknown artifact id(s): {', '.join(unknown)}")
        return report
    root = out_root or workspace / OUT
    font = figure_font(spec, layout)
    for artifact in selected:
        if artifact.kind == "diagram":
            report.results.append(_run_diagram(workspace, artifact, root / artifact.id,
                                               layout, font))
        else:
            assert python is not None
            report.results.append(_run_artifact(python, workspace, artifact,
                                                root / artifact.id, layout, font,
                                                spec.methods))
    if not scratch:
        _record(workspace, spec, selected, report, paper_dir, font)
    return report


def _record(workspace: Path, spec: Spec, built: list[Artifact], report: BuildReport,
            paper_dir: Path | None, font: str) -> None:
    prov_path = workspace / OUT / PROVENANCE
    old = json.loads(prov_path.read_text(encoding="utf-8")) if prov_path.is_file() else {}
    entries: dict[str, Any] = old.get("artifacts", {})
    declared = {a.id for a in spec.artifacts}
    generated = paper_dir / GENERATED if paper_dir is not None and paper_dir.is_dir() else None
    for ident in [i for i in entries if i not in declared]:
        if generated is not None:
            for name in entries[ident].get("paper_outputs", []):
                (generated / name).unlink(missing_ok=True)
        del entries[ident]
    results = {r.id: r for r in report.results}
    for artifact in built:
        result = results[artifact.id]
        if not result.ok:
            entries.pop(artifact.id, None)
            continue
        if generated is not None:
            generated.mkdir(exist_ok=True)
            for name in artifact.paper_outputs():
                shutil.copyfile(workspace / OUT / artifact.id / name, generated / name)
        entries[artifact.id] = {
            "kind": artifact.kind, "script": artifact.script,
            "script_sha256": file_sha256(workspace / artifact.script),
            "inputs": {p: path_sha256(workspace / p) for p in artifact.inputs},
            "outputs": result.outputs, "paper_outputs": artifact.paper_outputs(),
            "width": artifact.width, "width_in": width_in(artifact, report.layout),
            "height_in": artifact.height_in, "font": font, "methods": spec.methods,
            "headline": artifact.headline,
            "claim": artifact.claim,
        }
    prov_path.parent.mkdir(parents=True, exist_ok=True)
    prov_path.write_text(json.dumps({
        "layout": report.layout, "layout_source": report.layout_source,
        "paper_dir": (os.path.relpath(paper_dir.resolve(), workspace)
                      if paper_dir is not None else None),
        "environment": environment_versions(), "artifacts": entries,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def environment_versions() -> dict[str, str]:
    python = figures_python()
    if python is None:
        return {}
    code = ("import json, sys, matplotlib, numpy, pandas, scipy; print(json.dumps({"
            "'python': sys.version.split()[0], 'matplotlib': matplotlib.__version__, "
            "'numpy': numpy.__version__, 'pandas': pandas.__version__, "
            "'scipy': scipy.__version__}))")
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    proc = subprocess.run([str(python), "-c", code], capture_output=True, text=True,
                          env=env, check=False)
    try:
        versions: dict[str, str] = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}
    return versions


def load_provenance(workspace: Path) -> dict[str, Any] | None:
    path = workspace / OUT / PROVENANCE
    if not path.is_file():
        return None
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data
