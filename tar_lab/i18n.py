"""Interface language (Chinese or English) for text TAR-Lab itself shows the user.

The interface uses the process-wide language (``--lang``, ``TARLAB_LANG``, or the project's
chat language); messages the platform sends on a project's behalf use that project's chat
language through :func:`pick`.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping

LANGUAGES = ("zh", "en")
_current = ["zh"]


def normalize(lang: str | None) -> str | None:
    if not lang:
        return None
    lang = lang.strip().lower()
    return "en" if lang.startswith("en") else "zh" if lang.startswith("zh") else None


def set_language(lang: str | None) -> str:
    _current[0] = normalize(lang) or "zh"
    return _current[0]


def language() -> str:
    return _current[0]


def choose(explicit: str | None = None, project: str | None = None) -> str:
    """``--lang`` first, then ``TARLAB_LANG``, then the project's chat language, else zh."""
    return (normalize(explicit) or normalize(os.environ.get("TARLAB_LANG"))
            or normalize(project) or "zh")


def tr(zh: str, en: str) -> str:
    return en if _current[0] == "en" else zh


def pick(lang: str | None, zh: str, en: str) -> str:
    return en if normalize(lang) == "en" else zh


class Terms(Mapping[str, str]):
    """A label table that answers in the current language."""

    def __init__(self, table: Mapping[str, tuple[str, str]]) -> None:
        self._table = dict(table)

    def __getitem__(self, key: str) -> str:
        zh, en = self._table[key]
        return tr(zh, en)

    def __iter__(self) -> Iterator[str]:
        return iter(self._table)

    def __len__(self) -> int:
        return len(self._table)
