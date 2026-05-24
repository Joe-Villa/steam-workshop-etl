#This is a python class definition, not Executable file
"""
Multi-port HTTP crawler with async I/O.

``Port`` owns per-exit scheduling (when that proxy may be used again) and a
transport-failure circuit breaker (healthy → degraded → quarantined).
``AsyncCrawler`` owns concurrent HTTP, retries, 429 / Retry-After handling, and
exponential backoff for transport errors. Callers use ``await AsyncCrawler.crawl(url)``
and inspect ``CrawlResult``.

Each request uses only the egress from cfg ``Ports`` (``-1`` = direct, else
127.0.0.1:<port>). System proxy environment variables are cleared and ignored.
"""

from __future__ import annotations

import asyncio
import ssl
import sys
import time
from dataclasses import dataclass
from urllib.parse import urlparse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Literal

import aiohttp

from egress import clear_proxy_env, requests_proxies_for_port

PortHealthState = Literal["healthy", "degraded", "quarantined"]

_GATEWAY_STATUS_CODES = frozenset({502, 503, 504})


@dataclass(frozen=True)
class EgressHealthSettings:
    """Per-port circuit breaker thresholds."""

    transport_fail_to_degraded: int = 2
    transport_fail_to_quarantine: int = 5
    quarantine_s: float = 600.0

    def __post_init__(self) -> None:
        if self.transport_fail_to_degraded < 1:
            raise ValueError("transport_fail_to_degraded must be >= 1")
        if self.transport_fail_to_quarantine < self.transport_fail_to_degraded:
            raise ValueError(
                "transport_fail_to_quarantine must be >= transport_fail_to_degraded"
            )
        if self.quarantine_s <= 0:
            raise ValueError("quarantine_s must be > 0")


@dataclass(frozen=True)
class CrawlResult:
    """Either a successful body (``ok=True``) or a failure description (``ok=False``)."""

    ok: bool
    text: str | None = None
    error: str | None = None
    status_code: int | None = None
    egress_exhausted: bool = False


