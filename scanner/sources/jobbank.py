"""Job Bank (https://www.jobbank.gc.ca): the Government of Canada's national job board.

Job Bank's search results are public pages, 25 postings a page, newest first (`sort=D`), paged with `page=N`. They
carry far more than the Atom feed of the same search (the feed holds only the employer-posted jobs and is capped at
10 for a keyword search): a search for one occupation shows the jobs employers posted on Job Bank *and* the ones
partner sites send it, among them **CivicJobs.ca**, the municipal job board (Capital Regional District, city halls,
regional districts). So the reader asks for each tech occupation by its NOC code (`fn21=21232` is "software
developers and programmers") and for a few keywords, and pages through each answer until it runs out.

robots.txt asks for a 5-second `Crawl-delay`; the client paces this host accordingly (`jd.host_delays`). Quebec is not
searched specially: its postings fail the location filter like any other.
"""
import html
import math
import re
import string
from collections import deque
from datetime import datetime, timezone
from urllib.parse import urlencode

from scanner.text import html_to_text

SOURCE = "Job Bank"
HOST = "www.jobbank.gc.ca"
RESULTS = f"https://{HOST}/jobsearch/jobsearch"
CRAWL_DELAY = 5.0                      # seconds between requests (robots.txt)
PAGE = 25                              # postings per results page
MAX_PAGES = 12                         # per search: 300 postings
MAX_QUERIES = 200                      # per run: 200 x 5 s = 17 minutes at the very most

_ARTICLE = re.compile(r'<article id="article-(\d+)".*?</article>', re.S)
_TITLE = re.compile(r'class="noctitle">\s*(.*?)\s*</span>', re.S)
_DATE = re.compile(r'<li class="date">\s*(.*?)\s*</li>', re.S)
_BUSINESS = re.compile(r'<li class="business">(.*?)</li>', re.S)
_WHERE = re.compile(r'<span class="wb-inv">Location</span>\s*(.*?)\s*</li>', re.S)
_VIA = re.compile(r'<li class="source">\s*<span class="job-source[^"]*"><span class="wb-inv">(.*?)</span>', re.S)
_PLACE = re.compile(r"^(?P<city>.*?)\s*\((?P<prov>[A-Z]{2})\)\s*$")


class JobBankError(Exception):
    """The results page or a posting page is not what it used to be."""


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment or ""))).strip()


def place(raw: str) -> str:
    """"Vancouver (BC)" -> "Vancouver, BC" (the location filter wants a comma before the province)."""
    raw = _text(raw)
    m = _PLACE.match(raw)
    return f"{m.group('city')}, {m.group('prov')}" if m else raw


def title_case(title: str) -> str:
    """Job Bank titles are lower case ("software developer"); anything with a capital is left as the employer wrote it."""
    return " ".join(w[:1].upper() + w[1:] if w.islower() else w for w in _text(title).split())


def _when(text: str) -> datetime | None:
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(_text(text), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def parse_results(page_html: str) -> list[dict]:
    """A results page -> records. Raises JobBankError when the page has postings but none can be read."""
    articles, out = _ARTICLE.findall(page_html or ""), []
    for m in _ARTICLE.finditer(page_html or ""):
        art = m.group(0)
        title, date, where = _TITLE.search(art), _DATE.search(art), _WHERE.search(art)
        when = _when(date.group(1)) if date else None
        if not (title and when):
            continue
        business = _BUSINESS.search(art)
        employer = _text(business.group(1)) if business else ""
        via = _VIA.search(art)
        out.append({
            "company": employer or "Job Bank employer", "company_name": employer,
            "title": title_case(title.group(1)), "location": place(where.group(1)) if where else "",
            "url": f"https://{HOST}/jobsearch/jobposting/{m.group(1)}", "source": SOURCE, "first_seen": when,
            "via": _text(via.group(1)) if via else "",
        })
    if articles and not out:
        raise JobBankError(f"{len(articles)} postings on the page, none readable: the markup changed")
    return out


def results_url(days: int, page: int, *, noc: str | None = None, term: str | None = None) -> str:
    params = {"fage": days, "sort": "D", "page": page}
    if noc:
        params["fn21"] = noc
    if term:
        params["searchstring"] = term
    return f"{RESULTS}?{urlencode(params)}"


def fetch(client, nocs: list[str], terms: list[str], window_days: float, *, max_pages: int = MAX_PAGES,
          max_queries: int = MAX_QUERIES) -> list[dict]:
    """Every NOC code and keyword, page by page until a page is short (the last one) or `max_pages` is reached."""
    days = max(1, min(30, math.ceil(window_days)))
    queue = deque([("noc", n) for n in nocs] + [("term", t) for t in terms])
    seen, out, asked = set(), [], 0
    while queue and asked < max_queries:
        kind, value = queue.popleft()
        for page in range(1, max_pages + 1):
            if asked >= max_queries:
                break
            asked += 1
            recs = parse_results(client.get(results_url(days, page, **{"noc" if kind == "noc" else "term": value}), SOURCE).text)
            for rec in recs:
                if rec["url"] not in seen:
                    seen.add(rec["url"])
                    out.append(rec)
            if len(recs) < PAGE:
                break
    return out


# --- a posting's page ------------------------------------------------------------------------

_BODY_START = 'job-posting-detail-requirements'        # structured postings: education, experience, tasks, skills
_BODY_END = 'class="job-posting-detail-apply"'
_FREE_START = 'job-posting-details-body'                # postings with a free-text description instead
_FREE_END = 'job-posting-details-menu'
_MARKUP = re.compile(r"</?(?:p|ul|ol|li|strong|em|br|div|span|b|h\d)\b", re.IGNORECASE)
_AFTER_DESCRIPTION = re.compile(r"\n(?:Location|Work location|Salary|Who can apply|How to apply|Advertised until)\b"
                                r"|\s-\s(?:Location|Work location|Salary)\b")


_PARTNER = re.compile(r'View the full job posting on</span>\s*(?:<span[^>]*></span>)?\s*([^<]+?)\s*<', re.S)


def partner_note(page_html: str) -> str:
    """A posting Job Bank received from a partner site (Talent.com, Indeed ...) shows only an excerpt and a button to the
    partner's page. That page refuses automated readers (HTTP 403), so lockin does not follow it; the note tells the reader
    of the Feed row what to do instead."""
    m = _PARTNER.search(page_html or "")
    if not m:
        return ""
    name = _text(m.group(1))
    return (f"[Job Bank shows only an excerpt of this posting, which {name} supplied. Open the link and press "
            f"\"View the full job posting on {name}\" for all of it.]\n\n")


def parse_detail(page_html: str) -> str:
    return partner_note(page_html) + _parse_detail(page_html)


def _parse_detail(page_html: str) -> str:
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
