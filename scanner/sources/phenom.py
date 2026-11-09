"""Phenom People career sites (jobs.bell.ca, careers.aircanada.com, careers.opentext.com ...).

Many large employers run their careers site on Phenom. The site's own search box calls a public JSON endpoint,
`POST https://<host>/widgets`, which answers with the postings (title, location, posted date, the full description).
A watchlist entry names the site and its locale path: `{ats: phenom, slug: jobs.bell.ca/ca/en}`. A posting's page is
`https://<host>/<locale>/job/<id>`, and the page carries the same data in its `phApp.ddo = {...}` script, which is
where the job description of a candidate is read from.
"""
import json
import re
import time
from datetime import datetime

from scanner.normalize import parse_iso
from scanner.text import html_to_text

PAGE = 40                 # postings per request (the endpoint accepts at least 40)
MAX_PAGES = 25            # 1,000 postings per site
BOARD_SECONDS = 150       # newest first: what is read in this time is what matters
_SLUG = re.compile(r"^(?P<host>[A-Za-z0-9.-]+)/(?P<country>[a-z]{2})/(?P<lang>[a-z]{2})$")
_JOB_PATH = re.compile(r"^/[a-z]{2}/[a-z]{2}/job/[A-Za-z0-9_-]+")


class PhenomError(Exception):
    """The endpoint or a posting page is not what it used to be."""


def split_slug(slug: str) -> tuple[str, str, str]:
    """"jobs.bell.ca/ca/en" -> ("jobs.bell.ca", "ca/en", "en_ca")."""
    m = _SLUG.match(slug or "")
    if not m:
        raise PhenomError(f"phenom slug must look like <host>/<country>/<language>, e.g. jobs.bell.ca/ca/en, got {slug!r}")
    return m.group("host"), f"{m.group('country')}/{m.group('lang')}", f"{m.group('lang')}_{m.group('country')}"


def is_job_url(url_path: str) -> bool:
    return bool(_JOB_PATH.match(url_path or ""))


def search_body(slug: str, offset: int, size: int = PAGE) -> dict:
    _, _, lang = split_slug(slug)
    return {"lang": lang, "deviceType": "desktop", "country": lang.split("_")[1], "pageName": "search-results",
            "ddoKey": "refineSearch", "sortBy": "Most recent", "subsearch": "", "from": offset, "jobs": True,
            "counts": False, "all_fields": ["category", "country", "state", "city", "type"], "size": size,
            "clearAll": False, "jdsource": "facets", "isSliderEnable": False, "pageId": "page11", "siteType": "external",
            "keywords": "", "global": True, "selected_fields": {},
            "sort": {"order": "desc", "field": "postedDate"}, "locationData": {}}


def _when(job: dict) -> datetime | None:
    for field in ("postedDate", "dateCreated"):
        try:
            if job.get(field):
                return parse_iso(str(job[field]).replace("+0000", "+00:00"))
        except ValueError:
            continue
    return None


def parse_search(slug: str, name: str, answer: dict) -> tuple[list[dict], int]:
    """(records, total hits). Raises PhenomError when the answer has no search block at all."""
    block = (answer or {}).get("refineSearch")
    if not isinstance(block, dict):
        raise PhenomError("no refineSearch block in the answer: the endpoint changed")
    host, path, _ = split_slug(slug)
    out = []
    for j in (block.get("data") or {}).get("jobs") or []:
        job_id, title = j.get("jobId") or j.get("reqId"), j.get("title")
        if not (job_id and title):
            continue
        places = [p for p in map(_place, j.get("multi_location") or []) if p] or [j.get("location") or ""]
        out.append({"company": slug, "company_name": name, "title": title, "url": f"https://{host}/{path}/job/{job_id}",
                    "location": " ; ".join(dict.fromkeys(places)), "source": "Phenom", "first_seen": _when(j)})
    return out, int(block.get("totalHits") or 0)


def fetch_board(slug: str, name: str, client) -> list[dict]:
    host, _, _ = split_slug(slug)
    url = f"https://{host}/widgets"
    out: list[dict] = []
    started = time.monotonic()
    for page in range(MAX_PAGES):
        if time.monotonic() - started > BOARD_SECONDS:
            break
        recs, total = parse_search(slug, name, client.post_json(url, "Phenom", json=search_body(slug, page * PAGE)))
        out += recs
        if not recs or len(out) >= total:
            break
    return out


# --- a posting's page ---------------------------------------------------------------------------

def _ddo(page_html: str) -> dict:
    start = (page_html or "").find("phApp.ddo = ")
    if start < 0:
        raise PhenomError("no phApp.ddo data on the page: the markup changed")
    try:
        data, _ = json.JSONDecoder().raw_decode(page_html[start + len("phApp.ddo = "):])
    except ValueError as exc:
        raise PhenomError(f"phApp.ddo is not JSON: {exc}") from None
    return data


def _place(entry) -> str:
    """A location is a string in the search answer and a dict ({"location": "Verdun, Quebec", "city": ...}) on the page."""
    return str((entry.get("location") or entry.get("cityState") or "") if isinstance(entry, dict) else entry or "")


def parse_detail(page_html: str) -> tuple[str, list[str]]:
    """A posting's page -> (description as plain text, locations)."""
    job = ((_ddo(page_html).get("jobDetail") or {}).get("data") or {}).get("job")
    if not job:
        raise PhenomError("no job in phApp.ddo")
    places = [_place(p) for p in (job.get("multi_location") or [])] or [job.get("location") or ""]
    return html_to_text(job.get("description") or ""), [p for p in dict.fromkeys(places) if p]
