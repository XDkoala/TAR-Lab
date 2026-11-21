"""Pre-submission check of a LaTeX paper directory (``tarlab tex check``).

The paper is copied without build products to a fresh temporary directory and built there
with :func:`tar_lab.tools.tex.build`, as arXiv and venue systems do, which proves the source
tree is self-contained. Then the LaTeX log, the PDF (``pdffonts``, ``pdfinfo``,
``pdftotext`` from poppler; a check is skipped when its tool is missing), the bibliography
and, optionally, anonymity are checked, as are the generated figures and tables of
:mod:`tar_lab.figures` when the workspace declares them (:mod:`tar_lab.figures.audit`). Each check is PASS, WARN, FAIL or SKIP.
"""

from __future__ import annotations

import fnmatch
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .tex import build

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"
BUILD_PRODUCTS = ("*.aux", "*.log", "*.out", "*.bbl", "*.blg", "*.fls", "*.fdb_latexmk",
                  "*.synctex.gz", "*.bcf", "*.run.xml", "*.toc", "*.xdv")
OVERFULL_TOLERANCE_PT = 1.0
MAX_DETAILS = 20


@dataclass
class Check:
    name: str
    status: str
    summary: str
    details: list[str] = field(default_factory=list)


@dataclass
class CheckReport:
    paper_dir: str
    main: str
    engine: str
    checks: list[Check]

    @property
    def ok(self) -> bool:
        return all(c.status != FAIL for c in self.checks)

    def to_dict(self) -> dict[str, Any]:
        counts = {s: sum(c.status == s for c in self.checks) for s in (PASS, WARN, FAIL, SKIP)}
        return {"paper_dir": self.paper_dir, "main": self.main, "engine": self.engine,
                "ok": self.ok, "counts": counts, "checks": [asdict(c) for c in self.checks]}

    def render(self) -> str:
        lines = [f"Paper: {self.paper_dir} ({self.main}); engine: {self.engine or 'none'}"]
        for c in self.checks:
            lines.append(f"{c.status:4}  {c.name:20} {c.summary}")
            shown = c.details[:MAX_DETAILS]
            lines.extend(f"      {d}" for d in shown)
            if len(c.details) > len(shown):
                lines.append(f"      ... {len(c.details) - len(shown)} more")
        counts = self.to_dict()["counts"]
        verdict = "PASS" if self.ok else "FAIL"
        lines.append(f"Result: {verdict} ({counts[FAIL]} FAIL, {counts[WARN]} WARN, "
                     f"{counts[SKIP]} SKIP, {counts[PASS]} PASS)")
        return "\n".join(lines)


# --- LaTeX log ---------------------------------------------------------------------------


@dataclass
class LogFindings:
    errors: list[str] = field(default_factory=list)
    undefined_citations: list[str] = field(default_factory=list)
    undefined_references: list[str] = field(default_factory=list)
    missing_files: list[str] = field(default_factory=list)
    overfull: list[str] = field(default_factory=list)  # only boxes > OVERFULL_TOLERANCE_PT
    font_warnings: list[str] = field(default_factory=list)


_QUOTE = r"[`'\"]([^'\"]+)['\"]"
_CITATION = re.compile(r"(?:LaTeX|Package natbib|Package biblatex) Warning: Citation " + _QUOTE)
_REFERENCE = re.compile(r"LaTeX Warning: Reference " + _QUOTE)
_MISSING = re.compile(r"(?:Error|[Ww]arning)[^:]*: File " + _QUOTE + r" not found")
_MISSING_FILE_ERROR = re.compile(r"! I can't find file " + _QUOTE)
_FILE_LINE_ERROR = re.compile(r"^(?:\./)?[^\s:]+\.\w+:\d+: (.*)")
_OVERFULL = re.compile(r"^Overfull \\hbox \((\d+(?:\.\d+)?)pt too wide\) (.*)")
_FONT = re.compile(r"LaTeX Font Warning: (.*)")


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def parse_log(text: str) -> LogFindings:
    found = LogFindings()
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if m := _CITATION.search(line):
            found.undefined_citations.append(m.group(1))
        elif m := _REFERENCE.search(line):
            found.undefined_references.append(m.group(1))
        if m := _MISSING.search(line) or _MISSING_FILE_ERROR.search(line):
            found.missing_files.append(m.group(1))
        if line.startswith("! ") or _FILE_LINE_ERROR.match(line):
            found.errors.append(line.strip())
        if (m := _OVERFULL.match(line)) and float(m.group(1)) > OVERFULL_TOLERANCE_PT:
            found.overfull.append(f"{m.group(1)}pt too wide {m.group(2).strip()}")
        if m := _FONT.search(line):
            message = m.group(1).strip()
            # "Font shape `X' undefined" continues on the next line with "(Font) using ...".
            if i + 1 < len(lines) and lines[i + 1].startswith("(Font)"):
                message += " " + lines[i + 1].removeprefix("(Font)").strip()
            found.font_warnings.append(message)
    found.errors = _dedupe(found.errors)
    found.undefined_citations = _dedupe(found.undefined_citations)
    found.undefined_references = _dedupe(found.undefined_references)
    found.missing_files = _dedupe(found.missing_files)
    found.font_warnings = _dedupe(found.font_warnings)
    return found


