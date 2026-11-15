from dataclasses import dataclass, field
from datetime import datetime

PROVINCES = ("BC", "Remote", "Alberta", "Ontario", "Canada")   # the Feed's Province tags, in the owner's order


def province_tags(tags) -> list[str]:
    """Tags in the owner's order; "Canada" (no province named) only when there is nothing more precise."""
    tags = set(tags)
    if len(tags) > 1:
        tags.discard("Canada")
    return [t for t in PROVINCES if t in tags]


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
    weak_title: bool = False  # the title names no core role (software, data, cloud ...): Filters.role_tier is adjacent or generic
    key: str = ""
    location_pending: bool = False   # Workday "N Locations": phase 2 reads the real list from the job detail
    location_unresolved: bool = False  # ... and could not (kept anyway, reported)
    relocation: str = ""             # Workday id that stopped resolving: "matched" (url replaced) or "no_match"
    drop_note: str = ""              # why a JD rule dropped it, in a few words (reports/dropped.md)
    location_dropped: bool = False   # dropped in phase 2 for its resolved locations (not a Canadian job)
    student: bool = False            # an intern / co-op / student title (dropped when the JD requires enrollment)
    provinces: list[str] = field(default_factory=list)   # the Feed's Province tags: BC, Remote, Alberta, Ontario, Canada
    # phase 2 (filled by scanner.enrich)
    jd: str = ""
    jd_status: str = ""            # "ok" | "unsupported" | "not_found" | "error" | "" (not fetched)
    jd_error: str = ""             # detail for non-ok fetches, e.g. "http 403"
    jd_locations: list[str] = field(default_factory=list)   # every location the ATS lists for the job (Workday)
    fit: float | None = None       # share of the JD's tech terms that are the owner's skills (0-1); None: too few terms
    matched: list[str] = field(default_factory=list)   # the owner's skills the JD names, strongest first
    gaps: list[str] = field(default_factory=list)      # tech terms the JD names that the owner does not list
    hard: int = 0                  # matched skills that are tools or techniques (not process words such as Agile)
    notes: list[str] = field(default_factory=list)     # what the rules noticed: "new grad", "asks 2 years", "no description" ...
    min_years: float | None = None   # years of experience the JD asks for; None: it states none
    role: str = ""                 # "core" | "adjacent" | "generic" (scanner.rating.role_tier)
    exp: int = 0                   # the Feed's Exp column: 1 = 0-1 years, 2 = 1-2 years or not stated, 3 = 2 years
    rating: int = 0                # the Feed's Score column, 1-5

    def source_label(self) -> str:
        others = sorted(self.sources - {self.source})
        return "+".join([self.source, *others])
