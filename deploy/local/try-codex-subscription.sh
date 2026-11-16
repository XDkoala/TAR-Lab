#!/usr/bin/env bash
# Default: preview only. Pass --run to start the one-cycle experiment.
set -euo pipefail
local_bundle_dir="$(cd -- "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")" && pwd)"
export HEADLESS_BILLING=subscription
# This smoke run uses ChatGPT login, without forwarding paid API credentials.
unset OPENAI_API_KEY CODEX_API_KEY
local_mode=--dry-run
case "${1:-}" in
  '') ;;
  --run) local_mode=run ;;
  *) echo '用法：bash try-codex-subscription.sh [--run]'; exit 2 ;;
esac
if (( $# > 1 )); then
  echo '此入门脚本不接受额外参数。自定义参数请使用 agsci-local.sh。'
  exit 2
fi
local_common=(
  agent.name=codex
  agent.model=gpt-6.1-sol
  container.enabled=true
  container.image=agentic-scientist/agent-cli:codex-0.169.0
  "container.env_file='$local_bundle_dir/env/codex-subscription.env'"
)
if [[ "$local_mode" == run ]]; then
  echo '检查环境；通过后将运行一次研究循环，消耗 ChatGPT/Codex 可用额度。'
  bash "$local_bundle_dir/agsci-local.sh" doctor examples/circpack "${local_common[@]}"
  local_preview=()
else
  echo '仅预览，不调用模型。Docker 安装完成后可加 --run 启动。'
  local_preview=(--dry-run)
fi
exec bash "$local_bundle_dir/agsci-local.sh" run examples/circpack \
  "${local_preview[@]}" "${local_common[@]}" \
  execution.phases=investigation \
  investigation.execution.max_idea_cycles=1
