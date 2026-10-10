"""Direct ATS fetchers for a single job's description text (SPEC §3c).

`locate(job)` maps a job to an `AtsRef` (which ATS, which endpoint); `fetch_jd(ref, client)`
returns plain text. Unsupported ATSes (Paylocity, iCIMS, Workable, ...) locate to None.
Endpoint shapes follow Feashliaa/job-board-aggregator and career-ops providers.
"""
import re
import threading
from dataclasses import dataclass, field

import requests
from urllib.parse import parse_qs, quote, unquote, urlsplit

from scanner.models import Job
from scanner.text import html_to_text

SUPPORTED = ("Greenhouse", "Lever", "Ashby", "Workday", "BambooHR")


class NotFound(Exception):
    """The posting no longer exists on the ATS."""


@dataclass(frozen=True)
class AtsRef:
    ats: str
    api_url: str            # endpoint to GET
    job_id: str = ""        # id to pick out of a board listing (Ashby)


_LOCALE = re.compile(r"^[a-z]{2}([-_][A-Za-z]{2})?$")


def detect_ats(url: str) -> str | None:
    host = urlsplit(url or "").netloc.lower()
    if "greenhouse.io" in host or "gh_jid=" in (url or ""):
        return "Greenhouse"
    if host.endswith("lever.co"):
        return "Lever"
    if host.endswith("ashbyhq.com"):
        return "Ashby"
    if host.endswith("myworkdayjobs.com"):
        return "Workday"
    if host.endswith("bamboohr.com"):
        return "BambooHR"
    return None


