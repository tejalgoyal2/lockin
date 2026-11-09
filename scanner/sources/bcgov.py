"""BC Public Service (https://bcpublicservice.hua.hrsmart.com): the provincial government's own job board.

One server-rendered table lists every external posting (about 80 at a time): ministry, requisition number, title,
union, work option, location, date opened and close date. The page size can be set to 100 in the URL, so a run reads
one page. A posting's page carries the description in its "job-detail" block.

BC titles start with the classification ("IS 24 - Application Developer", "ISL 27R - Senior QA Analyst", "PS INT PRO -
Public Service Intern - Programming and Database"); the title filters see them as written.
"""
import html
import re
from datetime import datetime, timezone

from scanner.text import html_to_text

SOURCE = "BC Gov"
BASE = "https://bcpublicservice.hua.hrsmart.com"
PAGE_SIZE = 100
MAX_PAGES = 5
COMPANY = "BC Public Service"

_ROW = re.compile(r"<tr>(.*?)</tr>", re.S)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_LINK = re.compile(r'<a href="(/hr/ats/Posting/view/\d+)"[^>]*>(.*?)</a>', re.S)
_DATE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
_POSTAL = re.compile(r"\b[A-Z]\d[A-Z]\s?\d[A-Z]\d\b")


class BcGovError(Exception):
    """The job table or a posting page is not what it used to be."""


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment or ""))).strip()


def list_url(page: int) -> str:
    return (f"{BASE}/hr/ats/JobSearch/viewAll/jobSearchPaginationExternal_pageSize:{PAGE_SIZE}"
            f"/jobSearchPaginationExternal_page:{page}")


def where(cell: str) -> str:
    """"Victoria, BC, CA V9B 6X2<br />Langford, BC, CA<br />Multiple Locations, BC, CA (Primary)" ->
    "Victoria, BC; Langford, BC; Multiple Locations, BC": no postal code, no country, no "(Primary)"."""
    places = []
    for line in re.split(r"<br\s*/?>", cell or ""):
        line = _POSTAL.sub("", _text(line))
        line = re.sub(r"\(Primary\)", "", line)
        line = re.sub(r",\s*CA\b", "", line).strip(" ,")
        if line and line not in places:
            places.append(line)
    return "; ".join(places)


def parse_list(page_html: str) -> list[dict]:
    """The job table -> records. Raises BcGovError when the table has rows but none can be read."""
    rows, out = [], []
    for row in _ROW.findall(page_html or ""):
        link = _LINK.search(row)
        if not link:
            continue
        rows.append(row)
        cells = _CELL.findall(row)
        opened = _DATE.search(_text(cells[6])) if len(cells) > 6 else None
        title = _text(link.group(2))
        if not (title and opened):
            continue
        month, day, year = (int(g) for g in opened.groups())
        out.append({
            "company": "bcpublicservice", "company_name": COMPANY, "title": title,
            "location": where(cells[5]) if len(cells) > 5 else "", "url": BASE + link.group(1), "source": SOURCE,
            "first_seen": datetime(year, month, day, tzinfo=timezone.utc),
            "ministry": _text(cells[0]),
        })
    if rows and not out:
        raise BcGovError(f"{len(rows)} postings in the table, none readable: the markup changed")
    return out


def fetch(client) -> list[dict]:
    out, seen = [], set()
    for page in range(1, MAX_PAGES + 1):
        recs = parse_list(client.get(list_url(page), SOURCE).text)
        out += [r for r in recs if r["url"] not in seen]
        seen |= {r["url"] for r in recs}
        if len(recs) < PAGE_SIZE:
            break
    return out


_BODY_START = 'id="job-detail"'
_BODY_END = 'id="job_details_ats_requisition_category_id"'


def parse_detail(page_html: str) -> str:
    """A posting's page -> the "job-detail" block as plain text (title, classification, union, location, salary,
    description). Raises BcGovError when the block is not on the page."""
    start = (page_html or "").find(_BODY_START)
    if start < 0:
        raise BcGovError("no job-detail block on the page: the markup changed")
    start = page_html.rfind("<", 0, start)
    end = page_html.find(_BODY_END, start)
    return html_to_text(page_html[start:end if end > start else None])
