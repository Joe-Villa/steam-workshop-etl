"""HTTPS TLS verification and explicit local-proxy openers (ignore system proxy env)."""

from __future__ import annotations

import argparse
import os
import ssl
import urllib.request

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


def clear_proxy_env() -> list[str]:
    """Remove proxy env vars; return ``KEY=value`` lines that were set."""
    removed: list[str] = []
    for key in PROXY_ENV_KEYS:
        if key in os.environ:
            removed.append(f"{key}={os.environ[key]!r}")
            del os.environ[key]
    return removed


def ssl_context(*, verify_tls: bool) -> ssl.SSLContext:
    if verify_tls:
        return ssl.create_default_context()
    return ssl._create_unverified_context()


def add_no_tls_verify_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--no-tls-verify",
        action="store_true",
        help="Disable HTTPS certificate verification (overrides cfg; for MITM proxy).",
    )


def verify_tls_enabled(args: argparse.Namespace, cfg: dict | None = None) -> bool:
    if bool(getattr(args, "no_tls_verify", False)):
        return False
    if cfg is not None and bool(cfg.get("no_tls_verify")):
        return False
    return True


def build_https_opener(
    *, verify_tls: bool, proxy_port: int | None = None
) -> urllib.request.OpenerDirector:
    handlers: list[urllib.request.BaseHandler] = []
    if proxy_port is not None:
        proxy_url = f"http://127.0.0.1:{proxy_port}"
        handlers.append(
            urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
        )
    else:
        # Explicit empty dict: do not call getproxies() / honor environment.
        handlers.append(urllib.request.ProxyHandler({}))
    handlers.append(
        urllib.request.HTTPSHandler(context=ssl_context(verify_tls=verify_tls))
    )
    return urllib.request.build_opener(*handlers)


def open_url(
    req: urllib.request.Request,
    *,
    verify_tls: bool,
    proxy_port: int | None = None,
    timeout: float,
) -> urllib.response.addinfourl:
    opener = build_https_opener(verify_tls=verify_tls, proxy_port=proxy_port)
    return opener.open(req, timeout=timeout)
