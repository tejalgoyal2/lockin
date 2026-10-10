"""Phase 2: fetch JD text, apply JD rules, score (SPEC §4, §5, §9)."""
import hashlib
import json
import logging
import re
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import requests

from scanner import jd_rules
from scanner.cluster import ClusterResolver
from scanner.filters import Filters
from scanner.models import Job
from scanner.normalize import job_key
from scanner.score import Scorer
from scanner.sources import ats

log = logging.getLogger(__name__)
_warned: set[str] = set()   # one warning per ATS; per-job failures go to debug

# Drop-reason names (the keys of EnrichResult.drops)
R_ENROLLMENT = "requires_enrollment"
R_CLEARANCE = "security_clearance"
R_US_AUTH = "us_work_authorization"
R_EXPERIENCE = "experience_3plus"
R_TWO_YEAR_INTERN = "two_years_excludes_internships"
R_TWO_YEAR_NO_FIT = "two_years_no_fit"
R_TWO_YEAR_LOW_FIT = "two_years_low_fit"
R_FRENCH = "requires_french"
R_QUEBEC_ONLY = "location_quebec_only"
R_NOT_CANADA = "location_not_canada"
R_UNRESOLVED_FOREIGN = "location_unresolved_url_not_canadian"
R_ATS_NOT_CANADA = "location_ats_not_canada"
R_WEAK = "weak_title_low_fit"
R_WEAK_NO_JD = "weak_title_no_jd"
R_WEAK_FEW_TERMS = "weak_title_few_terms"
R_STUDENT_NO_JD = "student_title_no_jd"
R_STUDENT = "student_title_not_rescued"


@dataclass
class EnrichResult:
    kept: list[Job] = field(default_factory=list)
    drops: Counter = field(default_factory=Counter)        # reason -> count (candidates + held)
    fetch: Counter = field(default_factory=Counter)        # "<ATS>:<status>" -> count
    forbidden: Counter = field(default_factory=Counter)    # host -> jobs whose JD fetch got HTTP 403
    forbidden_jobs: list[Job] = field(default_factory=list)  # the jobs behind `forbidden`
    held_total: int = 0
    rescued: int = 0


class JDCache:
    """On-disk cache of successful fetches (JD text + readable company name), keyed by URL."""

    def __init__(self, directory: str | Path | None, ttl_days: float):
        self.dir = Path(directory) if directory else None
        self.ttl = ttl_days * 86400

    def _path(self, url: str) -> Path:
        return self.dir / (hashlib.sha1(url.encode()).hexdigest() + ".json")

    def get(self, url: str) -> tuple[str, str, list[str], str] | None:
        if not self.dir:
            return None
        p = self._path(url)
        if p.is_file() and time.time() - p.stat().st_mtime < self.ttl:
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                return data["text"], data.get("company", ""), data["locations"], data.get("live_url", "")
            except (ValueError, KeyError):
                return None
        return None

    def put(self, url: str, text: str, company: str = "", locations: list[str] | None = None,
            live_url: str = "") -> None:
        if self.dir and text:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._path(url).write_text(json.dumps({"text": text, "company": company, "locations": locations or [],
                                                   "live_url": live_url}), encoding="utf-8")


@dataclass
class FetchResult:
    status: str            # "ok" | "unsupported" | "not_found" | "error"
    text: str = ""
    detail: str = ""       # e.g. "http 403"
    company_name: str = ""
    locations: list[str] = field(default_factory=list)
    live_url: str = ""       # a Workday id that stopped resolving was re-found under this URL
    relocation: str = ""     # "matched" | "no_match" | "" (not tried)


def _log_failure(ats_name: str, url: str, exc: Exception) -> None:
    log.debug("JD fetch failed for %s: %s", url, exc)
    if ats_name not in _warned:
        _warned.add(ats_name)
        log.warning("JD fetch failing for %s (first error: %s: %s); further %s failures logged at debug level",
                    ats_name, type(exc).__name__, str(exc)[:160], ats_name)


def _workday_gone(exc: Exception) -> bool:
    """Workday answers 403 (errorCode S22) or 404 for an id it no longer serves."""
    if isinstance(exc, ats.NotFound):
        return True
    return isinstance(exc, requests.HTTPError) and exc.response is not None and exc.response.status_code in (403, 404)


