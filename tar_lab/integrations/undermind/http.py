"""HTTP clients that honor the host proxy settings httpx2 cannot parse itself."""

from __future__ import annotations

import urllib.request
from typing import Any
from urllib.parse import urlparse

import httpx2

# Clash and similar tools export ``all_proxy=socks://...``; httpx2 only knows socks5(h).
_SCHEME_ALIASES = {"socks": "socks5h", "socks5": "socks5", "socks5h": "socks5h",
                   "http": "http", "https": "https"}


def proxy_for(url: str) -> str | None:
    """Pick the proxy for ``url`` from ``*_proxy`` variables, honoring ``no_proxy``."""
    parsed = urlparse(url)
    if parsed.hostname and urllib.request.proxy_bypass(parsed.hostname):
        return None
    proxies = urllib.request.getproxies()
    proxy = proxies.get(parsed.scheme) or proxies.get("all")
    if not proxy:
        return None
    if "://" not in proxy:
        proxy = f"http://{proxy}"
    scheme, rest = proxy.split("://", 1)
    normalized = _SCHEME_ALIASES.get(scheme.lower())
    if normalized is None:
        raise ValueError(f"unsupported proxy scheme {scheme!r} in proxy settings ({proxy})")
    return f"{normalized}://{rest}"


def make_http_client(url: str, **kwargs: Any) -> httpx2.AsyncClient:
    """``httpx2.AsyncClient`` for requests to ``url`` with an explicitly resolved proxy.

    Environment proxies are resolved here instead of by httpx2, which rejects
    ``socks://`` URLs while constructing the client.
    """
    return httpx2.AsyncClient(proxy=proxy_for(url), trust_env=False, **kwargs)
