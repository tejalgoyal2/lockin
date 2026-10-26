"""Company display names from the ATS itself (SPEC §11 #68).

The datasets only carry the ATS slug ("tjx", "cityofhamilton", "sandboxaq"). Each ATS publishes the employer's
own name somewhere:
  Greenhouse  GET boards-api.greenhouse.io/v1/boards/<slug>            -> name ("BitGo")
  Ashby       POST jobs.ashbyhq.com/api/non-user-graphql (hosted page) -> organization.name ("SandboxAQ")
  BambooHR    GET <sub>.bamboohr.com/careers/company-info             -> result.name ("City of Hamilton")
  Workday     GET <host>/<site> (career site HTML)                     -> og:title ("TJX Careers" -> "TJX"); often empty
Lever has no public name source. Results (found or not) are kept in state/company_names.json and re-checked after
`refresh_days`, so a daily run asks each employer at most once a month. Names are public; nothing personal is stored.
"""
import html
import json
import logging
import re
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from scanner.normalize import norm_text
from scanner.sources import ats

log = logging.getLogger(__name__)

_ASHBY_QUERY = ("query ApiOrganizationFromHostedJobsPageName($organizationHostedJobsPageName: String!, "
                "$searchContext: OrganizationSearchContext) { organization: organizationFromHostedJobsPageName("
                "organizationHostedJobsPageName: $organizationHostedJobsPageName, searchContext: $searchContext) "
                "{ name } }")
_OG_TITLE = re.compile(r'<meta[^>]*property="og:title"[^>]*content="([^"]*)"', re.IGNORECASE)
_NOISE = re.compile(r"(?i)^(?:careers?\s+(?:at|with)\s+|jobs?\s+(?:at|with)\s+|join\s+)|"
                    r"\s*[-|:–]?\s*(?:careers?(?:\s+site)?|jobs?|job\s+board|career\s+opportunities|opportunities)\s*$")


_LEGAL = re.compile(r"(?i)[\s,]+(?:inc|incorporated|corp|corporation|co|company|ltd|limited|llc|plc|ulc|lp)\.?$")


def clean(name: str) -> str:
    """'TJX Careers' -> 'TJX', 'Careers at Acme' -> 'Acme', 'Interac Corp.' -> 'Interac', 'SS&amp;amp;C' -> 'SS&C'.
    Empty when nothing is left."""
    text = name or ""
    for _ in range(3):                                 # some career sites escape twice
        text = html.unescape(text)
    text = text.strip()
    for _ in range(2):
        text = _NOISE.sub("", text).strip(" -|:–")
        text = _LEGAL.sub("", text).strip(" ,")
    return text if re.search(r"[A-Za-z]", text) else ""


def _matches_tenant(name: str, tenant: str) -> bool:
    """A Workday og:title is the employer only when it resembles the tenant ("Sun Life" / sunlife, "SS&C" / ssctech);
    "Canada" (bdo) and "Employment Channel" (acciona) are not."""
    a, b = norm_text(name), norm_text(tenant)
    return bool(a and b) and (a in b or b in a)


def source_key(url: str) -> tuple[str, str] | None:
    """(ATS, key) that identifies the employer's board: Greenhouse/Ashby/Lever slug, BambooHR subdomain, Workday
    host + site. None for an unsupported ATS."""
    name = ats.detect_ats(url)
    parts = urlsplit(url or "")
    segs = [s for s in parts.path.split("/") if s]
    if name == "Greenhouse" and "greenhouse.io" in parts.netloc and segs:
        return name, segs[0].lower()
    if name in ("Ashby", "Lever") and segs:
        return name, segs[0].lower()
    if name == "BambooHR":
        return name, parts.netloc.split(".")[0].lower()
    if name == "Workday":
        if segs and re.match(r"^[a-z]{2}(?:-[A-Za-z]{2})?$", segs[0]):
            segs = segs[1:]
        return (name, f"{parts.netloc.lower()}/{segs[0]}") if segs else None
    return None


def slug_from_url(url: str) -> str:
    """The employer slug a URL carries (Workday: the tenant), '' if none."""
    key = source_key(url)
    if not key:
        return ""
    ats_name, k = key
    return k.split(".")[0] if ats_name == "Workday" else k


def fetch_name(ats_name: str, key: str, client) -> str:
    """Ask the ATS for the employer's name; '' when it has none. Raises on network errors."""
    if ats_name == "Greenhouse":
        return clean(client.get_json(f"https://boards-api.greenhouse.io/v1/boards/{key}", ats_name).get("name", ""))
    if ats_name == "BambooHR":
        data = client.get_json(f"https://{key}.bamboohr.com/careers/company-info", ats_name)
        return clean((data.get("result") or {}).get("name", ""))
    if ats_name == "Ashby":
        data = client.post_json("https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiOrganizationFromHostedJobsPageName",
                                ats_name, json={"operationName": "ApiOrganizationFromHostedJobsPageName",
                                                "variables": {"organizationHostedJobsPageName": key,
                                                              "searchContext": "JobBoard"},
                                                "query": _ASHBY_QUERY})
        return clean((((data or {}).get("data") or {}).get("organization") or {}).get("name", ""))
    if ats_name == "Workday":
        resp = client.get(f"https://{key}", ats_name, headers={"Accept": "text/html"})
        m = _OG_TITLE.search(resp.text or "")
        name = clean(m.group(1)) if m else ""
        return name if _matches_tenant(name, key.split(".")[0]) else ""
    return ""


class NameStore:
    """state/company_names.json: {"<ATS>:<key>": {"name": "...", "checked": "YYYY-MM-DD"}}."""

    def __init__(self, path, refresh_days: int = 30):
        self.path, self.refresh_days = Path(path), refresh_days
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            self.data = {}

    def get(self, ats_name: str, key: str, today: date) -> str | None:
        """The stored name ('' = the ATS has none), or None when unknown or due for a re-check."""
        e = self.data.get(f"{ats_name}:{key}")
        if not e or date.fromisoformat(e["checked"]) < today - timedelta(days=self.refresh_days):
            return None
        return e["name"]

    def put(self, ats_name: str, key: str, name: str, today: date) -> None:
        self.data[f"{ats_name}:{key}"] = {"name": name, "checked": today.isoformat()}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(dict(sorted(self.data.items())), indent=1, ensure_ascii=False) + "\n",
                             encoding="utf-8")


def ats_name(url: str, store: NameStore, client, today: date) -> str:
    """The employer's own name for the board `url` belongs to ('' if the ATS has none or the request failed)."""
    key = source_key(url)
    if not key or key[0] == "Lever":
        return ""
    known = store.get(*key, today)
    if known is not None:
        return known
    try:
        name = fetch_name(*key, client)
    except Exception as exc:                       # a name is a nicety: never fail a run for it
        log.debug("company name lookup failed for %s: %s", key, exc)
        return ""
    store.put(*key, name, today)
    return name


def fill_names(jobs, overrides: dict[str, str], store: NameStore, client, today: date) -> int:
    """Give jobs without a readable name (no company_names.yaml entry, none from Simplify / the job) the ATS's
    name. Returns how many jobs got one."""
    done = 0
    for job in jobs:
        if overrides.get(norm_text(job.company)) or (job.company_name or "").strip():
            continue
        name = ats_name(job.url, store, client, today)
        if name:
            job.company_name = name
            done += 1
    return done
