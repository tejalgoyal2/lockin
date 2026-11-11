"""Google for Jobs (the job panel Google shows for "software developer jobs vancouver"), read through SerpApi.

Google for Jobs is the index every employer and job board feeds: companies mark their postings up with schema.org
JobPosting (and many push them through Google's Indexing API), and LinkedIn, Indeed, Glassdoor, ZipRecruiter, Talent.com
and the ATS boards all appear there. lockin cannot read LinkedIn or Indeed itself (they refuse automated readers), and it
does not scrape Google either; SerpApi (https://serpapi.com) is a paid service with a free plan (250 searches a month)
that answers Google for Jobs queries as JSON, which is the honest route in.

The key comes from the environment (`SERPAPI_KEY`, a GitHub Actions secret); without it the source is skipped with a
note. The free plan pays for ~4 searches a run at two runs a day, so each run asks `queries_per_run` of the configured
terms, the next ones in turn (all of them come round every few days), and a term's answer is 10 postings, newest first.

Each posting carries its full description, so the Feed row has the JD even when the link is LinkedIn. The link is the
employer's own ATS when Google lists one lockin reads (the JD and locations then come from there and the posting dedupes
with the same job from the other sources), otherwise the first apply link that is not an aggregator, otherwise Google's.
"""
import html
import os
import re
from datetime import datetime
from urllib.parse import urlsplit

from scanner.sources.listing import parse_age

SOURCE = "Google Jobs"
API = "https://serpapi.com/search.json"
QUERIES_PER_RUN = 4
_AGGREGATOR = re.compile(
    r"(?:^|\.)(?:linkedin|indeed|glassdoor|ziprecruiter|monster|talent|simplyhired|jooble|workopolis|careerjet|jobillico|"
    r"eluta|adzuna|bebee|learn4good|dice|wellfound|jobrapido|lensa|jobleads|whatjobs|jobted|teal|salary|tiktok|"
    r"recruit\.net|jobsora|careerbuilder|snagajob|grabjobs|myjobmag|joblist|jobgether|hiringcafe|built ?in)\.",
    re.IGNORECASE)


class GoogleJobsError(Exception):
    """A request to SerpApi failed (the message never carries the key)."""


def api_key() -> str:
    return os.environ.get("SERPAPI_KEY", "").strip()


def terms_for_run(terms: list[str], now: datetime, per_run: int) -> list[str]:
    """`per_run` terms, a different slice each run (two runs a day): over a few days every term is asked."""
    if not terms or per_run <= 0:
        return []
    slot = now.toordinal() * 2 + (1 if now.hour >= 20 else 0)
    start = (slot * per_run) % len(terms)
    return [terms[(start + i) % len(terms)] for i in range(min(per_run, len(terms)))]


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def pick_link(job: dict, prefer) -> str:
    """The employer's ATS link when Google lists one lockin reads, else the first non-aggregator link, else any."""
    links = [o.get("link") for o in (job.get("apply_options") or []) if isinstance(o, dict) and o.get("link")]
    for link in links:
        if prefer(link):
            return link
    for link in links:
        if not _AGGREGATOR.search(_host(link)):
            return link
    return (links[0] if links else "") or job.get("share_link") or ""


def description(job: dict) -> str:
    """The description, then the highlights Google pulled out of it (Qualifications, Responsibilities, Benefits)."""
    parts = [html.unescape(job.get("description") or "").strip()]
    for block in job.get("job_highlights") or []:
        items = [html.unescape(i).strip() for i in (block.get("items") or []) if isinstance(i, str) and i.strip()]
        if items:
            parts.append((block.get("title") or "").strip() + "\n" + "\n".join(f"- {i}" for i in items))
    return "\n\n".join(p for p in parts if p)


def posted(job: dict, now: datetime) -> datetime:
    """"3 days ago" from detected_extensions or the raw extensions; a posting that names no age counts as new
    (seen.json shows it once)."""
    ext = job.get("detected_extensions") or {}
    for text in [ext.get("posted_at") or "", *(e for e in (job.get("extensions") or []) if isinstance(e, str))]:
        when = parse_age(text, now) if "ago" in text.lower() or text.lower() in ("today", "yesterday") else None
        if when:
            return when
    return now


def parse(answer: dict, now: datetime, prefer=lambda url: False) -> list[dict]:
    out = []
    for j in (answer or {}).get("jobs_results") or []:
        title, url = html.unescape(j.get("title") or "").strip(), pick_link(j, prefer)
        if not (title and url):
            continue
        name = html.unescape(j.get("company_name") or "").strip()
        out.append({"company": name or "Google Jobs employer", "company_name": name, "title": title, "url": url,
                    "location": html.unescape(j.get("location") or "").strip(), "source": SOURCE,
                    "first_seen": posted(j, now), "jd": description(j), "via": (j.get("via") or "").strip()})
    return out


def fetch(client, terms: list[str], location: str, now: datetime, *, per_run: int = QUERIES_PER_RUN,
          prefer=lambda url: False) -> list[dict]:
    key = api_key()
    if not key:
        print("Google Jobs: SERPAPI_KEY not set, source skipped")
        return []
    seen, out = set(), []
    for term in terms_for_run(terms, now, per_run):
        params = {"engine": "google_jobs", "q": term, "location": location, "google_domain": "google.ca", "gl": "ca",
                  "hl": "en", "api_key": key}
        try:
            answer = client.get_json(API, SOURCE, params=params)
        except Exception as exc:           # an HTTP error quotes the URL, and the key is in it: never let it out
            raise GoogleJobsError(f"{type(exc).__name__} for {term!r}"
                                  + (f" (HTTP {exc.response.status_code})" if getattr(exc, "response", None) is not None else "")) from None
        if (answer or {}).get("error") and not (answer or {}).get("jobs_results"):
            if "hasn't returned any results" in str(answer["error"]):
                continue
            raise GoogleJobsError(f"SerpApi error for {term!r}: {str(answer['error'])[:120]}")
        for rec in parse(answer, now, prefer):
            if rec["url"] not in seen:
                seen.add(rec["url"])
                out.append(rec)
    return out
