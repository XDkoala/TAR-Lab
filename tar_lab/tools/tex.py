"""Build a LaTeX paper on the host.

Engines, in order of preference:

1. The local TeX Live next to the repository (``.tools/texlive/<year>/bin/<arch>``, the newest
   year wins), or the directory in ``TARLAB_TEXLIVE_BIN``. This is what arXiv and the
   conference systems run (``pdflatex``), so fonts and layout match the camera-ready.
2. ``latexmk`` + ``pdflatex`` on ``PATH`` (a system TeX Live); plain ``pdflatex`` + ``bibtex``
   reruns if ``latexmk`` is missing.
3. Tectonic as a last resort (``TARLAB_TECTONIC``, ``.tools/tectonic/bin/tectonic`` or
   ``tectonic`` on ``PATH``). It is XeTeX-based, so fonts and line breaks can differ from
   ``pdflatex`` venues.

TeX Live builds run ``latexmk -pdf`` (it reruns BibTeX/Biber and LaTeX as needed) with restricted
shell escape (only TeX Live's whitelisted tools, e.g. ``repstopdf`` for EPS figures). The build output goes to ``build.log`` in the paper directory; its first line
names the engine used.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

LOCAL_TOOLS = Path(__file__).resolve().parents[3] / ".tools"
LOCAL_TECTONIC = LOCAL_TOOLS / "tectonic" / "bin" / "tectonic"
LOCAL_TEXLIVE = LOCAL_TOOLS / "texlive"


@dataclass(frozen=True)
class TexBuild:
    pdf: Path | None
    log: Path
    returncode: int
    engine: str = ""


@dataclass(frozen=True)
class TexEngine:
    name: str  # "texlive", "texlive-path", "pdflatex-path" or "tectonic"
    command: list[str]
    bin_dir: Path | None = None  # prepended to PATH so latexmk finds pdflatex/bibtex

    def describe(self) -> str:
        where = f" ({self.bin_dir})" if self.bin_dir else ""
        return f"{self.name}{where}: {' '.join(self.command)}"


def _executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def find_texlive_bin(root: Path | None = None) -> Path | None:
    """The bin directory of the local TeX Live (``TARLAB_TEXLIVE_BIN`` overrides)."""
    override = os.environ.get("TARLAB_TEXLIVE_BIN")
    if override:
        return Path(override) if _executable(Path(override) / "latexmk") else None
    root = LOCAL_TEXLIVE if root is None else root
    years = sorted((p for p in root.glob("[0-9][0-9][0-9][0-9]") if p.is_dir()), reverse=True)
    for year in years:
        for arch in ("x86_64-linux", *sorted(p.name for p in (year / "bin").glob("*"))):
            candidate = year / "bin" / arch
            if _executable(candidate / "latexmk") and _executable(candidate / "pdflatex"):
                return candidate
    return None


def find_tectonic() -> str | None:
    for candidate in (os.environ.get("TARLAB_TECTONIC"), str(LOCAL_TECTONIC)):
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    return shutil.which("tectonic")


def latexmk_command(main: str, latexmk: str = "latexmk") -> list[str]:
    return [latexmk, "-pdf", "-interaction=nonstopmode", "-halt-on-error", "-shell-restricted",
            "-file-line-error", main]


def select_engine(main: str) -> TexEngine:
    """Pick the TeX engine; raises FileNotFoundError when there is none."""
    bin_dir = find_texlive_bin()
    if bin_dir:
        return TexEngine("texlive", latexmk_command(main, str(bin_dir / "latexmk")), bin_dir)
    latexmk, pdflatex = shutil.which("latexmk"), shutil.which("pdflatex")
    if latexmk and pdflatex:
        return TexEngine("texlive-path", latexmk_command(main, latexmk))
    if pdflatex:
        stem = Path(main).stem
        latex = f"pdflatex -interaction=nonstopmode -halt-on-error -file-line-error {main}"
        # bibtex is optional: papers without citations would otherwise fail the build.
        return TexEngine("pdflatex-path", [
            "sh", "-c", f"{latex} && (bibtex {stem} || true) && {latex} && {latex}"])
    tectonic = find_tectonic()
    if tectonic:
        return TexEngine("tectonic", [tectonic, "-X", "compile", "--keep-logs", main])
    raise FileNotFoundError(
        "no TeX engine found: install TeX Live into .tools/texlive (see README), "
        "put latexmk/pdflatex on PATH, or install Tectonic into .tools/tectonic/bin")


def build_command(main: str) -> list[str]:
    """The command run inside the paper directory."""
    return select_engine(main).command


def build(paper_dir: Path, main: str = "main.tex", timeout: float = 600) -> TexBuild:
    paper_dir = paper_dir.resolve()
    log = paper_dir / "build.log"
    with log.open("w", encoding="utf-8") as out:
        try:
            engine = select_engine(main)
        except FileNotFoundError as exc:
            out.write(f"{exc}\n")
            return TexBuild(None, log, 127)
        out.write(f"[tarlab] engine: {engine.describe()}\n")
        out.flush()
        env = dict(os.environ)
        # Unwrapped log lines keep citation keys and file names intact for `tex check`.
        env["max_print_line"] = "10000"
        if engine.bin_dir:
            env["PATH"] = f"{engine.bin_dir}{os.pathsep}{env.get('PATH', '')}"
        try:
            proc = subprocess.run(engine.command, cwd=paper_dir, stdout=out,
                                  stderr=subprocess.STDOUT, timeout=timeout, check=False,
                                  env=env)
        except FileNotFoundError as exc:
            out.write(f"{exc}\n")
            return TexBuild(None, log, 127, engine.name)
        except subprocess.TimeoutExpired:
            out.write(f"[tarlab] build timed out after {timeout:.0f}s\n")
            return TexBuild(None, log, 124, engine.name)
    pdf = paper_dir / f"{Path(main).stem}.pdf"
    return TexBuild(pdf if proc.returncode == 0 and pdf.is_file() else None, log,
                    proc.returncode, engine.name)
