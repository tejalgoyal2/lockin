"""Skills-overlap Fit %, cluster, and ranking Score (SPEC §5)."""
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent
CLUSTER_ORDER = ("software", "data", "ml", "security")

_REQ_HEADING = re.compile(
    r"^\W*(?:requirements?|qualifications?|what\s+you['’]?ll\s+need|must[\s-]have)\b[^\n]{0,40}$",
    re.IGNORECASE | re.MULTILINE)
_STOP_HEADING = re.compile(
    r"^\W*(?:responsibilit\w+|what\s+you['’]?ll\s+do|about\b|benefits|perks|what\s+we\s+offer|"
    r"nice[\s-]to[\s-]have|preferred|bonus|compensation|why\b|our\s+culture|equal\s+opportunity)[^\n]{0,40}$",
    re.IGNORECASE | re.MULTILINE)
REQUIREMENT_WEIGHT = 2


def _alias_pattern(alias: str, case_sensitive: bool) -> re.Pattern:
    # Word boundary that tolerates C++, C#, .NET, Node.js, CI/CD: the alias must not be glued
    # to a letter/digit/underscore on either side (and "C" must not swallow the "++" of "C++").
    body = re.escape(alias)
    tail = r"(?![A-Za-z0-9_])" if alias[-1] not in "+#" else r"(?![A-Za-z0-9_+#])"
    return re.compile(rf"(?<![A-Za-z0-9_]){body}{tail}", 0 if case_sensitive else re.IGNORECASE)


@dataclass
class Term:
    name: str
    cluster: str | None
    patterns: list[re.Pattern]

    def count_in(self, text: str) -> int:
        return sum(len(p.findall(text)) for p in self.patterns)


def _terms(entries, cluster: str | None) -> list[Term]:
    out = []
    for e in entries:
        cs = bool(e.get("cs"))
        out.append(Term(e["name"], cluster, [_alias_pattern(a, cs) for a in e["aliases"]]))
    return out


@dataclass
class Fit:
    matched: list[str] = field(default_factory=list)   # skill names, strongest first
    gaps: list[str] = field(default_factory=list)
    fit_pct: float | None = None                       # None: JD mentions no known tech term
    cluster: str | None = None


class Scorer:
    def __init__(self, skills: dict, gaps: dict):
        self.skills = [t for c, entries in skills["clusters"].items() for t in _terms(entries, c)]
        self.gaps = _terms(gaps["terms"], None)

    @classmethod
    def load(cls, skills_path=None, gaps_path=None) -> "Scorer":
        with open(skills_path or _ROOT / "skills.yaml", encoding="utf-8") as fh:
            skills = yaml.safe_load(fh)
        with open(gaps_path or _ROOT / "gaps.yaml", encoding="utf-8") as fh:
            gaps = yaml.safe_load(fh)
        return cls(skills, gaps)

    def evaluate(self, jd: str) -> Fit:
        req = requirements_text(jd)
        weights_m, weights_g, cluster_hits = {}, {}, {}
        for term in self.skills:
            if term.count_in(jd):
                weights_m[term.name] = REQUIREMENT_WEIGHT if term.count_in(req) else 1
                cluster_hits[term.cluster] = cluster_hits.get(term.cluster, 0) + 1
        for term in self.gaps:
            if term.count_in(jd):
                weights_g[term.name] = REQUIREMENT_WEIGHT if term.count_in(req) else 1
        wm, wg = sum(weights_m.values()), sum(weights_g.values())
        cluster = None
        if cluster_hits:
            cluster = max(CLUSTER_ORDER, key=lambda c: cluster_hits.get(c, 0))
        return Fit(
            matched=sorted(weights_m, key=lambda n: (-weights_m[n], n)),
            gaps=sorted(weights_g, key=lambda n: (-weights_g[n], n)),
            fit_pct=round(100 * wm / (wm + wg), 1) if wm + wg else None,
            cluster=cluster,
        )


def requirements_text(jd: str) -> str:
    """Text of the first 'Requirements / Qualifications / What you'll need / Must have' section."""
    m = _REQ_HEADING.search(jd or "")
    if not m:
        return ""
    stop = _STOP_HEADING.search(jd, m.end())
    return jd[m.start(): stop.start() if stop else len(jd)]
