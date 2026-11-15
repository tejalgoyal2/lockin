"""Phase 2: fetch JD text, apply JD rules, score."""
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

from scanner import jd_rules, rating
from scanner.filters import Filters
from scanner.models import Job
from scanner.normalize import job_key
from scanner.score import Scorer
from scanner.sources import ats, supplied

log = logging.getLogger(__name__)
_warned: set[str] = set()   # one warning per ATS; per-job failures go to debug

# Drop-reason names (the keys of EnrichResult.drops)
R_ENROLLMENT = "requires_enrollment"
R_CLEARANCE = "security_clearance"
R_US_AUTH = "us_work_authorization"
R_EXPERIENCE = "experience_too_high"
R_FRENCH = "requires_french"
R_QUEBEC_ONLY = "location_quebec_only"
R_NOT_CANADA = "location_not_canada"
R_OTHER_AREA = "location_other_province"
R_UNRESOLVED_FOREIGN = "location_unresolved_url_not_canadian"
R_ATS_NOT_CANADA = "location_ats_not_canada"
R_NOT_TECH = "not_a_tech_job"
R_NO_JD_GENERIC = "generic_title_no_description"


@dataclass
class EnrichResult:
    kept: list[Job] = field(default_factory=list)
    drops: Counter = field(default_factory=Counter)        # reason -> count (candidates + held)
    fetch: Counter = field(default_factory=Counter)        # "<ATS>:<status>" -> count
    forbidden: Counter = field(default_factory=Counter)    # host -> jobs whose JD fetch got HTTP 403
    forbidden_jobs: list[Job] = field(default_factory=list)  # the jobs behind `forbidden`
    held_total: int = 0                                    # intern / co-op / student titles among the candidates
    dropped: list = field(default_factory=list)            # (reason, Job): the jobs behind `drops`, for reports/dropped.md

    def drop(self, reason: str, job: Job) -> None:
        self.drops[reason] += 1
        self.dropped.append((reason, job))


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
    """The job's own page first; when that cannot be read, the description its source handed over (Google for Jobs)."""
    r = _fetch_job(job, client, cache, filters)
    if r.status != "ok":
        text = supplied.get(job.url)
        if text:
            return FetchResult("ok", text, relocation=r.relocation)
    return r


def _fetch_job(job: Job, client, cache: JDCache, filters: Filters | None = None) -> FetchResult:
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

    # A host that asks for slow requests (Job Bank: 5 s apart) gets a lane of its own. In the shared pool its jobs would each
    # hold a worker while waiting for the host's turn, and a few hundred of them would leave no worker for anyone else.
    slow_hosts = {h for h, d in (getattr(client, "host_delays", None) or {}).items() if d >= 1}
    slow = [j for j in jobs if urlsplit(j.url).netloc in slow_hosts]
    slow_ids = {id(j) for j in slow}
    fast = [j for j in jobs if id(j) not in slow_ids]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool, ThreadPoolExecutor(max_workers=1) as lane:
        lane_futures = [lane.submit(work, j) for j in slow]
        list(pool.map(work, fast))
        for fut in lane_futures:
            fut.result()


def url_location(url: str) -> str:
    """The location slug in a Workday URL (.../job/Toronto-ON-CAN/Title_R1 -> "Toronto ON CAN"): the job's
    primary location, the only one visible when the detail cannot be read."""
    m = re.search(r"/job/([^/]+)/", url or "")
    text = re.sub(r"[-_]+", " ", m.group(1)).strip() if m else ""
    return re.sub(r"(?<=\w) (ON|BC|AB|QC|MB|SK|NS|NB|NL|PE|YT|NT|NU)\b", r", \1", text)      # "Guelph ON" -> "Guelph, ON"


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
    """The ATS posting's own, current location wins over the dataset's for every job: a Simplify row lists the
    locations of a whole programme ("Vancouver, BC; Dublin, Ireland; United States") and a dataset row can be a
    day old while the employer has moved the posting ("Remote Canada" -> "Remote USA"). If the ATS names places
    and none is Canadian, the job is not a Canadian job. Vague ATS locations ("Remote", "Hybrid") say nothing
    and are ignored."""
    if not job.jd_locations:
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