def log_checks(found: LogFindings) -> list[Check]:
    checks = [
        Check("latex errors", FAIL if found.errors else PASS,
              f"{len(found.errors)} error(s)" if found.errors else "no errors", found.errors),
    ]
    undefined = ([f"citation {k}" for k in found.undefined_citations]
                 + [f"reference {k}" for k in found.undefined_references])
    checks.append(Check("citations/refs", FAIL if undefined else PASS,
                        f"{len(undefined)} undefined" if undefined
                        else "all citations and references resolved", undefined))
    checks.append(Check("missing files", FAIL if found.missing_files else PASS,
                        f"{len(found.missing_files)} missing" if found.missing_files
                        else "none", found.missing_files))
    checks.append(Check("overfull boxes", WARN if found.overfull else PASS,
                        f"{len(found.overfull)} overfull \\hbox > {OVERFULL_TOLERANCE_PT:g}pt"
                        if found.overfull else f"none > {OVERFULL_TOLERANCE_PT:g}pt",
                        found.overfull))
    checks.append(Check("font substitution", WARN if found.font_warnings else PASS,
                        f"{len(found.font_warnings)} font warning(s)" if found.font_warnings
                        else "none", found.font_warnings))
    return checks


# --- PDF ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class PdfFont:
    name: str
    type: str
    embedded: bool
    subset: bool


def parse_pdffonts(text: str) -> list[PdfFont]:
    """Parse ``pdffonts`` output.

    Rows are split on whitespace from the right (name, type..., encoding, emb, sub, uni,
    object number, generation) because long names push the fixed columns sideways.
    """
    lines = text.splitlines()
    sep = next((i for i, line in enumerate(lines) if line.startswith("---")), None)
    if sep is None:
        return []
    fonts = []
    for line in lines[sep + 1:]:
        tokens = line.split()
        if len(tokens) < 8:
            continue
        fonts.append(PdfFont(name=tokens[0], type=" ".join(tokens[1:-6]),
                             embedded=tokens[-5] == "yes", subset=tokens[-4] == "yes"))
    return fonts


def font_check(fonts: list[PdfFont]) -> Check:
    not_embedded = [f"{f.name} ({f.type}) not embedded" for f in fonts if not f.embedded]
    type3 = [f"{f.name or '[none]'} is Type 3" for f in fonts if f.type.startswith("Type 3")]
    details = not_embedded + type3
    if type3:
        details.append("Type 3 fonts usually come from figures; for matplotlib set "
                       "rcParams['pdf.fonttype'] = 42 and re-export")
    names = sorted({re.sub(r"^[A-Z]{6}\+", "", f.name) for f in fonts if f.name})
    summary = (f"{len(fonts)} fonts, {len(not_embedded)} not embedded, {len(type3)} Type 3"
               if details else f"{len(fonts)} fonts, all embedded, no Type 3: "
               + ", ".join(names[:8]) + (" ..." if len(names) > 8 else ""))
    return Check("fonts", FAIL if details else PASS, summary, details)


def _run(cmd: list[str]) -> str | None:
    if not shutil.which(cmd[0]):
        return None
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False, errors="replace")
    return proc.stdout if proc.returncode == 0 else None


