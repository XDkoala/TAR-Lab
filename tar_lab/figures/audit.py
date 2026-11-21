"""Figure and table checks for ``tarlab tex check``.

- ``generated artifacts`` (FAIL): every artifact in ``figures/spec.toml`` was built; its
  script and inputs still have the hashes recorded at build time (else the paper shows stale
  results); the copies in ``paper/generated/`` are byte-identical to the build outputs (not
  edited by hand); a rebuild in a scratch directory reproduces the outputs byte for byte;
  headline artifacts read only the sealed final evaluation.
- ``figure sizes`` (WARN): generated figures were drawn for the paper's measured column/text
  width and are included at natural size or at that width (scaling changes their font size).
- ``hand-written tables`` (WARN): ``tabular`` environments with decimal numbers outside
  ``generated/``.
- ``image resolution`` (WARN): raster images placed below 300 ppi (``pdfimages -list``).
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

from ..tools.paper_check import FAIL, PASS, SKIP, WARN, Check, strip_comments
from . import build as fb

MIN_PPI = 300
WIDTH_TOLERANCE = 0.02
_INCLUDE = re.compile(r"\\includegraphics\s*(?:\[([^\]]*)\])?\s*\{([^}]*)\}")
_FULL_WIDTH = re.compile(r"^\s*width\s*=\s*(?:1(?:\.0*)?)?\s*\\(columnwidth|linewidth|"
                         r"textwidth|hsize)\s*$")


def artifact_check(workspace: Path, paper_dir: Path, rebuild: bool = True) -> Check:
    spec = fb.load_spec(workspace)
    if spec.errors:
        return Check("generated artifacts", FAIL, f"{fb.SPEC} has errors", spec.errors)
    prov = fb.load_provenance(workspace)
    if prov is None:
        return Check("generated artifacts", FAIL, "never built: run `tarlab figures build`")
    entries: dict[str, Any] = prov.get("artifacts", {})
    issues: list[str] = []
    generated = paper_dir / fb.GENERATED
    for artifact in spec.artifacts:
        entry = entries.get(artifact.id)
        if entry is None:
            issues.append(f"{artifact.id}: not built (or its last build failed)")
            continue
        if entry["script_sha256"] != fb.file_sha256(workspace / artifact.script):
            issues.append(f"{artifact.id}: {artifact.script} changed since the last build")
        if set(entry["inputs"]) != set(artifact.inputs):
            issues.append(f"{artifact.id}: inputs in the spec changed since the last build")
        explicit_font = spec.layout.get("font")
        if entry.get("methods", []) != spec.methods or (
                explicit_font and entry.get("font") != explicit_font):
            issues.append(f"{artifact.id}: [layout] font or [style] methods changed since the "
                          "last build")
        for path, sha in entry["inputs"].items():
            if not (workspace / path).exists():
                issues.append(f"{artifact.id}: input {path} no longer exists")
            elif fb.path_sha256(workspace / path) != sha:
                issues.append(f"{artifact.id}: input {path} changed since the last build")
        for name in entry["paper_outputs"]:
            copy = generated / name
            if not copy.is_file():
                issues.append(f"{artifact.id}: {fb.GENERATED}/{name} missing from the paper")
            elif fb.file_sha256(copy) != entry["outputs"].get(name):
                issues.append(f"{artifact.id}: {fb.GENERATED}/{name} differs from the build "
                              "output (edited by hand or copied from elsewhere)")
    stale = bool(issues)
    if issues:
        issues.append("rerun `tarlab figures build` (never edit generated files)")
    note = ""
    if rebuild and not stale:
        if fb.figures_python() is None:
            note = "; rebuild skipped (no figures environment)"
        else:
            layout = prov.get("layout") or fb.DEFAULT_LAYOUT
            with tempfile.TemporaryDirectory(prefix="tarlab-fig-rebuild-") as tmp:
                again = fb.build(workspace, out_root=Path(tmp), layout=layout)
            issues += again.errors
            for result in again.results:
                if not result.ok:
                    issues.append(f"{result.id}: rebuild failed: {result.message}")
                elif result.outputs != entries[result.id]["outputs"]:
                    differ = sorted(n for n, h in result.outputs.items()
                                    if entries[result.id]["outputs"].get(n) != h)
                    issues.append(f"{result.id}: rebuild does not reproduce "
                                  f"{', '.join(differ)} (non-deterministic script?)")
            note = "; rebuilt and reproduced byte for byte" if not issues else ""
    headline = sum(a.headline for a in spec.artifacts)
    summary = (f"{len(issues)} problem(s)" if issues else
               f"{len(spec.artifacts)} artifact(s) up to date, {headline} headline from "
               f"{fb.FINAL_RESULTS}/{note}")
    return Check("generated artifacts", FAIL if issues else PASS, summary, issues)


def size_check(workspace: Path, tex: str, layout: dict[str, Any] | None) -> Check:
    prov = fb.load_provenance(workspace) or {}
    figures = {i: e for i, e in prov.get("artifacts", {}).items() if e["kind"] == "figure"}
    if not figures:
        return Check("figure sizes", PASS, "no generated figures")
    issues: list[str] = []
    if layout is None:
        issues.append("could not measure \\columnwidth/\\textwidth from the paper")
    else:
        explicit_font = fb.load_spec(workspace).layout.get("font")
        body = layout.get("body_font")
        for ident, entry in figures.items():
            if body and not explicit_font and entry.get("font") != body:
                issues.append(f"{ident}: drawn in the {entry.get('font')} font but the body "
                              f"text is {body} ({layout.get('rmdefault')}); rerun `tarlab "
                              "figures build`")
            key = {"column": "columnwidth_in", "text": "textwidth_in"}.get(entry["width"])
            if key is None:
                continue
            want = layout[key]
            if abs(entry["width_in"] - want) > WIDTH_TOLERANCE * want:
                issues.append(f"{ident}: drawn {entry['width_in']:.2f} in wide but the "
                              f"paper's {entry['width']} is {want:.2f} in; rerun "
                              "`tarlab figures build`")
    for m in _INCLUDE.finditer(tex):
        target = Path(m.group(2).strip())
        if target.parent.as_posix() != fb.GENERATED or target.stem not in figures:
            continue
        options = [o for o in (m.group(1) or "").split(",") if o.strip()]
        sizing = [o.strip() for o in options if re.match(r"\s*(width|height|scale|"
                                                         r"totalheight)\s*=", o)]
        if any(not _FULL_WIDTH.match(o) for o in sizing):
            issues.append(f"{target.stem}: included with [{', '.join(sizing)}]; generated "
                          "figures are drawn at final size, include them without scaling")
    status = WARN if issues else PASS
    return Check("figure sizes", status, f"{len(issues)} issue(s)" if issues else
                 f"{len(figures)} generated figure(s) drawn at their final width", issues)


def _environments(tex: str, name: str) -> list[str]:
    pattern = re.compile(r"\\begin\{" + name + r"\*?\}(.*?)\\end\{" + name + r"\*?\}", re.DOTALL)
    return [m.group(1) for m in pattern.finditer(tex)]


def handwritten_table_check(paper_dir: Path) -> Check:
    found = []
    for path in sorted(paper_dir.rglob("*.tex")):
        rel = path.relative_to(paper_dir)
        if rel.parts[0] == fb.GENERATED:
            continue
        text = strip_comments(path.read_text(encoding="utf-8", errors="replace"))
        for body in _environments(text, "tabular") + _environments(text, "tabularx"):
            numbers = re.findall(r"(?<![\w.])\d+\.\d+", body)
            if numbers:
                found.append(f"{rel}: tabular with {len(numbers)} decimal number(s), e.g. "
                             f"{', '.join(numbers[:3])}")
    return Check("hand-written tables", WARN if found else PASS,
                 f"{len(found)} table(s) with typed numbers; generate them with `tarlab "
                 "figures build`" if found else "none", found)


def parse_pdfimages(text: str) -> list[tuple[int, str, int, int]]:
    """(page, type, x-ppi, y-ppi) per image from ``pdfimages -list``."""
    rows = []
    for line in text.splitlines()[2:]:
        tokens = line.split()
        if len(tokens) < 14 or not tokens[0].isdigit():
            continue
        try:
            rows.append((int(tokens[0]), tokens[2], int(tokens[12]), int(tokens[13])))
        except ValueError:
            continue
    return rows


def resolution_check(pdfimages_out: str | None) -> Check:
    if pdfimages_out is None:
        return Check("image resolution", SKIP, "pdfimages not available or failed")
    images = [r for r in parse_pdfimages(pdfimages_out) if r[1] == "image"]
    low = [f"page {p}: {x}x{y} ppi" for p, _, x, y in images if min(x, y) < MIN_PPI]
    if low:
        return Check("image resolution", WARN, f"{len(low)} raster image(s) below {MIN_PPI} "
                     "ppi; use vector PDF figures or re-export at higher dpi", low)
    return Check("image resolution", PASS, f"{len(images)} raster image(s), all >= {MIN_PPI} "
                 "ppi" if images else "no raster images")
