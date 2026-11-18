#!/bin/bash

set -euo pipefail

ensure_staged_task_copy() {
  local repo_root="$1"
  local source_task_dir="$2"
  local results_root="$3"
  "$PYTHON_BIN" - "$repo_root" "$source_task_dir" "$results_root" <<'PY'
import sys
from pathlib import Path

repo_root = Path(sys.argv[1])
source_task_dir = Path(sys.argv[2])
results_root = Path(sys.argv[3])

sys.path.insert(0, str(repo_root))
from src.investigation.files import ensure_task_copy_in_results  # noqa: E402

print(str(ensure_task_copy_in_results(source_task_dir, results_root)))
PY
}
