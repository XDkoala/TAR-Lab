#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests.program_testutils import make_program_dir

from src.investigation import cli as investigation_cli

def _write_fake_agent(bin_dir: Path, binary_name: str) -> None:
    script = bin_dir / binary_name
    script.write_text(
        """#!/bin/bash
set -euo pipefail
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
if [ -n "${IDEA_BUNDLE_PATH:-}" ] && [ -n "${TARGET_RUBRICS_PATH:-}" ]; then
cat > "${IDEA_BUNDLE_PATH}" <<'JSON'
{"idea_id":"idea_0001","title":"fast smoke idea","hypothesis":"quickly verify pipeline","implementation_plan":["make one small improvement"],"target_rubrics":[{"id":"score_threshold","description":"combined_score > 2.6","type":"metric"}],"notes_for_agent":"smoke run"}
JSON
cat > "${TARGET_RUBRICS_PATH}" <<'JSON'
[{"id":"score_threshold","description":"combined_score > 2.6","type":"metric"}]
JSON
if [ -n "${GLOBAL_MEMORY_MD_FILE:-}" ]; then
  mkdir -p "$(dirname "${GLOBAL_MEMORY_MD_FILE}")"
  printf "### %s | step=idea | scope=idea\\n- summary: generated idea bundle\\n- evidence: idea_bundle.json + target_rubrics.json\\n- next_action: run attempt\\n- idea_id: idea_0001\\n\\n" "${TS}" >> "${GLOBAL_MEMORY_MD_FILE}"
fi
if [ -n "${GLOBAL_MEMORY_JSONL_FILE:-}" ]; then
  mkdir -p "$(dirname "${GLOBAL_MEMORY_JSONL_FILE}")"
  cat >> "${GLOBAL_MEMORY_JSONL_FILE}" <<JSON
{"timestamp":"${TS}","step":"idea","scope":"idea","summary":"generated idea bundle","evidence":"idea_bundle.json + target_rubrics.json","next_action":"run attempt","idea_id":"idea_0001"}
JSON
fi
echo "fake-agent ${0##*/} generated idea artifacts"
exit 0
fi
ATTEMPT="${ATTEMPT_ID:-attempt_unknown}"
IDEA="${IDEA_ID:-idea_unknown}"
RUBRIC_ID="${RUBRIC_ID:-score_threshold}"
mkdir -p program_1/src
cat > program_1/program_spec.toml <<'TOML'
schema_version = 1
code_root = "src"

[entrypoint]
path = "program.py"
language = "python"
type = "script"

[copy]
exclude = []
include = []

[integration]
kind = "generic"
notes = ""
TOML
cat > program_1/src/program.py <<'PY'
print("ok")
PY
cat > program_1/metrics.json <<'JSON'
{"combined_score":2.71}
JSON
cat > program_1/correct.json <<'JSON'
{"correct":true}
JSON
cat > program_1/rubric_status.json <<JSON
{"attempt_id":"${ATTEMPT}","idea_id":"${IDEA}","overall_pass":true,"rubrics":[{"id":"${RUBRIC_ID}","pass":true,"evidence":"ok"}],"metrics":{"combined_score":2.71,"correct":true},"next_recommendation":"done"}
JSON
cp program_1/rubric_status.json rubric_status.json
if [ -n "${GLOBAL_MEMORY_MD_FILE:-}" ]; then
  mkdir -p "$(dirname "${GLOBAL_MEMORY_MD_FILE}")"
  printf "### %s | step=evaluate | scope=attempt\\n- summary: evaluated program_1\\n- evidence: combined_score=2.71, correct=true\\n- next_action: verify rubrics\\n- idea_id: %s\\n- attempt_id: %s\\n- program_id: program_1\\n\\n" "${TS}" "${IDEA}" "${ATTEMPT}" >> "${GLOBAL_MEMORY_MD_FILE}"
fi
if [ -n "${GLOBAL_MEMORY_JSONL_FILE:-}" ]; then
  mkdir -p "$(dirname "${GLOBAL_MEMORY_JSONL_FILE}")"
  cat >> "${GLOBAL_MEMORY_JSONL_FILE}" <<JSON
{"timestamp":"${TS}","step":"evaluate","scope":"attempt","summary":"evaluated program_1","evidence":"combined_score=2.71, correct=true","next_action":"verify rubrics","idea_id":"${IDEA}","attempt_id":"${ATTEMPT}","program_id":"program_1","metrics":{"combined_score":2.71,"correct":true}}
JSON
fi
echo "fake-agent ${0##*/} completed"
""",
        encoding="utf-8",
    )
    script.chmod(0o755)


