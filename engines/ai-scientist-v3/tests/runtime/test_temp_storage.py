"""Private scratch storage, container temp leaves, bounded log sinks, and temp artifact cleanup."""
from __future__ import annotations

import contextlib
import errno
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.investigation.evaluation_artifact_cleanup as cleanup_module
from src.investigation import temp_storage
from src.investigation.evaluation_artifact_cleanup import cleanup_unmanaged_evaluation_outputs
from src.scripts.lib import (bounded_log)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "src/scripts"
ENV_NAMES = (
    "AGENTIC_TEMP_ROOT", "AGENTIC_TEMP_DIR", "AGENTIC_CACHE_ROOT",
    "TMPDIR", "TMP", "TEMP",
)
_STATEFUL_DOCKER = '''#!/usr/bin/env python3
import os, pathlib, sys
state = pathlib.Path(os.environ["DOCKER_STATE"])
if os.environ.get("DOCKER_CALLS"):
    with open(os.environ["DOCKER_CALLS"], "a") as output:
        output.write(sys.argv[1] + "\\n")
if sys.argv[1] == "inspect" and state.exists():
    print(state.read_text())
elif sys.argv[1] == "rm" and os.environ.get("DOCKER_STOP_FAIL") != "1":
    state.unlink(missing_ok=True)
elif sys.argv[1] == "inspect":
    print("Error: No such container: session", file=sys.stderr)
    sys.exit(1)
else:
    sys.exit(1)
'''


# --- helpers --------------------------------------------------------------------------------


def _script(name: str) -> str:
    return (SCRIPTS / name).read_text()


def _bash_function(source: str, name: str) -> str:
    """The complete ``name() { ... }`` definition extracted from a shell script."""
    start = source.index(f"{name}() {{")
    return source[start:source.index("\n}\n", start) + 3]


def _bash(command: str, *, env=None, args=(), timeout=10, check=False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", command, *args], env=env, capture_output=True, text=True, timeout=timeout, check=check
    )








def _private_dir(path: Path) -> Path:
    path.mkdir(mode=0o700)
    return path


def local_spools(tmp_path, monkeypatch):
    original = tempfile.mkdtemp
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **kw: original(prefix="spool-", dir=tmp_path))


def input_stream(monkeypatch, data):
    stream = io.BytesIO(data)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=stream))
    return stream


def _notices(capsys) -> list[dict]:
    return [json.loads(line) for line in capsys.readouterr().err.splitlines()]




# --- temp_storage (isolated environment) ----------------------------------------------------


