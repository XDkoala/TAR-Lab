"""Working guides per role: deliverable formats, evidence rules and resource limits.

Paths are relative to the project workspace (the agent's working directory).
"""

from __future__ import annotations

import sys

_EVIDENCE = """\
- Never invent results, numbers, citations, or links. If something failed or is unknown,
  say so in the report; failed runs stay on disk and are mentioned.
- Cite only sources you actually opened or that a literature tool returned; give a DOI,
  arXiv id, or URL for each."""

GUIDES: dict[str, str] = {
    "researcher": f"""\
- Deliverable: a markdown report under `reports/` with: the question you covered, how you
  searched (tools, queries), a table of key papers (title, year, venue, link, code repo and
  license if any, main result), benchmarks and environments in use, open gaps, and concrete
  directions that fit the charter's constraints.
- Look beyond papers when useful: official code repositories, OpenReview discussions,
  leaderboards, surveys. Prefer work with public code that runs on small hardware.
- Deep searches are rationed per project; use `search_papers` first and launch a deep search
  only for the central question.
{_EVIDENCE}""",
    "designer": f"""\
- For candidate topics, write one section per candidate: research question; hypothesis;
  closest prior work and the exact difference (novelty); method sketch and which papers or
  code each part is adapted from; baselines (with repos and licenses); environment or
  dataset; metrics; ablations; compute estimate; main risks; what result would count as
  success or failure.
- For an experiment plan, list every run: purpose, config, seeds (at least 3 where
  feasible), expected wall time and peak GPU memory, and the output path under `results/`.
- Respect the hardware: each training run must finish within 15 minutes and stay below
  6 GB of GPU memory on the 8 GB card; shrink models, images, or horizons rather than break
  this.
{_EVIDENCE}""",
    "engineer": f"""\
- Layout: Python environment in `.venv/` (create it with `uv venv` / `uv pip`, never touch
  the system Python), code in `code/`, outputs in `results/<experiment>/` with `config.json`,
  `metrics.json`, the seed, and a log. Record reused code with repo, commit, and license in
  `SOURCES.md`.
- Snapshots: before every attempt the workspace is committed to git, except `.venv/`,
  `.cache/` and files over 10 MB (datasets, wheels, checkpoints). Those are not recoverable
  from snapshots, so record where each one came from (URL, version, checksum) and the command
  that rebuilds it.
- GPU: an RTX 5060 Laptop (Blackwell, 8 GB). Use PyTorch wheels built for CUDA 12.8 or newer;
  older wheels fail on this GPU. Record `torch.cuda.max_memory_allocated()` and wall time in
  every `metrics.json`. Keep each run under 15 minutes and 6 GB; if a run would exceed
  this, reduce it and report the change.
- Before a long run, do a short smoke run. Run several seeds when the plan asks for them.
- If something cannot work within the limits, stop and report `needs_help` with what you
  tried, instead of polishing or retrying the same thing.
{_EVIDENCE}""",
    "analyst": f"""\
- Read `results/`; compute mean and spread (std or 95% CI) over seeds; make figures in
  `figures/` (PNG and PDF); test whether differences are meaningful given the seeds.
- Deliverable: `reports/analysis.md` mapping every claim to the result files that support
  it, and stating negative or inconclusive findings plainly.
{_EVIDENCE}""",
    "writer": f"""\
- Reports go to `reports/` in the charter's report language; the paper goes to `paper/`
  (`main.tex`, `refs.bib`, figures copied in) in the charter's paper language.
- Every number in the paper must come from `results/` or `reports/analysis.md`; keep
  `paper/claims.md` mapping each claim to its source file. Use only citations from the
  survey reports and verify each one with the literature tools.
- Include limitations, compute used, and a statement that the work was produced with an
  automated research system. Build the PDF with:
  `{sys.executable} -m tar_lab.cli tex build paper` (TeX runs in Docker; read
  `paper/build.log` on failure).
{_EVIDENCE}""",
    "reviewer": f"""\
- Topic review: for each candidate rate novelty, value to the field, feasibility within the
  charter, and risk (1-10 each), check the closest prior work yourself, and recommend one
  candidate or none. Write `reports/topic_review.md`.
- Paper review: review as a reviewer for the charter's target venue: summary, strengths,
  weaknesses, major and minor issues, questions, and an overall score (1-10) with the
  venue's usual meaning. Check that numbers match `results/` and that citations exist.
  Write `reports/final_review.md`. Do not edit the paper.
{_EVIDENCE}""",
}
