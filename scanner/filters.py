"""Config-driven title / location / company / language filters (SPEC §4).

Each predicate returns True when the record should be KEPT.
"""
import re
from dataclasses import dataclass

from scanner.normalize import norm_text


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
        )

    # -- location -----------------------------------------------------------
    def canadian_location(self, location: str) -> str | None:
        """Return the first Canadian part of a (possibly multi-location) string, else None."""
        for part in self.loc_split.split(location or ""):
            part = part.strip()
            cleaned = part
            for pat in self.loc_exclude:
                cleaned = pat.sub(" ", cleaned)
            if _any(self.loc_include, cleaned):
                return part
        return None

    # -- company ------------------------------------------------------------
    def company_ok(self, company: str) -> bool:
        n = norm_text(company)
        return not any(b in n for b in self.blocklist)

    # -- title --------------------------------------------------------------
    def language_ok(self, title: str) -> bool:
        if not self.drop_french or not self.french.search(title):
            return True
        return _any(self.english_roles, title)

    def title_tier(self, title: str) -> str | None:
        """'strong' passes outright, 'weak' matches only generic words, None is no match."""
        if _any(self.title_strong, title):
            return "strong"
        return "weak" if _any(self.title_weak, title) else None

    def title_not_excluded(self, title: str) -> bool:
        return not _any(self.title_exclude, title)

    def not_student_only(self, title: str) -> bool:
        return not _any(self.student, title)
