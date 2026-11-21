# Notes for coding agents

- Checks: `env -u PYTHONPATH .venv/bin/ruff check . && env -u PYTHONPATH .venv/bin/mypy tar_lab tests && env -u PYTHONPATH .venv/bin/pytest -q`.
  This machine's shell exports a ROS `PYTHONPATH` whose pytest plugins break the suite; always clear it.
- A project may be running (`tarlab run`) while you work. Its Undermind proxy is started fresh per order as
  `python -m tar_lab.cli undermind serve`, so `tar_lab/cli.py` must stay importable at all times; import optional
  subsystems (e.g. `tar_lab.tui`) lazily.
- Never modify `engines/ai-scientist-v3-original`; it must stay byte-identical to upstream.
- New dependencies: versions released at least 7 days ago (`[tool.uv] exclude-newer` enforces this).
- `engines/ai-scientist-v3` is a trimmed fork (investigation loop only, native, no Docker) and no longer tracks upstream.
  Its checks: `cd engines/ai-scientist-v3 && env -u PYTHONPATH .venv/bin/ruff check src tests && env -u PYTHONPATH
  .venv/bin/mypy src tests && env -u PYTHONPATH .venv/bin/pytest -q -n 8 -p no:cacheprovider tests`.
- Engine agents launch through `../.tools/headless/dist/cli.js`; PDFs build with the TeX Live in
  `../.tools/texlive/<year>` (`latexmk`), falling back to `../.tools/tectonic/bin/tectonic`.
- Paper figures/tables run in their own env `../.tools/figures/venv` (`tarlab figures setup`, pinned by
  `tar_lab/figures/requirements.lock`). `tar_lab/figures/runtime/` runs in that env, not the platform's: keep it
  free of `tar_lab` imports, and keep the table helpers in `tarlab_fig.py` stdlib-only (tests import them).
