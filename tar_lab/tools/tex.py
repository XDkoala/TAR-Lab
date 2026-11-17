"""Build a LaTeX paper inside the engine's TeX-enabled Docker image (no host TeX needed)."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_IMAGE = "agentic-scientist/agent-cli:codex-0.169.0-pdf"


@dataclass(frozen=True)
class TexBuild:
    pdf: Path | None
    log: Path
    returncode: int


def build(paper_dir: Path, main: str = "main.tex", image: str | None = None,
          timeout: float = 600) -> TexBuild:
    paper_dir = paper_dir.resolve()
    stem = Path(main).stem
    latex = f"pdflatex -interaction=nonstopmode -halt-on-error {main}"
    # bibtex is optional: papers without citations would otherwise fail the build.
    script = (f"{latex} && (bibtex {stem} || true) && {latex} && {latex}")
    cmd = ["docker", "run", "--rm", "--network", "none", "--entrypoint", "sh",
           "-u", f"{os.getuid()}:{os.getgid()}", "-v", f"{paper_dir}:/work", "-w", "/work",
           image or os.environ.get("TARLAB_TEX_IMAGE", DEFAULT_IMAGE), "-c", script]
    log = paper_dir / "build.log"
    with log.open("w", encoding="utf-8") as out:
        proc = subprocess.run(cmd, stdout=out, stderr=subprocess.STDOUT, timeout=timeout,
                              check=False)
    pdf = paper_dir / f"{stem}.pdf"
    return TexBuild(pdf if proc.returncode == 0 and pdf.is_file() else None, log,
                    proc.returncode)