class TestTempStorage:
    @pytest.fixture(autouse=True)
    def _isolated_environment(self, monkeypatch):
        monkeypatch.setattr(os, "environ", os.environ.copy())
        monkeypatch.setattr(tempfile, "tempdir", tempfile.tempdir)
        for name in ENV_NAMES:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(temp_storage, "LOCAL_SSD", Path("/nonexistent-local-ssd"))

    @staticmethod
    @contextlib.contextmanager
    def _fresh(monkeypatch, root: Path | None = None):
        """A section with its own environment copy, optionally pointing AGENTIC_TEMP_ROOT at ``root``."""
        with monkeypatch.context() as m:
            m.setattr(os, "environ", os.environ.copy())
            m.setattr(tempfile, "tempdir", tempfile.tempdir)
            for name in ENV_NAMES:
                m.delenv(name, raising=False)
            if root is not None:
                m.setenv("AGENTIC_TEMP_ROOT", str(root))
            yield m


    def test_base_selection_precedence_and_preview(self, tmp_path, monkeypatch):
        # preview never creates namespace
        preview = _private_dir(tmp_path / "preview")
        with self._fresh(monkeypatch, preview):
            assert str(preview) in temp_storage.preview_temp_storage()
            assert list(preview.iterdir()) == []
            assert "AGENTIC_TEMP_DIR" not in os.environ
        # explicit TMPDIR selected
        with self._fresh(monkeypatch) as m:
            m.setenv("TMPDIR", str(tmp_path))
            assert temp_storage.initialize_temp_storage().base == tmp_path
        # default system temp used without SSD
        with self._fresh(monkeypatch) as m:
            m.setattr(temp_storage, "_system_temporary_base", lambda: tmp_path)
            assert temp_storage.initialize_temp_storage().base == tmp_path
        # available local SSD selected
        with self._fresh(monkeypatch) as m:
            m.setattr(temp_storage, "LOCAL_SSD", tmp_path)
            m.setattr(temp_storage, "_is_scratch_mount", lambda path: path == tmp_path)
            assert temp_storage.initialize_temp_storage().base == tmp_path
        # explicit root overrides TMPDIR
        with self._fresh(monkeypatch, tmp_path) as m:
            m.setenv("TMPDIR", "/does-not-exist")
            assert temp_storage.initialize_temp_storage().base == tmp_path
        # sticky shared base supported
        sticky = _private_dir(tmp_path / "sticky")
        sticky.chmod(0o1777)
        with self._fresh(monkeypatch, sticky):
            assert temp_storage.initialize_temp_storage().base == sticky

    def test_invalid_explicit_roots_rejected(self, tmp_path, monkeypatch):
        for value in ("relative", "", "/missing-agentic-test-root", "/tmp\ninjected"):
            with self._fresh(monkeypatch) as m:
                m.setenv("AGENTIC_TEMP_ROOT", value)
                with pytest.raises((OSError, ValueError)):
                    temp_storage.initialize_temp_storage()
        # symlink base rejected
        link = tmp_path / "link"
        link.symlink_to(tmp_path, target_is_directory=True)
        with self._fresh(monkeypatch, link), pytest.raises(ValueError, match="symlink"):
            temp_storage.initialize_temp_storage()
        # public existing namespace rejected
        public = _private_dir(tmp_path / "public")
        (public / f"agentic-scientist-{os.getuid()}").mkdir(mode=0o755)
        with self._fresh(monkeypatch, public), pytest.raises(ValueError, match="private"):
            temp_storage.initialize_temp_storage()
        # unwritable explicit setting rejected
        with self._fresh(monkeypatch, tmp_path) as m:
            m.setattr(os, "access", lambda *args: False)
            with pytest.raises(ValueError, match="writable"):
                temp_storage.initialize_temp_storage()
        # foreign namespace owner rejected
        real_fstat = os.fstat

        def foreign_owner(descriptor):
            metadata = real_fstat(descriptor)
            return SimpleNamespace(st_uid=metadata.st_uid + 1, st_mode=metadata.st_mode)

        with self._fresh(monkeypatch, tmp_path) as m:
            m.setattr(os, "fstat", foreign_owner)
            with pytest.raises(ValueError, match="owned"):
                temp_storage.initialize_temp_storage()
        # non-sticky shared ancestor rejected
        parent = tmp_path / "shared"
        parent.mkdir(mode=0o777)
        parent.chmod(0o777)
        base = _private_dir(parent / "scratch")
        with self._fresh(monkeypatch, base), pytest.raises(ValueError, match="unsafe writable"):
            temp_storage.initialize_temp_storage()

    def test_inherited_state_mismatches_rejected(self, tmp_path, monkeypatch):
        # inherited unrelated directory rejected
        with self._fresh(monkeypatch, tmp_path) as m:
            temp_storage.initialize_temp_storage()
            m.setenv("AGENTIC_TEMP_DIR", str(tmp_path))
            with pytest.raises(ValueError, match="inherited"):
                temp_storage.initialize_temp_storage()
        # partial inherited state rejected
        with self._fresh(monkeypatch) as m:
            m.setenv("AGENTIC_TEMP_DIR", str(tmp_path))
            with pytest.raises(ValueError, match="inherited"):
                temp_storage.initialize_temp_storage()
        # inherited directory permission change rejected
        with self._fresh(monkeypatch, tmp_path):
            temp_storage.initialize_temp_storage().temporary.chmod(0o755)
            with pytest.raises(ValueError, match="private"):
                temp_storage.initialize_temp_storage()


    @pytest.mark.parametrize("root", ["missing", "relative"], ids=["init-failure", "unsafe-configuration"])
    def test_logger_storage_failure_still_drains_and_saves_shared_log(self, tmp_path, monkeypatch, root):
        monkeypatch.setenv("AGENTIC_TEMP_ROOT", str(tmp_path / "missing") if root == "missing" else root)
        stream = input_stream(monkeypatch, b"output" * 50000)
        target = tmp_path / "agent.log"
        assert bounded_log.retain_bounded_stream(target, 2048) == 4
        assert stream.tell() == 300000
        if root == "missing":
            assert 0 < target.stat().st_size <= 2048

    def test_cli_returns_three_paths_or_rejects_invalid_base_without_path_output(self, tmp_path, monkeypatch):
        # CLI returns three paths and storage diagnostic
        monkeypatch.setenv("AGENTIC_TEMP_ROOT", str(tmp_path))
        result = subprocess.run(
            [sys.executable, str(Path(temp_storage.__file__))],
            capture_output=True, text=True, check=True, env=os.environ,
        )
        lines = result.stdout.splitlines()
        assert len(lines) == 3
        assert lines[0] == str(tmp_path)
        assert all(Path(line).is_dir() for line in lines)
        assert "free_bytes=" in result.stderr
        # CLI rejects invalid base without path output
        monkeypatch.setenv("AGENTIC_TEMP_ROOT", "/does-not-exist")
        result = subprocess.run(
            [sys.executable, str(Path(temp_storage.__file__))], capture_output=True, text=True, env=os.environ
        )
        assert result.returncode == 1
        assert result.stdout == ""
        assert "initialization failed" in result.stderr


