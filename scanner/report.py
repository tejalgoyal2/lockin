import random
from datetime import datetime
from pathlib import Path

from scanner.models import Job

STAGE_LABELS = {
    "raw": "raw records",
    "active_category": "active, visible, tech category",
    "fresh": "first seen within window",
    "location": "Canadian location",
    "not_quebec_only": "not Quebec-only",
    "wanted_area": "BC, Alberta, Ontario or remote",
    "company": "not on blocklist",
    "language": "not French-only",
    "title_match": "title matches tech roles",
    "title_not_senior": "not senior / non-software",
    "not_student_only": "not an intern / co-op / student title",
    "weak_title": "  of which not a core title",
    "before dedupe": "before dedupe",
    "merged_url": "  merged by canonical URL",
    "merged_fuzzy": "  merged by fuzzy company+title",
}


def _cell(s: str) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ")


def format_counts(name: str, stages: list[tuple[str, int]]) -> str:
    lines = [f"{name}:"]
    lines += [f"  {STAGE_LABELS.get(k, k):<34}{v:>10,}" for k, v in stages]
    return "\n".join(lines)


def sample_rows(jobs: list[Job], n: int, seed: int | None = None) -> list[Job]:
    rng = random.Random(seed)
    return rng.sample(jobs, min(n, len(jobs)))


def format_sample(jobs: list[Job]) -> str:
    lines = []
    for j in jobs:
        lines.append(
            f"- {j.company} | {j.title} | {j.location} | {j.source_label()}\n"
            f"    Score {j.rating} | Exp {j.exp} | {', '.join(j.provinces)} | {j.role} | {', '.join(j.notes) or '-'}\n"
            f"    Matched: {', '.join(j.matched[:8]) or '-'}\n"
            f"    Gaps: {', '.join(j.gaps[:8]) or '-'}")
    return "\n".join(lines)


def render(jobs: list[Job], since_days: float, now: datetime, meta: dict | None,
           stage_blocks: dict[str, list[tuple[str, int]]], total_deduped: int,
           phase2: dict | None = None) -> str:
    """The run's full list, best first, with what each Score was built from. `phase2` (optional): the counters of
    scanner.enrich plus {"ratings": [...], "applied": {...}}."""
    out = [
        "# Canadian entry-level tech jobs: candidates",
        "",
        f"- Generated: {now:%Y-%m-%d %H:%M} UTC",
        f"- Window: first seen in the last {since_days:g} day(s)",
        f"- Feashliaa dataset last updated: {meta['last_updated'] if meta else 'n/a'}",
        f"- New candidates after dedupe: **{total_deduped}**",
    ]
    if phase2:
        out.append(f"- Kept by the JD rules: **{len(jobs)}** (best Score first)")
        applied = phase2.get("applied") or {}
        if sum(applied.values()):
            out.append(f"- Skipped as already applied to: {sum(applied.values())} "
                       f"(by link {applied['url']}, by company + title {applied['company+title']})")
    out += ["", "Score = role + skills + level (scanner/rating.py). Fit is the share of the tech the posting names that "
                "is in skills.yaml; Skills counts your tools it names.", "",
            "| Score | Exp | Province | Posted | Company | Role | Title tier | Fit | Skills | Notes | Gaps | Source | URL |"
            if phase2 else "| Posted | Company | Role | Location | Title tier | Source | URL |",
            "|---:|---:|---|---|---|---|---|---:|---:|---|---|---|---|" if phase2 else "|---|---|---|---|---|---|---|"]
    for j in jobs:
        if phase2:
            fit = "" if j.fit is None else f"{j.fit:.0%}"
            out.append(f"| {j.rating} | {j.exp} | {', '.join(j.provinces)} | {j.first_seen:%Y-%m-%d} | {_cell(j.company)} | "
                       f"{_cell(j.title)} | {j.role} | {fit} | {j.hard} | {', '.join(j.notes)} | "
                       f"{_cell(', '.join(j.gaps[:6]))} | {j.source_label()} | {j.url} |")
        else:
            out.append(f"| {j.first_seen:%Y-%m-%d} | {_cell(j.company)} | {_cell(j.title)} | {_cell(j.location)} | "
                       f"{'generic or adjacent' if j.weak_title else 'core'} | {j.source_label()} | {j.url} |")
    out += ["", "## Stage counts", ""]
    for name, stages in stage_blocks.items():
        out += [f"**{name}**", "", "| Stage | Remaining |", "|---|---:|"]
        out += [f"| {STAGE_LABELS.get(k, k)} | {v:,} |" for k, v in stages]
        out.append("")
    if phase2:
        out += ["## JD rules", "", "**Drop reasons** (the jobs are in reports/dropped.md)", "", "| Reason | Jobs |", "|---|---:|"]
        out += [f"| {k} | {v} |" for k, v in sorted(phase2["drops"].items(), key=lambda kv: -kv[1])] or ["| none | 0 |"]
        out += ["", "**JD fetch status** (source:status)", "", "| Source:status | Jobs |", "|---|---:|"]
        out += [f"| {k} | {v} |" for k, v in sorted(phase2["fetch"].items())]
        if phase2.get("unresolved_locations"):
            out += ["", "**Kept with an unresolved location** (Workday \"N Locations\": the list could not be read)", "",
                    "| Company | Role | URL |", "|---|---|---|"]
            out += [f"| {_cell(c)} | {_cell(t)} | {u} |" for c, t, u in phase2["unresolved_locations"]]
        if phase2.get("slug_names"):
            out += ["", "**Company names that look like raw slugs** (add them to `company_names.yaml`)", "",
                    "| Slug | Shown as |", "|---|---|"]
            out += [f"| {_cell(slug)} | {_cell(shown)} |" for slug, shown in phase2["slug_names"]]
        out += ["", "**Scores (kept jobs)**", "", "| Score | Jobs |", "|---|---:|"]
        out += [f"| {k} | {v} |" for k, v in phase2["ratings"]]
        out.append("")
    return "\n".join(out) + "\n"