def _may_be_canadian(job: Job, filters: Filters) -> bool:
    """Worth searching for a live twin: the job's own location, or for a "N Locations" row the URL's, is Canadian."""
    where = url_location(job.url) if job.location_pending else job.location
    return filters.location_status(where)[0] in ("ok", "quebec_only")


def fetch_job(job: Job, client, cache: JDCache, filters: Filters | None = None) -> FetchResult:
    """Fetch one job's JD. Never raises: failures become a status plus detail like 'http 403'.

    With `filters`, a Workday id that answers 403 / 404 gets one search of the tenant for the same title: an
    exact title match in a Canadian location replaces the dead id (FetchResult.live_url)."""
    ref = ats.locate(job)
    if ref is None:
        return FetchResult("unsupported", detail="unsupported ATS")
    cached = cache.get(job.url)
    if cached is not None:
        return FetchResult("ok", cached[0], company_name=cached[1], locations=cached[2], live_url=cached[3],
                           relocation="matched" if cached[3] else "")
    relocation = ""
    try:
        got = ats.fetch_full(ref, client)
        text, company, locations = got.text, got.company, got.locations
    except Exception as exc:
        if filters is not None and ref.ats == "Workday" and _workday_gone(exc) and _may_be_canadian(job, filters):
            twin = ats.relocate_workday(ref, job.title, client,
                                        lambda where: filters.location_status(where)[0] in ("ok", "quebec_only"))
            if twin is not None:
                text, company, locations = twin.detail.text, twin.detail.company, twin.detail.locations
                if not text.strip():
                    return FetchResult("error", detail="empty description")
                cache.put(job.url, text, company, locations, twin.url)
                return FetchResult("ok", text, company_name=company, locations=locations, live_url=twin.url,
                                   relocation="matched")
            relocation = "no_match"
        r = _failure(ref, job, exc)
        r.relocation = relocation
        return r
    if not text.strip():
        return FetchResult("error", detail="empty description")
    cache.put(job.url, text, company, locations)
    return FetchResult("ok", text, company_name=company, locations=locations)


def _failure(ref, job: Job, exc: Exception) -> FetchResult:
    if isinstance(exc, ats.NotFound):
        return FetchResult("not_found", detail="not found")
    if isinstance(exc, requests.HTTPError):
        code = exc.response.status_code if exc.response is not None else 0
        if code == 404:
            return FetchResult("not_found", detail="http 404")
        _log_failure(ref.ats, job.url, exc)
        return FetchResult("error", detail=f"http {code}")
    _log_failure(ref.ats, job.url, exc)          # network, JSON shape, ... must not abort the run
    return FetchResult("error", detail=type(exc).__name__)


def fetch_one(job: Job, client, cache: JDCache) -> tuple[str, str, str]:
    """(status, text, detail) view of fetch_job."""
    r = fetch_job(job, client, cache)
    return r.status, r.text, r.detail


def fetch_all(jobs: list[Job], client, cache: JDCache, workers: int, filters: Filters | None = None) -> None:
    def work(job: Job):
        r = fetch_job(job, client, cache, filters)
        job.jd_status, job.jd, job.jd_error = r.status, r.text, r.detail
        job.jd_locations, job.relocation = r.locations, r.relocation
        if r.live_url:
            job.url = r.live_url
        job.company_name = job.company_name or r.company_name

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(work, jobs))


def _score(job: Job, cfg: dict, boost_re: re.Pattern) -> float:
    sc = cfg["scoring"]
    total = (job.fit_pct or 0.0) + min(len(job.matched), sc["matched_bonus_cap"]) * sc["matched_bonus_per_skill"]
    if "new grad" in job.signals or "recent graduate" in job.signals:
        total += sc["boost"]["new_grad"]
    if job.new_grad or "Simplify" in job.sources:
        total += sc["boost"]["simplify"]
    if boost_re.search(job.location) or re.search(r"\bremote\b", job.title, re.IGNORECASE):
        total += sc["boost"]["bc_or_remote"]
    if job.jd_status != "ok":
        total -= sc["penalty"]["no_jd"]
    return round(total, 1)


