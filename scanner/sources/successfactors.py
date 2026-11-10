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
from urllib.parse import unquote

from scanner.text import html_to_text

SOURCE = "SuccessFactors"
MAX_PAGES = 40            # pages of whatever size the site uses (15, 25 ...): 600 to 1,000 postings
BOARD_SECONDS = 300       # newest first: what is read in this time is what matters
HORIZON_DAYS = 31         # older than this is never wanted (the widest window anyone asks for is a month)
_SLUG = re.compile(r"^(?P<host>[A-Za-z0-9.-]+\.[A-Za-z]{2,})(?P<prefix>/[A-Za-z0-9_-]+)?$")
_JOB_PATH = re.compile(r"^(?:/[^/]+)?/job/[^/]+/\d{5,}/?$")
_ROW = re.compile(r'<tr class="data-row">(.*?)</tr>', re.S)
_TILE = re.compile(r'<li class="job-tile[^"]*"[^>]*data-url="([^"]+)"(.*?)(?=<li class="job-tile|</ul>|$)', re.S)
_TITLE = re.compile(r'<a (?:[^>]*?)href="((?:/[^/"]+)?/job/[^"]+)"[^>]*class="jobTitle-link[^"]*"[^>]*>(.*?)</a>'
                    r'|<a [^>]*class="jobTitle-link[^"]*"[^>]*href="((?:/[^/"]+)?/job/[^"]+)"[^>]*>(.*?)</a>', re.S)
_WHERE = re.compile(r'<td class="colLocation[^>]*>.*?<span class="jobLocation">(.*?)</span>', re.S)
_TILE_WHERE = re.compile(r'section-location-value"[^>]*>(.*?)</div>', re.S)
_DATE = re.compile(r'<span class="jobDate">(.*?)</span>', re.S)
_TILE_DATE = re.compile(r'section-date-value"[^>]*>(.*?)</div>', re.S)
_PROVINCE = re.compile(r"(?:^|-)(ON|BC|AB|QC|MB|SK|NS|NB|NL|PE|YT|NT|NU)(?:-|$)")
_POSTAL = re.compile(r"(?:^|-)([ABCEGHJKLMNPRSTVXY])\d[A-Z]-\d[A-Z]\d(?:-|$)")
_POSTAL_PROVINCE = {"A": "NL", "B": "NS", "C": "PE", "E": "NB", "G": "QC", "H": "QC", "J": "QC", "K": "ON", "L": "ON",
                    "M": "ON", "N": "ON", "P": "ON", "R": "MB", "S": "SK", "T": "AB", "V": "BC", "X": "NT", "Y": "YT"}


class SuccessFactorsError(Exception):
    """The search page or a posting page is not what it used to be."""


def split_slug(slug: str) -> tuple[str, str]:
    """"careers.telus.com" -> ("careers.telus.com", ""); "jobs.toronto.ca/jobsatcity" -> ("jobs.toronto.ca", "/jobsatcity")."""
    m = _SLUG.match(slug or "")
    if not m:
        raise SuccessFactorsError(f"successfactors slug must be the careers host (and its path if it has one), e.g. "
                                  f"careers.telus.com or jobs.toronto.ca/jobsatcity, got {slug!r}")
    return m.group("host"), m.group("prefix") or ""


def check_host(slug: str) -> str:
    split_slug(slug)
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


def location_from_url(href: str) -> str:
    """Some sites show no location column: the link carries it ("/job/Vancouver-Engineering-Technician-I-Brit-V6A-4K6/123/"
    or ".../Toronto-Division-Chief-ON-M5V-0C4/..."). The city is the first word, the province an explicit code or the
    postal code's first letter. "" when neither is there."""
    m = re.search(r"/job/([^/]+)/", unquote(href or ""))
    slug = m.group(1) if m else ""
    prov = _PROVINCE.search(slug)
    code = prov.group(1) if prov else None
    if not code:
        postal = _POSTAL.search(slug)
        code = _POSTAL_PROVINCE.get(postal.group(1)) if postal else None
    city = slug.split("-")[0]
    return f"{city}, {code}" if code and city else ""


def search_url(slug: str, offset: int, *, canada: bool = True) -> str:
    host, prefix = split_slug(slug)
    where = "&locationsearch=Canada" if canada else ""
    return f"https://{host}{prefix}/search/?q={where}&sortColumn=referencedate&sortDirection=desc&startrow={offset}"


def parse_search(slug: str, name: str, page_html: str) -> list[dict]:
    """A search page (table or tile layout) -> records. Raises SuccessFactorsError when it has rows but none can be read."""
    host, _ = split_slug(slug)
    blocks = [(m.group(0), _where_date(m.group(0), tile=False)) for m in _ROW.finditer(page_html or "")]
    blocks += [(m.group(0), _where_date(m.group(0), tile=True)) for m in _TILE.finditer(page_html or "")]
    out, seen = [], set()
    for block, (where, date) in blocks:
        link = _TITLE.search(block)
        if not link:
            continue
        href, title = (link.group(1), link.group(2)) if link.group(1) else (link.group(3), link.group(4))
        if href in seen:
            continue
        seen.add(href)
        out.append({"company": slug, "company_name": name, "title": _text(title),
                    "location": where or location_from_url(href), "url": f"https://{host}{href}",
                    "source": SOURCE, "first_seen": _day(date) if date else None})
    if blocks and not out:
        raise SuccessFactorsError(f"{len(blocks)} rows on the page, none readable: the markup changed")
    return out


def _where_date(block: str, *, tile: bool) -> tuple[str, str]:
    where = (_TILE_WHERE if tile else _WHERE).search(block)
    date = (_TILE_DATE if tile else _DATE).search(block)
    return (_text(where.group(1)) if where else "", _text(date.group(1)) if date else "")


def fetch_board(slug: str, name: str, client, now: datetime) -> list[dict]:
    """Page by page (the next page starts after the rows already read: sites use 15, 25 ... per page) until a page adds
    nothing, is older than the horizon, or the time budget is spent. Asks for Canada first; a site that answers that with
    nothing is asked again without the filter (a city's list has no country in it)."""
    split_slug(slug)
    cutoff, out, started, canada = now - timedelta(days=HORIZON_DAYS), [], time.monotonic(), True
    urls = set()
    for _ in range(MAX_PAGES):
        if time.monotonic() - started > BOARD_SECONDS:
            break
        recs = parse_search(slug, name, client.get(search_url(slug, len(urls), canada=canada), SOURCE).text)
        if not recs and canada and not urls:
            canada = False
            recs = parse_search(slug, name, client.get(search_url(slug, 0, canada=False), SOURCE).text)
        new = [r for r in recs if r["url"] not in urls]
        if not new:
            break
        urls |= {r["url"] for r in new}
        out += new
        dated = [r["first_seen"] for r in new if r["first_seen"]]
        if dated and max(dated) < cutoff:
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
