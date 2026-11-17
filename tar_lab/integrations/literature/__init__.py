"""Open literature sources (OpenAlex, Semantic Scholar, arXiv): Undermind's fallback."""

from .sources import SOURCES, Literature, Paper, SourceError

__all__ = ["SOURCES", "Literature", "Paper", "SourceError"]
