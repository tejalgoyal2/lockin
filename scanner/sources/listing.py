"""Helpers shared by the job-site readers (YC, Built In, Bay Street Oracle, Hacker News)."""
import re
from datetime import datetime, timedelta

# Job sites that list postings of employers whose own ATS the scanner usually reads too. The one list: `ats` finds
# their pages by it, the Feed's Source column offers them, and `dedupe` lets an ATS posting win over them.
SITE_SOURCES = ("YC", "Built In", "Bay Street Oracle", "HN", "Job Bank", "BC Gov", "Canada List", "Jobicy", "Adzuna", "Google Jobs", "Google Alerts", "Site Search", "Jobhive")

_NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
                 "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
_UNIT_DAYS = {"minute": 1 / 1440, "hour": 1 / 24, "day": 1, "week": 7, "month": 30, "year": 365}
_AGE = re.compile(r"(?P<n>\d+|an?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+"
                  r"(?P<unit>minute|hour|day|week|month|year)s?\b", re.IGNORECASE)


def parse_age(text: str, now: datetime) -> datetime | None:
    """"2 Days Ago", "9 days", "about 2 months", "over 1 year", "Reposted 10 Hours Ago", "One Month Ago",
    "Yesterday", "Today", "less than a minute" -> the moment it describes, counted back from `now`.
    None when the text says nothing about age. Months and years are approximate (30 and 365 days): they only ever
    land far outside the 3-day freshness window, where the exact day does not matter."""
    t = re.sub(r"\s+", " ", (text or "").strip().lower())
    if not t:
        return None
    if t in ("today", "just now") or t.startswith("less than a minute") or t.startswith("a few seconds"):
        return now
    if "yesterday" in t:
        return now - timedelta(days=1)
    m = _AGE.search(t)
    if not m:
        return None
    raw = m.group("n").lower()
    n = int(raw) if raw.isdigit() else _NUMBER_WORDS[raw]
    return now - timedelta(days=n * _UNIT_DAYS[m.group("unit").lower()])


def years_required(text: str) -> float | None:
    """The lower bound in YC's "minExperience" ("3+ years", "1+ years", "Any (new grads ok)"): None when there is none."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*\+?\s*(?:-\s*\d+\s*)?years?", text or "", re.IGNORECASE)
    return float(m.group(1)) if m else None


def raw_fields(rec: dict) -> dict:
    """Pipeline hook shared by the site readers: a record -> the fields run_stream needs."""
    from scanner.normalize import clean_title
    return {
        "company": rec["company"],
        "company_name": rec.get("company_name", ""),
        "title": clean_title(rec["title"]),
        "location": rec["location"],
        "url": rec["url"],
        "source": rec["source"],
        "first_seen": rec["first_seen"],
        "pay": rec.get("pay", ""),
    }