def _no_jd_junk(title: str, jdcfg: dict) -> bool:
    """A generic title naming a clearly non-tech field ("Warehouse Specialist"), with no description to say otherwise."""
    words = jdcfg.get("no_jd_title_exclude") or []
    return bool(words) and bool(re.search(r"\b(?:" + "|".join(re.escape(w) for w in words) + ")", title or "", re.IGNORECASE))


def experience_reason(exp: jd_rules.Experience, jdcfg: dict) -> str | None:
    """Why the experience a posting asks for is too much, or None. The owner's line: up to 2 years. "1-5 years" passes
    (it starts at 1); "2 years" and "2+ years" pass as long shots; "2-5 years", "3 years" and "3+" do not."""
    need, limit = exp.min_years, jdcfg.get("max_years", 2)
    if need is None or need < limit:
        return None
    if need > limit:
        return f"asks {need:g}+ years"
    if exp.max_years is not None and exp.max_years > limit:
        return f"asks {need:g}-{exp.max_years:g} years"
    return None


def relevance_reason(job: Job, fit, jdcfg: dict) -> str | None:
    """Why a posting whose description was read is not a tech job, or None. The less the title says, the more the
    description has to:

      core title, unmistakable ("Software Developer", "DevOps Engineer": `core_always`)  never doubted
      core title, ambiguous ("Event Security", "Field Data Collector")  a full description with no tech word at all fails
      another tech title ("IT Analyst", "Systems Specialist")           a full description must name one tool or gap term
      generic title ("Analyst", "Specialist", "Engineer")               it must name `generic_min_skills` of the owner's
                                                                        tools (one, when it is shorter than `min_judge_chars`)
    """
    rel = jdcfg.get("relevance") or {}
    full = len(job.jd) >= rel.get("min_judge_chars", 600)
    if job.role == "core":
        if not full or fit.n_terms or re.search(rel.get("core_always", "$^"), job.title, re.IGNORECASE):
            return None
        return "no tech term in the description"
    if job.role == "adjacent":
        return None if not full or fit.hard or fit.gaps else "no tool or technology named in the description"
    need = rel.get("generic_min_skills", 2) if full else 1
    return None if fit.hard >= need else f"generic title, {fit.hard} of your tools in the description (needs {need})"


