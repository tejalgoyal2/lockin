"""Filter audit: which title rules earn their keep, and which discarded jobs look like the ones you want.

`python -m scanner audit` reads the sources without a job description and, for every fresh Canadian posting, records
which phase-1 rule stopped it. Then it answers four questions the run report cannot:

1. **The funnel**: how many postings reach each rule, so a rule that removes half of everything is visible.
2. **Which exclude words cost the most**: "senior" removes thousands of jobs and is right; "lead" or "expert" might
   remove a few that were fine. For each word: how many titles it removed, how many it removed *alone* (nothing else
   would have), and sample titles. Alone-kills are the only ones a rule change could bring back.
3. **Near misses**: discarded titles that carry an entry-level word (junior, graduate, associate, entry, new grad ...):
   the jobs most likely to be wrongly lost.
4. **Words the title filter does not know**: the commonest words in titles that have no tech-role word but still sound
   technical (IT, systems, network, cloud ...), so a new word ("firmware", "application") can be considered with the
   evidence in front of you.

Idea from career-ops' filter-audit (measure which keywords earn their keep). Nothing is written to Notion or config.
"""
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from scanner.filters import Filters, fold

ENTRY_WORDS = re.compile(r"\b(junior|jr\.?|graduate|new grad|entry[- ]level|entry|associate|early career|trainee|apprentice|"
                         r"level (?:1|i)|l1)\b|\bI$", re.IGNORECASE)
# A discarded title with no tech-role word is only a near miss when it still sounds technical.
TECH_HINT = re.compile(r"\b(it|network\w*|systems?|applications?|firmware|embedded|infrastructure|technolog\w*|web|digital|"
                       r"cloud|computer|programm\w*|software|sql|python|java|\.net|api|devops|qa|test\w*|ux|ui|"
                       r"cyber\w*|information|informatics|algorithm\w*|automation|robotic\w*|hardware|electronics)\b", re.IGNORECASE)
STOP = frozenset("""a an and or of the for to in at on with as by from de du la le les et en des un une
canada canadian remote hybrid ca ontario toronto vancouver calgary ottawa montreal bc on ab id ii iii iv
full part time temporary contract permanent fixed term position job role opportunity new
manager senior associate director sales assistant lead business account management product representative consultant
operations coordinator service services project executive development technician advisor financial customer clerk
nurse driver registered clinical care health medical store retail branch team group global regional senior junior
operator warehouse general supervisor officer administrator administrative accounting finance human resources
specialist officer education teacher student support co-op coop intern internship summer winter fall spring
contractor casual relief seasonal site area district city centre center unit shift night day weekend""".split())
SAMPLES = 4


def classify(title: str, filters: Filters) -> tuple[str, list[str]]:
    """(verdict, rules): "kept" | "no_tech_word" | "excluded" | "student"; rules = every exclude rule that matches."""
    tier = filters.title_tier(title)
    reasons = filters.all_exclusion_reasons(title)
    if tier is None:
        return "no_tech_word", reasons
    if reasons:
        return "excluded", reasons
    if not filters.not_student_only(title):
        return "student", []
    return "kept", []