# --- container_temp (python) ----------------------------------------------------------------




















# --- shell cleanup of relocated private temp artifacts --------------------------------------

_TASK_CLEANUP_CASES = (
    ("DESIGN_PRIVATE_ROOT", "agentic-task-design-private-", "directory"),
    ("HARNESS_VALIDATION_ROOT", "agentic-task-validation-", "directory"),
    ("HARNESS_RECOVERY_ROOT", "agentic-task-recovery-", "directory"),
    ("DRAFT_CONTAINER_ENV_FILE", "agentic-task-provider-env-", "file"),
)




_RELOCATED_CLEANUP_CASES = [
    ("run.sh", "cleanup_search_only_task_source", "SEARCH_ONLY_TASK_SOURCE_DIR", "agentic-task-search-test", "directory"),
    ("run.sh", "cleanup_contracted_container_env", "CONTRACTED_CONTAINER_ENV_FILE", "agentic-run-provider-env-test", "file"),
    ("idea.sh", "cleanup_idea_security_paths", "CONTRACTED_CONTAINER_ENV_FILE", "agentic-idea-provider-env-test", "file"),
]






# --- bounded_log: a lost log sink must not break the producer -------------------------------


@pytest.mark.parametrize("operation", ["open", "write", "flush", "close"])
@pytest.mark.parametrize("number", [errno.ESTALE, errno.EDQUOT, errno.ENOSPC])
def test_failed_shared_sink_retains_bounded_local_log_and_drains(tmp_path, monkeypatch, capsys, operation, number):
    local_spools(tmp_path, monkeypatch)
    shared = tmp_path / "shared.log"
    original = getattr(bounded_log, "_open_log", None)

    class BrokenStream:
        def __init__(self, stream):
            self.stream = stream

        def __getattr__(self, name):
            if name == operation:
                def fail(*args, **kwargs):
                    if name == "close":
                        self.stream.close()
                    raise OSError(number, "private-path-and-token")
                return fail
            return getattr(self.stream, name)

    def open_log(path):
        if path == shared:
            if operation == "open":
                raise OSError(number, "private-path-and-token")
            return BrokenStream(original(path))
        return original(path)

    monkeypatch.setattr(bounded_log, "_open_log", open_log, raising=False)
    data = b"prefix\n" + b"x" * (2 * 1024 * 1024) + b"\ntail\n"
    stream = input_stream(monkeypatch, data)
    assert bounded_log.retain_bounded_stream(shared, 2048) == 4
    assert stream.tell() == len(data)
    logs = list(tmp_path.glob("spool-*/*.log"))
    assert len(logs) == 1
    retained = logs[0].read_bytes()
    assert len(retained) <= 2048
    assert retained.startswith(b"prefix\n") and retained.endswith(b"\ntail\n")
    assert stat.S_IMODE(logs[0].stat().st_mode) == 0o600
    assert stat.S_IMODE(logs[0].parent.stat().st_mode) == 0o700
    diagnostic = capsys.readouterr().err
    assert "infrastructure_failure" in diagnostic
    assert "private-path-and-token" not in diagnostic
    assert len(diagnostic) < 2048
    notices = [json.loads(line) for line in diagnostic.splitlines()]
    assert notices[-1]["local_spool_status"] == "retained"


