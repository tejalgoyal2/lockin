"""Skills-overlap Fit %, cluster, and ranking Score (SPEC §5)."""
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from scanner.normalize import norm_text

_ROOT = Path(__file__).resolve().parent.parent
CLUSTER_ORDER = ("software", "data", "ml", "security")

# --- JD sections ------------------------------------------------------------------------
# A heading is a short line that is not a list item. It is classified by the first family that matches, in
# this order: nice-to-have, requirements, other (responsibilities, about, benefits, ...). A short line that
# matches no family is not a heading: it stays in the section around it.
_NICE_HEADING = re.compile(
    r"\b(?:nice[\s-]to[\s-]haves?|nice[\s-]to[\s-]have['’]s|preferred|bonus|assets?|an\s+asset|desir(?:ed|able)|"
    r"highly\s+favou?rable|good[\s-]to[\s-]have|pluses|(?:would|it\s+would)\s+be\s+(?:great|nice|a\s+plus)|"
    r"extra\s+credit|even\s+better|we['’]d\s+love|ideally)\b", re.IGNORECASE)
_REQ_HEADING = re.compile(
    r"\b(?:requirements?|qualifications?|you(?:['’]ll|\s+will)?\s+(?:bring|need|have)|what\s+you\s+need|"
    r"look(?:ing)?\s+for|who\s+you\s+are|about\s+you\b|ideal\s+candidate|must[\s-]haves?|competenc\w*|"
    r"criteria|what\s+it\s+takes|technologies\s+you|education\b)", re.IGNORECASE)
# Generic words name a requirements heading only as its last word ("Required Skills", "Your Experience"), so a
# bullet-less line such as "Experience with Python" is not a heading.
_REQ_LAST_WORD = re.compile(r"\b(?:skills|experience|knowledge|capabilities|background|expertise|profile)\W*$",
                            re.IGNORECASE)
_OTHER_HEADING = re.compile(
    r"^(?:responsibilit|responsabilit|(?:key|major|main|primary|job|core)\s+(?:responsibilit|duties|accountabilit)|"
    r"duties|accountabilit|what\s+you(?:['’]ll|\s+will)?\s+(?:do|be\s+doing|get\s+to\s+do|work\s+on)|about\b|"
    r"who\s+we\s+are|why\b|benefits?|perks|what\s+we\s+offer|we\s+offer|compensation|salary|pay\b|"
    r"total\s+rewards|equal\s+opportunity|diversity|inclusion|accommodat|additional\s+information|"
    r"how\s+to\s+apply|the\s+(?:role|team|opportunity|company|position)\b|job\s+(?:summary|description|details)|"
    r"summary|overview|role\s+overview|location|in\s+this\s+role|your\s+(?:role|impact|day)|a\s+day\s+in|"
    r"what['’]s\s+in\s+it|you['’]ll\s+enjoy|life\s+at|working\s+at|hiring\s+(?:range|process)|interview|"
    r"disclaimer|note\b|position\s+details|our\b|"
    r"what\s+success\s+looks\s+like|work\s+(?:conditions|model|environment)|join\s+us|get\s+to\s+know\s+us|"
    r"as\s+an?\s+\w+)", re.IGNORECASE)
_BULLET = re.compile(r"^\s*[-•*·▪◦‣–]\s")

REQUIREMENT_WEIGHT = 1.0   # a term the requirements section asks for
OTHER_WEIGHT = 0.5         # ... only elsewhere (responsibilities, intro, nice-to-have) when a requirements section exists
NICE_WEIGHT = 0.5          # ... only under a nice-to-have / preferred heading (always)
FIT_SMOOTHING = 2          # Fit % = matched / (matched + gaps + 2), in requirement-term units


def heading_kind(line: str) -> str | None:
    """'nice', 'req', 'other' or None (not a heading)."""
    text = line.strip()
    if not text or len(text) > 80 or _BULLET.match(line) or text[-1] in ".,;":
        return None
    words = len(text.split())
    if words > 10:
        return None
    long_line = words > 7 and not text.endswith(":")     # only a nice-to-have / requirements phrase makes it a heading
    bare = re.sub(r"^[^\w]+", "", text)
    if _NICE_HEADING.search(bare):
        return "nice"
    if _REQ_HEADING.search(bare):
        return "req"
    if long_line:
        return None
    if _OTHER_HEADING.match(bare):
        return "other"
    if _REQ_LAST_WORD.search(bare.rstrip(":")):
        return "req"
    return None


