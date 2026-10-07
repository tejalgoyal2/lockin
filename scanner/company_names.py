"""Readable company names for the Notion `Company` column (SPEC §6a)."""
import re
from pathlib import Path

import yaml

from scanner.models import Job
from scanner.normalize import norm_text

_ROOT = Path(__file__).resolve().parent.parent
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sept", "oct", "nov", "dec")


def load_overrides(path: str | Path | None = None) -> dict[str, str]:
    """company_names.yaml: {slug: Display Name}. Keys are normalised the way slugs are compared."""
    with open(path or _ROOT / "company_names.yaml", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return {norm_text(k): str(v) for k, v in raw.items()}


def prettify(slug: str) -> str:
    """'periodic-labs' -> 'Periodic Labs'. Text that already has capitals is left alone."""
    text = re.sub(r"[-_.]+", " ", slug or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text.title() if text == text.lower() else text


def display_company(job: Job, overrides: dict[str, str]) -> str:
    """Owner override map > readable name from Simplify / the ATS response > prettified slug."""
    return (overrides.get(norm_text(job.company))
            or (job.company_name or "").strip()
            or prettify(job.company))


def date_tag(first_seen) -> str:
    """'oct6', 'sept20': lowercase month abbreviation + day, no space, no leading zero."""
    return f"{_MONTHS[first_seen.month - 1]}{first_seen.day}"


def company_cell(job: Job, overrides: dict[str, str]) -> str:
    """'<Company> · <date>', e.g. 'RBC · oct6'."""
    return f"{display_company(job, overrides)} · {date_tag(job.first_seen)}"
