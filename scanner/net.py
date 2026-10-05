"""Thin network clients so everything else is testable with fixtures (SPEC §10)."""
import subprocess
import threading
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

import requests

USER_AGENT = "lockin-job-scanner/0.1 (+https://github.com/tejalgoyal2/lockin)"


def http_get(url: str, *, retries: int = 4, timeout: int = 120, **kwargs) -> requests.Response:
    """GET with a descriptive User-Agent, exponential backoff, and Retry-After on 429.

    4xx other than 429 is not retried (the answer will not change).
    """
    delay = 2.0
    headers = {"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout, **kwargs)
            if resp.status_code == 429 and attempt < retries:
                time.sleep(float(resp.headers.get("Retry-After", delay)))
                continue
            resp.raise_for_status()
            return resp
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if (400 <= status < 500 and status != 429) or attempt == retries:
                raise
        except requests.exceptions.ProxyError:
            raise  # the egress proxy refused the host (network policy): retrying cannot help
        except requests.RequestException:
            if attempt == retries:
                raise
        time.sleep(delay)
        delay *= 2
    raise AssertionError("unreachable")


class PoliteClient:
    """Rate-limited JSON/text client for ATS endpoints (SPEC §3c politeness rules).

    Sequential per host with a minimum gap, at most `per_ats` requests in flight per ATS.
    Thread-safe: callers may fan out across a thread pool.
    """

    def __init__(self, delay: float = 0.3, per_ats: int = 5, retries: int = 3, timeout: int = 30):
        self.delay, self.retries, self.timeout = delay, retries, timeout
        self._ats_sem: dict[str, threading.BoundedSemaphore] = defaultdict(
            lambda: threading.BoundedSemaphore(per_ats))
        self._host_lock: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._host_last: dict[str, float] = {}
        self._guard = threading.Lock()

    def _slots(self, ats: str, host: str):
        with self._guard:
            return self._ats_sem[ats], self._host_lock[host]

    def get(self, url: str, ats: str, **kwargs) -> requests.Response:
        host = urlsplit(url).netloc
        sem, lock = self._slots(ats, host)
        with sem, lock:
            wait = self.delay - (time.monotonic() - self._host_last.get(host, 0.0))
            if wait > 0:
                time.sleep(wait)
            try:
                return http_get(url, retries=self.retries, timeout=self.timeout, **kwargs)
            finally:
                self._host_last[host] = time.monotonic()

    def get_json(self, url: str, ats: str, **kwargs):
        return self.get(url, ats, **kwargs).json()


def sync_git_repo(url: str, dest: Path) -> None:
    """Shallow clone `url` into `dest`, or fast-forward an existing shallow clone."""
    if (dest / ".git").is_dir():
        subprocess.run(["git", "-C", str(dest), "fetch", "--depth", "1", "-q", "origin"], check=True)
        subprocess.run(["git", "-C", str(dest), "reset", "--hard", "-q", "FETCH_HEAD"], check=True)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--depth", "1", "-q", url, str(dest)], check=True)
