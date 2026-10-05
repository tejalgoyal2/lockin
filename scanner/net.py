"""Thin network clients so everything else is testable with fixtures (SPEC §10)."""
import subprocess
import time
from pathlib import Path

import requests

USER_AGENT = "lockin-job-scanner/0.1 (+https://github.com/tejalgoyal2/lockin)"


def http_get(url: str, *, retries: int = 4, timeout: int = 120) -> requests.Response:
    """GET with a descriptive User-Agent, exponential backoff, and Retry-After on 429."""
    delay = 2.0
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
            if resp.status_code == 429 and attempt < retries:
                time.sleep(float(resp.headers.get("Retry-After", delay)))
                continue
            resp.raise_for_status()
            return resp
        except requests.RequestException:
            if attempt == retries:
                raise
            time.sleep(delay)
            delay *= 2
    raise AssertionError("unreachable")


def sync_git_repo(url: str, dest: Path) -> None:
    """Shallow clone `url` into `dest`, or fast-forward an existing shallow clone."""
    if (dest / ".git").is_dir():
        subprocess.run(["git", "-C", str(dest), "fetch", "--depth", "1", "-q", "origin"], check=True)
        subprocess.run(["git", "-C", str(dest), "reset", "--hard", "-q", "FETCH_HEAD"], check=True)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--depth", "1", "-q", url, str(dest)], check=True)
