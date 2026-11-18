#!/bin/bash
# Usage: bash src/scripts/idea.sh [--agent claude|codex|cursor|opencode|gemini|pi] [--agent-model <name>] [--reasoning-effort low|medium|high|xhigh] [--task-dir <path>] [--results-dir <path>] [--global-memory-dir <path>] [--cycle-index <n>] [--output-dir <path>] [--instruct <path>]

set -euo pipefail

# Execution time limits for CC
export BASH_DEFAULT_TIMEOUT_MS=600000
export BASH_MAX_TIMEOUT_MS=7200000

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$SCRIPT_DIR/lib/agent.sh"
source "$SCRIPT_DIR/lib/task_pathing.sh"

AGENT_LOG_MAX_BYTES=16777216
validate_agent_log_max_bytes "$AGENT_LOG_MAX_BYTES" || exit $?

AGENT="claude"
AGENT_MODEL=""
AGENT_PROFILE=""
AGENT_REASONING_EFFORT=""
TASK_DIR="examples/circpack"
RESULTS_DIR=""
GLOBAL_MEMORY_DIR=""
CONCLUSIONS_DIR=""
WORKER_INDEX=0
INSTRUCT_PATH=""
VALIDATION_FEEDBACK=""
CYCLE_INDEX=""
OUTPUT_DIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --agent|-a)
      AGENT="$2"
      shift 2
      ;;
    --agent-model|--model)
      AGENT_MODEL="$2"
      shift 2
      ;;
    --agent-profile|--profile)
      AGENT_PROFILE="$2"
      shift 2
      ;;
    --reasoning-effort)
      AGENT_REASONING_EFFORT="$2"
      shift 2
      ;;
    --task-dir|-t)
      TASK_DIR="$2"
      shift 2
      ;;
    --results-dir|-r)
      RESULTS_DIR="$2"
      shift 2
      ;;
    --global-memory-dir)
      GLOBAL_MEMORY_DIR="$2"
      shift 2
      ;;
    --conclusions-dir)
      CONCLUSIONS_DIR="$2"
      shift 2
      ;;
    --worker-index)
      WORKER_INDEX="$2"
      shift 2
      ;;
    --cycle-index)
      CYCLE_INDEX="$2"
      shift 2
      ;;
    --output-dir)
      OUTPUT_DIR="$2"
      shift 2
      ;;
    --instruct|-i)
      INSTRUCT_PATH="$2"
      shift 2
      ;;
    --validation-feedback)
      VALIDATION_FEEDBACK="$2"
      shift 2
      ;;
    claude|codex|cursor|opencode|gemini|pi)
      AGENT="$1"
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if [ -n "$AGENT_MODEL" ]; then
  AGENT_MODEL_NORMALIZED="$(printf '%s' "$AGENT_MODEL" | tr '[:upper:]' '[:lower:]')"
  case "$AGENT_MODEL_NORMALIZED" in
    none|null)
      AGENT_MODEL=""
      ;;
  esac
fi

if [ -z "$RESULTS_DIR" ]; then
  TASK_NAME="$(basename "${TASK_DIR%/}")"
  if [ -z "$TASK_NAME" ] || [ "$TASK_NAME" = "." ] || [ "$TASK_NAME" = "/" ]; then
    TASK_NAME="task"
  fi
  RESULTS_DIR="results/$TASK_NAME/$AGENT"
fi

