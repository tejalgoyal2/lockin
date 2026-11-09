"""Find an employer's job board, and explain why a posting was or was not picked up.

`python -m scanner discover "Company A" "Company B"` guesses the board slug from the company name, asks every
ATS the watchlist can read (Greenhouse, Lever, Ashby, SmartRecruiters) and prints the `watchlist.yaml` lines that
work, with how many Canadian and how many tech-entry postings each board has right now. `--workday
<tenant>.<wdN>/<site>` checks a Workday tenant (the slug is in the employer's careers URL).

`python -m scanner why "Company"` reads the same board live and runs each Canadian posting through the phase-1
filters, printing the first rule that stops it. It answers "the employer posted a job, why is it not in the Feed?"
without reading logs: either the employer is not on the watchlist (a coverage gap: add it), or a filter drops it
(a rule to tune), or the posting is older than the freshness window.

Idea borrowed from career-ops (discover-ats, coverage-check): resolve a company to a scannable board, and when a
posting is missed say which channel missed it. Nothing here writes to Notion or edits watchlist.yaml.
"""
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

from scanner.filters import Filters
from scanner.sources import watchlist
from scanner.sources.watchlist import Board

PROBE_ORDER = ("greenhouse", "lever", "ashby", "smartrecruiters")
WD_HOSTS = (1, 3, 5, 10, 12, 103, 501)          # the data centres Workday tenants live in: <tenant>.wd<N>.myworkdayjobs.com
# Paths a tenant's robots.txt disallows that are not career sites.
_NOT_A_SITE = {"refreshfacet", "wday", "login", "externalprivateposting", "externalprivatepostingstudents", "static",
               "assets", "api", "recruiting", "healthcheck", "pages"}


@dataclass
class Hit:
    board: Board
    total: int            # postings the board lists (Canada only for Workday and SmartRecruiters)
    canadian: int         # ... whose location passes the Canadian filter
    candidates: int       # ... Canadian postings that also pass the title filters (what phase 1 would keep)

    def yaml_line(self) -> str:
        b = self.board
        return (f"  - {{ats: {b.ats}, slug: {b.slug}, name: {b.name or b.slug!r}}}"
                f"        # {self.canadian} Canadian postings, {self.candidates} pass the title filters")


def slug_guesses(name: str) -> list[str]:
    """Plausible board slugs for a company name: "Arc'teryx Inc." -> arcteryx, arcteryx-inc, arcteryx.com ..."""
    words = re.findall(r"[a-z0-9]+", re.sub(r"['’.]", "", name.lower()))
    words = [w for w in words if w not in {"inc", "ltd", "corp", "corporation", "co", "llc", "company", "the"}] or words
    if not words:
        return []
    out = ["".join(words), "-".join(words), words[0]]
    return list(dict.fromkeys(g for g in out if g))


def _summarise(board: Board, records: list[dict], filters: Filters, now: datetime) -> Hit:
    canadian = candidates = 0
    for rec in records:
        status, _ = filters.location_status(rec["location"])
        if status != "ok" or not filters.title_location_ok(rec["title"]):
            continue                      # "N Locations" rows are not counted: a US employer lists hundreds of them
        canadian += 1
        if filters.title_tier(rec["title"]) and filters.title_not_excluded(rec["title"]):
            candidates += 1
    return Hit(board, len(records), canadian, candidates)


def probe(name: str, client, filters: Filters, now: datetime, *, slugs: list[str] | None = None,
          ats_list=PROBE_ORDER, workday: bool = True) -> list[Hit]:
    """Every board that exists for this company and lists at least one posting (Workday too: the tenant is guessed
    from the name, its career sites come from its robots.txt)."""
    hits = []
    for slug in slugs or slug_guesses(name):
        if workday and re.fullmatch(r"[a-z0-9]+", slug):
            hits += probe_workday_tenant(slug, name, client, filters, now)
        for ats in ats_list:
            board = Board(ats, slug, name)
            try:
                records = watchlist.fetch_board(board, client, now=now)
            except Exception:                         # 404 = no such board; anything else = cannot tell: next guess
                continue
            if records:
                hits.append(_summarise(board, records, filters, now))
    return sorted(hits, key=lambda h: (-h.canadian, -h.total))