EXPERIMENT_DIR_RE = re.compile(r"^Experiment directory:\s*(.+)$", re.MULTILINE)
IDEA_DIR_RE = re.compile(r"^Idea directory:\s*(.+)$", re.MULTILINE)
DRAFT_DIR_RE = re.compile(r"^Draft directory:\s*(.+)$", re.MULTILINE)
# End-to-end task.sh runs spawn a Python process per gate event and take about 30 s
# on a shared host; the deadline only guards against hangs.
TASK_SH_TIMEOUT_SECONDS = 120


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _script_path(name: str) -> Path:
    return _repo_root() / "src" / "scripts" / name


def _decoded_preflight_prompt(path: Path) -> str:
    prompt = path.read_text(encoding="utf-8")
    marker = "Preflight diagnostic (base64): "
    encoded = prompt.split(marker, 1)[1].splitlines()[0]
    return base64.b64decode(encoded).decode("utf-8")


def _write_task_dir(root: Path) -> Path:
    task_dir = root / "task"
    make_program_dir(task_dir / "program_0", content="def solve():\n    return 1\n")
    (task_dir / "capture.py").write_text("# capture stub\n", encoding="utf-8")
    (task_dir / "evaluate.py").write_text("# evaluate stub\n", encoding="utf-8")
    (task_dir / "task.md").write_text(
        "# Goal\n- optimize objective\n\n# Rubrics\n- score threshold\n",
        encoding="utf-8",
    )
    return task_dir


def _write_task_md_only(root: Path) -> Path:
    task_dir = root / "task"
    task_dir.mkdir(parents=True)
    (task_dir / "task.md").write_text(
        "# Goal\n- optimize objective\n\n# Rubrics\n- score threshold\n",
        encoding="utf-8",
    )
    return task_dir








def _write_freeform_verification_agent(bin_dir: Path) -> None:
    script = bin_dir / "headless"
    script.write_text(
        """#!/bin/bash
set -euo pipefail
cat > "${IDEA_BUNDLE_PATH}" <<'JSON'
{"idea_id":"idea_0001","title":"verification compatibility","hypothesis":"optional metadata is ignored","implementation_plan":["implement"],"target_rubrics":[{"id":"score_gain","description":"improves score","type":"metric"},{"id":"uses_solver","description":"uses solver","type":"implementation","verification":"Inspect the solver trace."}],"notes_for_agent":""}
JSON
cat > "${TARGET_RUBRICS_PATH}" <<'JSON'
[{"id":"score_gain","description":"improves score","type":"metric"},{"id":"uses_solver","description":"uses solver","type":"implementation","verification":"Inspect the solver trace."}]
JSON
""",
        encoding="utf-8",
    )
    script.chmod(0o755)








def _write_failing_git(bin_dir: Path) -> None:
    script = bin_dir / "git"
    script.write_text(
        "#!/bin/bash\nprintf '%s\\n' 'trusted source fetch failed' >&2\nexit 23\n",
        encoding="utf-8",
    )
    script.chmod(0o755)








def _write_fake_agents(bin_dir: Path) -> None:
    for binary_name in ("headless", "claude", "agent", "opencode", "gemini", "pi"):
        _write_fake_agent(bin_dir, binary_name)


def _write_fake_code_only_agent(bin_dir: Path, binary_name: str) -> None:
    script = bin_dir / binary_name
    script.write_text(
        """#!/bin/bash
set -euo pipefail
mkdir -p program_1/src
cat > program_1/program_spec.toml <<'TOML'
schema_version = 1
code_root = "src"

[entrypoint]
path = "program.py"
language = "python"
type = "script"

[copy]
exclude = []
include = []

[integration]
kind = "generic"
notes = ""
TOML
cat > program_1/src/program.py <<'PY'
def solve():
    return 7
PY
echo "fake-agent ${0##*/} wrote program_1 only"
""",
        encoding="utf-8",
    )
    script.chmod(0o755)


def _write_task_dir_with_counting_evaluator(root: Path) -> Path:
    task_dir = root / "task"
    make_program_dir(task_dir / "program_0", content="def solve():\n    return 1\n")
    (task_dir / "capture.py").write_text("# capture stub\n", encoding="utf-8")
    (task_dir / "evaluate.py").write_text(
        """from __future__ import annotations
import argparse
import json
from pathlib import Path

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--program_path", required=True)
    parser.add_argument("--results_dir", required=True)
    args = parser.parse_args()
    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    counter = results_dir.parent / "eval_count.txt"
    count = int(counter.read_text(encoding="utf-8")) if counter.exists() else 0
    counter.write_text(str(count + 1), encoding="utf-8")
    Path(args.program_path).read_text(encoding="utf-8")
    (results_dir / "metrics.json").write_text(
        json.dumps({"combined_score": 3.14}),
        encoding="utf-8",
    )
    (results_dir / "correct.json").write_text(
        json.dumps({"correct": True}),
        encoding="utf-8",
    )
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
""",
        encoding="utf-8",
    )
    (task_dir / "task.md").write_text(
        "# Goal\n- optimize objective\n\n# Rubrics\n- score threshold\n",
        encoding="utf-8",
    )
    return task_dir


