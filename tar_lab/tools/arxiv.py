"""arXiv submission checks (``tarlab tex check --arxiv``, ``tarlab tex arxiv``).

arXiv compiles LaTeX with ``pdflatex`` from its own TeX Live (2025 by default, frozen at
2025-08-03; 2023 on request), from the root of the uploaded files, running BibTeX/Biber when
the ``.bib`` is present. To reproduce that, the paper is built once, the files the build
actually read are taken from the recorder file (``<main>.fls``) into a package, and the
package alone is built again with the TeX Live in ``.tools/texlive-arxiv/<year>``
(``TARLAB_ARXIV_TEXLIVE_BIN`` overrides). The package (plus the ``.bbl``) is what to upload.

Rule checks follow arXiv's notes for TeX Live 2025
(https://info.arxiv.org/help/faq/texlive.html, https://info.arxiv.org/help/submit_tex.html).
"""

from __future__ import annotations

import gzip
import io
import os
import re
import shutil
import tarfile
from pathlib import Path

from .paper_check import BUILD_PRODUCTS, FAIL, PASS, WARN, Check, strip_comments
from .tex import LOCAL_TOOLS, find_texlive_bin

LOCAL_ARXIV_TEXLIVE = LOCAL_TOOLS / "texlive-arxiv"
ARXIV_TEXLIVE_YEAR = "2025"
MAX_PACKAGE_BYTES = 50 * 1024 * 1024  # arXiv's limit for a submission
_SAFE_NAME = re.compile(r"[A-Za-z0-9._+-]+")
_KEEP_PRODUCTS = (".bbl", ".ind", ".gls", ".nls")  # arXiv keeps these, see submit_tex


def find_arxiv_texlive_bin() -> Path | None:
    override = os.environ.get("TARLAB_ARXIV_TEXLIVE_BIN")
    if override:
        path = Path(override)
        return path if (path / "pdflatex").is_file() else None
    saved = os.environ.pop("TARLAB_TEXLIVE_BIN", None)
    try:
        return find_texlive_bin(LOCAL_ARXIV_TEXLIVE)
    finally:
        if saved is not None:
            os.environ["TARLAB_TEXLIVE_BIN"] = saved


def recorded_inputs(work: Path, main: str) -> list[str]:
    """Workspace-relative files read by the build, from latexmk's recorder file."""
    fls = work / f"{Path(main).stem}.fls"
    if not fls.is_file():
        return []
    root = work.resolve()
    pwd = root
    found: dict[str, None] = {}
    for line in fls.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("PWD "):
            pwd = Path(line[4:].strip())
        elif line.startswith("INPUT "):
            raw = Path(line[6:].strip())
            path = (raw if raw.is_absolute() else pwd / raw).resolve()
            if path.is_file() and path.is_relative_to(root):
                found[path.relative_to(root).as_posix()] = None
    return list(found)


def package_files(work: Path, main: str, bibs: list[Path]) -> list[str]:
    """What to upload: the recorded inputs without build products (but with ``.bbl`` and
    friends), the main file and the bibliography databases."""
    stem = Path(main).stem
    files = dict.fromkeys(recorded_inputs(work, main))
    files[main] = None
    for keep in _KEEP_PRODUCTS:
        if (work / f"{stem}{keep}").is_file():
            files[f"{stem}{keep}"] = None
    for bib in bibs:
        files[bib.resolve().relative_to(work.resolve()).as_posix()] = None
    products = {p.lstrip("*") for p in BUILD_PRODUCTS} - set(_KEEP_PRODUCTS)
    out = []
    for rel in files:
        name = Path(rel).name
        if any(name.endswith(p) for p in products) or name in (f"{stem}.pdf", "build.log"):
            continue
        out.append(rel)
    # EPS figures converted during the build: ship the converted PDF (arXiv's pdflatex may
    # not run the converter) next to the original.
    for rel in list(out):
        if rel.endswith("-eps-converted-to.pdf"):
            eps = rel.removesuffix("-eps-converted-to.pdf") + ".eps"
            if (work / eps).is_file() and eps not in out:
                out.append(eps)
    return sorted(out)