def write(path: str | Path, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


DROP_NOTES = {
    "experience_too_high": "the JD asks for more experience than jd.max_years (2): 3 or more years, or a range that starts at 2",
    "not_a_tech_job": "the description does not show a tech job: the less the title says, the more of your tools it must name (jd.relevance)",
    "generic_title_no_description": "a generic title in a plainly non-tech field, and no description to show otherwise",
    "requires_enrollment": "the JD asks for a currently enrolled student",
    "requires_french": "the JD requires French",
    "security_clearance": "the JD requires a security clearance",
    "us_work_authorization": "the JD requires US work authorization",
    "location_quebec_only": "every location it lists is in Quebec",
    "location_other_province": "every location it lists is in a province outside BC, Alberta and Ontario",
    "location_ats_not_canada": "the employer's own location list has no Canadian place",
    "location_unresolved_url_not_canadian": "\"N Locations\", the description could not be read and the place in the link is not Canadian",
}
LOCATION_REASONS = ("location_not_canada",)          # the unresolved-location drops are listed: a Canadian job could hide there


def render_dropped(dropped: list, per_reason: int = 40) -> str:
    """reports/dropped.md: the jobs the JD rules dropped, by reason, so a wrong rule can be seen instead of guessed at. The
    location drops are only counted (thousands of foreign postings); every other reason lists its jobs, newest first."""
    from collections import defaultdict
    by: dict[str, list] = defaultdict(list)
    for reason, job in dropped:
        by[reason].append(job)
    lines = ["# Dropped by the JD rules", "",
             "Read this when you suspect a good job is being lost: each reason names the rule, then lists the jobs it dropped "
             f"(up to {per_reason}, newest first). A reason that drops a job you wanted is a rule to loosen in `config.yaml`.", ""]
    for reason, jobs in sorted(by.items(), key=lambda kv: -len(kv[1])):
        lines += [f"## {reason}: {len(jobs):,}", ""]
        if reason in LOCATION_REASONS:
            lines += ["Not a Canadian posting; not listed.", ""]
            continue
        if DROP_NOTES.get(reason):
            lines += [f"_{DROP_NOTES[reason]}_", ""]
        for job in sorted(jobs, key=lambda j: j.first_seen, reverse=True)[:per_reason]:
            years = f" ({job.drop_note})" if getattr(job, "drop_note", "") else ""
            lines.append(f"- [{_cell(job.title)}]({job.url}) | {_cell(job.company_name or job.company)} | "
                         f"{_cell(job.location)} | {job.first_seen:%Y-%m-%d}{years}")
        lines.append("")
    return "\n".join(lines) + "\n"
