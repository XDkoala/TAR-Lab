#!/usr/bin/env bash
# Run in your own Ubuntu terminal. sudo may request your system password.
set -euo pipefail
local_bundle_dir="$(cd -- "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")" && pwd)"
local_engine_dir="$(cd -- "$local_bundle_dir/../../engines/ai-scientist-v3" && pwd)"
if (( EUID == 0 )); then
  echo '请使用普通用户运行此脚本；需要管理员权限的步骤会单独调用 sudo。'
  exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y docker.io
fi
sudo systemctl start docker
sudo docker info >/dev/null
cd "$local_engine_dir"
sudo docker build --network=host \
  -t agentic-scientist/agent-cli:latest \
  -f src/docker/agent-cli/Dockerfile .
sudo docker run --rm agentic-scientist/agent-cli:latest \
  bash -lc 'python --version && node --version && headless --version && claude --version && pdflatex --version | head -n 1'
# Only grant the installing user Docker access after installation succeeds.
if ! id -nG | tr ' ' '\n' | grep -Fxq docker; then
  sudo usermod -aG docker "$(id -un)"
  echo '已加入 docker 用户组。请注销系统后重新登录，再运行检查。该组拥有较高系统权限。'
fi
echo "Docker 和 Agent 镜像已准备好。下一步：bash '$local_bundle_dir/agsci-local.sh' doctor examples/circpack"
echo "尚未启动科研实验。请先在 '$local_engine_dir/.env' 中填写所选 Agent 的密钥。"
