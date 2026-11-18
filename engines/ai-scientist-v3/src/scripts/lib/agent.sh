#!/bin/bash
# Run a coding agent (through the headless CLI) natively on the host.

set -euo pipefail

AGENT_INNER_CMD=()
HEADLESS_NPX_SPEC="git+https://github.com/RobertTLange/headless-cli.git#93cd9b06b85f848af1308c41e018991b33907c5e"
case "${BASH_SOURCE[0]}" in
  */*) agent_lib_source_dir="${BASH_SOURCE[0]%/*}" ;;
  *) agent_lib_source_dir="." ;;
esac
AGENT_LIB_DIR="$(cd "$agent_lib_source_dir" && pwd)"
source "$AGENT_LIB_DIR/python_bin.sh"
PYTHON_BIN="$(resolve_python_bin)"
WRAPPER_PYTHON="$PYTHON_BIN"
export WRAPPER_PYTHON
AGENT_LOG_MIN_BYTES=1024
AGENT_LOG_MAX_ALLOWED_BYTES=1073741824
AGENT_PYTHON_BIN="${AGENT_PYTHON_BIN:-$PYTHON_BIN}"

validate_agent_log_max_bytes() {
  local max_bytes="$1"
  if [[ ! "$max_bytes" =~ ^[1-9][0-9]*$ ]] \
    || [ "${#max_bytes}" -gt 10 ] \
    || [ "$max_bytes" -lt "$AGENT_LOG_MIN_BYTES" ] \
    || [ "$max_bytes" -gt "$AGENT_LOG_MAX_ALLOWED_BYTES" ]; then
    echo "AGENT_LOG_MAX_BYTES must be an integer from ${AGENT_LOG_MIN_BYTES} to ${AGENT_LOG_MAX_ALLOWED_BYTES}" >&2
    return 2
  fi
}

bounded_agent_log_writer() {
  local python_bin="$1"
  local output_path="$2"
  local max_bytes="$3"
  local passthrough="${4:-0}"
  local -a writer_args=(
    --output "$output_path"
    --max-bytes "$max_bytes"
  )

  validate_agent_log_max_bytes "$max_bytes" || return $?
  if [ "$passthrough" = "1" ]; then
    writer_args+=(--passthrough)
  fi
  local writer_rc=0
  "$python_bin" "$AGENT_LIB_DIR/bounded_log.py" "${writer_args[@]}" || writer_rc=$?
  return "$writer_rc"
}

configure_cost_tracking() {
  local default_root="$1"
  local phase="$2"
  local output_format="${3:-human}"
  if [ -z "${AGENTIC_COST_ROOT:-}" ]; then
    export AGENTIC_COST_ROOT="$default_root"
    export AGENTIC_COST_TERMINAL="${AGENTIC_COST_TERMINAL:-1}"
  else
    export AGENTIC_COST_TERMINAL="${AGENTIC_COST_TERMINAL:-0}"
  fi
  export AGENTIC_COST_PHASE="$phase"
  export AGENTIC_COST_OUTPUT_FORMAT="${AGENTIC_COST_OUTPUT_FORMAT:-$output_format}"
}

record_headless_cost_log() {
  local python_bin="$1"
  local log_path="$2"
  local stage="$3"
  local invocation="$4"
  local status="$5"
  local -a print_args=()
  [ -n "${AGENTIC_COST_ROOT:-}" ] && [ -f "$log_path" ] || return 0
  if [ "${AGENTIC_COST_TERMINAL:-0}" = "1" ]; then
    print_args+=(--print-summary)
  fi
  local -a command=(
    "$python_bin" -m src.investigation.cost_tracking headless
    --root "$AGENTIC_COST_ROOT"
    --input "$log_path"
    --phase "${AGENTIC_COST_PHASE:-agent}"
    --stage "$stage"
    --invocation "$invocation"
    --agent "${AGENT:-unknown}"
    --status "$status"
    "${print_args[@]}"
  )
  if [ "${AGENTIC_COST_OUTPUT_FD:-1}" = "3" ]; then
    PYTHONPATH="$AGENT_LIB_DIR/../../..${PYTHONPATH:+:$PYTHONPATH}" \
      "${command[@]}" >&3 || true
  else
    PYTHONPATH="$AGENT_LIB_DIR/../../..${PYTHONPATH:+:$PYTHONPATH}" \
      "${command[@]}" || true
  fi
}

headless_cost_status() {
  local exit_code="$1"
  local interrupted="${2:-0}"
  if [ "$interrupted" -eq 1 ] || [ "$exit_code" -eq 130 ]; then
    echo interrupted
  elif [ "$exit_code" -eq 0 ]; then
    echo completed
  else
    echo failed
  fi
}

validate_reasoning_effort() {
  local effort="${1:-}"
  case "$effort" in
    ""|low|medium|high|xhigh) return 0 ;;
    *)
      echo "--reasoning-effort must be one of: low, medium, high, xhigh (got: $effort)" >&2
      return 1
      ;;
  esac
}

validate_agent_reasoning_effort() {
  local agent="$1"
  local effort="${2:-}"
  validate_reasoning_effort "$effort" || return 1
  if [ "$agent" = "gemini" ] && [ -n "$effort" ]; then
    echo "--reasoning-effort is not supported for gemini" >&2
    return 1
  fi
}

agent_streams_prompt_file() {
  case "$1" in
    claude|codex|gemini) return 0 ;;
    *) return 1 ;;
  esac
}

prompt_file_reference() {
  printf 'Read the complete task instructions from %s, then follow them exactly.' "$1"
}

write_agent_prompt_file() {
  local prompt_path="$1"
  local prompt_text="$2"
  local prompt_dir
  local prompt_tmp

  if [ -d "$prompt_path" ] && [ ! -L "$prompt_path" ]; then
    echo "[agent] prompt path is a directory: $prompt_path" >&2
    return 1
  fi

  prompt_dir="$(dirname "$prompt_path")"
  if ! prompt_tmp="$(mktemp "$prompt_dir/.agent-prompt-XXXXXX")"; then
    echo "[agent] failed to create temporary prompt in: $prompt_dir" >&2
    return 1
  fi
  if ! printf '%s\n' "$prompt_text" > "$prompt_tmp"; then
    rm -f -- "$prompt_tmp"
    echo "[agent] failed to write prompt file: $prompt_path" >&2
    return 1
  fi
  if [ -z "$AGENT_PYTHON_BIN" ]; then
    rm -f -- "$prompt_tmp"
    echo "[agent] python is required to install prompt files safely" >&2
    return 1
  fi
  if ! "$AGENT_PYTHON_BIN" - "$prompt_tmp" "$prompt_path" <<'PY'
import os
import sys

os.replace(sys.argv[1], sys.argv[2])
PY
  then
    rm -f -- "$prompt_tmp"
    echo "[agent] failed to install prompt file: $prompt_path" >&2
    return 1
  fi
  if [ ! -f "$prompt_path" ] || [ -L "$prompt_path" ]; then
    echo "[agent] prompt path is not a regular file: $prompt_path" >&2
    return 1
  fi
}

build_agent_inner_command() {
  local agent="$1"
  local prompt="$2"
  local agent_model="${3:-}"
  local agent_profile="${4:-}"
  local prompt_file="${AGENT_PROMPT_FILE:-}"
  local reasoning_effort="${AGENT_REASONING_EFFORT:-}"
  local -a headless_command=()

  agent_model="${agent_model#"${agent_model%%[![:space:]]*}"}"
  agent_model="${agent_model%"${agent_model##*[![:space:]]}"}"

  if [ -n "$agent_profile" ] && [ "$agent" != "codex" ]; then
    echo "[agent] agent profile is only supported for codex" >&2
    return 2
  fi
  if [ -n "$agent_profile" ] && [ -z "$agent_model" ]; then
    echo "[agent] agent model is required when agent profile is provided" >&2
    return 2
  fi
  validate_agent_reasoning_effort "$agent" "$reasoning_effort" || return 2
  case "$agent" in
    claude|codex|cursor|opencode|gemini|pi) ;;
    *)
      echo "Unsupported agent: $agent" >&2
      return 1
      ;;
  esac

  AGENT_INNER_CMD=(env HEADLESS_MODELS_DEV_CACHE=)
  if [ -n "${HEADLESS_BILLING:-}" ]; then
    AGENT_INNER_CMD+=("HEADLESS_BILLING=$HEADLESS_BILLING")
  fi
  if [ -n "${HEADLESS_BIN:-}" ]; then
    headless_command=("$HEADLESS_BIN")
  else
    headless_command=(npx -y "$HEADLESS_NPX_SPEC")
  fi
  AGENT_INNER_CMD+=("${headless_command[@]}" "$agent" --debug --usage --allow yolo)
  if [ -n "$agent_model" ]; then
    AGENT_INNER_CMD+=(--model "$agent_model")
  fi
  if [ -n "$agent_profile" ]; then
    AGENT_INNER_CMD+=(--profile "$agent_profile")
  fi
  if [ -n "$reasoning_effort" ]; then
    AGENT_INNER_CMD+=(--reasoning-effort "$reasoning_effort")
  fi
  if [ -n "$prompt_file" ] && agent_streams_prompt_file "$agent"; then
    AGENT_INNER_CMD+=(--prompt-file "$prompt_file")
  elif [ -n "$prompt_file" ]; then
    AGENT_INNER_CMD+=(--prompt "$(prompt_file_reference "$prompt_file")")
  else
    AGENT_INNER_CMD+=(--prompt "$prompt")
  fi
}

kill_pid_tree() {
  local root_pid="$1"
  local signal_name="$2"
  local child_pid

  if [ -z "$root_pid" ]; then
    return 0
  fi
  if ! [[ "$root_pid" =~ ^[0-9]+$ ]]; then
    return 0
  fi

  for child_pid in $(pgrep -P "$root_pid" 2>/dev/null || true); do
    kill_pid_tree "$child_pid" "$signal_name"
  done

  kill "-$signal_name" "$root_pid" 2>/dev/null || true
}

terminate_pid_tree() {
  local root_pid="$1"
  local grace_seconds_raw="${2:-0.2}"
  local grace_seconds

  if [ -z "$root_pid" ]; then
    return 0
  fi
  if ! [[ "$root_pid" =~ ^[0-9]+$ ]]; then
    return 0
  fi
  if ! kill -0 "$root_pid" 2>/dev/null; then
    return 0
  fi

  grace_seconds="$grace_seconds_raw"
  if ! [[ "$grace_seconds" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    grace_seconds="0.2"
  fi
  kill_pid_tree "$root_pid" TERM
  sleep "$grace_seconds"
  kill_pid_tree "$root_pid" KILL
  wait "$root_pid" 2>/dev/null || true
}

resolve_agent_capacity_retries() {
  local agent="$1"
  [ "$agent" = "codex" ] || return 0
  [ "${HEADLESS_CAPACITY_RETRIES+x}" = "x" ] || return 0
  case "$HEADLESS_CAPACITY_RETRIES" in
    0|1|2|3) printf '%s\n' "$HEADLESS_CAPACITY_RETRIES" ;;
    *)
      echo "[agent] HEADLESS_CAPACITY_RETRIES must be 0, 1, 2, or 3" >&2
      return 2
      ;;
  esac
}

# run_agent_prompt agent prompt agent_model agent_profile work_dir
run_agent_prompt() {
  local agent="$1"
  local prompt="$2"
  local agent_model="${3:-}"
  local agent_profile="${4:-}"
  local work_dir="${5:-}"
  local capacity_retries

  if [ -z "$work_dir" ]; then
    echo "[agent] run_agent_prompt requires: agent prompt agent_model agent_profile work_dir" >&2
    return 2
  fi
  build_agent_inner_command "$agent" "$prompt" "$agent_model" "$agent_profile" || return $?
  capacity_retries="$(resolve_agent_capacity_retries "$agent")" || return $?
  if [ -n "$capacity_retries" ]; then
    AGENT_INNER_CMD=(env "HEADLESS_CAPACITY_RETRIES=$capacity_retries" "${AGENT_INNER_CMD[@]}")
  fi
  (
    cd "$work_dir"
    "${AGENT_INNER_CMD[@]}"
  )
}