def make_package(work: Path, files: list[str], dest: Path) -> int:
    """Copy ``files`` from ``work`` to ``dest``; returns the total size in bytes."""
    total = 0
    for rel in files:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(work / rel, target)
        total += target.stat().st_size
    return total


def write_tarball(pkg: Path, files: list[str], out: Path) -> None:
    """A deterministic ``.tar.gz`` (sorted names, zero times and owners)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for rel in sorted(files):
            data = (pkg / rel).read_bytes()
            info = tarfile.TarInfo(rel)
            info.size, info.mtime, info.mode = len(data), 0, 0o644
            tar.addfile(info, io.BytesIO(data))

    with out.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0,
                                              filename="") as gz:
        gz.write(buf.getvalue())


_PATH_COMMANDS = re.compile(r"\\(?:input|include|includegraphics|bibliography|"
                            r"addbibresource|subfile)\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")


def rule_check(paper_dir: Path, tex: str, files: list[str], size: int,
               unused: list[str]) -> Check:
    tex = strip_comments(tex)
    fails: list[str] = []
    warns: list[str] = []
    for m in _PATH_COMMANDS.finditer(tex):
        for target in (t.strip() for t in m.group(1).split(",")):
            if target.startswith(("/", "~")) or re.match(r"[A-Za-z]:[\\/]", target):
                fails.append(f"absolute path {target!r}: arXiv compiles from the upload root")
            elif ".." in Path(target).parts:
                fails.append(f"path {target!r} leaves the paper directory")
    packages = re.findall(r"\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}",
                          tex)
    order = [p.strip() for group in packages for p in group.split(",")]
    if "hyperxmp" in order and ("hyperref" not in order or
                                order.index("hyperxmp") < order.index("hyperref")):
        fails.append("hyperxmp is loaded before hyperref, which fails on TeX Live 2025: load "
                     "hyperref first and set its options with \\hypersetup")
    if "minted" in order and not any(f.startswith("_minted") for f in files):
        fails.append("minted needs shell escape, which arXiv does not run: compile once with "
                     "-shell-escape and upload the _minted directory")
    if "cleveref" in order and re.search(r"\\newtheorem", tex):
        warns.append("cleveref with \\newtheorem: on TeX Live 2025 every \\cref gets the same "
                     "name; add \\AddToHook{env/<name>/begin}{\\crefalias{section}{<name>}} "
                     "per theorem environment, or use zref-clever")
    if re.search(r"(?<!\\begin\{)\\(?:single|double|onehalf)space\b(?!ing)", tex):
        warns.append("\\singlespace/\\doublespace used as a command: they are environments, and "
                     "a later \\usepackage then fails on TeX Live 2025; use \\doublespacing")
    for rel in files:
        parts = Path(rel).parts
        if any(p.startswith(".") for p in parts):
            warns.append(f"{rel}: hidden files are deleted when the paper is announced")
        if not all(_SAFE_NAME.fullmatch(p) for p in parts):
            warns.append(f"{rel}: use only letters, digits, '.', '_', '+', '-' in file names")
    if size > MAX_PACKAGE_BYTES:
        fails.append(f"package is {size / 2**20:.1f} MiB, over arXiv's 50 MiB limit: compress "
                     "or downsample figures")
    summary = (f"{len(files)} file(s), {size / 2**20:.2f} MiB"
               + (f"; {len(unused)} unused file(s) in {paper_dir.name}/ left out" if unused
                  else ""))
    status = FAIL if fails else WARN if warns else PASS
    left_out = [f"left out (not read by the build): {', '.join(unused[:10])}"
                + (" ..." if len(unused) > 10 else "")] if unused else []
    return Check("arxiv rules", status, summary, fails + warns + left_out)
