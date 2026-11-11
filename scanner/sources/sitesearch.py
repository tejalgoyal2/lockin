"""Boolean "site:" searches on the ATS sites: the recruiter's sourcing trick, turned round to source jobs.

`site:jobs.ashbyhq.com ("software engineer" OR "software developer") (Vancouver OR "British Columbia" OR Canada)` finds
postings on every employer's Ashby board at once, including the employers lockin has never heard of. Searching by hand
is fine; a script may not run Google's search page itself (google.com/robots.txt disallows /search for automated
clients, and Google's terms forbid automated access against it), so the searches go through a search API that is
built to be called by programs:

- **Brave Search API** (`BRAVE_SEARCH_API_KEY`): its own web index, `site:` and a past-week filter supported. Brave gives
  $5 of free credit a month (about 1,000 searches), so a run can afford `brave_per_run` searches.
- **SerpApi** (`SERPAPI_KEY`, the key Google Jobs uses too): Google's own results. The free plan's 250 searches a month
  are shared with Google Jobs, so a run asks only `serpapi_per_run` here.

With both keys, Brave runs the searches (more of them); with neither, the source is skipped with a note. The queries are
every `hosts` x `titles` pair with the `where` clause, taken in turn from run to run so all of them come round. Each
result that is a posting page on an ATS becomes a record; phase 2 reads the posting itself (JD, real location), so a
job the ATS lists only outside Canada is dropped there like any other.
"""
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from scanner.sources.googlealerts import company_from_url, split_title
from scanner.sources.googlejobs import terms_for_run

SOURCE = "Site Search"
BRAVE = "https://api.search.brave.com/res/v1/web/search"
SERPAPI = "https://serpapi.com/search.json"
_ELLIPSIS = re.compile(r"\s*(?:\.\.\.|…)\s*$")
# A posting page, not a board's index or a company page, on each ATS.
_POSTING = {
    "lever.co": re.compile(r"^/[^/]+/[0-9a-f-]{20,}"),
    "greenhouse.io": re.compile(r"/jobs/\d+"),
    "ashbyhq.com": re.compile(r"^/[^/]+/[0-9a-f-]{20,}"),
    "workable.com": re.compile(r"/j/[0-9A-F]{6,}", re.I),
    "myworkdayjobs.com": re.compile(r"/job/"),
    "bamboohr.com": re.compile(r"/careers/\d+"),
    "smartrecruiters.com": re.compile(r"^/[^/]+/\d{6,}"),
    "icims.com": re.compile(r"/jobs/\d+/"),
}


class SiteSearchError(Exception):
    """A search API failed (the message never carries the key)."""


def keys() -> tuple[str, str]:
    return os.environ.get("BRAVE_SEARCH_API_KEY", "").strip(), os.environ.get("SERPAPI_KEY", "").strip()


def queries(hosts: list[str], titles: list[str], where: str) -> list[str]:
    return [f"site:{h} {t} {where}".strip() for h in hosts for t in titles]


def is_posting(url: str) -> bool:
    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()
    for suffix, pat in _POSTING.items():
        if host == suffix or host.endswith("." + suffix):
            return bool(pat.search(parts.path))
    return False


def record(url: str, title: str, now: datetime) -> dict | None:
    if not is_posting(url):
        return None
    job_title, company = split_title(_ELLIPSIS.sub("", title or ""), url)
    if not job_title:
        return None
    return {"company": company_from_url(url) or company or "Site Search employer", "company_name": company,
            "title": job_title, "url": url.split("#")[0], "location": "Canada", "source": SOURCE,
            "first_seen": now.astimezone(timezone.utc)}


def parse_brave(answer: dict, now: datetime) -> list[dict]:
    hits = ((answer or {}).get("web") or {}).get("results") or []
    return [r for r in (record(h.get("url") or "", h.get("title") or "", now) for h in hits) if r]


def parse_serpapi(answer: dict, now: datetime) -> list[dict]:
    hits = (answer or {}).get("organic_results") or []
    return [r for r in (record(h.get("link") or "", h.get("title") or "", now) for h in hits) if r]


def _ask(client, url: str, label: str, **kwargs) -> dict:
    try:
        return client.get_json(url, SOURCE, **kwargs)
    except Exception as exc:             # the key is in the URL (SerpApi) or a header (Brave): never let it out
        raise SiteSearchError(f"{label}: {type(exc).__name__}"
                              + (f" (HTTP {exc.response.status_code})" if getattr(exc, "response", None) is not None else "")) from None


def fetch(client, hosts: list[str], titles: list[str], where: str, now: datetime, *, brave_per_run: int = 15,
          serpapi_per_run: int = 2) -> list[dict]:
    brave, serp = keys()
    if not (brave or serp):
        print("Site Search: neither BRAVE_SEARCH_API_KEY nor SERPAPI_KEY is set, source skipped")
        return []
    every = queries(hosts, titles, where)
    asked = terms_for_run(every, now, brave_per_run if brave else serpapi_per_run)
    print(f"Site Search: {len(asked)} of {len(every)} searches this run, through {'Brave' if brave else 'SerpApi'}")
    seen, out = set(), []
    for q in asked:
        if brave:
            answer = _ask(client, BRAVE, "Brave", params={"q": q, "count": 20, "freshness": "pw", "country": "CA"},
                          headers={"Accept": "application/json", "X-Subscription-Token": brave})
            recs = parse_brave(answer, now)
        else:
            answer = _ask(client, SERPAPI, "SerpApi", params={"engine": "google", "q": q, "google_domain": "google.ca",
                                                             "gl": "ca", "hl": "en", "tbs": "qdr:w", "api_key": serp})
            if answer.get("error") and "hasn't returned any results" not in str(answer["error"]):
                raise SiteSearchError(f"SerpApi error: {str(answer['error'])[:120]}")
            recs = parse_serpapi(answer, now)
        for rec in recs:
            if rec["url"] not in seen:
                seen.add(rec["url"])
                out.append(rec)
    return out
