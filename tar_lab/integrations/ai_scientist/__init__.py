"""AI-Scientist-v3 engine adapter: write task directories, run agsci, parse run artifacts.

The platform talks to the engine only through its CLI and run-directory files;
it never imports engine code.
"""

from .engine import PIPELINE_PHASES, AIScientistEngine, EngineRun, RunRequest, format_override
from .executor import AIScientistExecutor
from .results import PhaseResult, RunSummary, load_run
from .task import TaskSpec, write_task_dir

__all__ = [
    "PIPELINE_PHASES",
    "AIScientistEngine",
    "AIScientistExecutor",
    "EngineRun",
    "PhaseResult",
    "RunRequest",
    "RunSummary",
    "TaskSpec",
    "format_override",
    "load_run",
    "write_task_dir",
]