def sections(jd: str) -> list[tuple[int, int, str]]:
    """(start, end, kind) spans covering the JD; text before the first heading is 'body'."""
    out, start, kind, pos = [], 0, "body", 0
    for line in (jd or "").splitlines(keepends=True):
        k = heading_kind(line)
        if k:
            if pos > start:
                out.append((start, pos, kind))
            start, kind = pos, k
        pos += len(line)
    if pos > start:
        out.append((start, pos, kind))
    return out


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

    def positions(self, text: str) -> list[int]:
        return [m.start() for p in self.patterns for m in p.finditer(text)]


def _terms(entries, cluster: str | None) -> list[Term]:
    out = []
    for e in entries:
        cs = bool(e.get("cs"))
        patterns = [_alias_pattern(a, cs) for a in e.get("aliases", [])]
        patterns += [re.compile(rx) for rx in e.get("regex", [])]      # raw, case-sensitive (context-dependent words)
        out.append(Term(e["name"], cluster, patterns))
    return out


@dataclass
class Fit:
    matched: list[str] = field(default_factory=list)   # skill names, strongest first
    gaps: list[str] = field(default_factory=list)
    fit_pct: float | None = None                       # None: fewer than `min_terms` terms found
    cluster: str | None = None                         # from JD terms only (see scanner.cluster for titles)
    n_terms: int = 0                                   # distinct matched + gap + context terms (signal strength)
    n_core: int = 0                                    # distinct matched + gap terms only
    context: list[str] = field(default_factory=list)   # generic engineering terms found (count towards n_terms only)
    weights: dict[str, float] = field(default_factory=dict)   # term -> weight used (1.0 required, 0.5 elsewhere)


class Scorer:
    def __init__(self, skills: dict, gaps: dict):
        self.skills = [t for c, entries in skills["clusters"].items() for t in _terms(entries, c)]
        self.gaps = _terms(gaps["terms"], None)
        # Generic engineering vocabulary ("backend", "DevOps", "Excel", "kernel", ...). It shows that a JD is
        # technical, so it counts towards the term threshold, but it is neither a skill nor a gap and does not
        # move Fit %. Without it, JDs for systems, consulting and analyst roles read as "low signal".
        self.context = _terms(gaps.get("context", []), None)

    @classmethod
    def load(cls, skills_path=None, gaps_path=None) -> "Scorer":
        with open(skills_path or _ROOT / "skills.yaml", encoding="utf-8") as fh:
            skills = yaml.safe_load(fh)
        with open(gaps_path or _ROOT / "gaps.yaml", encoding="utf-8") as fh:
            gaps = yaml.safe_load(fh)
        return cls(skills, gaps)

    def evaluate(self, jd: str, min_terms: int = 1, companies=(), min_core: int = 1) -> Fit:
        """Fit % = matched / (matched + gaps + FIT_SMOOTHING), each term weighted by the JD section it appears in.

        A term counts 1.0 when the requirements section asks for it. When the JD has a requirements section, a term
        found only elsewhere counts 0.5; when it has none, every term counts 1.0 (it cannot be told apart). A term
        found only under a nice-to-have / preferred heading always counts 0.5. Options in a list of alternatives
        ("React, Angular, or Vue", "e.g. Snowflake, Databricks") are not gaps when the owner has one of them, and
        count as one gap between them when the owner has none (see _apply_alternatives). Terms named like the hiring company
        (`companies`: slug and display name, e.g. GitLab at GitLab) are ignored. Fit % is only reported when at
        least `min_terms` distinct terms (matched + gaps + context) are found, `min_core` of them skills or gaps.
        """
        jd = jd or ""
        spans = sections(jd)
        has_req = any(k == "req" for _, _, k in spans)
        base = {"req": REQUIREMENT_WEIGHT, "nice": NICE_WEIGHT}
        other = OTHER_WEIGHT if has_req else REQUIREMENT_WEIGHT
        own = {norm_text(c) for c in companies if c}

        hits = []
        for terms, owned in ((self.skills, True), (self.gaps, False)):
            for term in terms:
                if norm_text(term.name) in own:
                    continue
                for p in term.patterns:
                    for m in p.finditer(jd):
                        kind = next((k for a, b, k in spans if a <= m.start() < b), "body")
                        hits.append(_Hit(m.start(), m.end(), term, owned, base.get(kind, other)))
        hits = _drop_nested(hits)
        _apply_alternatives(jd, hits)

        weights_m, weights_g, cluster_hits = {}, {}, {}
        for h in hits:
            book = weights_m if h.owned else weights_g
            book[h.term.name] = max(book.get(h.term.name, 0.0), h.weight)
        weights_m = {n: w for n, w in weights_m.items() if w}
        weights_g = {n: w for n, w in weights_g.items() if w}
        for term in self.skills:
            if term.name in weights_m:
                cluster_hits[term.cluster] = cluster_hits.get(term.cluster, 0) + 1
        context = sorted(t.name for t in self.context if t.count_in(jd))
        wm, wg = sum(weights_m.values()), sum(weights_g.values())
        n_core = len(weights_m) + len(weights_g)
        n_terms = n_core + len(context)
        cluster = None
        if any(cluster_hits.get(c) for c in CLUSTER_ORDER):
            cluster = max(CLUSTER_ORDER, key=lambda c: cluster_hits.get(c, 0))
        return Fit(
            matched=sorted(weights_m, key=lambda n: (-weights_m[n], n)),
            gaps=sorted(weights_g, key=lambda n: (-weights_g[n], n)),
            # matched / (total + 2): a thin match cannot reach 100 %
            fit_pct=(round(100 * wm / (wm + wg + FIT_SMOOTHING), 1)
                     if wm + wg and n_terms >= min_terms and n_core >= min_core else None),
            cluster=cluster,
            n_terms=n_terms,
            n_core=n_core,
            context=context,
            weights={**weights_g, **weights_m},
        )


