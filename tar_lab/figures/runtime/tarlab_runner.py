"""Run one figure/table script under a file-access guard (used by ``tarlab figures build``).

Invoked as ``python tarlab_runner.py <script>`` by the figures environment's interpreter,
with the artifact spec in ``TARLAB_FIG_ARTIFACT``. A Python audit hook enforces provenance:
inside the workspace the script may read only its declared inputs and the ``figures/``
scripts, and write only to its output directory; it may not start processes. Reads and
writes outside the workspace (libraries, fonts, the matplotlib cache) are allowed.
"""

from __future__ import annotations

import json
import os
import runpy
import sys
from typing import Any

_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
_PROCESS_EVENTS = ("subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn",
                   "os.fork", "os.forkpty", "pty.spawn")


def _under(path: str, roots: list[str]) -> bool:
    return any(path == r or path.startswith(r.rstrip(os.sep) + os.sep) for r in roots)


def install_guard(spec: dict[str, Any]) -> None:
    real = os.path.realpath
    workspace = real(spec["workspace"])
    out = real(spec["out_dir"])
    readable = [real(os.path.join(workspace, p)) for p in spec["inputs"]]
    readable += [real(os.path.join(workspace, p)) for p in spec.get("code_dirs", [])]
    readable.append(out)

    def hook(event: str, args: tuple[Any, ...]) -> None:
        if event == "open":
            target, mode, flags = args
            if isinstance(target, int) or target is None:
                return
            p = real(os.fsdecode(target))
            if not _under(p, [workspace]):
                return
            writing = (any(c in mode for c in "wax+") if isinstance(mode, str)
                       else bool((flags or 0) & _WRITE_FLAGS))
            rel = os.path.relpath(p, workspace)
            if writing and not _under(p, [out]):
                raise PermissionError(f"figure scripts may only write to their output "
                                      f"directory, not {rel}")
            if not writing and not _under(p, readable):
                raise PermissionError(f"{rel} is not a declared input of artifact "
                                      f"{spec['id']!r}; add it to `inputs` in "
                                      f"figures/spec.toml")
        elif event in _PROCESS_EVENTS:
            raise PermissionError(f"figure scripts may not start processes ({event})")

    sys.addaudithook(hook)


def main() -> None:
    script = os.path.realpath(sys.argv[1])
    spec = json.loads(os.environ["TARLAB_FIG_ARTIFACT"])
    sys.dont_write_bytecode = True
    sys.path.insert(0, os.path.dirname(script))
    install_guard(spec)
    sys.argv = [script]
    runpy.run_path(script, run_name="__main__")


if __name__ == "__main__":
    main()
