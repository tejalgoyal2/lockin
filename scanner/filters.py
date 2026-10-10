"""Config-driven title / location / company / language filters (SPEC §4).

Each predicate returns True when the record should be KEPT.
"""
import re
import unicodedata
from dataclasses import dataclass

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

    @classmethod
    def from_config(cls, cfg: dict) -> "Filters":
        loc, title = cfg["location"], cfg["title"]
        lang = cfg.get("language", {})
        return cls(
            loc_include=_compile(loc["include"]) + _compile(loc.get("include_cs"), 0),
            loc_exclude=_compile(loc.get("exclude")) + _compile(loc.get("exclude_cs"), 0),
            loc_split=re.compile(loc.get("split_on", "[;|]")),
            title_strong=_prefix(title["strong"]) + _words(title.get("strong_words")),
            title_weak=_prefix(title["weak"]) + _words(title.get("weak_words")),
            title_exclude=(
                _prefix(title["exclude"])
                + _words(title.get("exclude_words"))
                + _words(title.get("exclude_words_cs"), 0)
            ),
            student=(
                _words(title["student"])
                + _compile(title.get("student_regex"))
            ),
            blocklist=[norm_text(c) for c in cfg["company"]["blocklist"]],
            drop_french=lang.get("drop_french_titles", False),
            french=re.compile(lang.get("french_pattern", "$^"), re.IGNORECASE),
            english_roles=_words(lang.get("english_role_words")),
            quebec=_compile(loc.get("quebec")) + _compile(loc.get("quebec_cs"), 0),
            country_only=_compile(loc.get("country_only", [r"\bCanada\b"])),
            unresolved=re.compile(loc["unresolved"], re.IGNORECASE) if loc.get("unresolved") else None,
            title_exclude_phrases=[re.compile(r"\b" + re.escape(fold(p)) + r"\b") for p in title.get("exclude_phrases") or []],
            french_first=re.compile(lang.get("french_first_segment_pattern", "$^"), re.IGNORECASE),
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
        return not _any(self.title_exclude_phrases, folded)

    def not_student_only(self, title: str) -> bool:
        return not _any(self.student, title)