def url_location(url: str) -> str:
    """The location slug in a Workday URL (.../job/Toronto-ON-CAN/Title_R1 -> "Toronto ON CAN"): the job's
    primary location, the only one visible when the detail cannot be read."""
    m = re.search(r"/job/([^/]+)/", url or "")
    text = re.sub(r"[-_]+", " ", m.group(1)).strip() if m else ""
    return re.sub(r"(?<=\w) (ON|BC|AB|QC|MB|SK|NS|NB)\b", r", \1", text)      # "Guelph ON" -> "Guelph, ON"


def resolve_location(job: Job, filters: Filters, cfg: dict | None = None) -> str | None:
    """Workday "N Locations": decide from the full list in the job detail.

    Returns a drop reason, or None to keep. When the list cannot be read (no JD, 403, empty) the job is
    kept and flagged `location_unresolved` so the report lists it, unless the primary location in its URL
    is not Canadian: nothing then points to Canada, and keeping it would fill the Feed with US postings
    whose detail Workday does not serve. `location.keep_unresolved_with_foreign_url: true` restores
    "always keep".
    """
    if not job.location_pending:
        return _ats_location(job, filters)
    if not job.jd_locations:
        keep_all = ((cfg or {}).get("location") or {}).get("keep_unresolved_with_foreign_url", False)
        hint = filters.location_status(url_location(job.url))[0]
        if hint in ("not_canada", "unresolved") and not keep_all:
            job.location_dropped = True
            return R_UNRESOLVED_FOREIGN
        job.location_unresolved = True
        return None
    status, part = filters.location_status(" ; ".join(job.jd_locations))
    if status in ("not_canada", "unresolved"):
        job.location_dropped = True
        return R_NOT_CANADA
    if status == "quebec_only":
        job.location_dropped = True
        return R_QUEBEC_ONLY
    job.location, job.location_pending = part, False
    job.key = job_key(job.company, job.title, job.location)
    return None


_GENERIC_LOCATION = re.compile(r"^\W*(remote|hybrid|anywhere|global|worldwide|multiple|various|n/?a|flexible|tbd)\b",
                               re.IGNORECASE)


def _ats_location(job: Job, filters: Filters) -> str | None:
    """A Simplify row lists the locations of a whole programme ("Vancouver, BC; Dublin, Ireland; United States")
    while the ATS posting it links to has one, so the ATS wins: if it names places and none is Canadian, the
    job is not a Canadian job. Vague ATS locations ("Remote", "Hybrid") say nothing and are ignored."""
    if "Simplify" not in job.sources or not job.jd_locations:
        return None
    named = [loc for loc in job.jd_locations if not _GENERIC_LOCATION.search(loc)]
    if not named:
        return None
    status = filters.location_status(" ; ".join(named))[0]
    if status == "not_canada":
        job.location_dropped = True
        return R_ATS_NOT_CANADA
    if status == "quebec_only":
        job.location_dropped = True
        return R_QUEBEC_ONLY
    return None


def two_year_reason(exp: jd_rules.Experience, fit_pct: float | None, jdcfg: dict) -> str | None:
    """A 2-year requirement is kept only with a strong, evidenced fit and when internships count."""
    if exp.excludes_internships:
        return R_TWO_YEAR_INTERN
    if fit_pct is None:
        return R_TWO_YEAR_NO_FIT
    if fit_pct / 100 < jdcfg["two_year_min_fit"]:
        return R_TWO_YEAR_LOW_FIT
    return None