def test_successful_shared_sink_removes_own_spool_and_tolerates_symlink_or_passthrough_failure(
    tmp_path, monkeypatch
):
    # success removes only own local spool
    with monkeypatch.context() as m:
        root = tmp_path / "success"
        root.mkdir()
        local_spools(root, m)
        unrelated = root / "keep.log"
        unrelated.write_text("keep")
        shared = root / "log"
        input_stream(m, b"complete")
        assert bounded_log.retain_bounded_stream(shared, 2048) == 0
        assert shared.read_bytes() == b"complete"
        assert not list(root.glob("spool-*"))
        assert unrelated.read_text() == "keep"
    # shared symlink is not overwritten
    with monkeypatch.context() as m:
        root = tmp_path / "symlink"
        root.mkdir()
        local_spools(root, m)
        target = root / "target"
        target.write_text("keep")
        link = root / "log"
        link.symlink_to(target)
        stream = input_stream(m, b"complete")
        assert bounded_log.retain_bounded_stream(link, 2048) == 4
        assert target.read_text() == "keep" and stream.tell() == 8
    # passthrough failure still drains to bounded logs
    with monkeypatch.context() as m:
        root = tmp_path / "passthrough"
        root.mkdir()
        local_spools(root, m)

        class BrokenOutput:
            def write(self, data):
                raise BrokenPipeError(errno.EPIPE, "closed")

        m.setattr(sys, "stdout", SimpleNamespace(buffer=BrokenOutput()))
        data = b"prefix" + b"x" * (2 * 1024 * 1024) + b"tail"
        stream = input_stream(m, data)
        shared = root / "log"
        assert bounded_log.retain_bounded_stream(shared, 2048, passthrough=True) == 4
        assert stream.tell() == len(data)
        assert shared.read_bytes().endswith(b"tail")
        assert shared.stat().st_size <= 2048


def test_local_spool_failures_are_reported_without_private_details(tmp_path, monkeypatch, capsys):
    # both log sinks failing still drains input
    with monkeypatch.context() as m:
        root = tmp_path / "both"
        root.mkdir()
        local_spools(root, m)

        def fail(path):
            raise OSError(errno.ENOSPC, "private")

        m.setattr(bounded_log, "_open_log", fail, raising=False)
        data = b"x" * (2 * 1024 * 1024)
        stream = input_stream(m, data)
        assert bounded_log.retain_bounded_stream(root / "log", 2048) == 4
        assert stream.tell() == len(data)
        diagnostic = capsys.readouterr().err
        assert "private" not in diagnostic
        notices = [json.loads(line) for line in diagnostic.splitlines()]
        assert notices[-1]["local_spool_status"] == "unavailable"
        assert all("local_spool" not in notice for notice in notices)
    # fallback path reported before interrupted input
    with monkeypatch.context() as m:
        root = tmp_path / "interrupted"
        root.mkdir()
        local_spools(root, m)

        class InterruptedInput:
            def read(self, size):
                notices = _notices(capsys)
                assert notices[-1]["local_spool_status"] == "in_progress"
                assert Path(notices[-1]["local_spool"]).is_file()
                raise KeyboardInterrupt

        m.setattr(sys, "stdin", SimpleNamespace(buffer=InterruptedInput()))
        with pytest.raises(KeyboardInterrupt):
            bounded_log.retain_bounded_stream(root / "missing-parent" / "log", 2048)
    capsys.readouterr()
    # local write failure reports partial log
    with monkeypatch.context() as m:
        root = tmp_path / "partial"
        root.mkdir()
        local_spools(root, m)
        original = bounded_log._LogSink.write

        def write(sink, *args):
            if sink.name == "local":
                sink._fail("write", OSError(errno.ENOSPC, "private"))
            else:
                original(sink, *args)

        m.setattr(bounded_log._LogSink, "write", write)
        input_stream(m, b"complete")
        assert bounded_log.retain_bounded_stream(root / "log", 2048) == 4
        notices = _notices(capsys)
        assert notices[-1]["local_spool_status"] == "partial"
        assert Path(notices[-1]["local_spool"]).is_file()


def test_subprocess_producer_finishes_when_shared_log_cannot_open(tmp_path):
    producer = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x' * 8388608 + b'tail')"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    command = f'''
