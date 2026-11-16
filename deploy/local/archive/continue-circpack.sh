#!/usr/bin/env bash
set -euo pipefail
local_bundle_dir="$(cd -- "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")" && pwd)"
export HEADLESS_BILLING=subscription
unset OPENAI_API_KEY CODEX_API_KEY GEMINI_API_KEY ANTHROPIC_API_KEY
exec bash "$local_bundle_dir/agsci-local.sh" run examples/circpack \
  agent.name=codex agent.model=gpt-6.1-sol \
  container.enabled=true container.network=host \
  container.image=agentic-scientist/agent-cli:codex-0.169.0-pdf \
  "container.env_file='$local_bundle_dir/codex-subscription.env'" \
  paths.run_dir=results/circpack/codex/20261116_143042 \
  "execution.phases=${1:-ablations,posthoc,paper}" \
  ablations.execution.num_replications=3 \
  ablations.execution.max_ablations=2 \
  ablations.execution.max_extra_baselines=0 \
  ablations.execution.max_evals_per_variant=2 \
  posthoc.execution.max_selected_analyses=1 \
  posthoc.container.network=host \
  paper.paper_min_refs=8 paper.paper_max_refs=15 \
  "paper.contributions_file='$local_bundle_dir/../AI-Scientist-v3/results/circpack/codex/20261116_143042/baseline_comparison/summary.txt'" \
  "${@:2}"
