"""Watchlist: employers whose job boards are read directly on every run (Greenhouse, Lever, Ashby).

The Feashliaa dataset refreshes once a day and only covers the employers its author lists. A watchlist board is
asked for its jobs live, so a posting shows up on the next run (the morning run included) and employers the
dataset does not carry are covered. Each job keeps the ATS as its source ("Greenhouse", ...): downstream the
record is the same as a dataset row and dedupes against it by URL.

`watchlist.yaml` is the list of boards; see the file for how to add one.
"""
import logging
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import yaml

from scanner.normalize import clean_title, from_unix, parse_iso

log = logging.getLogger(__name__)

LABEL = {"greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby"}


class WatchlistError(Exception):
    """watchlist.yaml is malformed."""


@dataclass(frozen=True)
class Board:
    ats: str          # greenhouse | lever | ashby
    slug: str         # the id in the board's public URL
    name: str = ""    # readable company name for the Feed (blank: the ATS or the slug is used)

    @property
    def label(self) -> str:
        return LABEL[self.ats]

    @property
    def url(self) -> str:
        slug = quote(self.slug)
        return {
            "greenhouse": f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
            "lever": f"https://api.lever.co/v0/postings/{slug}?mode=json",
            "ashby": f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
        }[self.ats]


def load_boards(path: str | Path) -> list[Board]:
    """Read watchlist.yaml. A bad entry is an error, not a skipped line: a typo would silently drop an employer."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        raise WatchlistError(f"{path} not found (turn the watchlist off in config.yaml or restore the file)") from None
    boards, seen = [], set()
    for i, entry in enumerate(data.get("boards") or [], 1):
        ats = str((entry or {}).get("ats", "")).strip().lower()
        slug = str((entry or {}).get("slug", "")).strip()
        if ats not in LABEL or not slug:
            raise WatchlistError(f"{path}: entry {i} needs ats ({', '.join(LABEL)}) and slug, got {entry!r}")
        if (ats, slug.lower()) in seen:
            raise WatchlistError(f"{path}: {ats}/{slug} is listed twice")
        seen.add((ats, slug.lower()))
        boards.append(Board(ats, slug, str(entry.get("name") or "").strip()))
    return boards


# --- one board -> uniform records -------------------------------------------------------------

def _ts(value) -> datetime | None:
    """ISO string (Greenhouse, Ashby) or epoch milliseconds (Lever) -> aware datetime; None if absent or unreadable."""
    try:
        if isinstance(value, (int, float)):
            return from_unix(value / 1000)
        return parse_iso(str(value)) if value else None
    except ValueError:
        return None


def _where(*places) -> str:
    """Primary location first, then the others, joined the way Simplify's are (the location filter splits on ';')."""
    out: list[str] = []
    for p in places:
        p = (p or "").strip()
        if p and p not in out:
            out.append(p)
    return " ; ".join(out)


def parse_board(board: Board, data) -> list[dict]:
    """The board's JSON answer -> records: company (slug), company_name, title, location, url, source, first_seen."""
    base = {"company": board.slug, "company_name": board.name, "source": board.label}
    out = []
    if board.ats == "greenhouse":
        for j in (data or {}).get("jobs") or []:
            out.append({**base, "title": j.get("title"), "url": j.get("absolute_url"),
                        "location": _where((j.get("location") or {}).get("name")),
                        "first_seen": _ts(j.get("first_published") or j.get("updated_at"))})
    elif board.ats == "lever":
        for j in data if isinstance(data, list) else []:
            cats = j.get("categories") or {}
            out.append({**base, "title": j.get("text"), "url": j.get("hostedUrl"),
                        "location": _where(cats.get("location"), *(cats.get("allLocations") or [])),
                        "first_seen": _ts(j.get("createdAt"))})
    else:
        for j in (data or {}).get("jobs") or []:
            if j.get("isListed") is False:
                continue                                    # unlisted postings are not public
            second = [(s or {}).get("location") if isinstance(s, dict) else s for s in j.get("secondaryLocations") or []]
            out.append({**base, "title": j.get("title"), "url": j.get("jobUrl"),
                        "location": _where(j.get("location"), *second),
                        "first_seen": _ts(j.get("publishedAt"))})
    return [r for r in out if r["title"] and r["url"]]


def fetch_board(board: Board, client, *, seed_ashby: Callable[[str, dict], None] | None = None) -> list[dict]:
    data = client.get_json(board.url, board.label)
    if board.ats == "ashby" and seed_ashby and isinstance(data, dict):
        seed_ashby(board.url, data)                         # the JD fetch needs this same board: do not ask twice
    return parse_board(board, data)


def fetch_all(boards: Iterable[Board], client, *, workers: int = 6,
              seed_ashby: Callable[[str, dict], None] | None = None) -> tuple[list[dict], list[tuple[Board, str]]]:
    """Every board's records, plus (board, reason) for each board that could not be read. One dead board (renamed,
    closed, a 5xx) never stops the others."""
    boards = list(boards)

    def one(board: Board):
        try:
            return board, fetch_board(board, client, seed_ashby=seed_ashby), ""
        except Exception as exc:                            # network, HTTP error, bad JSON: report and carry on
            return board, [], f"{type(exc).__name__}: {str(exc)[:120]}"

    records, failed = [], []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for board, recs, err in pool.map(one, boards):
            if err:
                failed.append((board, err))
                log.warning("watchlist: %s/%s not read (%s)", board.ats, board.slug, err)
            records += recs
    return records, failed


# --- pipeline hooks (same shape as feashliaa / simplify) ---------------------------------------

def raw_fields(rec: dict) -> dict:
    return {
        "company": rec["company"],
        "company_name": rec["company_name"],
        "title": clean_title(rec["title"]),
        "location": rec["location"],
        "url": rec["url"],
        "source": rec["source"],
        "first_seen": rec["first_seen"],
    }