import tempfile
from pathlib import Path
from src.scripts.lib import bounded_log
original = tempfile.mkdtemp
tempfile.mkdtemp = lambda **kw: original(prefix="spool-", dir={str(tmp_path)!r})
raise SystemExit(bounded_log.retain_bounded_stream(Path({str(tmp_path / 'missing/log')!r}), 2048))
'''
    logger = subprocess.Popen(
        [sys.executable, "-c", command], stdin=producer.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    producer.stdout.close()
    try:
        _, diagnostic = logger.communicate(timeout=10)
        assert producer.wait(timeout=5) == 0, producer.stderr.read()
        assert logger.returncode == 4, diagnostic
        log, = tmp_path.glob("spool-*/*.log")
        assert log.read_bytes().endswith(b"tail")
        assert log.stat().st_size <= 2048
    finally:
        for process in (producer, logger):
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        producer.stderr.close()






# --- unmanaged evaluation artifact cleanup --------------------------------------------------


def _make_evaluator_runs(root: Path) -> tuple[Path, Path]:
    runs_dir = root / "runs"
    run_dir = runs_dir / "run_000_case_seed_0"
    run_dir.mkdir(parents=True)
    (run_dir / "config.json").write_text("{}", encoding="utf-8")
    (run_dir / "run_summary.json").write_text('{"outcome":"ok"}', encoding="utf-8")
    (run_dir / "program_stdout.txt").write_text("output", encoding="utf-8")
    return runs_dir, run_dir




def test_cleanup_preserves_managed_symlinked_undermarked_and_swapped_runs(tmp_path, monkeypatch):
    # cleanup preserves managed and unrecognized runs
    experiment_dir = tmp_path / "managed"
    managed_runs, _ = _make_evaluator_runs(experiment_dir / ".evaluation_outputs" / "program_1" / "attempt_a")
    unrelated_runs, _ = _make_evaluator_runs(experiment_dir / "analysis")

    report = cleanup_unmanaged_evaluation_outputs(experiment_dir)

    assert report.removed_roots == ()
    assert report.removed_bytes == 0
    assert managed_runs.is_dir()
    assert unrelated_runs.is_dir()

    # cleanup does not follow symlinked run directories
    experiment_dir = tmp_path / "symlinked"
    experiment_dir.mkdir()
    external_runs, _ = _make_evaluator_runs(tmp_path / "external")
    symlinked_runs = experiment_dir / ".dev_eval_program_1" / "runs"
    symlinked_runs.parent.mkdir()
    symlinked_runs.symlink_to(external_runs, target_is_directory=True)

    report = cleanup_unmanaged_evaluation_outputs(experiment_dir)

    assert report.removed_roots == ()
    assert symlinked_runs.is_symlink()
    assert external_runs.is_dir()

    # cleanup requires multiple evaluator markers
    experiment_dir = tmp_path / "undermarked"
    runs_dir = experiment_dir / "program_1" / "runs"
    run_dir = runs_dir / "run_000"
    run_dir.mkdir(parents=True)
    (run_dir / "config.json").write_text("{}", encoding="utf-8")

    report = cleanup_unmanaged_evaluation_outputs(experiment_dir)

    assert report.removed_roots == ()
    assert runs_dir.is_dir()

    # cleanup preserves directory swapped after validation
    experiment_dir = tmp_path / "swapped"
    runs_dir, evaluator_run = _make_evaluator_runs(experiment_dir / ".dev_eval_program_1")
    saved_run = runs_dir / "saved_verified_run"
    real_rename = os.rename
    swapped = False

    def swap_before_rename(src, dst, *, src_dir_fd=None, dst_dir_fd=None):
        nonlocal swapped
        if not swapped and src == evaluator_run.name:
            swapped = True
            real_rename(src, saved_run.name, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)
            replacement = runs_dir / evaluator_run.name
            replacement.mkdir()
            (replacement / "config.json").write_text("replacement", encoding="utf-8")
            (replacement / "run_summary.json").write_text("replacement", encoding="utf-8")
        return real_rename(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    monkeypatch.setattr(cleanup_module.os, "rename", swap_before_rename)

    report = cleanup_unmanaged_evaluation_outputs(experiment_dir)

    assert report.removed_roots == ()
    assert len(report.failures) == 1
    assert (evaluator_run / "config.json").read_text(encoding="utf-8") == "replacement"
    assert saved_run.is_dir()
