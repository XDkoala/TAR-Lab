# AI-Scientist-v3 in TAR-Lab

`engines/ai-scientist-v3/` started as a subtree copy of SakanaAI/AI-Scientist-v3 (Apache-2.0).
TAR-Lab only uses the engine's idea search (the `investigation` loop: generate an idea with
target rubrics, let a coding agent edit and evaluate programs, keep the best-scoring one), so
the copy was trimmed to that loop and made to run natively on the host. It no longer tracks
upstream; `engines/ai-scientist-v3-original/` stays byte-identical to upstream commit `df83138`
for baseline comparisons.

## Removed

| Part | Why |
| --- | --- |
| Pipeline wrapper (`src/pipeline`, `agsci`) | TAR-Lab launches `python -m src.investigation.cli` directly |
| Task drafting (`src/task_drafting`, task contracts, qualification, repair) | TAR-Lab engineers write tasks; contracted tasks required containers |
| Ablations (`src/variants_pipeline`), post-hoc analysis (`src/posthoc_pipeline`) | TAR-Lab's analyst does this |
| Final evaluation (`src/final_evaluation`), managed task runtime | Container-only; TAR-Lab has its own sealed final evaluation |
| Paper writing, final AI review, paper prompts/skills/templates, `src/orchestrator/paper_*` | TAR-Lab's writer and reviewer do this |
| Harness-evaluated attempts | Only trusted sandboxed (container) evaluations |
| Docker support (container sessions, images, GPU slicing, sealed sandboxes, `src/docker`) | Everything runs on the host like the rest of TAR-Lab |
| Parallel workers and pruning | TAR-Lab runs one worker per engine order |
| Sidecar git checkpoints | TAR-Lab snapshots the workspace itself |
| Run Inspector web app (`apps/`), docs, CI, GRPO example | Unused |

Tests for removed parts were deleted; the remaining 103 engine tests cover the kept loop.

## Added

- `paths.task_python` (default: `<task_dir>/.venv/bin/python`): official evaluations run the
  evaluator and candidate with the task's own interpreter, so tasks can use their own
  dependencies and the GPU.
- `src/scripts/lib/agent.sh` replaces `container_agent.sh`: agents launch natively through the
  pinned headless CLI (`HEADLESS_BIN`).
- `src/investigation/attempt_feedback.py` (was `harness_feedback.py`, without task-contract metrics).

The earlier patch `FIX-003-tex-path-compatibility.patch` touched the paper phase, which is gone.