class Audit:
    def __init__(self, filters: Filters, now: datetime, days: float):
        self.filters, self.cutoff, self.days = filters, now - timedelta(days=days), days
        self.funnel = Counter()
        self.by_rule: dict[str, dict] = defaultdict(lambda: {"all": 0, "alone": 0, "samples": []})
        self.near: list[tuple[str, str, str, str]] = []
        self.unknown_words: Counter = Counter()
        self.unknown_samples: dict[str, list[str]] = defaultdict(list)
        self._titles: set[str] = set()
        self.excluded_words = {p.pattern.replace("\\b", "").replace("\\", "").lower() for p in filters.title_exclude}

    def add(self, f: dict) -> None:
        """One record's pipeline fields (company, title, location, source, first_seen)."""
        self.funnel["records"] += 1
        if f["first_seen"] is None or f["first_seen"] < self.cutoff:
            return
        self.funnel["fresh"] += 1
        status, _ = self.filters.location_status(f["location"])
        if status != "ok" or not self.filters.title_location_ok(f["title"]):
            return                        # not Canadian, Quebec only, or "N Locations" (resolved from the JD in a real run)
        self.funnel["canadian"] += 1
        if not self.filters.company_ok(f["company"]) or not self.filters.language_ok(f["title"]):
            return
        self.funnel["company+language ok"] += 1
        title = f["title"]
        sig = f"{f['company']}|{title}".lower()
        if sig in self._titles:
            return
        self._titles.add(sig)
        verdict, rules = classify(title, self.filters)
        self.funnel[f"-> {verdict}"] += 1
        label = f["company_name"] or f["company"]
        if verdict == "excluded":
            for rule in set(rules):
                row = self.by_rule[rule]
                row["all"] += 1
                if len(set(rules)) == 1:
                    row["alone"] += 1
                    if len(row["samples"]) < SAMPLES:
                        row["samples"].append(f"{title} ({label})")
        if (ENTRY_WORDS.search(title) and (verdict == "excluded" or (verdict == "no_tech_word" and TECH_HINT.search(title)))
                and not re.search(r"\b(senior|staff|principal|manager|director|sr)\b", title, re.IGNORECASE)):
            self.near.append((title, label, f["location"], verdict + (f": {', '.join(sorted(set(rules)))}" if rules else "")))
        if verdict == "no_tech_word" and TECH_HINT.search(title):        # AgeCare's nursing aides would drown the rest
            for w in set(re.findall(r"[a-z][a-z+#.]{1,}", fold(title))):
                if w not in STOP and w not in self.excluded_words:
                    self.unknown_words[w] += 1
                    if len(self.unknown_samples[w]) < 3:
                        self.unknown_samples[w].append(title)

    def render(self) -> str:
        fn = self.funnel
        out = [f"# Filter audit: fresh Canadian postings of the last {self.days:g} days", "",
               "Which phase-1 rule stops them, before any job description is read (the JD rules come after, see the run report).", "",
               "## Funnel", "", "| step | postings |", "|---|---:|"]
        for k in ("records", "fresh", "canadian", "company+language ok", "-> kept", "-> student", "-> excluded", "-> no_tech_word"):
            out.append(f"| {k} | {fn[k]:,} |")
        out += ["", "`kept` goes on to the JD rules; `student` is held for the JD (kept only if it welcomes recent graduates); "
                "`excluded` has a title-exclude word; `no_tech_word` has no tech-role word in the title.", "",
                "## Title-exclude words, by jobs removed", "",
                "`alone` = the only rule that removed the job: those are the ones changing the rule would bring back.", "",
                "| word | removed | alone | alone examples |", "|---|---:|---:|---|"]
        for rule, row in sorted(self.by_rule.items(), key=lambda kv: -kv[1]["alone"])[:30]:
            out.append(f"| {rule} | {row['all']:,} | {row['alone']:,} | {'; '.join(row['samples'])} |".replace("|  |", "| |"))
        out += ["", f"## Near misses ({len(self.near)}): discarded titles with an entry-level word", "",
                "Most are right to lose (the exclude word is the point). The ones that look wrong are a rule to loosen.", ""]
        for title, label, loc, why in sorted(self.near)[:60]:
            out.append(f"- **{title}**: {label}, {loc[:40]}: _{why}_")
        out += ["", "## Words the title filter does not know", "",
                "The commonest words in titles that have no tech-role word but sound technical. A word that is clearly a software or data role "
                "belongs in `title.strong` or `title.weak` in config.yaml.", "", "| word | titles | examples |", "|---|---:|---|"]
        for word, n in self.unknown_words.most_common(40):
            out.append(f"| {word} | {n:,} | {'; '.join(self.unknown_samples[word])} |")
        return "\n".join(out) + "\n"
