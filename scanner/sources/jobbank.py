"""Job Bank (https://www.jobbank.gc.ca): the Government of Canada's national job board.

Job Bank publishes an Atom feed for every search (the "RSS" link of a results page, meant for readers like this
one). A feed carries at most 10 postings, newest first, so the reader asks many narrow questions instead of one big
one: every search term for the whole country, and when a term fills the 10 slots, once more per province. Job Bank's
robots.txt asks for a 5-second `Crawl-delay`; the client paces this host accordingly (`jd.host_delays`).

A feed entry has the NOC-style job title ("software developer"), the employer, the location ("Vancouver (BC)") and
the date; the posting page has the description. Quebec is never searched (the owner cannot work in French).
"""
import html
import math
import re
import string
import xml.etree.ElementTree as ET
from collections import deque
from datetime import datetime, timezone
from urllib.parse import urlencode

from scanner.text import html_to_text

SOURCE = "Job Bank"
HOST = "www.jobbank.gc.ca"
FEED = f"https://{HOST}/jobsearch/feed/jobSearchRSSfeed"
CRAWL_DELAY = 5.0                      # seconds between requests (robots.txt)
FEED_CAP = 10                          # postings in one feed: a full feed may hide older ones
PROVINCES = ("BC", "AB", "ON", "SK", "MB", "NS", "NB", "NL", "PE", "YT", "NT", "NU")      # no QC
MAX_QUERIES = 60                       # per run: 60 x 5 s = 5 minutes at the very most

_NS = {"a": "http://www.w3.org/2005/Atom"}
_FIELD = re.compile(r"<strong>\s*(.*?)\s*:\s*</strong>\s*(.*?)\s*(?=<br\s*/?>|<strong>|$)", re.S | re.I)
_PLACE = re.compile(r"^(?P<city>.*?)\s*\((?P<prov>[A-Z]{2})\)\s*$")


class JobBankError(Exception):
    """The feed or a posting page is not what it used to be."""


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment or ""))).strip()


def place(raw: str) -> str:
    """"Vancouver (BC)" -> "Vancouver, BC" (the location filter wants a comma before the province)."""
    raw = _text(raw)
    m = _PLACE.match(raw)
    return f"{m.group('city')}, {m.group('prov')}" if m else raw


def title_case(title: str) -> str:
    """Job Bank titles are lower case ("software developer"); anything with a capital is left as the employer wrote it."""
    title = _text(title)
    return string.capwords(title) if title and title == title.lower() else title


def _when(text: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat((text or "").replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def parse_feed(xml_text: str) -> list[dict]:
    """An Atom feed -> records. Raises JobBankError when the XML is broken or entries have none of the usual fields."""
    try:
        root = ET.fromstring((xml_text or "").encode("utf-8"))
    except ET.ParseError as exc:
        raise JobBankError(f"feed is not XML: {exc}") from None
    entries = root.findall("a:entry", _NS)
    out = []
    for entry in entries:
        link = next((l.get("href") for l in entry.findall("a:link", _NS) if l.get("rel") in (None, "alternate")), "")
        title = entry.findtext("a:title", default="", namespaces=_NS)
        summary = entry.findtext("a:summary", default="", namespaces=_NS)
        fields = {_text(k).lower(): _text(v) for k, v in _FIELD.findall(summary)}
        when = _when(entry.findtext("a:updated", namespaces=_NS))
        if not (link and title and when):
            continue
        employer = fields.get("employer", "")
        out.append({
            "company": employer or "Job Bank employer", "company_name": employer,
            "title": title_case(title), "location": place(fields.get("location", "")),
            "url": link.split("?")[0], "source": SOURCE, "first_seen": when,
        })
    if entries and not out:
        raise JobBankError(f"{len(entries)} feed entries, none readable: the feed changed")
    return out


def feed_url(term: str, days: int, province: str | None = None) -> str:
    params = {"searchstring": term, "fage": days, "sort": "D"}
    if province:
        params["fprov"] = province
    return f"{FEED}?{urlencode(params)}"


def fetch(client, terms: list[str], window_days: float, *, provinces=PROVINCES, max_queries: int = MAX_QUERIES) -> list[dict]:
    """Every term for the whole country; a term whose feed is full is asked again per province."""
    days = max(1, min(30, math.ceil(window_days)))
    queue = deque((t, None) for t in terms)
    seen, out, asked = set(), [], 0
    while queue and asked < max_queries:
        term, prov = queue.popleft()
        asked += 1
        xml_text = client.get(feed_url(term, days, prov), SOURCE).text
        recs = parse_feed(xml_text)
        full = xml_text.count("<entry>") >= FEED_CAP
        for rec in recs:
            if rec["url"] not in seen:
                seen.add(rec["url"])
                out.append(rec)
        if full and prov is None:
            queue.extend((term, p) for p in provinces)
    return out


# --- a posting's page ------------------------------------------------------------------------

_BODY_START = 'job-posting-detail-requirements'        # structured postings: education, experience, tasks, skills
_BODY_END = 'class="job-posting-detail-apply"'
_FREE_START = 'job-posting-details-body'                # postings with a free-text description instead
_FREE_END = 'job-posting-details-menu'
_MARKUP = re.compile(r"</?(?:p|ul|ol|li|strong|em|br|div|span|b|h\d)\b", re.IGNORECASE)
_AFTER_DESCRIPTION = re.compile(r"\n(?:Location|Work location|Salary|Who can apply|How to apply|Advertised until)\b"
                                r"|\s-\s(?:Location|Work location|Salary)\b")


def parse_detail(page_html: str) -> str:
    """A posting's page -> the description as plain text. Two layouts: the structured "Job details" block (education,
    experience, tasks, skills, work conditions), or a free-text description the employer typed (stored as escaped HTML,
    so it is unescaped and read again). Raises JobBankError when neither is on the page."""
    page_html = page_html or ""
    start = page_html.find(_BODY_START)
    if start >= 0:
        start = page_html.rfind("<div", 0, start)
        end = page_html.find(_BODY_END, start)
        return html_to_text(page_html[start:end if end > start else None])
    start = page_html.find(_FREE_START)
    if start < 0:
        raise JobBankError("no job-details block on the page: the markup changed")
    end = page_html.find(_FREE_END, start)
    text = html_to_text(page_html[start:end if end > start else None])
    if _MARKUP.search(text):                              # the employer's own HTML, shown as text: read it as HTML
        text = html_to_text(text)
    i = text.find("Job details")
    text = text[i + len("Job details"):] if i >= 0 else text
    m = _AFTER_DESCRIPTION.search(text)
    text = text[:m.start()] if m else text
    if len(text.strip()) < 40:
        raise JobBankError("no description in the job-details block: the markup changed")
    return text.strip()
