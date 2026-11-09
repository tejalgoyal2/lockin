"""SAP SuccessFactors career sites (careers.telus.com, jobs.scotiabank.com, jobs.rogers.com ...).

Employers that use SuccessFactors' "Career Site Builder" publish a server-rendered search page,
`https://<host>/search/?q=&locationsearch=Canada&sortColumn=referencedate&sortDirection=desc&startrow=<n>`, 25 postings a
page, newest first (`locationsearch=Canada` keeps a global employer's list to its Canadian postings):
title link, location and date posted in each row. A watchlist entry is just the host: `{ats: successfactors, slug:
careers.telus.com}`. The reader walks the pages until a whole page is older than `HORIZON_DAYS`. A posting's page
(`/job/<words>/<number>/`, on any host) carries the description in `<span class="jobdescription">`.

These sites' robots.txt does not disallow `/search/` or `/job/`; the pages are what any visitor sees.
"""
import html
import re
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

from scanner.text import html_to_text

SOURCE = "SuccessFactors"
PAGE = 25
MAX_PAGES = 20            # 500 postings
BOARD_SECONDS = 150      # newest first: what is read in this time is what matters
HORIZON_DAYS = 31         # older than this is never wanted (the widest window anyone asks for is a month)
_HOST = re.compile(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_JOB_PATH = re.compile(r"^/job/[^/]+/\d{5,}/?$")
_ROW = re.compile(r'<tr class="data-row">(.*?)</tr>', re.S)
_TITLE = re.compile(r'<a href="(/job/[^"]+)" class="jobTitle-link">(.*?)</a>', re.S)
_WHERE = re.compile(r'<td class="colLocation[^>]*>.*?<span class="jobLocation">(.*?)</span>', re.S)
_DATE = re.compile(r'<span class="jobDate">(.*?)</span>', re.S)


class SuccessFactorsError(Exception):
    """The search page or a posting page is not what it used to be."""


def check_host(slug: str) -> str:
    if not _HOST.match(slug or ""):
        raise SuccessFactorsError(f"successfactors slug must be the careers host, e.g. careers.telus.com, got {slug!r}")
    return slug


def is_job_url(url_path: str) -> bool:
    return bool(_JOB_PATH.match(url_path or ""))


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment or ""))).strip()


def _day(text: str) -> datetime | None:
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def search_url(host: str, offset: int) -> str:
    return f"https://{host}/search/?q=&locationsearch=Canada&sortColumn=referencedate&sortDirection=desc&startrow={offset}"


def parse_search(host: str, name: str, page_html: str) -> list[dict]:
    """A search page -> records. Raises SuccessFactorsError when it has rows but none can be read."""
    rows, out = _ROW.findall(page_html or ""), []
    for row in rows:
        link, where, date = _TITLE.search(row), _WHERE.search(row), _DATE.search(row)
        if not link:
            continue
        out.append({"company": host, "company_name": name, "title": _text(link.group(2)),
                    "location": _text(where.group(1)) if where else "", "url": f"https://{host}{link.group(1)}",
                    "source": SOURCE, "first_seen": _day(_text(date.group(1))) if date else None})
    if rows and not out:
        raise SuccessFactorsError(f"{len(rows)} rows on the page, none readable: the markup changed")
    return out


def fetch_board(host: str, name: str, client, now: datetime) -> list[dict]:
    check_host(host)
    cutoff, out, started = now - timedelta(days=HORIZON_DAYS), [], time.monotonic()
    for page in range(MAX_PAGES):
        if time.monotonic() - started > BOARD_SECONDS:
            break
        recs = parse_search(host, name, client.get(search_url(host, page * PAGE), SOURCE).text)
        out += recs
        dated = [r["first_seen"] for r in recs if r["first_seen"]]
        if len(recs) < PAGE or (dated and max(dated) < cutoff):
            break
    return out


# --- a posting's page --------------------------------------------------------------------------

class _ByClass(HTMLParser):
    """Inner HTML of the first element with a given class (nested tags of the same name are followed)."""

    def __init__(self, cls: str):
        super().__init__(convert_charrefs=False)
        self.cls, self.depth, self.tag, self.parts = cls, 0, "", []

    def handle_starttag(self, tag, attrs):
        if self.depth:
            if tag == self.tag:
                self.depth += 1
            self.parts.append(self.get_starttag_text())
        elif self.cls in (dict(attrs).get("class") or "").split() and not self.parts:
            self.tag, self.depth = tag, 1

    def handle_endtag(self, tag):
        if self.depth:
            if tag == self.tag:
                self.depth -= 1
            if self.depth:
                self.parts.append(f"</{tag}>")

    def handle_startendtag(self, tag, attrs):
        if self.depth:
            self.parts.append(self.get_starttag_text())

    def handle_data(self, data):
        if self.depth:
            self.parts.append(data)

    def handle_entityref(self, name):
        if self.depth:
            self.parts.append(f"&{name};")

    def handle_charref(self, name):
        if self.depth:
            self.parts.append(f"&#{name};")


def parse_detail(page_html: str) -> str:
    """A posting's page -> the description as plain text. Raises SuccessFactorsError when it is not on the page."""
    body = _ByClass("jobdescription")
    body.feed(page_html or "")
    text = html_to_text("".join(body.parts))
    if not text:
        raise SuccessFactorsError("no jobdescription block on the page: the markup changed")
    return text
