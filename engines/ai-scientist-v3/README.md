# AI-Scientist-v3 idea search (TAR-Lab engine)

A trimmed fork of [SakanaAI/AI-Scientist-v3](https://github.com/SakanaAI/AI-Scientist-v3)
(Apache-2.0, see `LICENSE` and `CITATION.cff`). Only the investigation loop is kept: for a
task with an automatic score, a coding agent proposes an idea with target rubrics, edits and
evaluates programs, and the best-scoring program is kept. It runs natively on the host; there
is no Docker support. What was removed and why: `../../patches/ai-scientist-v3/README.md`.

## Run

```bash
uv sync --frozen
HEADLESS_BIN=/path/to/headless/dist/cli.js HEADLESS_BILLING=subscription \
  .venv/bin/python -m src.investigation.cli \
  paths.task_dir=examples/circpack paths.results_dir=/tmp/run agent.name=codex \
  agent.model=gpt-6.1-sol execution.max_idea_cycles=2
```

`python -m src.investigation.cli --help` lists every key. Agents launch through the pinned
[headless CLI](https://github.com/RobertTLange/headless-cli) (commit `93cd9b06`). Official
evaluations run with `paths.task_python` (default: `<task_dir>/.venv/bin/python`).

## Task layout

`task.md`, `evaluate.py` (`--program_path`, `--results_dir`; writes `metrics.json` with
`combined_score` and `correct.json`), `capture.py`, and `program_0/` (`program_spec.toml` +
`src/`). See `examples/circpack`.

## Checks

```bash
.venv/bin/ruff check src tests && .venv/bin/mypy src tests && .venv/bin/pytest -q -n auto
```
