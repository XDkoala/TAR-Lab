"""``tarlab literature status``: is literature search configured? Never reveals a secret."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import anyio

from .ratelimit import default_state_dir
from .sources import (
    ARXIV_MIN_INTERVAL,
    OPENALEX_KEY_NAMES,
    S2_KEY_NAMES,
    S2_MIN_INTERVAL,
    Getter,
    Literature,
    LiteratureConfig,
    literature_env_path,
)

PROBE_QUERY = "world model"
LOCK_NAMES = {"semantic_scholar": "s2", "arxiv": "arxiv"}
LABELS = {"openalex": "OpenAlex", "semantic_scholar": "Semantic Scholar", "arxiv": "arXiv"}


def mask(value: str) -> str:
    """At most the last 4 characters, and none at all for short values."""
    return f"…{value[-4:]}" if len(value) >= 12 else "…"


def _setting(cfg: LiteratureConfig, names: tuple[str, ...], secret: bool = True
             ) -> dict[str, Any]:
    for source, values in (("env", cfg.env), ("file", cfg.file)):
        for name in names:
            if value := values.get(name):
                shown = mask(value) if secret else value
                return {"set": True, "from": source, "name": name if source == "env" else None,
                        "value": shown}
    return {"set": False, "from": None, "name": None, "value": None}


def _last_request(name: str, state_dir: Path, now: float) -> float | None:
    """Seconds since the last request recorded in the limiter's lock file."""
    try:
        last = float((state_dir / f"{name}.lock").read_text().strip())
    except (OSError, ValueError):
        return None
    return max(0.0, now - last) if last > 0 else None


def collect(cfg: LiteratureConfig | None = None, now: float | None = None) -> dict[str, Any]:
    cfg = cfg or LiteratureConfig.load()
    now = time.time() if now is None else now
    path = cfg.path or literature_env_path()
    mode: str | None = None
    warning: str | None = None
    try:
        mode = f"{path.stat().st_mode & 0o777:04o}"
        if int(mode, 8) & 0o077:
            warning = f"readable by other users; run `chmod 600 {path}`"
    except OSError:
        pass
    state_dir = default_state_dir()
    intervals = {"openalex": None,
                 "semantic_scholar": cfg.interval("TARLAB_S2_MIN_INTERVAL", S2_MIN_INTERVAL),
                 "arxiv": cfg.interval("TARLAB_ARXIV_MIN_INTERVAL", ARXIV_MIN_INTERVAL)}
    return {
        "key_file": {"path": str(path), "exists": mode is not None, "mode": mode,
                     "warning": warning},
        "semantic_scholar_key": _setting(cfg, S2_KEY_NAMES),
        "openalex_key": _setting(cfg, OPENALEX_KEY_NAMES),
        "openalex_mailto": _setting(cfg, ("TARLAB_OPENALEX_MAILTO",), secret=False),
        "rate_limits": {
            name: {"min_interval_s": interval,
                   "seconds_since_last_request": (
                       _last_request(LOCK_NAMES[name], state_dir, now)
                       if name in LOCK_NAMES else None)}
            for name, interval in intervals.items()},
        "rate_state_dir": str(state_dir),
        "meaning": _meaning(cfg),
    }


def _meaning(cfg: LiteratureConfig) -> dict[str, str]:
    s2 = cfg.lookup(*S2_KEY_NAMES)[0]
    oa = cfg.lookup(*OPENALEX_KEY_NAMES)[0]
    mailto = cfg.get("TARLAB_OPENALEX_MAILTO")
    return {
        "semantic_scholar": (
            "dedicated key: 1 request/s" if s2 else
            "anonymous shared pool, expect HTTP 429 under load"),
        "openalex": (
            "API key set" if oa else
            "polite pool via mailto (no key)" if mailto else
            "anonymous, works but with the lowest limits; set a mailto or key"),
        "arxiv": "no key needed; at most one request every few seconds",
    }


def _status_code(message: str) -> int | None:
    m = re.match(r"HTTP (\d{3})", message)
    return int(m.group(1)) if m else None


