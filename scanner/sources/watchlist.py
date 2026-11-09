"""Watchlist: employers whose job boards are read directly on every run (Greenhouse, Lever, Ashby, Workday,
SmartRecruiters).

The Feashliaa dataset refreshes once a day and only covers the employers its author lists. A watchlist board is
asked for its jobs live, so a posting shows up on the next run (the morning run included) and employers the
dataset does not carry are covered. Each job keeps the ATS as its source ("Greenhouse", ...): downstream the
record is the same as a dataset row and dedupes against it by URL.

`watchlist.yaml` is the list of boards; see the file for how to add one.
"""
import logging
import re
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import yaml

from scanner.normalize import clean_title, from_unix, parse_iso
from scanner.sources import phenom, successfactors

log = logging.getLogger(__name__)

LABEL = {"greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby", "workday": "Workday",
         "smartrecruiters": "SmartRecruiters", "phenom": "Phenom", "successfactors": "SuccessFactors"}
WORKDAY_PAGE = 20          # Workday's maximum page size
WORKDAY_MAX_PAGES = 50     # 1,000 postings per tenant (TD lists ~950 Canadian ones)
WORKDAY_MAX_PAGES_FULL = 50  # no country facet: read up to 1,000 postings (~20 s at the polite pace)
SR_PAGE = 100
SR_MAX_PAGES = 5
BOARD_SECONDS = 150        # one board never holds the run longer than this: a big tenant is read newest first, so the
                           # postings that matter are in the pages already read (the rest is logged, not waited for)


class WatchlistError(Exception):
    """watchlist.yaml is malformed."""


@dataclass(frozen=True)
class Board:
    ats: str          # greenhouse | lever | ashby | workday | smartrecruiters
    slug: str         # the id in the board's public URL. Workday: "<tenant>.<wdN>/<site>" (generalmotors.wd5/Careers_GM)
    name: str = ""    # readable company name for the Feed (blank: the ATS or the slug is used)

    @property
    def label(self) -> str:
        return LABEL[self.ats]

    @property
    def workday(self) -> tuple[str, str, str]:
        """(host, tenant, site) of a Workday board."""
        m = re.fullmatch(r"([A-Za-z0-9-]+)\.(wd\d+)/([A-Za-z0-9_.-]+)", self.slug)
        if not m:
            raise WatchlistError(f"workday slug must look like <tenant>.<wdN>/<site>, got {self.slug!r}")
        return f"{m.group(1)}.{m.group(2)}.myworkdayjobs.com", m.group(1), m.group(3)

    @property
    def url(self) -> str:
        if self.ats == "workday":
            host, tenant, site = self.workday
            return f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
        if self.ats == "phenom":
            return f"https://{phenom.split_slug(self.slug)[0]}/widgets"
        if self.ats == "successfactors":
            return successfactors.search_url(self.slug, 0)
        slug = quote(self.slug)
        return {
            "greenhouse": f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
            "lever": f"https://api.lever.co/v0/postings/{slug}?mode=json",
            "ashby": f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
            "smartrecruiters": f"https://api.smartrecruiters.com/v1/companies/{slug}/postings",
        }[self.ats]


def load_boards(path: str | Path, *extra: str | Path) -> list[Board]:
    """Read watchlist.yaml, then the optional `extra` files (watchlist.auto.yaml: boards the scan found itself; a missing
    one is fine, and a board already listed earlier is skipped there). A bad entry is an error, not a skipped line: a
    typo would silently drop an employer."""
    boards, seen = _read_file(path, [], set(), required=True)
    for more in extra:
        boards, seen = _read_file(more, boards, seen, required=False)
    return boards


def _read_file(path, boards: list[Board], seen: set, *, required: bool) -> tuple[list[Board], set]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        if required:
            raise WatchlistError(f"{path} not found (turn the watchlist off in config.yaml or restore the file)") from None
        return boards, seen
    boards, seen, earlier = list(boards), set(seen), set(seen)
    for i, entry in enumerate(data.get("boards") or [], 1):
        ats = str((entry or {}).get("ats", "")).strip().lower()
        slug = str((entry or {}).get("slug", "")).strip()
        if ats not in LABEL or not slug:
            raise WatchlistError(f"{path}: entry {i} needs ats ({', '.join(LABEL)}) and slug, got {entry!r}")
        if (ats, slug.lower()) in earlier:
            continue                                        # already listed in the hand-kept file: that line wins
        if (ats, slug.lower()) in seen:
            raise WatchlistError(f"{path}: {ats}/{slug} is listed twice")
        seen.add((ats, slug.lower()))
        board = Board(ats, slug, str(entry.get("name") or "").strip())
        if ats == "workday":
            board.workday                                   # raises WatchlistError on a malformed slug
        if ats == "phenom":
            try:
                phenom.split_slug(slug)
            except phenom.PhenomError as exc:
                raise WatchlistError(f"{path}: {exc}") from None
        if ats == "successfactors":
            try:
                successfactors.check_host(slug)
            except successfactors.SuccessFactorsError as exc:
                raise WatchlistError(f"{path}: {exc}") from None
        boards.append(board)
    return boards, seen


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


_AGO = re.compile(r"posted\s+(\d+)\+?\s+days?\s+ago", re.I)


def workday_posted(text: str | None, now: datetime) -> datetime | None:
    """Workday's list says "Posted Today", "Posted Yesterday", "Posted 3 Days Ago", "Posted 30+ Days Ago"."""
    t = (text or "").strip().lower()
    if t == "posted today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if t == "posted yesterday":
        return now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    m = _AGO.fullmatch(t)
    if m:
        return now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=int(m.group(1)))
    return None


