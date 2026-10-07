"""Direct ATS fetchers for a single job's description text (SPEC §3c).

`locate(job)` maps a job to an `AtsRef` (which ATS, which endpoint); `fetch_jd(ref, client)`
returns plain text. Unsupported ATSes (Paylocity, iCIMS, Workable, ...) locate to None.
Endpoint shapes follow Feashliaa/job-board-aggregator and career-ops providers.
"""
import re
import threading
from dataclasses import dataclass
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


def _ashby_text(ref: AtsRef, client, cache: dict | None = None) -> str:
    """Ashby has no single-job endpoint: fetch the company board once and pick the job."""
    cache = _ashby_boards if cache is None else cache
    with _ashby_lock:
        board = cache.get(ref.api_url)
        if board is None:
            board = cache[ref.api_url] = client.get_json(ref.api_url, "Ashby")
    for j in board.get("jobs", []):
        if j.get("id") == ref.job_id:
            return j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml", ""))
    raise NotFound(ref.job_id)


def fetch_details(ref: AtsRef, client) -> tuple[str, str]:
    """Fetch one job: (description as plain text, readable company name or '').

    Only Greenhouse reports a clean brand name (`company_name`); Workday's hiringOrganization is
    often a legal entity ("Autodesk Canada Co.") and the others give none. Raises NotFound on 404.
    """
    if ref.ats == "Ashby":
        return _ashby_text(ref, client), ""
    data = client.get_json(ref.api_url, ref.ats)
    text = {
        "Greenhouse": _greenhouse_text,
        "Lever": _lever_text,
        "Workday": _workday_text,
        "BambooHR": _bamboo_text,
    }[ref.ats](data)
    return text, (data.get("company_name") or "").strip() if ref.ats == "Greenhouse" else ""


def fetch_jd(ref: AtsRef, client) -> str:
    """Description text only (see fetch_details)."""
    return fetch_details(ref, client)[0]
