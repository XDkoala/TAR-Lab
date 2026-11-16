import importlib

import pytest

SUBPACKAGES = [
    "core",
    "agents",
    "orchestration",
    "tools",
    "integrations",
    "integrations.undermind",
    "integrations.ai_scientist",
    "evolution",
    "memory",
]


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_is_importable(name: str) -> None:
    assert importlib.import_module(f"tar_lab.{name}").__doc__
