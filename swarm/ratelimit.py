"""Back-off and throttling. Never loop without delay (guide §3.3: Parasail free tier is 5 RPM)."""
import threading
import time
from collections import deque
from email.utils import parsedate_to_datetime

import httpx

from swarm.config import CONFIG
from swarm.log import log


def retry_delay(resp: httpx.Response, attempt: int) -> float:
    """Seconds to wait before retrying, from Retry-After / X-RateLimit-Reset, else exponential."""
    ra = resp.headers.get("retry-after")
    if ra:
        try:
            return max(0.5, float(ra))
        except ValueError:
            try:
                return max(0.5, parsedate_to_datetime(ra).timestamp() - time.time())
            except (TypeError, ValueError):
                pass
    reset = resp.headers.get("x-ratelimit-reset")
    if reset:
        try:
            value = float(reset)
            # epoch seconds or seconds-from-now
            return max(0.5, value - time.time() if value > 1e9 else value)
        except ValueError:
            pass
    return min(60.0, 2.0 * 2**attempt)


def request_with_backoff(method: str, url: str, *, attempts: int = 5, **kwargs) -> httpx.Response:
    """httpx request that retries 429 and 5xx with server-advised delays."""
    kwargs.setdefault("timeout", 60.0)
    for attempt in range(attempts):
        resp = httpx.request(method, url, **kwargs)
        if resp.status_code != 429 and resp.status_code < 500:
            return resp
        if attempt == attempts - 1:
            return resp
        delay = retry_delay(resp, attempt)
        log.info("backoff", url=url.split("?")[0], status=resp.status_code, wait_s=round(delay, 1))
        time.sleep(delay)
    return resp


class RpmLimiter:
    """Sliding-window limiter shared by every Parasail call in the process."""

    def __init__(self, rpm: float):
        self.rpm = rpm
        self.calls: deque[float] = deque()
        self.lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                while self.calls and now - self.calls[0] >= 60:
                    self.calls.popleft()
                if len(self.calls) < self.rpm:
                    self.calls.append(now)
                    return
                wait = 60 - (now - self.calls[0]) + 0.05
            log.info("parasail_throttle", wait_s=round(wait, 1), rpm=self.rpm)
            time.sleep(wait)


PARASAIL_LIMITER = RpmLimiter(CONFIG.parasail_rpm)