def _write_idea_and_rubrics(root: Path) -> tuple[Path, Path]:
    idea = root / "idea.json"
    rubrics = root / "rubrics.json"
    idea.write_text(
        json.dumps(
            {
                "idea_id": "idea_0001",
                "title": "fast smoke idea",
                "hypothesis": "quickly verify pipeline",
                "implementation_plan": ["make one small improvement"],
                "target_rubrics": [
                    {
                        "id": "score_threshold",
                        "description": "combined_score > 2.6",
                        "type": "metric",
                    }
                ],
                "notes_for_agent": "smoke run",
            }
        ),
        encoding="utf-8",
    )
    rubrics.write_text(
        json.dumps(
            [
                {
                    "id": "score_threshold",
                    "description": "combined_score > 2.6",
                    "type": "metric",
                }
            ]
        ),
        encoding="utf-8",
    )
    return idea, rubrics




































class RunShAgentExecutionTests(unittest.TestCase):



    def test_run_sh_default_results_dir_includes_agent_segment(self) -> None:
        script = _script_path("run.sh")
        self.assertTrue(script.exists())
        script_text = script.read_text(encoding="utf-8")
        self.assertIn('RESULTS_DIR="results/$TASK_NAME/$AGENT"', script_text)

    def test_run_sh_does_not_publish_runtime_manifest_to_search_agent(self) -> None:
        script_text = _script_path("run.sh").read_text(encoding="utf-8")

        self.assertNotIn('copy_file(Path(".agentic_runtime.json"))', script_text)

    def test_run_sh_workspace_sweep_kills_full_pid_tree(self) -> None:
        script = _script_path("run.sh")
        self.assertTrue(script.exists())
        script_text = script.read_text(encoding="utf-8")
        self.assertIn('kill_pid_tree "$pid" "$signal_name"', script_text)

    def test_run_sh_stages_top_level_python_helpers(self) -> None:
        script = _script_path("run.sh")
        self.assertTrue(script.exists())

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = _write_task_dir(root)
            (task_dir / "helper_mod.py").write_text(
                "VALUE = 123\n",
                encoding="utf-8",
            )
            results_dir = root / "results"
            bin_dir = root / "bin"
            bin_dir.mkdir()
            _write_fake_agent(bin_dir, "headless")
            idea_file, rubrics_file = _write_idea_and_rubrics(root)

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            env["HEADLESS_BIN"] = str(bin_dir / "headless")
            env["WRAPPER_PYTHON"] = sys.executable
            env["RUBRIC_ID"] = "score_threshold"

            cmd = [
                "bash",
                str(script),
                "--agent",
                "codex",
                "--task-dir",
                str(task_dir),
                "--results-dir",
                str(results_dir),
                "--idea-file",
                str(idea_file),
                "--rubrics-file",
                str(rubrics_file),
                "--attempt-id",
                "idea_0001_attempt_01",
            ]
            run = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=20,
                env=env,
            )
            output = (run.stdout or "") + (run.stderr or "")
            self.assertEqual(run.returncode, 0, msg=output)

            match = EXPERIMENT_DIR_RE.search(output)
            self.assertIsNotNone(match, msg=output)
            assert match is not None
            exp_dir = Path(match.group(1).strip())
            self.assertTrue((exp_dir / "helper_mod.py").is_file())
            self.assertTrue((results_dir / "task").is_dir())
            self.assertFalse((exp_dir / "task").exists())
            self.assertFalse((exp_dir / "global_memory").exists())

    def test_run_sh_writes_latest_run_manifest(self) -> None:
        script = _script_path("run.sh")
        self.assertTrue(script.exists())

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = _write_task_dir(root)
            results_dir = root / "results"
            bin_dir = root / "bin"
            bin_dir.mkdir()
            _write_fake_agent(bin_dir, "headless")
            idea_file, rubrics_file = _write_idea_and_rubrics(root)

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            env["HEADLESS_BIN"] = str(bin_dir / "headless")
            env["WRAPPER_PYTHON"] = sys.executable
            env["RUBRIC_ID"] = "score_threshold"

            cmd = [
                "bash",
                str(script),
                "--agent",
                "codex",
                "--task-dir",
                str(task_dir),
                "--results-dir",
                str(results_dir),
                "--idea-file",
                str(idea_file),
                "--rubrics-file",
                str(rubrics_file),
                "--attempt-id",
                "idea_0001_attempt_01",
            ]
            run = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=20,
                env=env,
            )
            output = (run.stdout or "") + (run.stderr or "")
            self.assertEqual(run.returncode, 0, msg=output)

            exp_match = EXPERIMENT_DIR_RE.search(output)
            self.assertIsNotNone(exp_match, msg=output)
            assert exp_match is not None
            exp_dir = Path(exp_match.group(1).strip())

            manifest_path = results_dir / "latest_run_manifest.json"
            self.assertTrue(manifest_path.is_file())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest.get("agent"), "codex")
            self.assertEqual(manifest.get("attempt_id"), "idea_0001_attempt_01")
            self.assertEqual(manifest.get("idea_id"), "idea_0001")
            self.assertEqual(Path(manifest.get("experiment_dir", "")), exp_dir)
            self.assertEqual(Path(manifest.get("log_file", "")).parent, exp_dir)


    def test_run_sh_interrupt_kills_detached_workspace_process(self) -> None:
        script = _script_path("run.sh")
        self.assertTrue(script.exists())

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = _write_task_dir(root)
            results_dir = root / "results"
            bin_dir = root / "bin"
            bin_dir.mkdir()
            fake_codex = bin_dir / "headless"
            fake_codex.write_text(
                """#!/bin/bash
set -euo pipefail
${WRAPPER_PYTHON:-python} - <<'PY'
import os
import pathlib
import sys
import time

work = pathlib.Path.cwd()
(work / "program_1").mkdir(parents=True, exist_ok=True)

pid = os.fork()
if pid > 0:
    sys.exit(0)
os.setsid()
pid = os.fork()
if pid > 0:
    sys.exit(0)

# Fully detach stdio so the daemon does not hold run.sh pipe/fifo handles.
devnull_fd = os.open(os.devnull, os.O_RDWR)
for fd in (0, 1, 2):
    os.dup2(devnull_fd, fd)
if devnull_fd > 2:
    os.close(devnull_fd)

(work / "detached_eval.pid").write_text(str(os.getpid()), encoding="utf-8")
lock = (work / "program_1" / "detached_eval.lock").open("w", encoding="utf-8")
lock.write("running\\n")
lock.flush()

while True:
    time.sleep(1)
PY
sleep 60
""",
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)
            idea_file, rubrics_file = _write_idea_and_rubrics(root)

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            env["HEADLESS_BIN"] = str(fake_codex)
            env["WRAPPER_PYTHON"] = sys.executable

            cmd = [
                "bash",
                str(script),
                "--agent",
                "codex",
                "--task-dir",
                str(task_dir),
                "--results-dir",
                str(results_dir),
                "--idea-file",
                str(idea_file),
                "--rubrics-file",
                str(rubrics_file),
                "--attempt-id",
                "idea_0001_attempt_01",
            ]
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            exp_dir = results_dir / "ideas" / "01_fast_smoke_idea"
            pid_file = exp_dir / "detached_eval.pid"
            try:
                deadline = time.time() + 10.0
                while time.time() < deadline:
                    if pid_file.is_file():
                        break
                    if proc.poll() is not None:
                        break
                    time.sleep(0.1)
                proc.send_signal(signal.SIGINT)
                stdout, stderr = proc.communicate(timeout=20)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate()

            output = (stdout or "") + (stderr or "")
            self.assertEqual(proc.returncode, 130, msg=output)

            match = EXPERIMENT_DIR_RE.search(output)
            self.assertIsNotNone(match, msg=output)
            assert match is not None
            exp_dir = Path(match.group(1).strip())
            pid_file = exp_dir / "detached_eval.pid"
            self.assertTrue(pid_file.is_file(), msg=output)

            pid = int(pid_file.read_text(encoding="utf-8").strip())
            detached_alive = True
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                detached_alive = False
            except PermissionError:
                detached_alive = True

            if detached_alive:
                os.kill(pid, signal.SIGKILL)

            self.assertFalse(
                detached_alive,
                msg=f"detached process still alive after interrupt (pid={pid})\n{output}",
            )

    def test_run_sh_renders_prompt_with_configurable_eval_cap(self) -> None:
        script = _script_path("run.sh")
        self.assertTrue(script.exists())

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = _write_task_dir(root)
            results_dir = root / "results"
            bin_dir = root / "bin"
            bin_dir.mkdir()
            _write_fake_agent(bin_dir, "headless")
            idea_file, rubrics_file = _write_idea_and_rubrics(root)

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            env["HEADLESS_BIN"] = str(bin_dir / "headless")
            env["WRAPPER_PYTHON"] = sys.executable
            env["RUBRIC_ID"] = "score_threshold"

            cmd = [
                "bash",
                str(script),
                "--agent",
                "codex",
                "--task-dir",
                str(task_dir),
                "--results-dir",
                str(results_dir),
                "--idea-file",
                str(idea_file),
                "--rubrics-file",
                str(rubrics_file),
                "--attempt-id",
                "idea_0001_attempt_01",
                "--max-program-evals-per-attempt",
                "3",
            ]
            run = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=20,
                env=env,
            )
            output = (run.stdout or "") + (run.stderr or "")
            self.assertEqual(run.returncode, 0, msg=output)

            match = EXPERIMENT_DIR_RE.search(output)
            self.assertIsNotNone(match, msg=output)
            assert match is not None
            exp_dir = Path(match.group(1).strip())
            self.assertEqual(
                exp_dir,
                results_dir / "ideas" / "01_fast_smoke_idea",
            )
            prompt = (exp_dir / "prompt.md").read_text(encoding="utf-8")
            self.assertEqual(stat.S_IMODE((exp_dir / "prompt.md").stat().st_mode), 0o600)
            self.assertIn("You may make at most `3` total `evaluate.py` calls", prompt)
            self.assertIn("If `3` is `1`", prompt)
            self.assertIn(
                '"$WRAPPER_PYTHON" -I -m src.investigation.evaluate_program',
                prompt,
            )
            self.assertIn('--evaluator "./evaluate.py"', prompt)
            self.assertIn(
                '--program-dir "./program_<idx>"',
                prompt,
            )
            self.assertNotIn("You may make at most `4` total `evaluate.py` calls", prompt)

    def test_run_sh_fails_when_eval_cap_exceeded(self) -> None:
        script = _script_path("run.sh")
        self.assertTrue(script.exists())

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = _write_task_dir(root)
            results_dir = root / "results"
            bin_dir = root / "bin"
            bin_dir.mkdir()
            fake_codex = bin_dir / "headless"
            fake_codex.write_text(
                """#!/bin/bash
set -euo pipefail
ATTEMPT="${ATTEMPT_ID:-attempt_unknown}"
IDEA="${IDEA_ID:-idea_unknown}"
RUBRIC_ID="${RUBRIC_ID:-score_threshold}"
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
for i in 1 2 3 4 5; do
  mkdir -p "program_${i}/src"
  cat > "program_${i}/program_spec.toml" <<'TOML'
schema_version = 1
code_root = "src"

[entrypoint]
path = "program.py"
language = "python"
type = "script"

[copy]
exclude = []
include = []

[integration]
kind = "generic"
notes = ""
TOML
  cat > "program_${i}/src/program.py" <<'PY'
print("ok")
PY
  cat > "program_${i}/metrics.json" <<'JSON'
{"combined_score":2.71}
JSON
  cat > "program_${i}/correct.json" <<'JSON'
{"correct":true}
JSON
  cat > "program_${i}/rubric_status.json" <<JSON
{"attempt_id":"${ATTEMPT}","idea_id":"${IDEA}","overall_pass":false,"rubrics":[{"id":"${RUBRIC_ID}","pass":false,"evidence":"cap test"}],"metrics":{"combined_score":2.71,"correct":true},"next_recommendation":"stop"}
JSON
done
cp program_5/rubric_status.json rubric_status.json
if [ -n "${GLOBAL_MEMORY_JSONL_FILE:-}" ]; then
  mkdir -p "$(dirname "${GLOBAL_MEMORY_JSONL_FILE}")"
  cat >> "${GLOBAL_MEMORY_JSONL_FILE}" <<JSON
{"timestamp":"${TS}","step":"evaluate","scope":"attempt","summary":"cap test entry","evidence":"latest program_5 evaluated","next_action":"stop","idea_id":"${IDEA}","attempt_id":"${ATTEMPT}","program_id":"program_5","metrics":{"combined_score":2.71,"correct":true}}
JSON
fi
echo "fake-agent codex completed"
""",
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)
            idea_file, rubrics_file = _write_idea_and_rubrics(root)

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            env["HEADLESS_BIN"] = str(fake_codex)
            env["WRAPPER_PYTHON"] = sys.executable
            env["RUBRIC_ID"] = "score_threshold"

            cmd = [
                "bash",
                str(script),
                "--agent",
                "codex",
                "--task-dir",
                str(task_dir),
                "--results-dir",
                str(results_dir),
                "--idea-file",
                str(idea_file),
                "--rubrics-file",
                str(rubrics_file),
                "--attempt-id",
                "idea_0001_attempt_01",
                "--max-program-evals-per-attempt",
                "4",
            ]
            run = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=20,
                env=env,
            )
            output = (run.stdout or "") + (run.stderr or "")
            self.assertNotEqual(run.returncode, 0, msg=output)
            self.assertIn("evaluated candidate program count exceeds cap", output)

    def test_run_sh_does_not_require_baseline_rubric_status(self) -> None:
        script = _script_path("run.sh")
        self.assertTrue(script.exists())

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = _write_task_dir(root)
            results_dir = root / "results"
            bin_dir = root / "bin"
            bin_dir.mkdir()
            fake_codex = bin_dir / "headless"
            fake_codex.write_text(
                """#!/bin/bash
set -euo pipefail
ATTEMPT="${ATTEMPT_ID:-attempt_unknown}"
IDEA="${IDEA_ID:-idea_unknown}"
RUBRIC_ID="${RUBRIC_ID:-score_threshold}"
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
cat > program_0/metrics.json <<'JSON'
{"combined_score":1.0}
JSON
cat > program_0/correct.json <<'JSON'
{"correct":true}
JSON
mkdir -p program_1/src
cat > program_1/program_spec.toml <<'TOML'
schema_version = 1
code_root = "src"

[entrypoint]
path = "program.py"
language = "python"
type = "script"

[copy]
exclude = []
include = []

[integration]
kind = "generic"
notes = ""
TOML
cat > program_1/src/program.py <<'PY'
print("ok")
PY
cat > program_1/metrics.json <<'JSON'
{"combined_score":2.71}
JSON
cat > program_1/correct.json <<'JSON'
{"correct":true}
JSON
cat > program_1/rubric_status.json <<JSON
{"attempt_id":"${ATTEMPT}","idea_id":"${IDEA}","overall_pass":true,"rubrics":[{"id":"${RUBRIC_ID}","pass":true,"evidence":"candidate ok"}],"metrics":{"combined_score":2.71,"correct":true},"next_recommendation":"done"}
JSON
cp program_1/rubric_status.json rubric_status.json
if [ -n "${GLOBAL_MEMORY_JSONL_FILE:-}" ]; then
  mkdir -p "$(dirname "${GLOBAL_MEMORY_JSONL_FILE}")"
  cat >> "${GLOBAL_MEMORY_JSONL_FILE}" <<JSON
{"timestamp":"${TS}","step":"evaluate","scope":"attempt","summary":"evaluated program_1","evidence":"combined_score=2.71, correct=true","next_action":"done","idea_id":"${IDEA}","attempt_id":"${ATTEMPT}","program_id":"program_1","metrics":{"combined_score":2.71,"correct":true}}
JSON
fi
echo "fake-agent codex completed"
""",
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)
            idea_file, rubrics_file = _write_idea_and_rubrics(root)

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            env["HEADLESS_BIN"] = str(fake_codex)
            env["WRAPPER_PYTHON"] = sys.executable
            env["RUBRIC_ID"] = "score_threshold"

            cmd = [
                "bash",
                str(script),
                "--agent",
                "codex",
                "--task-dir",
                str(task_dir),
                "--results-dir",
                str(results_dir),
                "--idea-file",
                str(idea_file),
                "--rubrics-file",
                str(rubrics_file),
                "--attempt-id",
                "idea_0001_attempt_01",
            ]
            run = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=20,
                env=env,
            )
            output = (run.stdout or "") + (run.stderr or "")
            self.assertEqual(run.returncode, 0, msg=output)
            self.assertIn("rubric_status validated", output)
            self.assertNotIn("program_0/rubric_status.json", output)










    def test_run_sh_fails_for_invalid_attempt_id(self) -> None:
        script = _script_path("run.sh")
        self.assertTrue(script.exists())

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = _write_task_dir(root)
            results_dir = root / "results"
            idea_file, _ = _write_idea_and_rubrics(root)

            cmd = [
                "bash",
                str(script),
                "--agent",
                "codex",
                "--task-dir",
                str(task_dir),
                "--results-dir",
                str(results_dir),
                "--idea-file",
                str(idea_file),
                "--attempt-id",
                "bad/attempt",
            ]
            run = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=20,
                env={**os.environ, "WRAPPER_PYTHON": sys.executable},
            )
            output = (run.stdout or "") + (run.stderr or "")
            self.assertNotEqual(run.returncode, 0, msg=output)
            self.assertIn("Invalid attempt id", output)

    def test_run_sh_fails_when_idea_directory_contains_run_artifacts(self) -> None:
        script = _script_path("run.sh")
        self.assertTrue(script.exists())

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = _write_task_dir(root)
            results_dir = root / "results"
            idea_file, _ = _write_idea_and_rubrics(root)
            (results_dir / "ideas" / "01_fast_smoke_idea" / "program_0").mkdir(parents=True)

            cmd = [
                "bash",
                str(script),
                "--agent",
                "codex",
                "--task-dir",
                str(task_dir),
                "--results-dir",
                str(results_dir),
                "--idea-file",
                str(idea_file),
                "--attempt-id",
                "idea_0001_attempt_01",
            ]
            run = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=20,
                env={**os.environ, "WRAPPER_PYTHON": sys.executable},
            )
            output = (run.stdout or "") + (run.stderr or "")
            self.assertNotEqual(run.returncode, 0, msg=output)
            self.assertIn("Idea directory already contains run artifacts", output)

    def test_run_sh_fails_for_invalid_idea_id(self) -> None:
        script = _script_path("run.sh")
        self.assertTrue(script.exists())

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = _write_task_dir(root)
            results_dir = root / "results"
            idea_file = root / "idea_invalid.json"
            idea_file.write_text(
                json.dumps(
                    {
                        "idea_id": "bad/idea",
                        "target_rubrics": [
                            {
                                "id": "score_threshold",
                                "description": "combined_score > 2.6",
                                "type": "metric",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            cmd = [
                "bash",
                str(script),
                "--agent",
                "codex",
                "--task-dir",
                str(task_dir),
                "--results-dir",
                str(results_dir),
                "--idea-file",
                str(idea_file),
                "--attempt-id",
                "idea_0001_attempt_01",
            ]
            run = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=20,
                env={**os.environ, "WRAPPER_PYTHON": sys.executable},
            )
            output = (run.stdout or "") + (run.stderr or "")
            self.assertNotEqual(run.returncode, 0, msg=output)
            self.assertIn("Invalid idea id", output)


class IdeaShExecutionTests(unittest.TestCase):






    def test_idea_sh_executes_agent_and_validates_artifacts(self) -> None:
        script = _script_path("idea.sh")
        self.assertTrue(script.exists())

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = _write_task_dir(root)
            results_dir = root / "results"
            output_dir = root / "idea_out"
            bin_dir = root / "bin"
            bin_dir.mkdir()
            _write_fake_agent(bin_dir, "headless")

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            env["HEADLESS_BIN"] = str(bin_dir / "headless")
            env["WRAPPER_PYTHON"] = sys.executable

            cmd = [
                "bash",
                str(script),
                "--agent",
                "codex",
                "--task-dir",
                str(task_dir),
                "--results-dir",
                str(results_dir),
                "--cycle-index",
                "1",
                "--output-dir",
                str(output_dir),
            ]
            run = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=20,
                env=env,
            )
            output = (run.stdout or "") + (run.stderr or "")
            self.assertEqual(run.returncode, 0, msg=output)
            self.assertIn("validated idea artifacts", output)

            match = IDEA_DIR_RE.search(output)
            self.assertIsNotNone(match, msg=output)
            assert match is not None
            idea_dir = Path(match.group(1).strip())
            self.assertEqual(idea_dir.resolve(), output_dir.resolve())
            self.assertTrue((idea_dir / "idea_bundle.json").is_file())
            self.assertTrue((idea_dir / "target_rubrics.json").is_file())
            self.assertTrue((results_dir / "task").is_dir())
            self.assertFalse((idea_dir / "task").exists())
            self.assertFalse((idea_dir / "results").exists())
            self.assertFalse((idea_dir / "global_memory").exists())
            self.assertTrue((idea_dir / ".codex" / "skills" / "create-idea" / "SKILL.md").is_file())
            self.assertTrue((idea_dir / ".codex" / "skills" / "memory-log" / "SKILL.md").is_file())
            self.assertFalse((idea_dir / ".codex" / "skills" / "edit-program").exists())

            rendered_prompt = (idea_dir / "idea_prompt.md").read_text(encoding="utf-8")
            self.assertEqual(
                stat.S_IMODE((idea_dir / "idea_prompt.md").stat().st_mode),
                0o600,
            )
            rendered_skill = (
                idea_dir / ".codex" / "skills" / "create-idea" / "SKILL.md"
            ).read_text(encoding="utf-8")
            expected_results_dir = investigation_cli.relative_display_path(results_dir, base_dir=idea_dir)
            expected_memory_md = investigation_cli.relative_display_path(
                results_dir / "memory.md", base_dir=idea_dir
            )
            expected_memory_json = investigation_cli.relative_display_path(
                results_dir / "memory.jsonl", base_dir=idea_dir
            )
            self.assertIn("create-idea", rendered_prompt)
            self.assertIn("Omit `verification` from every rubric", rendered_prompt)
            self.assertNotIn(str(task_dir.resolve()), rendered_prompt)
            self.assertIn(expected_results_dir, rendered_prompt)
            self.assertIn(expected_memory_md, rendered_prompt)
            self.assertIn(expected_memory_json, rendered_prompt)
            self.assertNotIn(str(output_dir.resolve()), rendered_prompt)
            self.assertNotIn(str(task_dir.resolve()), rendered_skill)
            self.assertNotIn(str(results_dir.resolve()), rendered_skill)
            self.assertNotIn(str(output_dir.resolve()), rendered_skill)



    def test_idea_sh_repairs_agent_trailing_commas(self) -> None:
        script = _script_path("idea.sh")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = _write_task_dir(root)
            results_dir = root / "results"
            output_dir = root / "idea_out"
            bin_dir = root / "bin"
            bin_dir.mkdir()
            fake_codex = bin_dir / "codex"
            fake_codex.write_text(
                """#!/bin/bash
set -euo pipefail
cat > "${IDEA_BUNDLE_PATH}" <<'JSON'
{"idea_id":"idea_0001","title":"repair","hypothesis":"repair trailing commas","implementation_plan":["x",],"target_rubrics":[{"id":"score_threshold","description":"score","type":"metric"},],"notes_for_agent":"literal comma, } remains",}
JSON
cat > "${TARGET_RUBRICS_PATH}" <<'JSON'
[{"id":"score_threshold","description":"score","type":"metric"},]
JSON
""",
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            env["HEADLESS_BIN"] = str(fake_codex)
            env["WRAPPER_PYTHON"] = sys.executable

            run = subprocess.run(
                [
                    "bash",
                    str(script),
                    "--agent",
                    "codex",
                    "--task-dir",
                    str(task_dir),
                    "--results-dir",
                    str(results_dir),
                    "--cycle-index",
                    "1",
                    "--output-dir",
                    str(output_dir),
                ],
                capture_output=True,
                text=True,
                timeout=20,
                env=env,
            )
            output = (run.stdout or "") + (run.stderr or "")

            self.assertEqual(run.returncode, 0, msg=output)
            self.assertIn("repaired trailing commas", output)
            bundle = json.loads((output_dir / "idea_bundle.json").read_text())
            self.assertEqual(bundle["notes_for_agent"], "literal comma, } remains")

    def test_idea_sh_fails_on_mismatched_rubric_files(self) -> None:
        script = _script_path("idea.sh")
        self.assertTrue(script.exists())

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = _write_task_dir(root)
            results_dir = root / "results"
            output_dir = root / "idea_out"
            bin_dir = root / "bin"
            bin_dir.mkdir()
            fake_headless = bin_dir / "headless"
            fake_headless.write_text(
                """#!/bin/bash
set -euo pipefail
cat > "${IDEA_BUNDLE_PATH}" <<'JSON'
{"idea_id":"idea_0001","title":"bad","hypothesis":"bad","implementation_plan":["x"],"target_rubrics":[{"id":"score_threshold","description":"score","type":"metric"}],"notes_for_agent":""}
JSON
cat > "${TARGET_RUBRICS_PATH}" <<'JSON'
[{"id":"different_id","description":"score","type":"metric"}]
JSON
""",
                encoding="utf-8",
            )
            fake_headless.chmod(0o755)

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            env["HEADLESS_BIN"] = str(fake_headless)
            env["WRAPPER_PYTHON"] = sys.executable

            cmd = [
                "bash",
                str(script),
                "--agent",
                "codex",
                "--task-dir",
                str(task_dir),
                "--results-dir",
                str(results_dir),
                "--cycle-index",
                "1",
                "--output-dir",
                str(output_dir),
            ]
            run = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=20,
                env=env,
            )
            output = (run.stdout or "") + (run.stderr or "")
            self.assertNotEqual(run.returncode, 0, msg=output)
            self.assertIn("does not match", output)

    def test_idea_sh_autofills_missing_idea_memory_entry(self) -> None:
        script = _script_path("idea.sh")
        self.assertTrue(script.exists())

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = _write_task_dir(root)
            results_dir = root / "results"
            output_dir = root / "idea_out"
            bin_dir = root / "bin"
            bin_dir.mkdir()

            fake_codex = bin_dir / "headless"
            fake_codex.write_text(
                """#!/bin/bash
set -euo pipefail
cat > "${IDEA_BUNDLE_PATH}" <<'JSON'
{"idea_id":"idea_autofill_0001","title":"autofill","hypothesis":"memory fallback","implementation_plan":["x"],"target_rubrics":[{"id":"score_threshold","description":"score","type":"metric"}],"notes_for_agent":""}
JSON
cat > "${TARGET_RUBRICS_PATH}" <<'JSON'
[{"id":"score_threshold","description":"score","type":"metric"}]
JSON
echo "fake-agent codex generated idea artifacts without memory log"
""",
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)

            results_dir.mkdir(parents=True)
            existing_memory_entry = {"step": "seed", "summary": "unterminated record"}
            (results_dir / "memory.jsonl").write_text(
                json.dumps(existing_memory_entry),
                encoding="utf-8",
            )

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            env["HEADLESS_BIN"] = str(fake_codex)
            env["WRAPPER_PYTHON"] = sys.executable

            cmd = [
                "bash",
                str(script),
                "--agent",
                "codex",
                "--task-dir",
                str(task_dir),
                "--results-dir",
                str(results_dir),
                "--cycle-index",
                "1",
                "--output-dir",
                str(output_dir),
            ]
            run = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=20,
                env=env,
            )
            output = (run.stdout or "") + (run.stderr or "")
            self.assertEqual(run.returncode, 0, msg=output)
            self.assertIn("appended fallback idea-step memory entry", output)

            memory_path = results_dir / "memory.jsonl"
            self.assertTrue(memory_path.is_file())
            entries = [
                json.loads(line)
                for line in memory_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(entries[0], existing_memory_entry)
            fallback_entries = [
                entry
                for entry in entries
                if entry.get("step") == "idea" and entry.get("idea_id") == "idea_autofill_0001"
            ]
            self.assertEqual(
                len(fallback_entries),
                1,
                msg=f"missing fallback idea memory entry in {memory_path}",
            )
            global_md = (results_dir / "memory.md").read_text(encoding="utf-8")
            self.assertEqual(global_md.count("idea_id: idea_autofill_0001"), 1)




if __name__ == "__main__":
    unittest.main()
