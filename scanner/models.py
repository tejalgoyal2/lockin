from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Job:
    company: str
    title: str
    location: str          # the Canadian location chosen for display / dedupe
    url: str
    source: str            # ATS name for Feashliaa rows, "Simplify" for Simplify-only rows
    first_seen: datetime   # tz-aware UTC
    sources: set[str] = field(default_factory=set)  # every feed that listed this job
    new_grad: bool = False  # listed by Simplify's new-grad feed
    weak_title: bool = False  # matched only generic title words (e.g. "Engineer")
    key: str = ""

    def source_label(self) -> str:
        others = sorted(self.sources - {self.source})
        return "+".join([self.source, *others])
