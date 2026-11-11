"""A polite HTTP client: robots.txt, per-host rate limiting, retries with backoff."""

import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from tracker.log import get_logger
from tracker.settings import get_settings

log = get_logger(__name__)
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_DISALLOW_ALL = ["User-agent: *", "Disallow: /"]


def _log_retry(state: RetryCallState) -> None:
    """Log each retry with the error that caused it."""
    error = state.outcome.exception() if state.outcome else None
    log.warning("http_retry", attempt=state.attempt_number, error=str(error))


class RetryableHTTPError(Exception):
    """Server said 'try again later' (429 or 5xx)."""


class RobotsDisallowedError(Exception):
    """robots.txt forbids fetching this URL for our user agent."""


class PoliteClient:
    """Wraps httpx so every request respects robots.txt, rate limits and retries."""

    def __init__(
        self,
        min_interval_s: float = 1.0,
        max_attempts: int = 4,
        backoff_initial_s: float = 1.0,
        timeout_s: float = 10.0,
        user_agent: str | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.user_agent = user_agent or get_settings().user_agent
        self._client = httpx.Client(
            headers={"User-Agent": self.user_agent},
            timeout=timeout_s,
            transport=transport,
            follow_redirects=True,
        )
        self._min_interval_s = min_interval_s
        self._sleep = sleep
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser] = {}
        self._retrying = Retrying(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential_jitter(
                multiplier=backoff_initial_s, max=30, jitter=backoff_initial_s
            ),
            retry=retry_if_exception_type((httpx.TransportError, RetryableHTTPError)),
            sleep=sleep,
            before_sleep=_log_retry,
            reraise=True,
        )

    def get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        """GET a URL politely. Raises RobotsDisallowedError or the final HTTP error."""
        if not self.allowed(url):
            raise RobotsDisallowedError(url)
        response: httpx.Response = self._retrying(self._get_once, url, params)
        return response

    def allowed(self, url: str) -> bool:
        """Check robots.txt for this URL (cached per host)."""
        parts = urlsplit(url)
        host = f"{parts.scheme}://{parts.netloc}"
        if host not in self._robots:
            self._robots[host] = self._load_robots(host)
        return self._robots[host].can_fetch(self.user_agent, url)

    def _load_robots(self, host: str) -> RobotFileParser:
        """Fetch robots.txt. Per RFC 9309: 4xx allows everything; 5xx/unreachable blocks."""
        parser = RobotFileParser()
        try:
            resp = self._client.get(f"{host}/robots.txt")
        except httpx.TransportError:
            log.warning("robots_unreachable", host=host)
            parser.parse(_DISALLOW_ALL)
            return parser
        if resp.status_code >= 500:
            log.warning("robots_server_error", host=host, status=resp.status_code)
            parser.parse(_DISALLOW_ALL)
        elif resp.status_code >= 400:
            parser.parse([])
        else:
            parser.parse(resp.text.splitlines())
        return parser

    def _get_once(self, url: str, params: dict[str, Any] | None) -> httpx.Response:
        """A single attempt, after waiting out the per-host rate limit."""
        self._throttle(urlsplit(url).netloc)
        resp = self._client.get(url, params=params)
        if resp.status_code in RETRYABLE_STATUS:
            raise RetryableHTTPError(f"{resp.status_code} from {url}")
        resp.raise_for_status()
        return resp

    def _throttle(self, host: str) -> None:
        """Sleep so requests to one host are at least `min_interval_s` apart."""
        last = self._last_request.get(host)
        if last is not None:
            wait = self._min_interval_s - (time.monotonic() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_request[host] = time.monotonic()

    def close(self) -> None:
        """Close the underlying connection pool."""
        self._client.close()
