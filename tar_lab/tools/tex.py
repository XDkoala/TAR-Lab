"""Build a LaTeX paper on the host.

Tectonic is preferred: one self-contained binary (``.tools/tectonic/bin/tectonic`` next to
the repository, or ``TARLAB_TECTONIC``, or ``tectonic`` on ``PATH``) that reruns LaTeX and
BibTeX as needed and fetches missing packages on first use. A host TeX Live
(``pdflatex`` + ``bibtex``) is used when Tectonic is absent.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

LOCAL_TECTONIC = Path(__file__).resolve().parents[3] / ".tools" / "tectonic" / "bin" / "tectonic"


@dataclass(frozen=True)
class TexBuild:
    pdf: Path | None
    log: Path
    returncode: int


def find_tectonic() -> str | None:
    for candidate in (os.environ.get("TARLAB_TECTONIC"), str(LOCAL_TECTONIC)):
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    return shutil.which("tectonic")


def build_command(main: str) -> list[str]:
    """The command run inside the paper directory."""
    tectonic = find_tectonic()
    if tectonic:
        return [tectonic, "-X", "compile", "--keep-logs", main]
    if shutil.which("pdflatex"):
        stem = Path(main).stem
        latex = f"pdflatex -interaction=nonstopmode -halt-on-error {main}"
        # bibtex is optional: papers without citations would otherwise fail the build.
        return ["sh", "-c", f"{latex} && (bibtex {stem} || true) && {latex} && {latex}"]
    raise FileNotFoundError(
        "no TeX engine found: install Tectonic into .tools/tectonic/bin (see README) "
        "or TeX Live (pdflatex)")


def build(paper_dir: Path, main: str = "main.tex", timeout: float = 600) -> TexBuild:
    paper_dir = paper_dir.resolve()
    log = paper_dir / "build.log"
    with log.open("w", encoding="utf-8") as out:
        try:
            proc = subprocess.run(build_command(main), cwd=paper_dir, stdout=out,
                                  stderr=subprocess.STDOUT, timeout=timeout, check=False)
        except FileNotFoundError as exc:
            out.write(f"{exc}\n")
            return TexBuild(None, log, 127)
    pdf = paper_dir / f"{Path(main).stem}.pdf"
    return TexBuild(pdf if proc.returncode == 0 and pdf.is_file() else None, log,
                    proc.returncode)