def apply_rules(job: Job, cfg: dict, filters: Filters, scorer: Scorer,
                clusters: ClusterResolver | None = None) -> str | None:
    """Fill phase-2 fields on `job`; return a drop reason, or None to keep it."""
    clusters = clusters or ClusterResolver(cfg)
    job.cluster = clusters.resolve(job.title, None)   # overwritten below when a JD adds terms
    reason = resolve_location(job, filters, cfg)
    if reason:
        return reason
    jdcfg = cfg["jd"]
    if job.jd_status == "ok":
        jd = job.jd
        exp = jd_rules.parse_experience(jd)
        fit = scorer.evaluate(jd, cfg["scoring"]["min_terms_for_fit"])
        job.fit_pct, job.matched, job.gaps = fit.fit_pct, fit.matched, fit.gaps
        job.cluster = clusters.resolve(job.title, fit.cluster)
        job.signals = jd_rules.detect_signals(job.title, jd, exp)
        if fit.fit_pct is None:
            job.signals.append(jd_rules.LOW_SIGNAL)
        if cfg.get("language", {}).get("drop_french_required_jd", True) and jd_rules.requires_french(jd):
            return R_FRENCH
        if jd_rules.requires_enrollment(jd):
            return R_ENROLLMENT
        if jd_rules.requires_clearance(jd):
            return R_CLEARANCE
        if jd_rules.requires_us_authorization(jd) and filters.canadian_location(job.location) is None:
            return R_US_AUTH
        if exp.min_years is not None and exp.min_years >= jdcfg["min_years_drop"]:
            return R_EXPERIENCE
        if exp.min_years is not None and exp.min_years >= 2:
            reason = two_year_reason(exp, fit.fit_pct, jdcfg)
            if reason:
                return reason
            job.signals.append(jdcfg["two_year_signal"])
        if job.weak_title:
            # A generic title must be vouched for by the JD: >= min_terms distinct skill / gap terms, and a real fit.
            if fit.n_core < cfg["scoring"]["min_terms_for_fit"]:
                return R_WEAK_FEW_TERMS
            if not job.matched or (job.fit_pct or 0) < cfg["scoring"]["weak_title_min_fit"]:
                return R_WEAK
    else:
        # No JD: a strong title stays (with the penalty); a weak one has nothing to vouch for it.
        if job.weak_title:
            return R_WEAK_NO_JD
        job.signals = jd_rules.detect_signals(job.title, "", jd_rules.Experience(None))
        job.signals.append(jd_rules.JD_UNAVAILABLE)
    return None


def enrich(candidates: list[Job], held: list[Job], cfg: dict, filters: Filters, scorer: Scorer,
           client, *, cache: JDCache | None = None,
           progress: Callable[[str], None] = lambda _msg: None) -> EnrichResult:
    jdcfg = cfg["jd"]
    cache = cache or JDCache(jdcfg.get("cache_dir"), jdcfg.get("cache_ttl_days", 7))
    held = held if jdcfg.get("rescue_student_titles", True) else []
    res = EnrichResult(held_total=len(held))
    boost_re = re.compile(cfg["scoring"]["bc_or_remote_pattern"], re.IGNORECASE)
    clusters = ClusterResolver(cfg)

    progress(f"fetching JDs for {len(candidates)} candidates + {len(held)} student-titled jobs")
    fetch_all(candidates + held, client, cache, jdcfg["workers"], filters)
    for job in candidates + held:
        res.fetch[f"{job.source}:{job.jd_status}"] += 1
        if job.jd_error == "http 403":
            res.forbidden[urlsplit(job.url).netloc] += 1
            res.forbidden_jobs.append(job)

    for job in candidates:
        reason = apply_rules(job, cfg, filters, scorer, clusters)
        if reason:
            res.drops[reason] += 1
            continue
        job.score = _score(job, cfg, boost_re)
        res.kept.append(job)

    for job in held:
        if job.jd_status != "ok":
            res.drops[R_STUDENT_NO_JD] += 1
            continue
        if jd_rules.requires_enrollment(job.jd):
            res.drops[R_ENROLLMENT] += 1
            continue
        if not jd_rules.welcomes_recent_grads(job.jd):
            res.drops[R_STUDENT] += 1
            continue
        reason = apply_rules(job, cfg, filters, scorer, clusters)
        if reason:
            res.drops[reason] += 1
            continue
        res.rescued += 1
        job.score = _score(job, cfg, boost_re)
        res.kept.append(job)

    res.kept.sort(key=lambda j: (-j.score, -j.first_seen.timestamp()))
    return res


def fit_distribution(jobs: list[Job]) -> list[tuple[str, int]]:
    """Histogram of Fit % in 20-point buckets plus 'no JD / no terms'."""
    buckets = [("0-19", 0), ("20-39", 0), ("40-59", 0), ("60-79", 0), ("80-100", 0)]
    counts = [0] * 5
    none = 0
    for j in jobs:
        if j.fit_pct is None:
            none += 1
        else:
            counts[min(int(j.fit_pct // 20), 4)] += 1
    return [(label, c) for (label, _), c in zip(buckets, counts)] + [("n/a (no JD or no tech terms)", none)]
