"""Working guides per role: deliverable formats, evidence rules and resource limits.

Paths are relative to the project workspace (the agent's working directory).
"""

from __future__ import annotations

import sys
from pathlib import Path

# A complete engine task (task.md, evaluate.py, program_0/) to copy the layout from.
ENGINE_EXAMPLE = (Path(__file__).resolve().parents[2] / "engines" / "ai-scientist-v3"
                  / "examples" / "circpack")
# Official LaTeX templates per venue and year, each with a SOURCE.md (see its README.md).
VENUE_TEMPLATES = Path(__file__).resolve().parents[2] / "templates" / "venues"

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
  only for the central question. Novelty of the recommended topic is that question: run one
  deep search on its closest prior work before the topic goes to the user, and report what it
  found. If deep search is unavailable, say why and what you used instead.
{_EVIDENCE}""",
    "designer": f"""\
- For candidate topics, write one section per candidate: research question; hypothesis;
  closest prior work and the exact difference (novelty); method sketch and which papers or
  code each part is adapted from; baselines (with repos and licenses); environment or
  dataset; metrics; ablations; compute estimate; main risks; what result would count as
  success or failure.
- Treatment check, for every comparison the claim rests on: write down what concretely
  differs between the arms when they run (which configuration, which code path, which inputs)
  and walk the selection or matching rule through on real or estimated numbers. If the arms
  can end up running the same thing, or the difference exists only as a label, the design is
  invalid: say so and redesign instead of adding statistics.
- For an experiment plan, list every run: purpose, config, seeds (at least 3 where
  feasible), expected wall time and peak GPU memory, and the output path under `results/`.
- Every plan fixes its final evaluation up front: a held-out split, environment seeds or
  episodes that development, tuning and model selection never see; the metrics; and how the
  frozen methods are run on it once. This becomes `eval/final/protocol.md`. Say which
  sub-problems, if any, suit the AI-Scientist-v3 engine (an automatic score worth searching
  over) and what its evaluator would measure on development data only.
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
  that rebuilds it. Repositories cloned into the workspace are recorded as origin, commit
  and a patch of your local changes (`.tar-lab/nested/`); commit nothing inside them.
- GPU: an RTX 5060 Laptop (Blackwell, 8 GB). Use PyTorch wheels built for CUDA 12.8 or newer;
  older wheels fail on this GPU. Record `torch.cuda.max_memory_allocated()` and wall time in
  every `metrics.json`. Keep each run under 15 minutes and 6 GB; if a run would exceed
  this, reduce it and report the change.
- Before a long run, do a short smoke run. Run several seeds when the plan asks for them.
- If something cannot work within the limits, stop and report `needs_help` with what you
  tried, instead of polishing or retrying the same thing.
- Engine tasks (for AI-Scientist-v3 engine orders): build a directory such as
  `engine_tasks/<name>/` with `task.md` (goal, what may change, the score to maximise and its
  constraints), `evaluate.py` (`--program_path <file> --results_dir <dir>`; writes
  `metrics.json` with a numeric `combined_score`, higher is better, and `correct.json` with
  `correct` and `message`), `capture.py` (copy it unchanged from the example),
  `program_0/program_spec.toml` with `program_0/src/` holding the baseline program, and a
  pinned `requirements.txt`. Copy the layout from `{ENGINE_EXAMPLE}`. Create the task's
  environment in `engine_tasks/<name>/.venv` (`uv venv` + `uv pip install -r
  requirements.txt`): the engine evaluates every candidate with that interpreter, on this
  machine, GPU included. The evaluator must use development data only, never the held-out
  final split. Run it on the baseline once with that interpreter and report the score.
- Final evaluation: when asked to prepare it, write `eval/final/protocol.md` (what is run,
  on which held-out data, the exact command, the metrics), the evaluation script under
  `eval/final/`, and `eval/final/frozen.txt` (one workspace path per line: the code, configs
  and checkpoints being evaluated). Do not run it: a separate sealed order does. Until then,
  never load the held-out data.
{_EVIDENCE}""",
    "analyst": f"""\
- Read `results/`; compute mean and spread (std or 95% CI) over seeds; make figures in
  `figures/` (PNG and PDF); test whether differences are meaningful given the seeds.
- Deliverable: `reports/analysis.md` mapping every claim to the result files that support
  it, and stating negative or inconclusive findings plainly.
- Headline results come from the sealed final evaluation in `results/final/<order id>/`;
  read its `SEAL.json` and state how many final evaluations ran and whether sealed files
  changed. Label everything else (including engine scores) as development results.
{_EVIDENCE}""",
    "writer": f"""\
- Topic summary (`reports/topic_summary.md`, in the charter's chat language): one page the
  user reads before approving a topic, under 2500 characters, for a reader outside the
  subfield: no formulas, symbols or unexplained acronyms. Exactly four parts: what we will
  do; why it is worth doing (what is new compared with the closest prior work, and what the
  novelty search found); the most likely way it fails and what the design check showed; the
  decisions the user must make (each with the options and the recommendation). Point to
  `reports/topic_report.md` for details.
- Reports go to `reports/` in the charter's report language; the paper goes to `paper/`
  (`main.tex`, `refs.bib`, figures copied in) in the charter's paper language. Start it
  from the target venue's official template in `{VENUE_TEMPLATES}` (its `README.md` and
  the venue's `SOURCE.md` give the page limit and anonymity rules); if the venue is
  absent, download the official template from the venue's own site and record its URL in
  `paper/SOURCE.md`.
- Every number in the paper must come from `results/` or `reports/analysis.md`; keep
  `paper/claims.md` mapping each claim to its source file. Use only citations from the
  survey reports and verify each one with the literature tools.
- Main results come from the sealed final evaluation (`results/final/*/SEAL.json`); report
  how many final evaluations ran and any sealed file that changed. Development and engine
  scores are labelled as such.
- Include limitations, compute used, and a statement that the work was produced with an
  automated research system. Build the PDF with:
  `{sys.executable} -m tar_lab.cli tex build paper` (TeX Live `latexmk`/`pdflatex` on
  this machine, as arXiv and venues use, Tectonic only as a fallback; restricted shell escape only; read
  `paper/build.log` on failure).
- Before handing in the paper run
  `{sys.executable} -m tar_lab.cli tex check paper` (add `--page-limit N` and `--anonymous`
  as the target venue requires) and fix every FAIL; it builds a clean copy and checks the
  log, embedded fonts, page count, bibliography and anonymity.
{_EVIDENCE}""",
    "reviewer": f"""\
- Topic review: first try to break each candidate. List at least three concrete ways its
  main comparison could turn out vacuous or confounded (e.g. both arms select the same
  configuration, the manipulated variable never changes what runs, the metric cannot move)
  and check each against the design and any design-check result; a design that fails here
  is rejected however careful its statistics are. Then rate novelty, value to the field,
  feasibility within the charter, and risk (1-10 each), check the closest prior work
  yourself (run a deep search on it if none has been run for this candidate), and recommend one candidate or none. Write `reports/topic_review.md`.
- Paper review: review as a reviewer for the charter's target venue: summary, strengths,
  weaknesses, major and minor issues, questions, and an overall score (1-10) with the
  venue's usual meaning. Check that numbers match `results/`, that headline
  numbers come from the sealed final evaluation (`results/final/`), and that citations exist.
  Run `{sys.executable} -m tar_lab.cli tex check paper` (with the venue's `--page-limit` and
  `--anonymous` if it is double-blind) and include its result in the review.
  Write `reports/final_review.md`. Do not edit the paper.
{_EVIDENCE}""",
}