def workday_sites(tenant: str, client, wds=WD_HOSTS) -> list[tuple[str, str]]:
    """[(wdN, site)] of a Workday tenant. A tenant's robots.txt lists its career sites as `Allow:` / `Disallow: /<site>/` and `Sitemap:` lines; a
    tenant that does not exist answers 422, so trying each data centre finds both the tenant's home and its sites."""
    out = []
    for n in wds:
        try:
            text = client.get(f"https://{tenant}.wd{n}.myworkdayjobs.com/robots.txt", "workday").text
        except Exception:                                  # 422 / 404 / refused: not this data centre
            continue
        sites = re.findall(r"^\s*(?:Allow|Disallow):\s*/([A-Za-z0-9_.-]+)/?\s*$", text, re.M | re.I)
        sites += re.findall(r"^\s*Sitemap:\s*\S+?\.com/([A-Za-z0-9_.-]+)/siteMap\.xml", text, re.M | re.I)
        for site in dict.fromkeys(sites):
            if site.lower() not in _NOT_A_SITE and "private" not in site.lower():
                out.append((f"wd{n}", site))
        if out:
            break
    return out


def probe_workday_tenant(tenant: str, name: str, client, filters: Filters, now: datetime) -> list[Hit]:
    """Every career site of a tenant that lists postings, found without knowing the site name."""
    hits = []
    for wd, site in workday_sites(tenant, client):
        try:
            hit = probe_workday(f"{tenant}.{wd}/{site}", name, client, filters, now)
        except Exception:
            continue
        if hit:
            hits.append(hit)
    return sorted(hits, key=lambda h: (-h.canadian, -h.total))


def probe_workday(slug: str, name: str, client, filters: Filters, now: datetime) -> Hit | None:
    board = Board("workday", slug, name)
    board.workday                                                  # validates the slug shape
    records = watchlist.fetch_board(board, client, now=now)
    return _summarise(board, records, filters, now) if records else None


def probe_successfactors(host: str, name: str, client, filters: Filters, now: datetime) -> Hit | None:
    """A SuccessFactors careers site: the slug is the host, e.g. careers.telus.com."""
    board = Board("successfactors", host, name)
    records = watchlist.fetch_board(board, client, now=now)
    return _summarise(board, records, filters, now) if records else None


def probe_phenom(slug: str, name: str, client, filters: Filters, now: datetime) -> Hit | None:
    """A Phenom careers site: slug is <host>/<country>/<language>, e.g. jobs.bell.ca/ca/en."""
    board = Board("phenom", slug, name)
    records = watchlist.fetch_board(board, client, now=now)
    return _summarise(board, records, filters, now) if records else None


# --- why -----------------------------------------------------------------------------------------

def explain(rec: dict, filters: Filters, now: datetime, since_days: float) -> str:
    """The first phase-1 rule a record breaks, in pipeline order ("kept" when it passes all of them). Phase 2
    (JD rules: years of experience, enrolment, French ...) needs the JD and is not run here."""
    seen = rec.get("first_seen")
    if seen is None:
        return "no posting date"
    if seen < now - timedelta(days=since_days):
        return f"older than the {since_days:g}-day window (posted {seen:%Y-%m-%d})"
    status, _ = filters.location_status(rec["location"])
    if status == "not_canada" or not filters.title_location_ok(rec["title"]):
        return f"location: {rec['location']!r} is not Canadian"
    if status == "quebec_only":
        return "location: Quebec only"
    if not filters.company_ok(rec.get("company", "")):
        return "company is on the blocklist"
    if not filters.language_ok(rec["title"]):
        return "title is French only"
    if filters.title_tier(rec["title"]) is None:
        return "title: no tech-role word"
    if not filters.title_not_excluded(rec["title"]):
        return "title: contains an excluded word (senior, lead, manager, II/III ...)"
    if not filters.not_student_only(rec["title"]):
        return "title: student-only role (kept aside; kept only if the JD welcomes recent graduates)"
    return "kept (phase 2 reads the JD next)"


def why(hits: list[Hit], client, filters: Filters, now: datetime, since_days: float) -> str:
    lines, tally = [], Counter()
    for hit in hits:
        records = watchlist.fetch_board(hit.board, client, now=now)
        lines.append(f"{hit.board.ats}/{hit.board.slug}: {len(records)} postings listed")
        for rec in records:
            status, _ = filters.location_status(rec["location"])
            if status in ("not_canada",) and "Locations" not in rec["location"]:
                continue                                   # not a Canadian job: nothing to explain
            verdict = explain(rec, filters, now, since_days)
            tally[verdict.split(":")[0].split(" (")[0]] += 1
            lines.append(f"  {verdict:<70} {rec['title'][:60]} | {rec['location'][:40]}")
    if tally:
        lines.append("summary: " + ", ".join(f"{n} {k}" for k, n in tally.most_common()))
    return "\n".join(lines)
