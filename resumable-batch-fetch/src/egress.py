"""Shared egress rules: ignore system proxy env, use only cfg-specified ports."""

from __future__ import annotations

import os

PROXY_ENV_KEYS: tuple[str, ...] = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)

# requests: explicit direct (do not use None — Session may still honor trust_env).
REQUESTS_NO_PROXY: dict[str, None] = {"http": None, "https": None}


def clear_proxy_env() -> list[str]:
    """Remove proxy env vars from this process; return ``KEY=value`` lines that were set."""
    removed: list[str] = []
    for key in PROXY_ENV_KEYS:
        if key in os.environ:
            removed.append(f"{key}={os.environ[key]!r}")
            del os.environ[key]
    return removed


def requests_proxies_for_port(portnum: int) -> dict[str, str | None]:
    """``-1`` = direct; otherwise HTTP proxy on 127.0.0.1:<portnum>."""
    if portnum == -1:
        return dict(REQUESTS_NO_PROXY)
    base = f"http://127.0.0.1:{portnum}"
    return {"http": base, "https": base}