def parse_workday(board: Board, postings: list[dict], now: datetime) -> list[dict]:
    host, _tenant, site = board.workday
    base = {"company": board.slug.split(".")[0], "company_name": board.name, "source": board.label}
    out = []
    for j in postings:
        path = j.get("externalPath") or ""
        if not (j.get("title") and path):
            continue
        out.append({**base, "title": j["title"], "url": f"https://{host}/{site}{path}",
                    "location": _where(j.get("locationsText")), "first_seen": workday_posted(j.get("postedOn"), now)})
    return out


def parse_smartrecruiters(board: Board, postings: list[dict]) -> list[dict]:
    base = {"company": board.slug, "company_name": board.name, "source": board.label}
    out = []
    for j in postings:
        loc = j.get("location") or {}
        place = ", ".join(x for x in (loc.get("city"), loc.get("region") or "", (loc.get("country") or "").upper()) if x)
        if loc.get("remote") and place:
            place = f"Remote, {place}"
        if not j.get("id"):
            continue
        out.append({**base, "title": j.get("name"), "url": f"https://jobs.smartrecruiters.com/{quote(board.slug)}/{j['id']}",
                    "location": _where(place), "first_seen": _ts(j.get("releasedDate"))})
    return [r for r in out if r["title"]]


def _canada_facet(facets) -> tuple[str, list[str]] | None:
    """(facet parameter, ids) of the tenant's "Canada" country value, searching nested facet groups (TD hides
    `locationCountry` inside `locationMainGroup`; GM has `Location_Country` at the top). None when the tenant has no
    country facet (RBC, Cisco): the caller then reads the whole board and the location filter does the work."""
    for f in facets or []:
        param = f.get("facetParameter") or ""
        values = f.get("values") or []
        if re.search(r"country", param, re.I):
            ids = [v["id"] for v in values if (v.get("descriptor") or "").strip() == "Canada" and v.get("id")]
            if ids:
                return param, ids
        found = _canada_facet([v for v in values if "facetParameter" in v or "values" in v])
        if found:
            return found
    return None


def _fetch_workday(board: Board, client, now: datetime, budget: float = BOARD_SECONDS) -> list[dict]:
    """Canada only where the tenant has a country facet (a 5,000-posting employer costs a few requests); otherwise
    the whole board, capped. Facet ids differ per tenant: the first (unfiltered) answer lists them."""
    first = client.post_json(board.url, board.label, json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""})
    found = _canada_facet(first.get("facets"))
    if found is None and not (first.get("total") or 0):
        return []
    applied, max_pages = ({found[0]: found[1]}, WORKDAY_MAX_PAGES) if found else ({}, WORKDAY_MAX_PAGES_FULL)
    postings: list[dict] = []
    started = time.monotonic()
    for page in range(max_pages):
        if time.monotonic() - started > budget:
            log.warning("watchlist: %s/%s cut short after %d postings (%d s)", board.ats, board.slug, len(postings), budget)
            break
        data = client.post_json(board.url, board.label, json={
            "appliedFacets": applied, "limit": WORKDAY_PAGE, "offset": page * WORKDAY_PAGE, "searchText": ""})
        got = data.get("jobPostings") or []
        postings += got
        if len(got) < WORKDAY_PAGE:
            break
    return parse_workday(board, postings, now)


def _fetch_smartrecruiters(board: Board, client) -> list[dict]:
    postings: list[dict] = []
    for page in range(SR_MAX_PAGES):
        data = client.get_json(f"{board.url}?country=ca&limit={SR_PAGE}&offset={page * SR_PAGE}", board.label)
        got = (data or {}).get("content") or []
        postings += got
        if len(got) < SR_PAGE:
            break
    return parse_smartrecruiters(board, postings)


def fetch_board(board: Board, client, *, now: datetime | None = None, seed_ashby: Callable[[str, dict], None] | None = None,
                budget: float = BOARD_SECONDS) -> list[dict]:
    """`budget`: seconds a Workday board may take (it is read newest first); a probe asks for less than a full run."""
    if board.ats == "workday":
        return _fetch_workday(board, client, now or datetime.now(timezone.utc), budget)
    if board.ats == "smartrecruiters":
        return _fetch_smartrecruiters(board, client)
    if board.ats == "phenom":
        return phenom.fetch_board(board.slug, board.name, client)
    if board.ats == "successfactors":
        return successfactors.fetch_board(board.slug, board.name, client, now or datetime.now(timezone.utc))
    data = client.get_json(board.url, board.label)
    if board.ats == "ashby" and seed_ashby and isinstance(data, dict):
        seed_ashby(board.url, data)                         # the JD fetch needs this same board: do not ask twice
    return parse_board(board, data)


def fetch_all(boards: Iterable[Board], client, *, workers: int = 6, now: datetime | None = None,
              seed_ashby: Callable[[str, dict], None] | None = None) -> tuple[list[dict], list[tuple[Board, str]]]:
    """Every board's records, plus (board, reason) for each board that could not be read. One dead board (renamed,
    closed, a 5xx) never stops the others."""
    boards = list(boards)

    def one(board: Board):
        started = time.monotonic()
        try:
            return board, fetch_board(board, client, now=now, seed_ashby=seed_ashby), ""
        except Exception as exc:                            # network, HTTP error, bad JSON: report and carry on
            return board, [], f"{type(exc).__name__}: {str(exc)[:120]}"
        finally:
            if time.monotonic() - started > 45:
                log.warning("watchlist: %s/%s took %d s", board.ats, board.slug, time.monotonic() - started)

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
