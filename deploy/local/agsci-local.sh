#!/usr/bin/env bash
# Run agsci from this checkout with the workspace-local Node runtime and results root.
#
# Layout (see deploy/local/README.zh.md):
#   <workspace>/TAR-Lab        this repository
#   <workspace>/runs           run results (TARLAB_RUNS_DIR overrides)
#   <workspace>/.tools/node    optional local Node.js (TARLAB_NODE_DIR overrides)
set -euo pipefail
local_bundle_dir="$(cd -- "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")" && pwd)"
local_repo_dir="$(cd -- "$local_bundle_dir/../.." && pwd)"
local_workspace_dir="$(cd -- "$local_repo_dir/.." && pwd)"
runs_dir="${TARLAB_RUNS_DIR:-$local_workspace_dir/runs}"
node_dir="${TARLAB_NODE_DIR:-$local_workspace_dir/.tools/node}"
# The installer grants Docker group membership. Existing desktop sessions may
# still have the old groups; refresh them only for this command when needed.
if [[ -S /var/run/docker.sock && ! -w /var/run/docker.sock ]] && command -v sg >/dev/null 2>&1; then
  if id -nG "$(id -un)" | tr ' ' '\n' | grep -Fxq docker; then
    if ! id -nG | tr ' ' '\n' | grep -Fxq docker; then
      exec sg docker -c "$(printf '%q ' bash "$local_bundle_dir/agsci-local.sh" "$@")"
    fi
  fi
fi
if [[ -x "$node_dir/bin/node" ]]; then
  export PATH="$node_dir/bin:$PATH"
fi
export PATH="$local_repo_dir/.venv/bin:$PATH"
# The host ROS installation exports Python paths for a different interpreter.
unset PYTHONPATH PYTHONHOME
cd "$local_repo_dir"
# Keep results outside the repository unless the caller chose a location.
extra=()
case "${1:-}" in
  run)
    if [[ ! " $* " =~ [[:space:]]paths\.results_root= ]]; then
      mkdir -p "$runs_dir"
      extra=("paths.results_root=$runs_dir")
    fi
    ;;
  inspect)
    if [[ ! " $* " =~ [[:space:]]--results-root ]]; then
      extra=(--results-root "$runs_dir")
    fi
    ;;
esac
exec "$local_repo_dir/.venv/bin/agsci" "$@" "${extra[@]}"