def apply_rules(job: Job, cfg: dict, filters: Filters, scorer: Scorer) -> str | None:
    """Fill phase-2 fields on `job` (provinces, fit, notes, exp, rating); return a drop reason, or None to keep it."""
    reason = resolve_location(job, filters, cfg)
    if reason:
        return reason
    if job.jd_locations:                     # the posting's own, current places decide the Province tags
        places = " ; ".join(job.jd_locations)
        tags = filters.provinces(places, job.title)
        if tags == ["Other"] and filters.drop_other_areas:
            job.location_dropped = True
            return R_OTHER_AREA
        if tags:
            part = filters.best_part(places)
            if part and tags != job.provinces:
                job.location = part
            job.provinces = tags
    if not job.provinces:                    # a Workday "N Locations" row resolved in phase 2, or a bare "Canada"
        job.provinces = filters.provinces(job.location, job.title) or ["Canada"]
    job.role = filters.role_tier(job.title) or "generic"
    jdcfg = cfg["jd"]
    notes = [rating.INTERN] if job.student else []
    if job.jd_status == "ok":
        jd = job.jd
        exp = jd_rules.parse_experience(jd)
        fit = scorer.evaluate(jd, (job.company, job.company_name), title=job.title)
        job.fit, job.matched, job.gaps, job.hard = fit.fit, fit.matched, fit.gaps, fit.hard
        job.min_years = exp.min_years
        if cfg.get("language", {}).get("drop_french_required_jd", True) and jd_rules.requires_french(jd):
            return R_FRENCH
        if jd_rules.requires_enrollment(jd):         # the owner has graduated
            return R_ENROLLMENT
        if jd_rules.requires_clearance(jd):
            return R_CLEARANCE
        if jd_rules.requires_us_authorization(jd) and filters.canadian_location(job.location) is None:
            return R_US_AUTH
        if not job.student:                          # a bank's intern posting carries its "4-6 years" template line
            job.drop_note = experience_reason(exp, jdcfg) or ""
            if job.drop_note:
                return R_EXPERIENCE
        else:
            job.min_years = None
        job.drop_note = relevance_reason(job, fit, jdcfg) or ""
        if job.drop_note:
            return R_NOT_TECH
        if job.new_grad or "Simplify" in job.sources or jd_rules.addresses_new_grads(job.title, jd):
            notes.append(rating.NEW_GRAD)
        if jd_rules.junior_level(job.title, jd):
            notes.append(rating.JUNIOR)
        if job.min_years is not None and job.min_years >= 2:
            notes.append(rating.TWO_YEARS)
        if jd_rules.requires_peng(jd):
            notes.append(rating.PENG)
        if jd_rules.talent_pool(jd):
            notes.append(rating.POOL)
        if job.student and jd_rules.welcomes_recent_grads(jd):
            notes.append(rating.GRADS_OK)
        if fit.n_core <= rating.THIN_TERMS:
            notes.append(rating.THIN)
    else:
        # No description: nothing confirms the job, nothing rules it out. It is kept as a low score (the owner can open
        # the link), except a generic title in a field that is plainly not tech.
        if job.role == "generic" and _no_jd_junk(job.title, jdcfg):
            job.drop_note = "generic title in a non-tech field, and no description to show otherwise"
            return R_NO_JD_GENERIC
        if job.new_grad or "Simplify" in job.sources or jd_rules.addresses_new_grads(job.title, ""):
            notes.append(rating.NEW_GRAD)
        if jd_rules.junior_level(job.title, ""):
            notes.append(rating.JUNIOR)
        notes.append(rating.NO_JD)
    job.notes = notes
    job.exp = rating.exp_range(job.min_years, notes, jd_rules.entry_title(job.title))
    job.rating = rating.score(job)
    return None


def enrich(candidates: list[Job], cfg: dict, filters: Filters, scorer: Scorer,
           client, *, cache: JDCache | None = None,
           progress: Callable[[str], None] = lambda _msg: None) -> EnrichResult:
    """Fetch every candidate's description, apply the rules, rate what is left. Best first."""
    jdcfg = cfg["jd"]
    cache = cache or JDCache(jdcfg.get("cache_dir"), jdcfg.get("cache_ttl_days", 7))
    res = EnrichResult(held_total=sum(1 for j in candidates if j.student))
    progress(f"fetching JDs for {len(candidates)} candidates ({res.held_total} intern / co-op titles)")
    fetch_all(candidates, client, cache, jdcfg["workers"], filters)
    for job in candidates:
        res.fetch[f"{job.source}:{job.jd_status}"] += 1
        if job.jd_error == "http 403":
            res.forbidden[urlsplit(job.url).netloc] += 1
            res.forbidden_jobs.append(job)
        reason = apply_rules(job, cfg, filters, scorer)
        if reason:
            res.drop(reason, job)
        else:
            res.kept.append(job)
    res.kept.sort(key=rank_key)
    return res


def rank_key(job: Job):
    """Feed order when a run has more jobs than it may write: best Score first, then the lowest experience asked,
    then the newest posting."""
    return (-job.rating, job.exp, -job.first_seen.timestamp())


def rating_distribution(jobs: list[Job]) -> list[tuple[str, int]]:
    """How many kept jobs got each Score."""
    counts = Counter(j.rating for j in jobs)
    return [(f"Score {n}", counts.get(n, 0)) for n in (5, 4, 3, 2, 1)]
