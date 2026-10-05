"""Phase 2: fetch JD text, apply JD rules, score (SPEC §4, §5, §9)."""
import hashlib
import logging
import re
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import requests

from scanner import jd_rules
from scanner.filters import Filters
from scanner.models import Job
from scanner.score import Scorer
from scanner.sources import ats

log = logging.getLogger(__name__)
_warned: set[str] = set()   # one warning per ATS; per-job failures go to debug

# Drop-reason names (the keys of EnrichResult.drops)
R_ENROLLMENT = "requires_enrollment"
R_CLEARANCE = "security_clearance"
R_US_AUTH = "us_work_authorization"
R_EXPERIENCE = "experience_3plus"
R_WEAK = "weak_title_low_fit"
R_STUDENT_NO_JD = "student_title_no_jd"
R_STUDENT = "student_title_not_rescued"


@dataclass
class EnrichResult:
    kept: list[Job] = field(default_factory=list)
    drops: Counter = field(default_factory=Counter)        # reason -> count (candidates + held)
    fetch: Counter = field(default_factory=Counter)        # "<ATS>:<status>" -> count
    held_total: int = 0
    rescued: int = 0


class JDCache:
    """On-disk cache of successfully fetched JD text, keyed by URL."""

    def __init__(self, directory: str | Path | None, ttl_days: float):
        self.dir = Path(directory) if directory else None
        self.ttl = ttl_days * 86400

    def _path(self, url: str) -> Path:
        return self.dir / (hashlib.sha1(url.encode()).hexdigest() + ".txt")

    def get(self, url: str) -> str | None:
        if not self.dir:
            return None
        p = self._path(url)
        if p.is_file() and time.time() - p.stat().st_mtime < self.ttl:
            return p.read_text(encoding="utf-8")
        return None

    def put(self, url: str, text: str) -> None:
        if self.dir and text:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._path(url).write_text(text, encoding="utf-8")


def _log_failure(ats_name: str, url: str, exc: Exception) -> None:
    log.debug("JD fetch failed for %s: %s", url, exc)
    if ats_name not in _warned:
        _warned.add(ats_name)
        log.warning("JD fetch failing for %s (first error: %s: %s); further %s failures logged at debug level",
                    ats_name, type(exc).__name__, str(exc)[:160], ats_name)


def fetch_one(job: Job, client, cache: JDCache) -> tuple[str, str]:
    """Return (status, text). Never raises: failures become a status."""
    ref = ats.locate(job)
    if ref is None:
        return "unsupported", ""
    cached = cache.get(job.url)
    if cached is not None:
        return "ok", cached
    try:
        text = ats.fetch_jd(ref, client)
    except (ats.NotFound, requests.HTTPError) as exc:
        if isinstance(exc, requests.HTTPError) and (exc.response is None or exc.response.status_code != 404):
            _log_failure(ref.ats, job.url, exc)
            return "error", ""
        return "not_found", ""
    except Exception as exc:  # network, JSON shape, ... must not abort the run
        _log_failure(ref.ats, job.url, exc)
        return "error", ""
    if not text.strip():
        return "error", ""
    cache.put(job.url, text)
    return "ok", text


def fetch_all(jobs: list[Job], client, cache: JDCache, workers: int) -> None:
    def work(job: Job):
        job.jd_status, job.jd = fetch_one(job, client, cache)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(work, jobs))


def _score(job: Job, cfg: dict, boost_re: re.Pattern) -> float:
    sc = cfg["scoring"]
    total = job.fit_pct or 0.0
    if "new grad" in job.signals or "recent graduate" in job.signals:
        total += sc["boost"]["new_grad"]
    if job.new_grad or "Simplify" in job.sources:
        total += sc["boost"]["simplify"]
    if boost_re.search(job.location) or re.search(r"\bremote\b", job.title, re.IGNORECASE):
        total += sc["boost"]["bc_or_remote"]
    if job.jd_status != "ok":
        total -= sc["penalty"]["no_jd"]
    return round(total, 1)


def apply_rules(job: Job, cfg: dict, filters: Filters, scorer: Scorer) -> str | None:
    """Fill phase-2 fields on `job`; return a drop reason, or None to keep it."""
    if job.jd_status == "ok":
        jd = job.jd
        exp = jd_rules.parse_experience(jd)
        fit = scorer.evaluate(jd)
        job.fit_pct, job.cluster, job.matched, job.gaps = fit.fit_pct, fit.cluster, fit.matched, fit.gaps
        job.signals = jd_rules.detect_signals(job.title, jd, exp)
        if jd_rules.requires_enrollment(jd):
            return R_ENROLLMENT
        if jd_rules.requires_clearance(jd):
            return R_CLEARANCE
        if jd_rules.requires_us_authorization(jd) and filters.canadian_location(job.location) is None:
            return R_US_AUTH
        if exp.min_years is not None and exp.min_years >= cfg["jd"]["max_experience_years"]:
            return R_EXPERIENCE
        if job.weak_title and (not job.matched or (job.fit_pct or 0) < cfg["scoring"]["weak_title_min_fit"]):
            return R_WEAK
    else:
        job.signals = jd_rules.detect_signals(job.title, "", jd_rules.Experience(None))
    return None


def enrich(candidates: list[Job], held: list[Job], cfg: dict, filters: Filters, scorer: Scorer,
           client, *, cache: JDCache | None = None,
           progress: Callable[[str], None] = lambda _msg: None) -> EnrichResult:
    jdcfg = cfg["jd"]
    cache = cache or JDCache(jdcfg.get("cache_dir"), jdcfg.get("cache_ttl_days", 7))
    held = held if jdcfg.get("rescue_student_titles", True) else []
    res = EnrichResult(held_total=len(held))
    boost_re = re.compile(cfg["scoring"]["bc_or_remote_pattern"], re.IGNORECASE)

    progress(f"fetching JDs for {len(candidates)} candidates + {len(held)} student-titled jobs")
    fetch_all(candidates + held, client, cache, jdcfg["workers"])
    for job in candidates + held:
        res.fetch[f"{job.source}:{job.jd_status}"] += 1

    for job in candidates:
        reason = apply_rules(job, cfg, filters, scorer)
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
        if not jd_rules.mentions_recent_grad(job.jd):
            res.drops[R_STUDENT] += 1
            continue
        reason = apply_rules(job, cfg, filters, scorer)
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
