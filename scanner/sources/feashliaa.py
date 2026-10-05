"""Feashliaa/job-board-data: daily gzip'd JSON chunks of ATS postings (SPEC §3a)."""
import gzip
import json
import logging
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from scanner.net import sync_git_repo
from scanner.normalize import clean_title, parse_iso

log = logging.getLogger(__name__)


def fetch(cfg: dict) -> Path:
    cache = Path(cfg["cache_dir"])
    sync_git_repo(cfg["repo_url"], cache)
    return cache


def read_metadata(data_dir: Path) -> dict:
    with open(data_dir / "data" / "metadata.json", encoding="utf-8") as fh:
        return json.load(fh)


def check_freshness(meta: dict, stale_after_hours: float, now: datetime) -> float:
    """Return dataset age in hours; log a warning (and carry on) if it is stale."""
    age = (now - parse_iso(meta["last_updated"])).total_seconds() / 3600
    if age > stale_after_hours:
        log.warning("Feashliaa dataset is %.1f h old (> %s h); continuing", age, stale_after_hours)
    return age


def iter_raw(data_dir: Path) -> Iterator[dict]:
    """Stream every record, one chunk in memory at a time."""
    chunks = sorted((data_dir / "data" / "chunks").glob("jobs_chunk_*.json.gz"))
    for path in chunks:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            yield from json.load(fh)


def raw_fields(rec: dict) -> dict:
    """Map a raw record to the fields the pipeline needs."""
    return {
        "company": rec.get("company") or "",
        "title": clean_title(rec.get("title", "")),
        "location": rec.get("location") or "",
        "url": rec.get("url") or "",
        "source": rec.get("ats") or "Unknown",
        "first_seen": parse_iso(rec["first_seen"]) if rec.get("first_seen") else None,
    }