def parse_pdfinfo_pages(text: str) -> int | None:
    m = re.search(r"^Pages:\s+(\d+)", text, re.MULTILINE)
    return int(m.group(1)) if m else None


_REFS_HEADING = re.compile(r"^\s*(?:\d+\.?\s*|[IVX]+\.\s*)?(References|REFERENCES|Bibliography|"
                           r"BIBLIOGRAPHY|R\s?EFERENCES)\s*$", re.MULTILINE)


def references_page(pdf_text: str) -> int | None:
    """Best effort: the first page (1-based) with a References/Bibliography heading."""
    for number, page in enumerate(pdf_text.split("\f"), start=1):
        if _REFS_HEADING.search(page):
            return number
    return None


def page_check(pages: int | None, limit: int | None, pdf_text: str | None) -> Check:
    if pages is None:
        return Check("pages", SKIP, "pdfinfo not available or failed")
    refs = references_page(pdf_text) if pdf_text else None
    note = [f"references heading found on page {refs} (best effort)"] if refs else []
    if limit is None:
        return Check("pages", PASS, f"{pages} page(s) (no limit given; references counted)", note)
    status = FAIL if pages > limit else PASS
    return Check("pages", status, f"{pages} page(s), limit {limit} (references counted)", note)


# --- sources -----------------------------------------------------------------------------


def strip_comments(tex: str) -> str:
    return re.sub(r"(?<!\\)%.*", "", tex)


def read_sources(paper_dir: Path) -> str:
    return "\n".join(strip_comments(p.read_text(encoding="utf-8", errors="replace"))
                     for p in sorted(paper_dir.rglob("*.tex")))


_CITE = re.compile(r"\\([a-zA-Z]*cite[a-zA-Z]*)\*?(?:\s*\[[^\]]*\]){0,2}\s*\{([^}]*)\}")


def cite_keys(tex: str) -> list[str]:
    keys = [k.strip() for m in _CITE.finditer(tex) if not m.group(1).endswith("style")
            for k in m.group(2).split(",")]
    return _dedupe([k for k in keys if k and k != "*"])


