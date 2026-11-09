"""Config-driven title / location / company / language filters (SPEC §4).

Each predicate returns True when the record should be KEPT.
"""
import re
import unicodedata
from dataclasses import dataclass, field

from scanner.normalize import norm_text


def fold(text: str) -> str:
    """Lowercase and strip accents ("Directrice" == "DIRECTRICE", "Québec" == "quebec")."""
    return "".join(c for c in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(c)).lower()


def _compile(patterns, flags=re.IGNORECASE):
    return [re.compile(p, flags) for p in patterns or []]


def _prefix(terms):
    """Match a term at a word start ('engineer' also hits 'engineering')."""
    return [re.compile(r"\b" + re.escape(t), re.IGNORECASE) for t in terms or []]


def _words(terms, flags=re.IGNORECASE):
    return [re.compile(r"\b" + re.escape(t) + r"\b", flags) for t in terms or []]


def _label(pattern: re.Pattern) -> str:
    """The word a title rule was written as: re.compile(r"\\bsenior") -> "senior"."""
    return re.sub(r"\\b|\\", "", pattern.pattern)


def _any(patterns, text):
    return any(p.search(text) for p in patterns)


@dataclass
class Filters:
    loc_include: list
    loc_exclude: list
    loc_split: re.Pattern
    title_strong: list
    title_weak: list
    title_exclude: list
    student: list
    blocklist: list
    drop_french: bool
    french: re.Pattern
    english_roles: list
    quebec: list
    country_only: list
    unresolved: re.Pattern | None
    title_exclude_phrases: list
    french_first: re.Pattern
    title_foreign: list = field(default_factory=list)
    title_exclude_weak: list = field(default_factory=list)
    student_season: list = field(default_factory=list)
    grad_signal: list = field(default_factory=list)

    @classmethod
    def from_config(cls, cfg: dict) -> "Filters":
        loc, title = cfg["location"], cfg["title"]
        lang = cfg.get("language", {})
        return cls(
            loc_include=_compile(loc["include"]) + _compile(loc.get("include_cs"), 0),
            loc_exclude=_compile(loc.get("exclude")) + _compile(loc.get("exclude_cs"), 0),
            loc_split=re.compile(loc.get("split_on", "[;|]")),
            title_strong=_prefix(title["strong"]) + _words(title.get("strong_words")) + _words(title.get("strong_words_cs"), 0),
            title_weak=_prefix(title["weak"]) + _words(title.get("weak_words")) + _words(title.get("weak_words_cs"), 0),
            title_exclude=(
                _prefix(title["exclude"])
                + _words(title.get("exclude_words"))
                + _words(title.get("exclude_words_cs"), 0)
            ),
            student=_words(title["student"]),
            student_season=_compile(title.get("student_regex")),
            grad_signal=_compile(title.get("grad_signal")),
            blocklist=[norm_text(c) for c in cfg["company"]["blocklist"]],
            drop_french=lang.get("drop_french_titles", False),
            french=re.compile(lang.get("french_pattern", "$^"), re.IGNORECASE),
            english_roles=_words(lang.get("english_role_words")),
            quebec=_compile(loc.get("quebec")) + _compile(loc.get("quebec_cs"), 0),
            country_only=_compile(loc.get("country_only", [r"\bCanada\b"])),
            unresolved=re.compile(loc["unresolved"], re.IGNORECASE) if loc.get("unresolved") else None,
            title_exclude_phrases=[re.compile(r"\b" + re.escape(fold(p)) + r"\b") for p in title.get("exclude_phrases") or []],
            title_exclude_weak=_prefix(title.get("exclude_if_weak")),
            french_first=re.compile(lang.get("french_first_segment_pattern", "$^"), re.IGNORECASE),
            title_foreign=([re.compile(rf"(?<![\w.])(?:{p})(?![\w])", re.IGNORECASE) for p in loc.get("title_foreign") or []]
                           + [re.compile(rf"(?<![\w.])(?:{p})(?![\w])") for p in loc.get("title_foreign_cs") or []]),
        )

    # -- location -----------------------------------------------------------
    def _canadian_parts(self, location: str) -> list[str]:
        out = []
        for part in self.loc_split.split(location or ""):
            part = part.strip()
            cleaned = part
            for pat in self.loc_exclude:
                cleaned = pat.sub(" ", cleaned)
            if _any(self.loc_include, cleaned):
                out.append(part)
        return out

    def title_location_ok(self, title: str) -> bool:
        """False when the title names a place outside Canada and no Canadian place: "Software Engineer (New York)",
        "AI Product Manager (NYC-based)". "Data Engineer (NYC or Toronto)" and titles naming no place pass."""
        if not _any(self.title_foreign, title or ""):
            return True
        return bool(self._canadian_parts(title))

    def is_quebec(self, part: str) -> bool:
        """A location in Quebec and nowhere else: "Montréal, QC", "Quebec, Canada" yes; "Ottawa/Gatineau" no."""
        if not _any(self.quebec, part):
            return False
        rest = part
        for pat in self.quebec:
            rest = pat.sub(" ", rest)
        for pat in self.country_only:                    # "Canada" alone does not leave Quebec
            rest = pat.sub(" ", rest)
        return not _any(self.loc_include, rest)

    def location_status(self, location: str) -> tuple[str, str | None]:
        """('ok', part) | ('quebec_only', part) | ('unresolved', location) | ('not_canada', None).

        A job is dropped for Quebec only when EVERY Canadian location it lists is in Quebec.
        "unresolved" is a Workday "2 Locations" string whose real list comes from the job detail.
        """
        if self.unresolved and self.unresolved.search(location or ""):
            return "unresolved", location.strip()
        parts = self._canadian_parts(location)
        if not parts:
            return "not_canada", None
        for part in parts:
            if not self.is_quebec(part):
                return "ok", part
        return "quebec_only", parts[0]

    def canadian_location(self, location: str) -> str | None:
        """First Canadian part of a (possibly multi-location) string, preferring one outside Quebec."""
        status, part = self.location_status(location)
        return part if status in ("ok", "quebec_only") else None

    # -- company ------------------------------------------------------------
    def company_ok(self, company: str) -> bool:
        n = norm_text(company)
        return not any(b in n for b in self.blocklist)

    # -- title --------------------------------------------------------------
    def language_ok(self, title: str) -> bool:
        if not self.drop_french:
            return True
        # "Développeur de logiciels / Software Developer": a French first segment drops the job
        first = re.split(r"\s+/\s*|\s*/\s+", title or "", maxsplit=1)[0]   # " / ", "QC/ " but not "CI/CD"
        if first != title and self.french_first.search(first):
            return False
        if not self.french.search(title):
            return True
        return _any(self.english_roles, title)

    def title_tier(self, title: str) -> str | None:
        """'strong' passes outright, 'weak' matches only generic words, None is no match."""
        if _any(self.title_strong, title):
            return "strong"
        return "weak" if _any(self.title_weak, title) else None

    def title_not_excluded(self, title: str) -> bool:
        if _any(self.title_exclude, title):
            return False
        folded = fold(title)
        if _any(self.title_exclude_phrases, folded):
            return False
        # "soft" excludes (coordinator, marketing, technician ...) only count when the title's only tech match is a generic
        # word: "IT Coordinator" and "Data Coordinator" have a strong one and pass, "Event Coordinator" does not.
        return not (_any(self.title_exclude_weak, title) and not _any(self.title_strong, title))

    def exclusion_reason(self, title: str) -> str | None:
        """The first title-exclude rule that matches, as the word or phrase written in config.yaml ("senior", "lead",
        "director"); None when the title is not excluded. title_not_excluded() says whether, this says why."""
        for pat in self.title_exclude:
            if pat.search(title or ""):
                return _label(pat)
        folded = fold(title)
        for pat in self.title_exclude_phrases:
            if pat.search(folded):
                return _label(pat)
        if not _any(self.title_strong, title):
            for pat in self.title_exclude_weak:
                if pat.search(title or ""):
                    return _label(pat)
        return None

    def all_exclusion_reasons(self, title: str) -> list[str]:
        """Every title-exclude rule that matches (a rule that is the only one is the one a change would free the job from)."""
        out = [_label(p) for p in self.title_exclude if p.search(title or "")]
        folded = fold(title)
        out += [_label(p) for p in self.title_exclude_phrases if p.search(folded)]
        if not _any(self.title_strong, title):
            out += [_label(p) for p in self.title_exclude_weak if p.search(title or "")]
        return out

    def not_student_only(self, title: str) -> bool:
        if _any(self.student, title):
            return False                      # intern, co-op, student: held for the JD
        # "Summer 2027" alone says the job starts then; "New Grad Field Engineer I - Summer 2027" is a graduate job
        return not (_any(self.student_season, title) and not _any(self.grad_signal, title))
