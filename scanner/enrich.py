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

    def get(self, url: str) -> tuple[str, str, list[str]] | None:
        if not self.dir:
            return None
        p = self._path(url)
        if p.is_file() and time.time() - p.stat().st_mtime < self.ttl:
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                return data["text"], data.get("company", ""), data["locations"]   # older entries lack locations
            except (ValueError, KeyError):
                return None
        return None

    def put(self, url: str, text: str, company: str = "", locations: list[str] | None = None) -> None:
        if self.dir and text:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._path(url).write_text(json.dumps({"text": text, "company": company, "locations": locations or []}),
                                       encoding="utf-8")


@dataclass
class FetchResult:
    status: str            # "ok" | "unsupported" | "not_found" | "error"
    text: str = ""
    detail: str = ""       # e.g. "http 403"
    company_name: str = ""
    locations: list[str] = field(default_factory=list)


def _log_failure(ats_name: str, url: str, exc: Exception) -> None:
    log.debug("JD fetch failed for %s: %s", url, exc)
    if ats_name not in _warned:
        _warned.add(ats_name)
        log.warning("JD fetch failing for %s (first error: %s: %s); further %s failures logged at debug level",
                    ats_name, type(exc).__name__, str(exc)[:160], ats_name)


def fetch_job(job: Job, client, cache: JDCache) -> FetchResult:
    """Fetch one job's JD. Never raises: failures become a status plus detail like 'http 403'."""
    ref = ats.locate(job)
    if ref is None:
        return FetchResult("unsupported", detail="unsupported ATS")
    cached = cache.get(job.url)
    if cached is not None:
        return FetchResult("ok", cached[0], company_name=cached[1], locations=cached[2])
    try:
        got = ats.fetch_full(ref, client)
        text, company, locations = got.text, got.company, got.locations
    except ats.NotFound:
        return FetchResult("not_found", detail="not found")
    except requests.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else 0
        if code == 404:
            return FetchResult("not_found", detail="http 404")
        _log_failure(ref.ats, job.url, exc)
        return FetchResult("error", detail=f"http {code}")
    except Exception as exc:  # network, JSON shape, ... must not abort the run
        _log_failure(ref.ats, job.url, exc)
        return FetchResult("error", detail=type(exc).__name__)
    if not text.strip():
        return FetchResult("error", detail="empty description")
    cache.put(job.url, text, company, locations)
    return FetchResult("ok", text, company_name=company, locations=locations)


def fetch_one(job: Job, client, cache: JDCache) -> tuple[str, str, str]:
    """(status, text, detail) view of fetch_job."""
    r = fetch_job(job, client, cache)
    return r.status, r.text, r.detail


def fetch_all(jobs: list[Job], client, cache: JDCache, workers: int) -> None:
    def work(job: Job):
        r = fetch_job(job, client, cache)
        job.jd_status, job.jd, job.jd_error = r.status, r.text, r.detail
        job.jd_locations = r.locations
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


def resolve_location(job: Job, filters: Filters) -> str | None:
    """Workday "N Locations": decide from the full list in the job detail.

    Returns a drop reason, or None to keep. Nothing resolvable (no JD, 403, no list) keeps the job and
    flags it `location_unresolved` so the report can list it.
    """
    if not job.location_pending:
        return None
    if not job.jd_locations:
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
    reason = resolve_location(job, filters)
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
    fetch_all(candidates + held, client, cache, jdcfg["workers"])
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