def _greenhouse(url: str, company: str) -> AtsRef | None:
    parts = urlsplit(url)
    segs = [s for s in parts.path.split("/") if s]
    jid = (parse_qs(parts.query).get("gh_jid") or [""])[0]
    slug = ""
    if "greenhouse.io" in parts.netloc and len(segs) >= 3 and segs[1] == "jobs":
        slug, jid = segs[0], segs[2]
    elif jid:
        slug = company  # custom career site embedding a Greenhouse board; Feashliaa company id = slug
    if not (slug and jid.isdigit()):
        return None
    return AtsRef("Greenhouse", f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{jid}")


def _lever(url: str) -> AtsRef | None:
    parts = urlsplit(url)
    segs = [s for s in parts.path.split("/") if s]
    if len(segs) < 2:
        return None
    api = "api.eu.lever.co" if ".eu." in parts.netloc else "api.lever.co"
    return AtsRef("Lever", f"https://{api}/v0/postings/{segs[0]}/{segs[1]}")


def _ashby(url: str) -> AtsRef | None:
    segs = [unquote(s) for s in urlsplit(url).path.split("/") if s]
    if len(segs) < 2:
        return None
    slug, jid = segs[0], segs[1]
    return AtsRef("Ashby", f"https://api.ashbyhq.com/posting-api/job-board/{quote(slug)}", job_id=jid)


def _workday(url: str) -> AtsRef | None:
    parts = urlsplit(url)
    m = re.match(r"^([^.]+)\.(wd\d+)\.myworkdayjobs\.com$", parts.netloc.lower())
    if not m:
        return None
    tenant = m.group(1)
    segs = [s for s in parts.path.split("/") if s]
    if segs and _LOCALE.match(segs[0]):
        segs = segs[1:]
    if "job" not in segs[1:]:
        return None
    i = segs.index("job", 1)
    site, rest = segs[0], segs[i:]
    if rest and rest[-1].lower() == "apply":
        rest = rest[:-1]
    return AtsRef("Workday", f"https://{parts.netloc}/wday/cxs/{tenant}/{site}/{'/'.join(rest)}")


def _bamboohr(url: str) -> AtsRef | None:
    parts = urlsplit(url)
    m = re.match(r"^/careers/(\d+)", parts.path)
    if not m or not parts.netloc.lower().endswith(".bamboohr.com"):
        return None
    return AtsRef("BambooHR", f"{parts.scheme}://{parts.netloc}/careers/{m.group(1)}/detail")


def locate(job: Job) -> AtsRef | None:
    ats = detect_ats(job.url)
    if ats is None or ats not in SUPPORTED:
        return None
    if ats == "Greenhouse":
        return _greenhouse(job.url, job.company)
    return {"Lever": _lever, "Ashby": _ashby, "Workday": _workday, "BambooHR": _bamboohr}[ats](job.url)


# --- response → text -------------------------------------------------------

def _greenhouse_text(data: dict) -> str:
    return html_to_text(data.get("content", ""), unescape_first=True)  # content is HTML-escaped


def _greenhouse_locations(data: dict) -> list[str]:
    names = [(data.get("location") or {}).get("name", ""), *((o or {}).get("name", "") for o in data.get("offices") or [])]
    return _dedupe_locations(names)


def _lever_locations(data: dict) -> list[str]:
    cats = data.get("categories") or {}
    return _dedupe_locations([cats.get("location", ""), *(cats.get("allLocations") or [])])


def _ashby_locations(job: dict) -> list[str]:
    secondary = [(x or {}).get("location", "") if isinstance(x, dict) else str(x) for x in job.get("secondaryLocations") or []]
    return _dedupe_locations([job.get("location", ""), *secondary])


def _bamboo_locations(data: dict) -> list[str]:
    loc = ((data.get("result") or {}).get("jobOpening") or {}).get("location") or {}
    parts = [loc.get("city", ""), loc.get("state", ""), loc.get("addressCountry", "")]
    return _dedupe_locations([", ".join(p for p in parts if p)])


def _dedupe_locations(names) -> list[str]:
    seen, out = set(), []
    for n in names:
        n = (n or "").strip()
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _lever_text(data: dict) -> str:
    chunks = [data.get("descriptionPlain") or html_to_text(data.get("description", ""))]
    for sec in data.get("lists") or []:
        body = html_to_text(sec.get("content", ""))
        chunks.append(f"{sec.get('text', '').strip()}\n{body}".strip())
    chunks.append(data.get("additionalPlain") or html_to_text(data.get("additional", "")))
    return "\n\n".join(c.strip() for c in chunks if c and c.strip())


def _workday_text(data: dict) -> str:
    return html_to_text((data.get("jobPostingInfo") or {}).get("jobDescription", ""))


def _bamboo_text(data: dict) -> str:
    opening = (data.get("result") or {}).get("jobOpening") or {}
    return html_to_text(opening.get("description", ""))


_ashby_boards: dict[str, dict] = {}
_ashby_lock = threading.Lock()


def _ashby_job(ref: AtsRef, client, cache: dict | None = None) -> dict:
    """Ashby has no single-job endpoint: fetch the company board once and pick the job."""
    cache = _ashby_boards if cache is None else cache
    with _ashby_lock:
        board = cache.get(ref.api_url)
        if board is None:
            board = cache[ref.api_url] = client.get_json(ref.api_url, "Ashby")
    for j in board.get("jobs", []):
        if j.get("id") == ref.job_id:
            return j
    raise NotFound(ref.job_id)


def _ashby_text(ref: AtsRef, client, cache: dict | None = None) -> str:
    j = _ashby_job(ref, client, cache)
    return j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml", ""))


@dataclass
class Detail:
    text: str
    company: str = ""
    locations: list[str] = field(default_factory=list)   # every location the ATS lists (Workday only)


def workday_career_site(api_url: str) -> str:
    """https://host/wday/cxs/<tenant>/<site>/job/... -> https://host/<site> (the tenant's career site)."""
    m = re.match(r"^(https://[^/]+)/wday/cxs/[^/]+/([^/]+)/", api_url)
    return f"{m.group(1)}/{m.group(2)}" if m else api_url


def _workday_locations(data: dict) -> list[str]:
    info = data.get("jobPostingInfo") or {}
    seen, out = set(), []
    for loc in [info.get("location"), *(info.get("additionalLocations") or [])]:
        loc = (loc or "").strip()
        if loc and loc not in seen:
            seen.add(loc)
            out.append(loc)
    return out


def _get_workday(ref: AtsRef, client) -> dict:
    """Plain polite request first. On 403 try once more like a browser (cookies from the career site,
    Origin / Referer). Still 403 means the posting is not being served (Workday errorCode S22)."""
    try:
        return client.get_json(ref.api_url, ref.ats)
    except requests.HTTPError as exc:
        if exc.response is None or exc.response.status_code != 403 or not hasattr(client, "get_json_browser"):
            raise
        return client.get_json_browser(ref.api_url, ref.ats, workday_career_site(ref.api_url))


def fetch_full(ref: AtsRef, client) -> Detail:
    """Fetch one job: description as plain text, readable company name ('' if none), locations.

    Only Greenhouse reports a clean brand name (`company_name`); Workday's hiringOrganization is
    often a legal entity ("Autodesk Canada Co.") and the others give none. Every ATS reports where the
    posting is: Workday lists all of them (`location` + `additionalLocations`), which resolves its
    "N Locations" rows, and the ATS's own location is authoritative over a Simplify row's looser list.
    Raises NotFound on 404.
    """
    if ref.ats == "Ashby":
        j = _ashby_job(ref, client)
        return Detail(j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml", "")), "", _ashby_locations(j))
    data = _get_workday(ref, client) if ref.ats == "Workday" else client.get_json(ref.api_url, ref.ats)
    text = {
        "Greenhouse": _greenhouse_text,
        "Lever": _lever_text,
        "Workday": _workday_text,
        "BambooHR": _bamboo_text,
    }[ref.ats](data)
    company = (data.get("company_name") or "").strip() if ref.ats == "Greenhouse" else ""
    locations = {"Workday": _workday_locations, "Greenhouse": _greenhouse_locations, "Lever": _lever_locations,
                 "BambooHR": _bamboo_locations}[ref.ats](data)
    return Detail(text, company, locations)


def fetch_details(ref: AtsRef, client) -> tuple[str, str]:
    """(description as plain text, readable company name or ''). See fetch_full."""
    d = fetch_full(ref, client)
    return d.text, d.company


def fetch_jd(ref: AtsRef, client) -> str:
    """Description text only (see fetch_details)."""
    return fetch_details(ref, client)[0]


# --- stale Workday ids ------------------------------------------------------------------

@dataclass
class Relocated:
    url: str                 # the live posting's public URL
    detail: Detail
    location: str            # the Canadian location that qualified it


def _words(title: str) -> str:
    """Search text for Workday: the title's words. Punctuation breaks its text search ("DevOps Engineer -
    New Grad (January 2027)" finds nothing, "DevOps Engineer New Grad January 2027" finds the job)."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", title or "")).strip()


def _same_title(a: str, b: str) -> bool:
    key = lambda t: re.sub(r"[^a-z0-9]+", "", (t or "").lower())      # normalised, parentheses kept
    return bool(key(a)) and key(a) == key(b)


def relocate_workday(ref: AtsRef, title: str, client, is_canadian) -> Relocated | None:
    """A Workday id that now answers S22 / 403 / 404 is usually a posting re-issued under a new requisition
    number. Search the tenant's jobs for the title; take an exact (normalised) title match whose location is
    Canadian (`is_canadian(text) -> bool`), fetch its detail and return its live URL. None if nothing matches."""
    m = re.match(r"^(https://[^/]+)/wday/cxs/([^/]+)/([^/]+)/", ref.api_url)
    if not m or not hasattr(client, "post_json"):
        return None
    host, tenant, site = m.groups()
    body = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": _words(title)}
    try:
        found = client.post_json(f"{host}/wday/cxs/{tenant}/{site}/jobs", ref.ats, json=body).get("jobPostings") or []
    except Exception:                        # the search is a best effort; the job stays "jd unavailable"
        return None
    for posting in found:
        if not _same_title(posting.get("title", ""), title) or not posting.get("externalPath"):
            continue
        live = AtsRef("Workday", f"{host}/wday/cxs/{tenant}/{site}{posting['externalPath']}")
        try:
            detail = fetch_full(live, client)
        except Exception:
            continue
        where = " ; ".join(detail.locations) or posting.get("locationsText", "")
        if is_canadian(where):
            return Relocated(f"{host}/{site}{posting['externalPath']}", detail, where)
    return None
