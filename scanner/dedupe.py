"""Two-pass dedupe (SPEC §7): canonical URL, then fuzzy company + title within a city."""
import re
from collections.abc import Iterable
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit

from scanner.models import Job
from scanner.normalize import job_key, norm_location, norm_text

COMPANY_RATIO = 0.85
TITLE_JACCARD = 0.8
# Query params that identify the posting itself. Greenhouse-embedded career sites serve every
# job from one path (e.g. pinterestcareers.com/jobs/?gh_jid=123), so dropping these merges
# distinct jobs. Everything else (utm_*, gh_src, t, ...) is tracking noise and is stripped.
JOB_ID_PARAMS = frozenset({"gh_jid", "jid", "jobid", "job_id", "reqid", "req_id", "requisitionid"})
MIN_PREFIX_LEN = 3   # a company name must be at least this long to count as a prefix of another


def canonical_url(url: str) -> str:
    """Lowercase host, drop fragment, trailing slash and tracking query params.

    Job-identifying params (JOB_ID_PARAMS) are kept; see the note on that constant.
    """
    parts = urlsplit(url or "")
    if not parts.netloc:
        return url or ""
    path = parts.path.rstrip("/")
    keep = sorted((k, v) for k, v in parse_qsl(parts.query) if k.lower() in JOB_ID_PARAMS)
    query = f"?{urlencode(keep)}" if keep else ""
    return f"{parts.scheme}://{parts.netloc.lower()}{path}{query}"


def title_tokens(title: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (title or "").lower()))


def title_jaccard(a: str, b: str) -> float:
    ta, tb = title_tokens(a), title_tokens(b)
    if not ta and not tb:
        return 1.0
    return len(ta & tb) / len(ta | tb)


def company_match(a: str, b: str) -> bool:
    na, nb = norm_text(a), norm_text(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    short, long_ = sorted((na, nb), key=len)
    if len(short) >= MIN_PREFIX_LEN and long_.startswith(short):
        return True
    return SequenceMatcher(None, na, nb).ratio() >= COMPANY_RATIO


def _merge(a: Job, b: Job) -> Job:
    """Merge b into the preferred record. ATS rows win over Simplify (real URL/source);
    Simplify contributes the new-grad flag. Earliest first_seen is kept."""
    primary, other = (b, a) if (a.source == "Simplify" and b.source != "Simplify") else (a, b)
    primary.sources |= other.sources
    primary.new_grad = primary.new_grad or other.new_grad
    primary.weak_title = primary.weak_title and other.weak_title
    primary.company_name = primary.company_name or other.company_name
    primary.first_seen = min(primary.first_seen, other.first_seen)
    return primary


def _pass_url(jobs: list[Job]) -> tuple[list[Job], int]:
    by_url: dict[str, Job] = {}
    out: list[Job] = []
    merges = 0
    for job in jobs:
        key = canonical_url(job.url)
        cur = by_url.get(key) if job.url else None
        if cur is None:
            if job.url:
                by_url[key] = job
            out.append(job)
            continue
        merged = _merge(cur, job)
        merges += 1
        if merged is not cur:
            out[out.index(cur)] = merged
            by_url[key] = merged
    return out, merges


def _pass_fuzzy(jobs: list[Job]) -> tuple[list[Job], int]:
    """Same city (SPEC §7 key includes location) + similar company + similar title."""
    survivors: dict[str, list[Job]] = {}
    merges = 0
    for job in jobs:
        city = norm_location(job.location)
        bucket = survivors.setdefault(city, [])
        for i, cur in enumerate(bucket):
            if company_match(cur.company, job.company) and title_jaccard(cur.title, job.title) >= TITLE_JACCARD:
                bucket[i] = _merge(cur, job)
                merges += 1
                break
        else:
            bucket.append(job)
    return [j for bucket in survivors.values() for j in bucket], merges


def dedupe(jobs: Iterable[Job]) -> tuple[list[Job], dict[str, int]]:
    """Returns (jobs newest first, {'url': merges, 'fuzzy': merges})."""
    jobs = list(jobs)
    jobs, url_merges = _pass_url(jobs)
    jobs, fuzzy_merges = _pass_fuzzy(jobs)
    for job in jobs:
        job.key = job_key(job.company, job.title, job.location)
    jobs.sort(key=lambda j: j.first_seen, reverse=True)
    return jobs, {"url": url_merges, "fuzzy": fuzzy_merges}
