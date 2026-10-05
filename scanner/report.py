from datetime import datetime
from pathlib import Path

from scanner.models import Job

STAGE_LABELS = {
    "raw": "raw records",
    "active_category": "active, visible, tech category",
    "fresh": "first seen within window",
    "location": "Canadian location",
    "company": "not on blocklist",
    "language": "not French-only",
    "title_match": "title matches tech roles",
    "title_not_senior": "not senior / non-software",
    "not_student_only": "not student-only",
}


def _cell(s: str) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ")


def format_counts(name: str, stages: list[tuple[str, int]]) -> str:
    lines = [f"{name}:"]
    lines += [f"  {STAGE_LABELS.get(k, k):<34}{v:>10,}" for k, v in stages]
    return "\n".join(lines)


def render(jobs: list[Job], since_days: float, now: datetime, meta: dict | None,
           stage_blocks: dict[str, list[tuple[str, int]]], total_deduped: int) -> str:
    out = [
        "# Canadian entry-level tech jobs: candidates",
        "",
        f"- Generated: {now:%Y-%m-%d %H:%M} UTC",
        f"- Window: first seen in the last {since_days:g} day(s)",
        f"- Feashliaa dataset last updated: {meta['last_updated'] if meta else 'n/a'}",
        f"- Candidates after dedupe: **{total_deduped}**",
        "",
        "| First Seen | Company | Role | Location | Source | URL |",
        "|---|---|---|---|---|---|",
    ]
    for j in jobs:
        out.append(
            f"| {j.first_seen:%Y-%m-%d} | {_cell(j.company)} | {_cell(j.title)} | "
            f"{_cell(j.location)} | {j.source_label()} | {j.url} |"
        )
    out += ["", "## Stage counts", ""]
    for name, stages in stage_blocks.items():
        out += [f"**{name}**", "", "| Stage | Remaining |", "|---|---:|"]
        out += [f"| {STAGE_LABELS.get(k, k)} | {v:,} |" for k, v in stages]
        out.append("")
    return "\n".join(out) + "\n"


def write(path: str | Path, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
