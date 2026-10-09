"""SimplifyJobs/New-Grad-Positions listings.json (SPEC §3b)."""
import json
from collections.abc import Iterator
from pathlib import Path

from scanner.net import http_get
from scanner.normalize import clean_title, from_unix


def fetch(cfg: dict) -> list[dict]:
    return http_get(cfg["listings_url"]).json()


def load_file(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def iter_raw(listings: list[dict]) -> Iterator[dict]:
    yield from listings


def is_listed(rec: dict, categories: list[str]) -> bool:
    return bool(rec.get("active") and rec.get("is_visible") and rec.get("category") in categories)


def raw_fields(rec: dict) -> dict:
    # Multi-location rows: the filter picks the first Canadian one from this joined string.
    return {
        "company": rec.get("company_name") or "",
        "company_name": rec.get("company_name") or "",
        "title": clean_title(rec.get("title", "")),
        "location": " ; ".join(rec.get("locations") or []),
        "url": rec.get("url") or "",
        "source": "Simplify",
        "first_seen": from_unix(rec["date_posted"]) if rec.get("date_posted") else None,
    }
