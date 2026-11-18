"""AI-Scientist-v3 engine adapter: write task directories, run the idea search, parse runs.

The platform talks to the engine only through its CLI and run-directory files;
it never imports engine code.
"""

from .engine import AIScientistEngine, RunRequest, format_override
from .executor import AIScientistExecutor
from .results import RunSummary, load_run
from .task import task_problems

__all__ = [
    "AIScientistEngine",
    "AIScientistExecutor",
    "RunRequest",
    "RunSummary",
    "format_override",
    "load_run",
    "task_problems",
]