@dataclass
class _Hit:
    start: int
    end: int
    term: Term
    owned: bool          # a skill (True) or a gap term (False)
    weight: float


def _drop_nested(hits: list[_Hit]) -> list[_Hit]:
    """A skill found inside a longer gap term (or the reverse) is part of that term: "React" in "React Native"."""
    return [h for h in hits
            if not any(o.owned != h.owned and o.start <= h.start and h.end <= o.end and (o.end - o.start) > (h.end - h.start)
                       for o in hits)]


_CONJ = {"or", "and", "nor", "either", "&"}
_ALT_PREFIX = re.compile(r"(?:such\s+as|e\.\s?g\.|eg\.|for\s+example|\blike|including|either|one\s+of|any\s+of|"
                         r"\(|\bor\s+similar\s+(?:tools|technologies)\s+(?:like|such\s+as))[\s:,]*$", re.IGNORECASE)


def _joins(between: str) -> tuple[bool, bool]:
    """(the text between two terms keeps them in one list, it says "or"). A list never crosses a line or a sentence
    and allows at most one other word between two items ("AWS CloudFormation, Terraform")."""
    if len(between) > 25 or "\n" in between or re.search(r"[.;:!?](?:\s|$)", between) or _ALT_PREFIX.search(between):
        return False, False                         # "... tools (e.g., Looker": the example list starts after "e.g."
    words = re.findall(r"[A-Za-z][\w.+#-]*", between)
    if len([w for w in words if w.lower() not in _CONJ]) > 1:
        return False, False
    return True, "/" in between or any(w.lower() in ("or", "nor") for w in words)


def _apply_alternatives(jd: str, hits: list[_Hit]) -> None:
    """Re-weight the gap terms in lists of alternatives, in place.

    A list of alternatives is a run of terms joined by commas, slashes, "and" or "or" that says "or" (or uses a slash)
    or follows "such as / e.g. / like / including / (". If the owner has one option, the other options are not gaps
    ("React, Angular, or Vue" for a React developer). If the owner has none, the options share one gap's weight.
    A plain "and" list ("Python, SQL and Docker") asks for every item and is left alone.
    """
    order = sorted(hits, key=lambda h: (h.start, -h.end))
    slots: list[list[_Hit]] = []                     # overlapping hits are one list item
    for h in order:
        if slots and h.start < max(x.end for x in slots[-1]):
            slots[-1].append(h)
        else:
            slots.append([h])
    i = 0
    while i < len(slots):
        group, has_or = [slots[i]], False
        while i + 1 < len(slots):
            joined, said_or = _joins(jd[max(x.end for x in group[-1]):slots[i + 1][0].start])
            if not joined:
                break
            group.append(slots[i + 1])
            has_or = has_or or said_or
            i += 1
        i += 1
        if len(group) < 2:
            continue
        if not (has_or or _ALT_PREFIX.search(jd[max(0, group[0][0].start - 40):group[0][0].start])):
            continue
        members = [h for slot in group for h in slot]
        gap_hits = [h for h in members if not h.owned]
        if not gap_hits:
            continue
        if any(h.owned for h in members):
            for h in gap_hits:
                h.weight = 0.0
        else:
            share = len({h.term.name for h in gap_hits})
            for h in gap_hits:
                h.weight /= share


def requirements_text(jd: str) -> str:
    """Text of every requirements section (heading included)."""
    return "".join(jd[a:b] for a, b, k in sections(jd) if k == "req")