async def _probe(name: str, lit: Literature, has_key: bool) -> dict[str, Any]:
    runner = {"openalex": lit.openalex_search, "semantic_scholar": lit.s2_search,
              "arxiv": lit.arxiv_search}[name]
    started = time.monotonic()
    result: dict[str, Any] = {"ok": False, "http_status": None, "error": None}
    try:
        await runner(PROBE_QUERY, 1, None, None)
        result.update(ok=True, http_status=200)
    except Exception as exc:  # noqa: BLE001 - network problems must never crash the command
        text = str(exc)
        code = _status_code(text)
        result.update(http_status=code, error=f"{type(exc).__name__}: {text}"[:200])
        if code in (401, 403) and has_key:
            result["error"] = f"HTTP {code}: the API key is invalid or revoked"
    result["latency_ms"] = round((time.monotonic() - started) * 1000)
    return result


def probe_online(cfg: LiteratureConfig | None = None, get: Getter | None = None
                 ) -> dict[str, dict[str, Any]]:
    """One 1-result request per source through the normal client path (no retries)."""
    cfg = cfg or LiteratureConfig.load()
    kwargs: dict[str, Any] = {"get": get} if get is not None else {}
    lit = Literature(retries=0, config=cfg, **kwargs)
    keys = {"openalex": bool(lit.openalex_key), "semantic_scholar": bool(lit.s2_key),
            "arxiv": False}

    async def run() -> dict[str, dict[str, Any]]:
        return {name: await _probe(name, lit, has) for name, has in keys.items()}

    return anyio.run(run)


def _fmt_age(seconds: float | None) -> str:
    if seconds is None:
        return "never"
    if seconds < 90:
        return f"{seconds:.0f}s ago"
    if seconds < 5400:
        return f"{seconds / 60:.0f}min ago"
    return f"{seconds / 3600:.1f}h ago"


def _fmt_setting(s: dict[str, Any]) -> str:
    if not s["set"]:
        return "not set"
    where = f"env {s['name']}" if s["from"] == "env" else "file"
    return f"set ({where}, {s['value']})"


def render(report: dict[str, Any]) -> str:
    kf = report["key_file"]
    lines = [f"Key file: {kf['path']} "
             + (f"(exists, mode {kf['mode']})" if kf["exists"] else "(missing)")]
    if kf["warning"]:
        lines.append(f"  WARNING: {kf['warning']}")
    lines += [f"Semantic Scholar key: {_fmt_setting(report['semantic_scholar_key'])}",
              f"OpenAlex key:         {_fmt_setting(report['openalex_key'])}",
              f"OpenAlex mailto:      {_fmt_setting(report['openalex_mailto'])}",
              f"Rate limits (state: {report['rate_state_dir']}):"]
    for name, info in report["rate_limits"].items():
        interval = info["min_interval_s"]
        lines.append(f"  {LABELS[name]}: "
                     + ("not paced" if interval is None else f"min interval {interval:g}s, "
                        f"last request {_fmt_age(info['seconds_since_last_request'])}"))
    lines.append("Meaning:")
    lines += [f"  {LABELS[n]}: {text}" for n, text in report["meaning"].items()]
    if "online" in report:
        lines.append("Online check:")
        for name, res in report["online"].items():
            detail = "OK" if res["ok"] else (
                (f"HTTP {res['http_status']} " if res["http_status"] else "")
                + f"FAILED - {res['error']}")
            lines.append(f"  {LABELS[name]}: {detail} ({res['latency_ms']} ms)")
    return "\n".join(lines)


def run(online: bool = False, as_json: bool = False, cfg: LiteratureConfig | None = None,
        get: Getter | None = None) -> tuple[str, int]:
    """The text to print and the exit code."""
    import json

    cfg = cfg or LiteratureConfig.load()
    report = collect(cfg)
    code = 0
    if online:
        report["online"] = probe_online(cfg, get)
        code = 0 if any(r["ok"] for r in report["online"].values()) else 1
    out = json.dumps(report, indent=2, ensure_ascii=False) if as_json else render(report)
    return out, code


__all__ = ["collect", "mask", "probe_online", "render", "run"]