if [[ "$TASK_DIR" != /* ]]; then
  TASK_DIR="$REPO_ROOT/$TASK_DIR"
fi
if [[ "$RESULTS_DIR" != /* ]]; then
  RESULTS_DIR="$REPO_ROOT/$RESULTS_DIR"
fi
if [ -z "$GLOBAL_MEMORY_DIR" ]; then
  GLOBAL_MEMORY_DIR="$RESULTS_DIR"
fi
if [[ "$GLOBAL_MEMORY_DIR" != /* ]]; then
  GLOBAL_MEMORY_DIR="$REPO_ROOT/$GLOBAL_MEMORY_DIR"
fi
if [ -z "$CONCLUSIONS_DIR" ]; then
  CONCLUSIONS_DIR="$GLOBAL_MEMORY_DIR"
fi
if [[ "$CONCLUSIONS_DIR" != /* ]]; then
  CONCLUSIONS_DIR="$REPO_ROOT/$CONCLUSIONS_DIR"
fi
if ! [[ "$WORKER_INDEX" =~ ^[0-9]+$ ]]; then
  echo "--worker-index must be a non-negative integer (got: ${WORKER_INDEX:-<empty>})" >&2
  exit 1
fi
if [ -n "$INSTRUCT_PATH" ] && [[ "$INSTRUCT_PATH" != /* ]]; then
  INSTRUCT_PATH="$REPO_ROOT/$INSTRUCT_PATH"
fi
if [ -n "$OUTPUT_DIR" ] && [[ "$OUTPUT_DIR" != /* ]]; then
  OUTPUT_DIR="$REPO_ROOT/$OUTPUT_DIR"
fi

if [ -z "$INSTRUCT_PATH" ]; then
  INSTRUCT_PATH="$REPO_ROOT/src/prompts/idea.md"
fi
if [ -n "$AGENT_MODEL" ] && [ -z "${AGENT_MODEL//[[:space:]]/}" ]; then
  echo "--agent-model must be non-empty when provided" >&2
  exit 1
fi
if [ -n "$AGENT_PROFILE" ] && [ -z "${AGENT_PROFILE//[[:space:]]/}" ]; then
  echo "--agent-profile must be non-empty when provided" >&2
  exit 1
fi
if [ -n "$AGENT_PROFILE" ] && [ "$AGENT" != "codex" ]; then
  echo "--agent-profile is only supported with --agent codex" >&2
  exit 1
fi
validate_agent_reasoning_effort "$AGENT" "$AGENT_REASONING_EFFORT" || exit 1
export AGENT_REASONING_EFFORT

if [ ! -d "$TASK_DIR" ]; then
  echo "Missing task directory: $TASK_DIR" >&2
  exit 1
fi
RESOLVED_BOUNDARY_PATHS=()
mapfile -d '' -t RESOLVED_BOUNDARY_PATHS < <(
  "$PYTHON_BIN" - "$TASK_DIR" "$RESULTS_DIR" <<'PY'
import sys
from pathlib import Path

task_source = Path(sys.argv[1]).resolve(strict=True)
results_root = Path(sys.argv[2]).resolve(strict=False)
if task_source == results_root or task_source.is_relative_to(results_root):
    raise SystemExit(
        "canonical task source is inside agent-writable results: "
        f"{task_source} (results: {results_root})"
    )
for path in (task_source, results_root):
    sys.stdout.buffer.write(str(path).encode() + b"\0")
PY
)
if [ "${#RESOLVED_BOUNDARY_PATHS[@]}" -ne 2 ]; then
  echo "Failed to resolve task/results security boundary" >&2
  exit 1
fi
TASK_DIR="${RESOLVED_BOUNDARY_PATHS[0]}"
RESULTS_DIR="${RESOLVED_BOUNDARY_PATHS[1]}"
if [ ! -f "$INSTRUCT_PATH" ]; then
  echo "Missing idea instruction template: $INSTRUCT_PATH" >&2
  exit 1
fi
if ! [[ "$CYCLE_INDEX" =~ ^[0-9]+$ ]] || [ "$CYCLE_INDEX" -le 0 ]; then
  echo "--cycle-index must be a positive integer (got: ${CYCLE_INDEX:-<empty>})" >&2
  exit 1
fi
mkdir -p "$RESULTS_DIR"
mkdir -p "$GLOBAL_MEMORY_DIR"
mkdir -p "$CONCLUSIONS_DIR"
TASK_SOURCE_DIR="$TASK_DIR"
STAGED_TASK_DIR="$(ensure_staged_task_copy "$REPO_ROOT" "$TASK_SOURCE_DIR" "$RESULTS_DIR")"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
if [ -z "$OUTPUT_DIR" ]; then
  OUTPUT_DIR="$RESULTS_DIR/_idea_generation/cycle_${CYCLE_INDEX}_${TIMESTAMP}"
fi
if [ -e "$OUTPUT_DIR" ]; then
  echo "Idea output directory already exists: $OUTPUT_DIR" >&2
  exit 1
fi
mkdir -p "$OUTPUT_DIR"
configure_cost_tracking "$RESULTS_DIR" idea

case "$AGENT" in
  claude) AGENT_CONFIG_DIR=".claude" ;;
  codex) AGENT_CONFIG_DIR=".codex" ;;
  cursor) AGENT_CONFIG_DIR=".cursor" ;;
  opencode) AGENT_CONFIG_DIR=".opencode" ;;
  gemini) AGENT_CONFIG_DIR=".gemini" ;;
  pi) AGENT_CONFIG_DIR=".pi/agent" ;;
  *)
    echo "Unsupported agent: $AGENT" >&2
    exit 1
    ;;
esac

FORMATTED_SKILLS_REL_DIR="$AGENT_CONFIG_DIR/skills"
(
  cd "$OUTPUT_DIR"
  "$PYTHON_BIN" - "$REPO_ROOT" "$FORMATTED_SKILLS_REL_DIR" "$RESULTS_DIR" "$CYCLE_INDEX" "$GLOBAL_MEMORY_DIR" "$CONCLUSIONS_DIR" "$WORKER_INDEX" <<'PY'
import json
import shutil
import sys
from pathlib import Path

script_dir = Path(sys.argv[1])
formatted_skills_dir = Path(sys.argv[2])
results_dir = Path(sys.argv[3])
cycle_index = int(sys.argv[4])
global_memory_dir = Path(sys.argv[5])
conclusions_dir = Path(sys.argv[6])
worker_index = int(sys.argv[7])

sys.path.insert(0, str(script_dir))
from src.orchestrator import state as state_store  # noqa: E402
from src.orchestrator.idea_bundle import MAX_SOURCE_PATTERN_CHARS  # noqa: E402
from src.investigation.skill_scope import (  # noqa: E402
    skills_for_step,
    stage_skill_tree,
    validate_skill_set,
)
from src.investigation.attempt_feedback import summarize_recent_attempts  # noqa: E402
from src.investigation.files import load_instructions, relative_display_path  # noqa: E402

skills_src = script_dir / "src" / "agents" / "skills"
if not skills_src.is_dir():
    raise SystemExit(f"Missing skills directory: {skills_src}")
selected_skills = skills_for_step("idea")
validate_skill_set(skills_src, selected_skills)

recent_ideas = state_store.tail_jsonl(
    state_store.ideas_history_path(results_dir),
    max_lines=10,
)
recent_attempts = state_store.tail_jsonl(
    state_store.attempts_history_path(results_dir),
    max_lines=20,
)
state_store.ensure_histories(
    results_dir,
    include_global_memory=results_dir == global_memory_dir,
)
state_store.ensure_global_memory_files(global_memory_dir)
state_store.ensure_conclusions_files(conclusions_dir)
global_memory_md = state_store.global_memory_md_path(global_memory_dir)
global_memory_jsonl = state_store.global_memory_jsonl_path(global_memory_dir)
global_memory_recent = state_store.tail_jsonl(global_memory_jsonl, max_lines=30)
conclusions_md = state_store.conclusions_md_path(conclusions_dir)
conclusions_history = state_store.conclusions_history_path(conclusions_dir)
other_workers_conclusions_md = state_store.other_workers_conclusions_md_path(conclusions_dir)


def _read_conclusions_body(path: Path, fallback: str) -> str:
    if not path.is_file():
        return fallback
    try:
        body = path.read_text(encoding="utf-8").strip()
    except OSError:
        return fallback
    return body or fallback


own_conclusions_text = _read_conclusions_body(
    conclusions_md, "_No conclusions yet._"
)
other_workers_body = _read_conclusions_body(other_workers_conclusions_md, "")
_other_placeholder = state_store.EMPTY_OTHER_WORKERS_CONCLUSIONS_PLACEHOLDER.strip()
if other_workers_body and other_workers_body != _other_placeholder:
    other_workers_conclusions_section = (
        f"\n### From other workers\n\n{other_workers_body}\n"
    )
else:
    other_workers_conclusions_section = ""

best_manifest = state_store.load_json(results_dir / state_store.BEST_MANIFEST_FILE)
if not isinstance(best_manifest, dict):
    best_manifest = {}

def summarize_jsonl_entries(entries, keys):
    if not entries:
        return "- none"
    lines = []
    for idx, entry in enumerate(entries[-5:], start=1):
        if not isinstance(entry, dict):
            lines.append(f"- item {idx}: <non-object entry>")
            continue
        parts = []
        for key in keys:
            value = entry.get(key)
            if value in (None, "", [], {}):
                continue
            if isinstance(value, (dict, list)):
                value = json.dumps(value, sort_keys=True)
            parts.append(f"{key}={value}")
        if not parts:
            parts.append("<no summarized fields>")
        lines.append(f"- item {idx}: " + "; ".join(parts))
    return "\n".join(lines)

def summarize_best_manifest(manifest):
    if not manifest:
        return "- no best program selected yet"
    keys = ("program_dir", "entrypoint_path", "combined_score", "correct", "source_attempt_id")
    parts = []
    for key in keys:
        value = manifest.get(key)
        if value in (None, "", [], {}):
            continue
        parts.append(f"{key}={value}")
    return "- " + "; ".join(parts) if parts else "- best manifest present but empty"

base_dir = Path.cwd()
task_dir_display = Path(relative_display_path(results_dir / "task", base_dir=base_dir))
results_dir_display = Path(relative_display_path(results_dir, base_dir=base_dir))
ideas_history_display = relative_display_path(state_store.ideas_history_path(results_dir), base_dir=base_dir)
attempts_history_display = relative_display_path(state_store.attempts_history_path(results_dir), base_dir=base_dir)
best_manifest_display = relative_display_path(results_dir / state_store.BEST_MANIFEST_FILE, base_dir=base_dir)

extra = {
    "CYCLE_INDEX": str(cycle_index),
    "WORKER_INDEX": str(worker_index),
    "IDEA_OUTPUT_DIR": ".",
    "IDEA_BUNDLE_FILE": "./idea_bundle.json",
    "TARGET_RUBRICS_FILE": "./target_rubrics.json",
    "GLOBAL_RESULTS_DIR": relative_display_path(results_dir, base_dir=base_dir),
    "IDEAS_HISTORY_FILE": ideas_history_display,
    "ATTEMPTS_HISTORY_FILE": attempts_history_display,
    "BEST_MANIFEST_FILE": best_manifest_display,
    "RECENT_IDEAS_SUMMARY": summarize_jsonl_entries(recent_ideas, ("cycle_index", "idea_id", "title")),
    "RECENT_ATTEMPTS_SUMMARY": summarize_recent_attempts(results_dir / "task", recent_attempts),
    "GLOBAL_MEMORY_MD_FILE": relative_display_path(global_memory_md, base_dir=base_dir),
    "GLOBAL_MEMORY_JSONL_FILE": relative_display_path(global_memory_jsonl, base_dir=base_dir),
    "GLOBAL_MEMORY_RECENT_SUMMARY": summarize_jsonl_entries(
        global_memory_recent,
        ("step", "idea_id", "summary", "next_action"),
    ),
    "BEST_MANIFEST_SUMMARY": summarize_best_manifest(best_manifest),
    "CONCLUSIONS_MD_FILE": relative_display_path(conclusions_md, base_dir=base_dir),
    "CONCLUSIONS_HISTORY_FILE": relative_display_path(conclusions_history, base_dir=base_dir),
    "OWN_CONCLUSIONS": own_conclusions_text,
    "OTHER_WORKERS_CONCLUSIONS_SECTION": other_workers_conclusions_section,
}

for skill_name in selected_skills:
    src_dir = skills_src / skill_name
    dst_dir = formatted_skills_dir / src_dir.name
    stage_skill_tree(src_dir, dst_dir)

    src_skill = src_dir / "SKILL.md"
    if src_skill.is_file():
        rendered = load_instructions(src_skill, task_dir_display, results_dir_display, extra)
        (dst_dir / "SKILL.md").write_text(rendered, encoding="utf-8")
PY
)

PROMPT="$(
  cd "$OUTPUT_DIR"
  "$PYTHON_BIN" - "$REPO_ROOT" "$INSTRUCT_PATH" "$RESULTS_DIR" "$CYCLE_INDEX" "$GLOBAL_MEMORY_DIR" "$CONCLUSIONS_DIR" "$WORKER_INDEX" <<'PY'
import json
import sys
from pathlib import Path

script_dir = Path(sys.argv[1])
instruct_path = Path(sys.argv[2])
results_dir = Path(sys.argv[3])
cycle_index = int(sys.argv[4])
global_memory_dir = Path(sys.argv[5])
conclusions_dir = Path(sys.argv[6])
worker_index = int(sys.argv[7])
sys.path.insert(0, str(script_dir))

from src.orchestrator import state as state_store
from src.investigation.attempt_feedback import summarize_recent_attempts
from src.orchestrator.idea_bundle import MAX_SOURCE_PATTERN_CHARS
from src.investigation.files import load_instructions, relative_display_path

recent_ideas = state_store.tail_jsonl(
    state_store.ideas_history_path(results_dir),
    max_lines=10,
)
recent_attempts = state_store.tail_jsonl(
    state_store.attempts_history_path(results_dir),
    max_lines=20,
)
state_store.ensure_histories(
    results_dir,
    include_global_memory=results_dir == global_memory_dir,
)
state_store.ensure_global_memory_files(global_memory_dir)
state_store.ensure_conclusions_files(conclusions_dir)
global_memory_md = state_store.global_memory_md_path(global_memory_dir)
global_memory_jsonl = state_store.global_memory_jsonl_path(global_memory_dir)
global_memory_recent = state_store.tail_jsonl(global_memory_jsonl, max_lines=30)
conclusions_md = state_store.conclusions_md_path(conclusions_dir)
conclusions_history = state_store.conclusions_history_path(conclusions_dir)
other_workers_conclusions_md = state_store.other_workers_conclusions_md_path(conclusions_dir)


def _read_conclusions_body(path: Path, fallback: str) -> str:
    if not path.is_file():
        return fallback
    try:
        body = path.read_text(encoding="utf-8").strip()
    except OSError:
        return fallback
    return body or fallback


own_conclusions_text = _read_conclusions_body(
    conclusions_md, "_No conclusions yet._"
)
other_workers_body = _read_conclusions_body(other_workers_conclusions_md, "")
_other_placeholder = state_store.EMPTY_OTHER_WORKERS_CONCLUSIONS_PLACEHOLDER.strip()
if other_workers_body and other_workers_body != _other_placeholder:
    other_workers_conclusions_section = (
        f"\n### From other workers\n\n{other_workers_body}\n"
    )
else:
    other_workers_conclusions_section = ""

best_manifest = state_store.load_json(results_dir / state_store.BEST_MANIFEST_FILE)
if not isinstance(best_manifest, dict):
    best_manifest = {}

def summarize_jsonl_entries(entries, keys):
    if not entries:
        return "- none"
    lines = []
    for idx, entry in enumerate(entries[-5:], start=1):
        if not isinstance(entry, dict):
            lines.append(f"- item {idx}: <non-object entry>")
            continue
        parts = []
        for key in keys:
            value = entry.get(key)
            if value in (None, "", [], {}):
                continue
            if isinstance(value, (dict, list)):
                value = json.dumps(value, sort_keys=True)
            parts.append(f"{key}={value}")
        if not parts:
            parts.append("<no summarized fields>")
        lines.append(f"- item {idx}: " + "; ".join(parts))
    return "\n".join(lines)

def summarize_best_manifest(manifest):
    if not manifest:
        return "- no best program selected yet"
    keys = ("program_dir", "entrypoint_path", "combined_score", "correct", "source_attempt_id")
    parts = []
    for key in keys:
        value = manifest.get(key)
        if value in (None, "", [], {}):
            continue
        parts.append(f"{key}={value}")
    return "- " + "; ".join(parts) if parts else "- best manifest present but empty"

base_dir = Path.cwd()
task_dir_display = Path(relative_display_path(results_dir / "task", base_dir=base_dir))
results_dir_display = Path(relative_display_path(results_dir, base_dir=base_dir))
ideas_history_display = relative_display_path(state_store.ideas_history_path(results_dir), base_dir=base_dir)
attempts_history_display = relative_display_path(state_store.attempts_history_path(results_dir), base_dir=base_dir)
best_manifest_display = relative_display_path(results_dir / state_store.BEST_MANIFEST_FILE, base_dir=base_dir)

extra = {
    "CYCLE_INDEX": str(cycle_index),
    "WORKER_INDEX": str(worker_index),
    "IDEA_OUTPUT_DIR": ".",
    "IDEA_BUNDLE_FILE": "./idea_bundle.json",
    "TARGET_RUBRICS_FILE": "./target_rubrics.json",
    "GLOBAL_RESULTS_DIR": relative_display_path(results_dir, base_dir=base_dir),
    "IDEAS_HISTORY_FILE": ideas_history_display,
    "ATTEMPTS_HISTORY_FILE": attempts_history_display,
    "BEST_MANIFEST_FILE": best_manifest_display,
    "RECENT_IDEAS_SUMMARY": summarize_jsonl_entries(recent_ideas, ("cycle_index", "idea_id", "title")),
    "RECENT_ATTEMPTS_SUMMARY": summarize_recent_attempts(results_dir / "task", recent_attempts),
    "GLOBAL_MEMORY_MD_FILE": relative_display_path(global_memory_md, base_dir=base_dir),
    "GLOBAL_MEMORY_JSONL_FILE": relative_display_path(global_memory_jsonl, base_dir=base_dir),
    "GLOBAL_MEMORY_RECENT_SUMMARY": summarize_jsonl_entries(
        global_memory_recent,
        ("step", "idea_id", "summary", "next_action"),
    ),
    "BEST_MANIFEST_SUMMARY": summarize_best_manifest(best_manifest),
    "CONCLUSIONS_MD_FILE": relative_display_path(conclusions_md, base_dir=base_dir),
    "CONCLUSIONS_HISTORY_FILE": relative_display_path(conclusions_history, base_dir=base_dir),
    "OWN_CONCLUSIONS": own_conclusions_text,
    "OTHER_WORKERS_CONCLUSIONS_SECTION": other_workers_conclusions_section,
}

template = load_instructions(instruct_path, task_dir_display, results_dir_display, extra)
print(template)
PY
)"

if [ -n "$VALIDATION_FEEDBACK" ]; then
  PROMPT+="$("$PYTHON_BIN" - "$REPO_ROOT" "$VALIDATION_FEEDBACK" <<'PY'
import sys

sys.path.insert(0, sys.argv[1])
from src.investigation.idea_retry_feedback import render_validation_feedback

print(render_validation_feedback(sys.argv[2]))
PY
)"
fi

write_agent_prompt_file "$OUTPUT_DIR/idea_prompt.md" "$PROMPT"

LOG_FILE="$OUTPUT_DIR/idea_agent_${AGENT}_${TIMESTAMP}.log"
LOG_PIPE="$OUTPUT_DIR/.idea_stream_${TIMESTAMP}.fifo"
AGENT_PID=""
TEE_PID=""
INTERRUPTED=0
CLEANUP_IN_PROGRESS=0

cleanup_run_processes() {
  if [ "${CLEANUP_IN_PROGRESS:-0}" -eq 1 ]; then
    return 0
  fi
  CLEANUP_IN_PROGRESS=1

  if [ -n "${AGENT_PID:-}" ] && kill -0 "$AGENT_PID" 2>/dev/null; then
    terminate_pid_tree "$AGENT_PID" 0.2
  fi
  AGENT_PID=""

  if [ -n "${TEE_PID:-}" ] && kill -0 "$TEE_PID" 2>/dev/null; then
    kill -TERM "$TEE_PID" 2>/dev/null || true
    wait "$TEE_PID" 2>/dev/null || true
  fi
  TEE_PID=""

  if [ "${INTERRUPTED:-0}" -eq 1 ] && [ -f "${LOG_FILE:-}" ]; then
    record_headless_cost_log \
      "$PYTHON_BIN" "$LOG_FILE" idea_generation \
      "idea:${CYCLE_INDEX}:${TIMESTAMP}:worker-${WORKER_INDEX}" interrupted
  fi

  if [ -n "${LOG_PIPE:-}" ] && [ -p "$LOG_PIPE" ]; then
    rm -f "$LOG_PIPE"
  fi
  LOG_PIPE=""
}

on_interrupt() {
  trap - INT TERM
  INTERRUPTED=1
  echo "Interrupt received. Shutting down agent and log stream..." >&2
  cleanup_run_processes
  exit 130
}

trap on_interrupt INT TERM
trap cleanup_run_processes EXIT

export CYCLE_INDEX
export WORKER_INDEX
export IDEA_OUTPUT_DIR="$OUTPUT_DIR"
export IDEA_BUNDLE_PATH="$OUTPUT_DIR/idea_bundle.json"
export TARGET_RUBRICS_PATH="$OUTPUT_DIR/target_rubrics.json"
export GLOBAL_MEMORY_MD_FILE="$GLOBAL_MEMORY_DIR/memory.md"
export GLOBAL_MEMORY_JSONL_FILE="$GLOBAL_MEMORY_DIR/memory.jsonl"
export CONCLUSIONS_MD_FILE="$CONCLUSIONS_DIR/conclusions.md"
export CONCLUSIONS_HISTORY_FILE="$CONCLUSIONS_DIR/conclusions_history.jsonl"
export OTHER_WORKERS_CONCLUSIONS_FILE="$CONCLUSIONS_DIR/conclusions_other_workers.md"

echo "Idea directory: $OUTPUT_DIR"
echo "Agent config directory: $OUTPUT_DIR/$AGENT_CONFIG_DIR"
echo "Logging to: $LOG_FILE"
if [ -n "$AGENT_MODEL" ]; then
  echo "Agent model override: $AGENT_MODEL"
fi
if [ -n "$AGENT_REASONING_EFFORT" ]; then
  echo "Agent reasoning effort override: $AGENT_REASONING_EFFORT"
fi
mkfifo "$LOG_PIPE"
bounded_agent_log_writer "$PYTHON_BIN" "$LOG_FILE" "$AGENT_LOG_MAX_BYTES" \
  < "$LOG_PIPE" &
TEE_PID=$!

(
  if [ -n "${AGENTIC_TASK_FAILURE_FD:-}" ]; then
    exec {AGENTIC_TASK_FAILURE_FD}>&-
    unset AGENTIC_TASK_FAILURE_FD
  fi
  AGENT_PROMPT_FILE="idea_prompt.md" \
  run_agent_prompt \
    "$AGENT" \
    "$PROMPT" \
    "$AGENT_MODEL" \
    "$AGENT_PROFILE" \
    "$OUTPUT_DIR"
) > "$LOG_PIPE" 2>&1 &
AGENT_PID=$!

set +e
AGENT_EXIT_CODE=0
TEE_EXIT_CODE=0
if [ -n "${AGENT_PID:-}" ]; then
  wait "$AGENT_PID"
  AGENT_EXIT_CODE=$?
fi
if [ -n "${TEE_PID:-}" ]; then
  wait "$TEE_PID"
  TEE_EXIT_CODE=$?
fi
set -e

if [ "$INTERRUPTED" -eq 1 ] || [ "$AGENT_EXIT_CODE" -eq 130 ]; then
  exit 130
fi
if [ "$TEE_EXIT_CODE" -ne 0 ]; then
  echo "agent_step_status agent_exit_code=$AGENT_EXIT_CODE logger_exit_code=$TEE_EXIT_CODE failure_scope=infrastructure" >&2 || true
  if [ -n "${AGENTIC_TASK_FAILURE_FD:-}" ]; then
    "$PYTHON_BIN" -m src.investigation.task_failure \
      --log-failure-fd "$AGENTIC_TASK_FAILURE_FD" \
      --attempt-id "idea_generation_cycle_${CYCLE_INDEX}_worker_${WORKER_INDEX}" \
      --idea-id idea_generation
  fi
  exit 4
fi

record_headless_cost_log \
  "$PYTHON_BIN" "$LOG_FILE" idea_generation \
  "idea:${CYCLE_INDEX}:${TIMESTAMP}:worker-${WORKER_INDEX}" \
  "$(headless_cost_status "$AGENT_EXIT_CODE" "$INTERRUPTED")"

if [ -n "${LOG_PIPE:-}" ] && [ -p "$LOG_PIPE" ]; then
  rm -f "$LOG_PIPE"
fi

trap - INT TERM EXIT

if [ "$INTERRUPTED" -eq 1 ]; then
  exit 130
fi
if [ "$AGENT_EXIT_CODE" -ne 0 ]; then
  exit "$AGENT_EXIT_CODE"
fi
"$PYTHON_BIN" - "$REPO_ROOT" "$OUTPUT_DIR" "$CYCLE_INDEX" "$GLOBAL_MEMORY_JSONL_FILE" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

script_dir = Path(sys.argv[1])
output_dir = Path(sys.argv[2])
cycle_index = int(sys.argv[3])
global_memory_jsonl_path = Path(sys.argv[4])
bundle_path = output_dir / "idea_bundle.json"
rubrics_path = output_dir / "target_rubrics.json"

sys.path.insert(0, str(script_dir))
from src.orchestrator.idea_bundle import (  # noqa: E402
    IdeaBundleValidationError,
    normalize_idea_bundle,
    normalize_target_rubrics_payload,
    parse_agent_json,
    without_target_rubric_verification,
)
from src.orchestrator import state as state_store  # noqa: E402

if not bundle_path.exists():
    raise SystemExit(f"Missing required artifact: {bundle_path}")
if not rubrics_path.exists():
    raise SystemExit(f"Missing required artifact: {rubrics_path}")

try:
    raw_bundle, bundle_repaired = parse_agent_json(
        bundle_path.read_text(encoding="utf-8")
    )
except json.JSONDecodeError as exc:
    raise SystemExit(f"Invalid JSON in {bundle_path}: {exc}") from exc

try:
    raw_rubrics, rubrics_repaired = parse_agent_json(
        rubrics_path.read_text(encoding="utf-8")
    )
except json.JSONDecodeError as exc:
    raise SystemExit(f"Invalid JSON in {rubrics_path}: {exc}") from exc

raw_bundle = without_target_rubric_verification(raw_bundle)
raw_rubrics = without_target_rubric_verification(raw_rubrics)

try:
    normalized_bundle = normalize_idea_bundle(raw_bundle, cycle_index)
    normalized_rubrics = normalize_target_rubrics_payload(raw_rubrics)
except IdeaBundleValidationError as exc:
    raise SystemExit(f"Invalid idea artifacts: {exc}") from exc

if normalized_rubrics != normalized_bundle["target_rubrics"]:
    raise SystemExit(
        "target_rubrics.json does not match idea_bundle.json target_rubrics"
    )

if bundle_repaired or rubrics_repaired:
    repaired_names = []
    if bundle_repaired:
        repaired_names.append(bundle_path.name)
    if rubrics_repaired:
        repaired_names.append(rubrics_path.name)
    print(
        "[idea.sh] warning: repaired trailing commas in "
        + ", ".join(repaired_names)
    )

bundle_path.write_text(
    json.dumps(normalized_bundle, indent=2, sort_keys=True),
    encoding="utf-8",
)
rubrics_path.write_text(
    json.dumps(normalized_bundle["target_rubrics"], indent=2, sort_keys=True),
    encoding="utf-8",
)

print(
    "[idea.sh] validated idea artifacts: "
    f"idea_id={normalized_bundle['idea_id']} "
    f"rubrics={len(normalized_bundle['target_rubrics'])}"
)

fallback_entry = {
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "step": "idea",
    "scope": "idea",
    "summary": "generated idea bundle (auto-healed by idea.sh)",
    "evidence": "idea_bundle.json + target_rubrics.json",
    "next_action": "run attempt",
    "idea_id": normalized_bundle["idea_id"],
}
fallback_jsonl_appended = state_store.append_jsonl_if_absent(
    global_memory_jsonl_path,
    fallback_entry,
    matching={"step": "idea", "idea_id": normalized_bundle["idea_id"]},
)
global_memory_md_path = state_store.global_memory_md_path(global_memory_jsonl_path.parent)
fallback_markdown_appended = state_store.append_memory_markdown_entry_if_absent(
    global_memory_md_path,
    fallback_entry,
    matching_line=f"- idea_id: {normalized_bundle['idea_id']}",
)
if fallback_jsonl_appended or fallback_markdown_appended:
    print(
        "[idea.sh] warning: appended fallback idea-step memory entry "
        f"for idea_id={normalized_bundle['idea_id']}"
    )
PY
