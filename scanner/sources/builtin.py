"""Built In Vancouver (https://builtinvancouver.org): the entry-level listings by category.

The category pages (`/jobs/dev-engineering/entry-level`, ...) are server-rendered cards: company, title, how long ago
it was posted, work arrangement and location. A posting's page carries the description. Cards are read from the HTML
by their markers (`data-id="job-card-title"` and so on); when a page has cards but none can be read, the markup has
changed and the reader says so instead of returning nothing.
"""
import html
import re
from datetime import datetime
from html.parser import HTMLParser

from scanner.sources.listing import parse_age
from scanner.text import html_to_text

SOURCE = "Built In"
_CARD_SPLIT = re.compile(r'(?=<div id="job-card-\d+")')
_CARD_ID = re.compile(r'^<div id="job-card-(\d+)"')
_TITLE = re.compile(r'<a href="(/job/[^"]+)"[^>]*data-id="job-card-title"[^>]*>(.*?)</a>', re.S)
_COMPANY = re.compile(r'data-id="company-title"[^>]*>\s*<span>(.*?)</span>', re.S)
_COMPANY_SLUG = re.compile(r'<a href="/company/([^"/]+)"')
_AGE = re.compile(r'fa-clock[^>]*></i>\s*([^<]+)<')
_LOCATION = re.compile(r'fa-location-dot[^>]*></i>.*?<span[^>]*>(.*?)</span>', re.S)


class BuiltInPageError(Exception):
    """A page showed job cards but none could be read."""


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", fragment))).strip()


def parse_cards(page_html: str, base: str, now: datetime) -> list[dict]:
    chunks = [c for c in _CARD_SPLIT.split(page_html or "") if _CARD_ID.match(c)]
    out = []
    for chunk in chunks:
        title, company = _TITLE.search(chunk), _COMPANY.search(chunk)
        if not (title and company):
            continue
        slug = _COMPANY_SLUG.search(chunk)
        age, where = _AGE.search(chunk), _LOCATION.search(chunk)
        out.append({
            "company": slug.group(1) if slug else _text(company.group(1)), "company_name": _text(company.group(1)),
            "title": _text(title.group(2)), "location": _text(where.group(1)) if where else "",
            "url": base + title.group(1), "source": SOURCE,
            "first_seen": parse_age(age.group(1), now) if age else None,
        })
    if chunks and not out:
        raise BuiltInPageError(f"{len(chunks)} job cards on the page, none readable: the markup changed")
    return out


def fetch(base: str, paths: list[str], max_pages: int, client, now: datetime, window_days: float) -> list[dict]:
    """Each category path, page by page, until a page has nothing newer than the freshness window (the lists are
    ordered by date, with a few promoted cards on top, so one old page ends the walk) or `max_pages` is reached."""
    seen, out = set(), []
    for path in paths:
        for page in range(1, max_pages + 1):
            url = f"{base}{path}" + (f"?page={page}" if page > 1 else "")
            recs = parse_cards(client.get(url, SOURCE).text, base, now)
            fresh = [r for r in recs if r["first_seen"] is None or (now - r["first_seen"]).days <= window_days]
            for r in recs:
                if r["url"] not in seen:
                    seen.add(r["url"])
                    out.append(r)
            if not recs or not fresh:
                break
    return out


class _Body(HTMLParser):
    """Collects the inner HTML of the element with one id (nested elements of the same tag included)."""

    def __init__(self, element_id: str):
        super().__init__(convert_charrefs=False)
        self.element_id, self.depth, self.tag, self.parts = element_id, 0, "", []

    def handle_starttag(self, tag, attrs):
        if self.depth:
            if tag == self.tag:
                self.depth += 1
            self.parts.append(self.get_starttag_text())
        elif dict(attrs).get("id") == self.element_id:
            self.tag, self.depth = tag, 1

    def handle_endtag(self, tag):
        if self.depth:
            if tag == self.tag:
                self.depth -= 1
            if self.depth:
                self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        if self.depth:
            self.parts.append(data)

    def handle_entityref(self, name):
        if self.depth:
            self.parts.append(f"&{name};")

    def handle_charref(self, name):
        if self.depth:
            self.parts.append(f"&#{name};")


def parse_detail(page_html: str, job_id: str) -> str:
    """A posting's page -> the description as plain text ('' if the body is not there)."""
    body = _Body(f"job-post-body-{job_id}")
    body.feed(page_html or "")
    return html_to_text("".join(body.parts))


def job_id(url: str) -> str:
    m = re.search(r"/job/[^/]+/(\d+)", url or "")
    return m.group(1) if m else ""