def _braced(text: str, start: int) -> tuple[str, int] | None:
    """Content of the group opening at ``text[start]`` ('{' or '(') and the index after it."""
    if start >= len(text) or text[start] not in "{(":
        return None
    paren = text[start] == "("
    depth = 0  # brace depth inside the group
    for i in range(start + 1, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            if depth == 0 and not paren:
                return text[start + 1:i], i + 1
            depth -= 1
        elif ch == ")" and paren and depth == 0:
            return text[start + 1:i], i + 1
    return None


def parse_bib(text: str) -> dict[str, set[str]]:
    """Map each entry key to its lower-case field names."""
    entries: dict[str, set[str]] = {}
    for m in re.finditer(r"@(\w+)\s*([{(])", text):
        if m.group(1).lower() in ("string", "comment", "preamble"):
            continue
        group = _braced(text, m.end() - 1)
        if group is None:
            continue
        body = group[0]
        key, _, rest = body.partition(",")
        fields: set[str] = set()
        depth = 0
        token_start = 0
        for i, ch in enumerate(rest):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            elif depth == 0 and ch in ",":
                token_start = i + 1
            elif depth == 0 and ch == "=":
                name = rest[token_start:i].strip().lower()
                if re.fullmatch(r"[\w-]+", name):
                    fields.add(name)
        entries[key.strip()] = fields
    return entries


def bib_files(paper_dir: Path, tex: str) -> list[Path]:
    """The .bib files named by \\bibliography/\\addbibresource, else every .bib file."""
    named: dict[Path, None] = {}
    for m in re.finditer(r"\\(?:bibliography|addbibresource)\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}",
                         tex):
        for name in (n.strip() for n in m.group(1).split(",")):
            path = paper_dir / (name if name.endswith(".bib") else f"{name}.bib")
            if name and path.is_file():
                named[path] = None
    return list(named) or sorted(paper_dir.rglob("*.bib"))


def bib_check(paper_dir: Path, tex: str) -> Check:
    keys = cite_keys(tex)
    files = bib_files(paper_dir, tex)
    entries: dict[str, set[str]] = {}
    for f in files:
        entries.update(parse_bib(f.read_text(encoding="utf-8", errors="replace")))
    if "\\begin{thebibliography}" in tex:
        entries.update({k: {"title", "year"} for k in
                        re.findall(r"\\bibitem\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}", tex)})
    if not keys and not entries:
        return Check("bibliography", PASS, "no citations and no bibliography")
    missing = [f"cited key {k} not in {', '.join(f.name for f in files) or 'any .bib'}"
               for k in keys if k not in entries]
    incomplete = []
    for key, fs in entries.items():
        lacks = [n for n, ok in (("title", "title" in fs), ("year", bool({"year", "date"} & fs)))
                 if not ok]
        if lacks:
            incomplete.append(f"{key}: missing {', '.join(lacks)}")
    status = FAIL if missing else WARN if incomplete else PASS
    summary = (f"{len(keys)} cited keys, {len(entries)} entries; {len(missing)} missing, "
               f"{len(incomplete)} without title/year")
    return Check("bibliography", status, summary, missing + incomplete)


# --- anonymity ---------------------------------------------------------------------------


_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_GITHUB = re.compile(r"github\.com/[A-Za-z0-9_.-]+", re.IGNORECASE)
_ACK_TEX = re.compile(r"\\(?:section|subsection|paragraph)\*?\s*\{\s*Acknowledge?ments?\s*\}"
                      r"|\\begin\{ack(?:s|nowledge?ments?)?\}|\\acks\b", re.IGNORECASE)
_ACK_PDF = re.compile(r"^\s*(?:\d+\.?\s*)?Acknowledge?ments?\b", re.IGNORECASE | re.MULTILINE)
_ANON_WORDS = re.compile(r"anonym|blind|submission|withheld", re.IGNORECASE)
# Phrases near a link that present it as the authors' own (code release, project page).
_SELF = re.compile(
    r"\bour\s+(?:\w+\s+){0,2}?(?:code|implementation|repository|repo|project|website|page|"
    r"software|library|models?|data(?:set)?|benchmark)s?\b|"
    r"\bwe\s+(?:will\s+)?(?:release|open[- ]source|publish|provide|make)|"
    r"(?:code|implementation|data(?:set)?|models?|software)\s+(?:is|are|will be)"
    r"\s+(?:publicly\s+|freely\s+)?(?:available|released)|available (?:at|from|on)|"
    r"project page|\b(?:code|project|website|homepage|repository)\s*:",
    re.IGNORECASE)
SELF_BEFORE, SELF_AFTER = 120, 60  # characters around a link searched for _SELF


def author_blocks(tex: str) -> list[str]:
    blocks = []
    for m in re.finditer(r"\\author\s*(?:\[[^\]]*\])?\s*(?=\{)", tex):
        group = _braced(tex, m.end())
        if group:
            blocks.append(group[0])
    return blocks


def _plain(tex: str) -> str:
    text = re.sub(r"\\[a-zA-Z@]+\*?(?:\[[^\]]*\])?", " ", tex)
    text = re.sub(r"[{}$~\\&]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _owner(link: str) -> str:
    return link.split("/", 1)[1].lower() if "/" in link else link.lower()


def anonymity_check(tex: str, pdf_text: str | None, bib_text: str = "") -> Check:
    """Author block, emails, GitHub links and acknowledgements that could identify authors.

    Links and emails that come from the bibliography (the same GitHub owner or email occurs in
    a ``.bib`` file) are citations of other people's work and are ignored, as are GitHub links
    whose surrounding text does not present them as the authors' own (``our code``, ``we
    release``, ``available at``, ``project page``, ...); both are counted in the summary."""
    issues: list[str] = []
    cited_owners = {_owner(link) for link in _GITHUB.findall(bib_text)}
    cited_emails = {e.lower() for e in _EMAIL.findall(bib_text)}
    ignored: set[str] = set()
    for block in author_blocks(tex):
        plain = _plain(block)
        if plain and not _ANON_WORDS.search(plain):
            where = "in source"
            if pdf_text is not None:
                names = [w for w in re.split(r"[,\s]+", plain) if len(w) > 2 and w[0].isupper()]
                if any(n in pdf_text for n in names[:6]):
                    where = "in source and PDF"
                else:
                    where = "in source only (the template may hide it)"
            issues.append(f"\\author not anonymous {where}: {plain[:80]}")
    for label, text in (("source", tex), ("PDF", pdf_text or "")):
        for email in _dedupe(_EMAIL.findall(text)):
            if email.lower() in cited_emails:
                ignored.add(email.lower())
            else:
                issues.append(f"email in {label}: {email}")
        flagged: set[str] = set()
        for m in _GITHUB.finditer(text):
            link = m.group(0)
            owner = _owner(link)
            before = text[max(0, m.start() - SELF_BEFORE):m.start()]
            # Only the sentence the link is in: a full stop ends the context.
            before = re.split(r"[.!?]\s", before)[-1]
            after = re.split(r"[.!?]\s", text[m.end():m.end() + SELF_AFTER])[0]
            context = before + after
            if owner in cited_owners or not _SELF.search(context):
                ignored.add(owner)
            elif owner not in flagged:
                flagged.add(owner)
                issues.append(f"GitHub link presented as the authors' own in {label}: {link}")
        ignored -= flagged
    if _ACK_TEX.search(tex):
        issues.append("acknowledgements section in source")
    if pdf_text and _ACK_PDF.search(pdf_text):
        issues.append("acknowledgements heading in PDF")
    note = "" if pdf_text is not None else " (pdftotext unavailable: source only)"
    if ignored:
        note += (f"; {len(ignored)} cited or third-party link(s)/email(s) ignored: "
                 + ", ".join(sorted(ignored)[:5]) + (" ..." if len(ignored) > 5 else ""))
    return Check("anonymity", WARN if issues else PASS,
                 (f"{len(issues)} possible identity leak(s)" if issues
                  else "no author names, emails, GitHub links or acknowledgements") + note,
                 issues)


# --- driver ------------------------------------------------------------------------------


def copy_sources(src: Path, dst: Path, main: str = "main.tex") -> None:
    patterns = (*BUILD_PRODUCTS, f"{Path(main).stem}.pdf", "build.log")

    def ignore(directory: str, names: list[str]) -> set[str]:
        top = Path(directory).resolve() == src.resolve()
        return {n for n in names
                if any(fnmatch.fnmatch(n, p) for p in BUILD_PRODUCTS)
                or (top and n in patterns[len(BUILD_PRODUCTS):])}

    shutil.copytree(src, dst, ignore=ignore)


def check_paper(paper_dir: Path, main: str = "main.tex", page_limit: int | None = None,
                anonymous: bool = False, timeout: float = 600,
                workspace: Path | None = None, arxiv: bool = False,
                arxiv_out: Path | None = None) -> CheckReport:
    """``workspace`` (default: the paper's parent) holds ``figures/spec.toml``; when it
    exists, generated figures and tables are checked too. ``arxiv`` builds with arXiv's TeX
    Live, then checks the package of files the build read (:mod:`tar_lab.tools.arxiv`),
    written to ``arxiv_out`` as ``.tar.gz`` when given."""
    from ..figures import audit
    from ..figures.build import SPEC, insert_layout_probe, parse_layout
    from . import arxiv as ax

    paper_dir = paper_dir.resolve()
    workspace = (workspace or paper_dir.parent).resolve()
    if not (paper_dir / main).is_file():
        return CheckReport(str(paper_dir), main, "", [
            Check("build", FAIL, f"{main} not found in {paper_dir}")])
    with tempfile.TemporaryDirectory(prefix="tarlab-tex-check-") as tmp:
        work = Path(tmp) / "paper"
        copy_sources(paper_dir, work, main)
        insert_layout_probe(work / main)
        checks: list[Check] = []
        texlive_bin = None
        if arxiv:
            texlive_bin = ax.find_arxiv_texlive_bin()
            checks.append(Check("arxiv texlive", PASS, f"TeX Live {ax.ARXIV_TEXLIVE_YEAR} "
                                f"(arXiv's default): {texlive_bin}") if texlive_bin else
                          Check("arxiv texlive", WARN, f"arXiv's TeX Live "
                                f"{ax.ARXIV_TEXLIVE_YEAR} not found in "
                                f"{ax.LOCAL_ARXIV_TEXLIVE}; built with the local TeX Live "
                                "instead (see README)"))
        result = build(work, main, timeout=timeout, texlive_bin=texlive_bin)
        if arxiv and result.pdf:
            first = work
            files = ax.package_files(first, main, bib_files(first, read_sources(first)))
            work = Path(tmp) / "arxiv"
            size = ax.make_package(first, files, work)
            shutil.copyfile(paper_dir / main, work / main)  # without the layout probe
            if arxiv_out is not None:
                ax.write_tarball(work, files, arxiv_out)
            sources = {p.relative_to(paper_dir).as_posix() for p in paper_dir.rglob("*")
                       if p.is_file()}
            unused = sorted(sources - set(files) - {f"{Path(main).stem}.pdf", "build.log"}
                            - {n for n in sources if any(fnmatch.fnmatch(Path(n).name, p)
                                                         for p in BUILD_PRODUCTS)})
            checks.append(ax.rule_check(paper_dir, read_sources(work), files, size, unused))
            insert_layout_probe(work / main)
            packaged = build(work, main, timeout=timeout, texlive_bin=texlive_bin)
            if packaged.pdf is None:
                tail = packaged.log.read_text(encoding="utf-8",
                                              errors="replace").splitlines()[-10:]
                checks.append(Check("arxiv package", FAIL, "the package of recorded inputs "
                                    "does not build on its own", tail))
            result = packaged
        if result.pdf:
            checks.append(Check("build", PASS,
                                f"built from a clean copy with {result.engine}"))
        else:
            tail = result.log.read_text(encoding="utf-8", errors="replace").splitlines()[-15:]
            checks.append(Check("build", FAIL,
                                f"clean-copy build failed (exit {result.returncode}, "
                                f"engine {result.engine or 'none'})", tail))
        tex_log = work / f"{Path(main).stem}.log"
        layout = None
        if tex_log.is_file():
            log_text = tex_log.read_text(encoding="utf-8", errors="replace")
            layout = parse_layout(log_text)
            checks.extend(log_checks(parse_log(log_text)))
        else:
            checks.append(Check("latex log", SKIP, f"no {tex_log.name} produced"))
        pdf = result.pdf
        pdf_text = _run(["pdftotext", "-layout", str(pdf), "-"]) if pdf else None
        if pdf is None:
            checks.append(Check("fonts", SKIP, "no PDF"))
            checks.append(Check("pages", SKIP, "no PDF"))
        else:
            fonts_out = _run(["pdffonts", str(pdf)])
            checks.append(font_check(parse_pdffonts(fonts_out)) if fonts_out is not None
                          else Check("fonts", SKIP, "pdffonts not available or failed"))
            info = _run(["pdfinfo", str(pdf)])
            checks.append(page_check(parse_pdfinfo_pages(info) if info else None,
                                     page_limit, pdf_text))
            checks.append(audit.resolution_check(_run(["pdfimages", "-list", str(pdf)])))
        tex = read_sources(work)
        checks.append(bib_check(work, tex))
        checks.append(audit.handwritten_table_check(work))
        if (workspace / SPEC).is_file():
            checks.append(audit.artifact_check(workspace, paper_dir))
            checks.append(audit.size_check(workspace, tex, layout))
        if anonymous:
            bib_text = "\n".join(f.read_text(encoding="utf-8", errors="replace")
                                 for f in bib_files(work, tex))
            checks.append(anonymity_check(tex, pdf_text, bib_text))
    return CheckReport(str(paper_dir), main, result.engine, checks)


def render_pages(pdf: Path, out_dir: Path, dpi: int = 110) -> list[Path]:
    """Render every page to ``out_dir/page-NN.png`` (``pdftoppm``) for visual review;
    earlier ``page-*.png`` renders there are removed first."""
    if not shutil.which("pdftoppm"):
        raise FileNotFoundError("pdftoppm not found (install poppler-utils)")
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("page-*.png"):
        old.unlink()
    subprocess.run(["pdftoppm", "-r", str(dpi), "-png", str(pdf), str(out_dir / "page")],
                   check=True, capture_output=True)
    return sorted(out_dir.glob("page-*.png"))
