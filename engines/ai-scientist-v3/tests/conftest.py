"""Keep in-process entrypoint scratch settings local to each test."""
import os
from pathlib import Path
import stat
import tempfile
from collections.abc import Iterator

import pytest

from src.investigation.runtime import shutdown_event_logging


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    """Give ``tmp_path`` a short root so AF_UNIX socket paths fit in 107 bytes.

    pytest's default root is ``$TMPDIR/pytest-of-<user>/pytest-N`` and xdist adds
    ``popen-gwN``; with a long user name the broker sockets tests bind under
    ``tmp_path`` exceed the kernel limit. An explicit ``--basetemp`` still wins,
    and xdist workers keep the directory the controller hands them.
    """
    if config.option.basetemp is not None or hasattr(config, "workerinput"):
        return
    from _pytest.pathlib import make_numbered_dir_with_cleanup

    root = Path(tempfile.gettempdir()) / f"agsci-{os.getuid()}"
    try:
        root.mkdir(mode=0o700, exist_ok=True)
        metadata = root.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            return
        run = make_numbered_dir_with_cleanup(
            root=root, prefix="t", keep=3, lock_timeout=172800, mode=0o700
        )
    except OSError:
        return
    config.option.basetemp = run / "pt"


@pytest.fixture(autouse=True)
def restore_runtime_storage_environment() -> Iterator[None]:
    names = (
        "AGENTIC_TEMP_ROOT", "AGENTIC_TEMP_DIR", "AGENTIC_CACHE_ROOT",
        "TMPDIR", "TMP", "TEMP",
    )
    previous = {name: os.environ.get(name) for name in names}
    previous_tempdir = tempfile.tempdir
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        tempfile.tempdir = previous_tempdir


@pytest.fixture(autouse=True)
def release_process_event_logger() -> Iterator[None]:
    """Drop a process-wide event logger left behind by tests that drive ``_main`` directly.

    While the logger is configured, ``log_event`` emits to it instead of printing, so a leaked
    logger silently changes the behaviour of later investigation CLI logging tests.
    """
    yield
    shutdown_event_logging()