class Port:
    """
    One logical egress: ``portnum == -1`` means direct (no HTTP proxy); otherwise
    ``http://127.0.0.1:<portnum>`` for both http and https schemes.

    Transport failures increment ``consecutive_transport_failures`` and may move
    the port to ``degraded`` or ``quarantined``. While quarantined, the port is
    not selected until ``quarantine_s`` elapses, then it returns as ``degraded``.
    HTTP 200 clears the failure streak and restores ``healthy``.
    """

    DEFAULT_SUCCESS_GAP_S: float = 0.1

    def __init__(
        self,
        portnum: int,
        *,
        success_gap_s: float | None = None,
        skip_tls_verify: bool = False,
        health: EgressHealthSettings | None = None,
    ) -> None:
        if portnum != -1 and not (1 <= portnum <= 65535):
            raise ValueError("portnum must be -1 or in 1..65535")
        gap = self.DEFAULT_SUCCESS_GAP_S if success_gap_s is None else float(success_gap_s)
        if gap < 0:
            raise ValueError("success_gap_s must be >= 0")
        self.portnum = portnum
        self.success_gap_s = gap
        self.skip_tls_verify = bool(skip_tls_verify)
        self._health = health if health is not None else EgressHealthSettings()
        self._next_ok_monotonic = time.monotonic()
        self._health_state: PortHealthState = "healthy"
        self._consecutive_transport_failures = 0
        self._quarantine_until_monotonic = 0.0
        self._last_transport_error: str | None = None

    @property
    def health_state(self) -> PortHealthState:
        return self._health_state

    @property
    def consecutive_transport_failures(self) -> int:
        return self._consecutive_transport_failures

    def proxy_url(self) -> str | None:
        """``None`` = direct; else ``http://127.0.0.1:<port>`` for aiohttp."""
        if self.portnum == -1:
            return None
        return f"http://127.0.0.1:{self.portnum}"

    def proxies(self) -> dict[str, str | None]:
        return requests_proxies_for_port(self.portnum)

    def tls_verify(self, crawler_default: bool | str) -> bool | str:
        """Per-egress TLS verification for ``requests`` (see cfg ``safety.26561_ignore_tsl``)."""
        if self.skip_tls_verify:
            return False
        return crawler_default

    def seconds_until_ready(self) -> float:
        return max(0.0, self._next_ok_monotonic - time.monotonic())

    def seconds_until_selectable(self) -> float:
        """Time until this port may be picked (cooldown and active quarantine)."""
        self._refresh_quarantine_if_expired()
        if self._health_state == "quarantined":
            return max(0.0, self._quarantine_until_monotonic - time.monotonic())
        return self.seconds_until_ready()

    def defer(self, seconds: float) -> None:
        """Block this egress for at least ``seconds`` from now."""
        if seconds <= 0:
            return
        self._next_ok_monotonic = max(self._next_ok_monotonic, time.monotonic() + seconds)

    def mark_success_cooldown(self) -> None:
        self.record_success()

    def next_ready_monotonic(self) -> float:
        return self._next_ok_monotonic

    def is_selectable(self) -> bool:
        """False while actively quarantined; expired quarantine becomes degraded."""
        self._refresh_quarantine_if_expired()
        return self._health_state != "quarantined"

    def record_success(self) -> None:
        """HTTP 200: clear failure streak and restore healthy."""
        self._consecutive_transport_failures = 0
        self._health_state = "healthy"
        self._last_transport_error = None
        self.defer(self.success_gap_s)

    def record_rate_limit(self, wait_s: float) -> None:
        """HTTP 429: cooldown only; do not trip the transport circuit breaker."""
        self.defer(wait_s)

    def record_transport_failure(self, wait_s: float, *, detail: str) -> str:
        """
        Transport or gateway error: defer, update breaker, return a log suffix.
        """
        self._last_transport_error = detail
        self._consecutive_transport_failures += 1
        self.defer(wait_s)
        n = self._consecutive_transport_failures
        h = self._health
        if n >= h.transport_fail_to_quarantine:
            self._health_state = "quarantined"
            self._quarantine_until_monotonic = time.monotonic() + h.quarantine_s
            return (
                f"circuit quarantined {h.quarantine_s:.0f}s "
                f"(failures={n}, retry in {wait_s:.1f}s)"
            )
        if n >= h.transport_fail_to_degraded:
            self._health_state = "degraded"
            return f"circuit degraded (failures={n}, retry in {wait_s:.1f}s)"
        return f"circuit healthy (failures={n}, retry in {wait_s:.1f}s)"

    def _refresh_quarantine_if_expired(self) -> None:
        if self._health_state != "quarantined":
            return
        if time.monotonic() >= self._quarantine_until_monotonic:
            self._health_state = "degraded"

    def health_tag(self) -> str:
        """Short label for logs, e.g. ``proxy:7895[degraded,fail=3]``."""
        base = "direct" if self.portnum == -1 else f"proxy:{self.portnum}"
        n = self._consecutive_transport_failures
        if self._health_state == "quarantined":
            left = max(0.0, self._quarantine_until_monotonic - time.monotonic())
            return f"{base}[quarantined,{left:.0f}s_left,fail={n}]"
        if self._health_state == "degraded":
            return f"{base}[degraded,fail={n}]"
        if n > 0:
            return f"{base}[healthy,fail={n}]"
        return base


