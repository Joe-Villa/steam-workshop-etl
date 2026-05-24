"""
Multi-port, single-threaded HTTP crawler.

``Port`` owns per-exit scheduling (when that proxy may be used again).
``Crawler`` owns HTTP, retries, 429 / Retry-After handling, and exponential backoff
for transport errors. Callers only use ``Crawler.crawl(url)`` and inspect
``CrawlResult``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)


@dataclass(frozen=True)
class CrawlResult:
    """Either a successful body (``ok=True``) or a failure description (``ok=False``)."""

    ok: bool
    text: str | None = None
    error: str | None = None
    status_code: int | None = None


class Port:
    """
    One logical egress: ``portnum == -1`` means direct (no HTTP proxy); otherwise
    ``http://127.0.0.1:<portnum>`` for both http and https schemes.
    """

    # Steam++/Watt local HTTP proxy: HTTPS is MITM with a cert the system CA does not trust.
    # Same port as ``test/checkport.py``; use ``verify=False`` for that egress (see ``tls_verify``).
    STEAMPP_LOCAL_PROXY_PORT_SKIP_TLS_VERIFY: int = 26561

    # Minimum gap after a successful request before this slot is reused (seconds).
    success_gap_s: float = 4.0

    def __init__(self, portnum: int) -> None:
        if portnum != -1 and not (1 <= portnum <= 65535):
            raise ValueError("portnum must be -1 or in 1..65535")
        self.portnum = portnum
        self._next_ok_monotonic = time.monotonic()

    def proxies(self) -> dict[str, str] | None:
        if self.portnum == -1:
            return None
        base = f"http://127.0.0.1:{self.portnum}"
        return {"http": base, "https": base}

    def tls_verify(self, crawler_default: bool | str) -> bool | str:
        """
        Per-egress TLS verification for ``requests``.

        Port ``26561`` (Steam++ local proxy) matches ``checkport.py``: skip verification
        because the proxy terminates TLS with its own certificate.
        """
        if self.portnum == self.STEAMPP_LOCAL_PROXY_PORT_SKIP_TLS_VERIFY:
            return False
        return crawler_default

    def seconds_until_ready(self) -> float:
        return max(0.0, self._next_ok_monotonic - time.monotonic())

    def defer(self, seconds: float) -> None:
        """Block this egress for at least ``seconds`` from now."""
        if seconds <= 0:
            return
        self._next_ok_monotonic = max(self._next_ok_monotonic, time.monotonic() + seconds)

    def mark_success_cooldown(self) -> None:
        self.defer(self.success_gap_s)

    def next_ready_monotonic(self) -> float:
        return self._next_ok_monotonic


class Crawler:
    """
    Single-thread entry point: ``crawl(url)`` picks a ready port, waits if needed,
    performs GET with retries, returns ``CrawlResult``.
    """

    _HTTP_USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    _HTTP_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    _HTTP_ACCEPT_LANGUAGE = "en-US,en;q=0.9,zh-CN;q=0.8"

    def __init__(
        self,
        ports: list[Port],
        *,
        timeout_s: float = 25.0,
        max_rounds: int = 24,
        verify: bool | str = False,
        transport_backoff_base_s: float = 2.0,
        transport_backoff_cap_s: float = 120.0,
        cooldown_steps_s: tuple[float, ...] = (60.0, 120.0, 240.0),
    ) -> None:
        if not ports:
            raise ValueError("ports must be non-empty")
        self._ports = list(ports)
        self._timeout_s = float(timeout_s)
        self._max_rounds = int(max_rounds)
        self._verify: bool | str = verify
        self._transport_backoff_base_s = float(transport_backoff_base_s)
        self._transport_backoff_cap_s = float(transport_backoff_cap_s)
        self._cooldown_steps_s = cooldown_steps_s
        self._429_hits = 0
        self._transport_fail_streak = 0
        self._session = requests.Session()
        self._headers: dict[str, str] = {
            "User-Agent": self._HTTP_USER_AGENT,
            "Accept": self._HTTP_ACCEPT,
            "Accept-Language": self._HTTP_ACCEPT_LANGUAGE,
        }

    @classmethod
    def from_port_numbers(
        cls,
        portnums: list[int],
        **kwargs: Any,
    ) -> Crawler:
        """Build a ``Crawler`` from raw port integers (same convention as cfg ``Ports``)."""
        return cls([Port(n) for n in portnums], **kwargs)

    @classmethod
    def from_base_json(cls, path: str | Path | None = None, **kwargs: Any) -> Crawler:
        """Load ``Ports`` from ``cfg/base.json`` next to package layout (optional helper)."""
        import json

        cfg_path = Path(path) if path is not None else Path(__file__).resolve().parent.parent / "cfg" / "base.json"
        with cfg_path.open(encoding="utf-8") as f:
            cfg = json.load(f)
        raw = cfg.get("Ports")
        if not isinstance(raw, list) or not raw:
            raise ValueError("cfg must contain a non-empty Ports list")
        nums: list[int] = []
        for i, item in enumerate(raw):
            if isinstance(item, bool) or not isinstance(item, int):
                if isinstance(item, str) and item.lstrip("-").isdigit():
                    nums.append(int(item))
                    continue
                raise ValueError(f"Ports[{i}] must be int")
            nums.append(int(item))
        return cls.from_port_numbers(nums, **kwargs)

    def crawl(self, url: str) -> CrawlResult:
        """GET ``url`` until success (HTTP 200), or give up with ``CrawlResult.ok=False``."""
        rounds = 0
        while rounds < self._max_rounds:
            rounds += 1
            port = self._pick_port_soonest()
            self._sleep_until_ready(port)
            try:
                resp = self._session.get(
                    url,
                    headers=self._headers,
                    timeout=self._timeout_s,
                    verify=port.tls_verify(self._verify),
                    proxies=port.proxies(),
                )
            except requests.RequestException as e:
                self._transport_fail_streak += 1
                wait = self._transport_backoff_seconds(self._transport_fail_streak)
                port.defer(wait)
                continue

            self._transport_fail_streak = 0
            code = int(resp.status_code)

            if code == 429:
                self._429_hits += 1
                wait = self._retry_after_seconds(resp, self._429_hits)
                port.defer(wait)
                continue

            if code == 200:
                port.mark_success_cooldown()
                return CrawlResult(ok=True, text=resp.text, status_code=code)

            return CrawlResult(
                ok=False,
                text=None,
                error=f"HTTP {code} (non-retryable for this crawler)",
                status_code=code,
            )

        return CrawlResult(
            ok=False,
            text=None,
            error=f"gave up after {self._max_rounds} rounds (429 and/or transport errors)",
            status_code=None,
        )

    def _pick_port_soonest(self) -> Port:
        return min(self._ports, key=lambda p: p.next_ready_monotonic())

    def _sleep_until_ready(self, port: Port) -> None:
        delay = port.seconds_until_ready()
        if delay > 0:
            time.sleep(delay)

    def _transport_backoff_seconds(self, consecutive: int) -> float:
        base = max(0.5, self._transport_backoff_base_s)
        cap = max(base, self._transport_backoff_cap_s)
        exp = base * (2 ** max(0, consecutive - 1))
        return float(min(cap, max(base, exp)))

    def _exponential_backoff_seconds(self, hit_count: int) -> float:
        base = max(1.0, float(self._cooldown_steps_s[0]) if self._cooldown_steps_s else 60.0)
        cap = max(base, float(self._cooldown_steps_s[-1]) if self._cooldown_steps_s else base)
        wait_s = min(cap, base * (2 ** max(0, hit_count - 1)))
        return float(max(1.0, wait_s))

    def _retry_after_seconds(self, resp: requests.Response, hit_count: int) -> float:
        """Prefer ``Retry-After``; otherwise exponential backoff (same idea as vic3analysis fetch script)."""
        retry_after = (resp.headers.get("Retry-After") or "").strip()
        if retry_after:
            if retry_after.isdigit():
                return float(max(1, int(retry_after)))
            try:
                retry_at = parsedate_to_datetime(retry_after)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                seconds = (retry_at - datetime.now(timezone.utc)).total_seconds()
                return float(max(1.0, seconds))
            except (TypeError, ValueError, OverflowError):
                pass
        return self._exponential_backoff_seconds(hit_count)
