from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Job:
    company: str            # as the source reports it (an ATS slug for Feashliaa rows)
    title: str
    location: str          # the Canadian location chosen for display / dedupe
    url: str
    source: str            # ATS name for Feashliaa rows, "Simplify" for Simplify-only rows
    first_seen: datetime   # tz-aware UTC
    sources: set[str] = field(default_factory=set)  # every feed that listed this job
    new_grad: bool = False  # listed by Simplify's new-grad feed
    company_name: str = ""   # readable name when a source gives one (Simplify, Greenhouse)
    weak_title: bool = False  # matched only generic title words (e.g. "Engineer")
    key: str = ""
    location_pending: bool = False   # Workday "N Locations": phase 2 reads the real list from the job detail
    location_unresolved: bool = False  # ... and could not (kept anyway, reported)
    relocation: str = ""             # Workday id that stopped resolving: "matched" (url replaced) or "no_match"
    location_dropped: bool = False   # dropped in phase 2 for its resolved locations (not a Canadian job)
    # phase 2 (filled by scanner.enrich)
    jd: str = ""
    jd_status: str = ""            # "ok" | "unsupported" | "not_found" | "error" | "" (not fetched)
    jd_error: str = ""             # detail for non-ok fetches, e.g. "http 403"
    jd_locations: list[str] = field(default_factory=list)   # every location the ATS lists for the job (Workday)
    fit_pct: float | None = None
    cluster: str | None = None
    matched: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    score: float = 0.0

    def source_label(self) -> str:
        others = sorted(self.sources - {self.source})
        return "+".join([self.source, *others])