class AsyncCrawler:
    """
    Async entry point: ``await crawl(url)`` picks a ready port, waits if needed,
    performs GET with retries, returns ``CrawlResult``. Safe for concurrent callers.
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
        no_egress_wait_cap_s: float = 30.0,
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
        self._no_egress_wait_cap_s = float(no_egress_wait_cap_s)
        self._429_hits = 0
        self._port_lock = asyncio.Lock()
        self._ready_rr_index = 0
        self._port_in_flight: dict[int, int] = {}
        self._session: aiohttp.ClientSession | None = None
        clear_proxy_env()
        self._headers: dict[str, str] = {
            "User-Agent": self._HTTP_USER_AGENT,
            "Accept": self._HTTP_ACCEPT,
            "Accept-Language": self._HTTP_ACCEPT_LANGUAGE,
        }

    @classmethod
    def from_port_numbers(
        cls,
        portnums: list[int],
        *,
        success_gap_s: float | None = None,
        ignore_tls_for_26561: bool = False,
        egress_health: EgressHealthSettings | None = None,
        **kwargs: Any,
    ) -> AsyncCrawler:
        """Build an ``AsyncCrawler`` from raw port integers (same convention as cfg ``Ports``)."""
        from app_config import STEAMPP_LOCAL_PROXY_PORT

        health = egress_health if egress_health is not None else EgressHealthSettings()
        ports = [
            Port(
                n,
                success_gap_s=success_gap_s,
                skip_tls_verify=ignore_tls_for_26561 and n == STEAMPP_LOCAL_PROXY_PORT,
                health=health,
            )
            for n in portnums
        ]
        return cls(ports, **kwargs)

    @classmethod
    def from_config(cls, **kwargs: Any) -> AsyncCrawler:
        """Load ``Ports``, ``safety``, and ``egress_health`` from ``cfg/config.json``."""
        from app_config import load_app_config

        cfg = load_app_config()
        health = EgressHealthSettings(
            transport_fail_to_degraded=cfg.egress_health.transport_fail_to_degraded,
            transport_fail_to_quarantine=cfg.egress_health.transport_fail_to_quarantine,
            quarantine_s=cfg.egress_health.quarantine_s,
        )
        return cls.from_port_numbers(
            cfg.crawler.ports,
            success_gap_s=cfg.crawler.success_gap_s,
            ignore_tls_for_26561=cfg.safety.ignore_tls_for_26561,
            egress_health=health,
            **kwargs,
        )

    async def aclose(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self._timeout_s)
            self._session = aiohttp.ClientSession(
                headers=self._headers,
                timeout=timeout,
                trust_env=False,
            )
        return self._session

    def _ssl_for_port(self, port: Port) -> bool | ssl.SSLContext:
        verify = port.tls_verify(self._verify)
        if verify is False:
            return False
        if isinstance(verify, str):
            ctx = ssl.create_default_context(cafile=verify)
            return ctx
        return True

    async def crawl(self, url: str) -> CrawlResult:
        """GET ``url`` until success (HTTP 200), or give up with ``CrawlResult.ok=False``."""
        label = self._url_label(url)
        rounds = 0
        session = await self._ensure_session()
        while rounds < self._max_rounds:
            rounds += 1
            port: Port | None = None
            async with self._port_lock:
                port = self._pick_port_soonest()
                if port is None:
                    wait = self._seconds_until_any_port_selectable()
                    delay = 0.0
                else:
                    wait = 0.0
                    delay = port.seconds_until_selectable()
                    pn = port.portnum
                    self._port_in_flight[pn] = self._port_in_flight.get(pn, 0) + 1
            if port is None:
                if wait <= 0:
                    result = CrawlResult(
                        ok=False,
                        text=None,
                        error="no healthy egress (all ports quarantined or unavailable)",
                        status_code=None,
                        egress_exhausted=True,
                    )
                    self._log_final(label, result, rounds=rounds)
                    return result
                wait = min(wait, self._no_egress_wait_cap_s)
                self._log_attempt(
                    label,
                    rounds,
                    "pool",
                    f"all egress cooling (429/success gap), waiting {wait:.1f}s",
                )
                await asyncio.sleep(wait)
                continue

            try:
                if delay > 0:
                    await asyncio.sleep(min(delay, self._no_egress_wait_cap_s))

                egress = port.health_tag()
                try:
                    async with session.get(
                        url,
                        proxy=port.proxy_url(),
                        ssl=self._ssl_for_port(port),
                    ) as resp:
                        code = int(resp.status)

                        if code == 429:
                            self._429_hits += 1
                            wait = self._retry_after_seconds(resp, self._429_hits)
                            async with self._port_lock:
                                port.record_rate_limit(wait)
                            self._log_attempt(
                                label,
                                rounds,
                                egress,
                                f"HTTP 429 (retry in {wait:.1f}s)",
                            )
                            continue

                        if code in _GATEWAY_STATUS_CODES:
                            wait = self._transport_backoff_seconds(
                                port.consecutive_transport_failures
                            )
                            async with self._port_lock:
                                circuit = port.record_transport_failure(
                                    wait, detail=f"HTTP {code} gateway"
                                )
                            self._log_attempt(
                                label,
                                rounds,
                                egress,
                                f"HTTP {code} (gateway); {circuit}",
                            )
                            continue

                        if code == 200:
                            body = await resp.read()
                            async with self._port_lock:
                                port.record_success()
                            text = body.decode(
                                resp.charset or "utf-8", errors="replace"
                            )
                            result = CrawlResult(ok=True, text=text, status_code=code)
                            self._log_final(
                                label,
                                result,
                                rounds=rounds,
                                egress=egress,
                                nbytes=len(body),
                            )
                            return result

                        result = CrawlResult(
                            ok=False,
                            text=None,
                            error=f"HTTP {code} (non-retryable for this crawler)",
                            status_code=code,
                        )
                        self._log_final(label, result, rounds=rounds, egress=egress)
                        return result
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    detail = self._short_detail(e)
                    wait = self._transport_backoff_seconds(
                        port.consecutive_transport_failures
                    )
                    async with self._port_lock:
                        circuit = port.record_transport_failure(wait, detail=detail)
                    self._log_attempt(
                        label,
                        rounds,
                        egress,
                        f"transport error: {detail}; {circuit}",
                    )
                    continue
            finally:
                async with self._port_lock:
                    pn = port.portnum
                    n = self._port_in_flight.get(pn, 0) - 1
                    if n <= 0:
                        self._port_in_flight.pop(pn, None)
                    else:
                        self._port_in_flight[pn] = n

        result = CrawlResult(
            ok=False,
            text=None,
            error=f"gave up after {self._max_rounds} rounds (429 and/or transport errors)",
            status_code=None,
        )
        self._log_final(label, result, rounds=rounds)
        return result

    @staticmethod
    def _short_detail(exc: BaseException, *, max_len: int = 140) -> str:
        text = " ".join(str(exc).split())
        if len(text) <= max_len:
            return text
        return text[: max_len - 3] + "..."

    @staticmethod
    def _url_label(url: str, *, max_len: int = 96) -> str:
        parsed = urlparse(url)
        host = parsed.netloc or "?"
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        text = f"{host}{path}"
        if len(text) > max_len:
            return text[: max_len - 3] + "..."
        return text

    def _log_attempt(
        self, label: str, round_idx: int, egress: str, detail: str
    ) -> None:
        print(
            f"  crawl [{label}] try {round_idx}/{self._max_rounds} "
            f"{egress}: {detail}",
            flush=True,
            file=sys.stdout,
        )

    def _log_final(
        self,
        label: str,
        result: CrawlResult,
        *,
        rounds: int,
        egress: str | None = None,
        nbytes: int | None = None,
    ) -> None:
        if result.ok:
            size = f", {nbytes} bytes" if nbytes is not None else ""
            via = f" via {egress}" if egress else ""
            print(
                f"  crawl [{label}] OK HTTP {result.status_code}{via}{size} "
                f"(after {rounds} attempt(s))",
                flush=True,
                file=sys.stdout,
            )
            return
        code = result.status_code
        code_part = f"HTTP {code}" if code is not None else "no HTTP status"
        via = f" via {egress}" if egress else ""
        err = result.error or "unknown error"
        print(
            f"  crawl [{label}] FAIL {code_part}{via}: {err} "
            f"(after {rounds} attempt(s))",
            flush=True,
            file=sys.stdout,
        )

    def _pick_port_soonest(self) -> Port | None:
        """
        Pick a selectable egress: prefer healthy over degraded; skip quarantined.

        Among ready ports, round-robin for fair use (not lowest port number first).
        Prefer ports with no in-flight request; if all ready ports are busy, rotate
        across the full ready set. If none are ready, return ``None``.
        """
        for p in self._ports:
            p._refresh_quarantine_if_expired()

        selectable = [p for p in self._ports if p.is_selectable()]
        if not selectable:
            return None

        def health_rank(p: Port) -> int:
            return 0 if p.health_state == "healthy" else 1

        ready = sorted(
            (p for p in selectable if p.seconds_until_selectable() <= 0),
            key=lambda p: (health_rank(p), p.portnum if p.portnum != -1 else 99999),
        )
        if not ready:
            return None

        idle = [p for p in ready if self._port_in_flight.get(p.portnum, 0) == 0]
        pool = idle if idle else ready
        idx = self._ready_rr_index % len(pool)
        self._ready_rr_index += 1
        return pool[idx]

    def _seconds_until_any_port_selectable(self) -> float:
        delays = [p.seconds_until_selectable() for p in self._ports]
        if not delays:
            return 0.0
        return min(delays)

    def _transport_backoff_seconds(self, consecutive: int) -> float:
        base = max(0.5, self._transport_backoff_base_s)
        cap = max(base, self._transport_backoff_cap_s)
        exp = base * (2 ** max(0, consecutive))
        return float(min(cap, max(base, exp)))

    def _exponential_backoff_seconds(self, hit_count: int) -> float:
        base = max(1.0, float(self._cooldown_steps_s[0]) if self._cooldown_steps_s else 60.0)
        cap = max(base, float(self._cooldown_steps_s[-1]) if self._cooldown_steps_s else base)
        wait_s = min(cap, base * (2 ** max(0, hit_count - 1)))
        return float(max(1.0, wait_s))

    def _retry_after_seconds(
        self, resp: aiohttp.ClientResponse, hit_count: int
    ) -> float:
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


# Backward-compatible alias for callers that imported ``Crawler``.
Crawler = AsyncCrawler
