"""Y Combinator's job pages (https://www.ycombinator.com/jobs, the Work at a Startup listings).

The Canada, Toronto and Vancouver pages embed their postings as JSON in the page (`data-page`), with the company,
title, location, how long ago it was posted and the minimum experience the employer asks for. A posting's own page
adds the description and the skills. Only those fields are read: the page also carries signed image URLs for the
hiring manager, which are never kept.
"""
import html
import json
import re
from datetime import datetime

from scanner.sources.listing import parse_age, years_required

SOURCE = "YC"
BASE = "https://www.ycombinator.com"
_DATA_PAGE = re.compile(r'data-page="([^"]+)"')


class YcPageError(Exception):
    """The page no longer carries its data: the site changed."""


def page_props(page_html: str) -> dict:
    m = _DATA_PAGE.search(page_html or "")
    if not m:
        raise YcPageError("no data-page JSON in the page")
    return json.loads(html.unescape(m.group(1))).get("props") or {}


def parse_list(page_html: str, now: datetime) -> list[dict]:
    """One location page -> records."""
    out = []
    for p in page_props(page_html).get("jobPostings") or []:
        url = p.get("url") or ""
        if not (p.get("title") and url.startswith("/companies/")):
            continue
        out.append({
            "company": (p.get("companyUrl") or "").rsplit("/", 1)[-1] or p.get("companyName", ""),
            "company_name": p.get("companyName") or "",
            "title": p["title"], "location": p.get("location") or "", "url": BASE + url, "source": SOURCE,
            "first_seen": parse_age(p.get("createdAt") or "", now),
            "min_experience": p.get("minExperience") or "",
        })
    return out


def fetch(pages: list[str], client, now: datetime) -> list[dict]:
    """The postings of each location page (`canada`, `toronto`, ...), once each."""
    seen, out = set(), []
    for slug in pages:
        for rec in parse_list(client.get(f"{BASE}/jobs/location/{slug}", SOURCE).text, now):
            if rec["url"] not in seen:
                seen.add(rec["url"])
                out.append(rec)
    return out


def experience_ok(rec: dict, drop_at: float) -> bool:
    """False when the employer asks for `drop_at` years or more (the same bar as the JD rules)."""
    years = years_required(rec.get("min_experience", ""))
    return years is None or years < drop_at


def _plain(markdown: str) -> str:
    text = (markdown or "").replace("\\\n", "\n")
    text = re.sub(r"^\s*#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\\-", "-", text, flags=re.MULTILINE)                  # escaped list markers
    return re.sub(r"\*\*|__", "", text).strip()


def parse_detail(page_html: str) -> tuple[str, str, list[str]]:
    """A posting's page -> (description with the skills appended, company name, locations)."""
    job = page_props(page_html).get("job") or {}
    parts = [_plain(job.get("description", ""))]
    if job.get("skills"):
        parts.append("Skills: " + ", ".join(job["skills"]))
    if years_required(job.get("minExperience", "")) is not None:         # "Any (new grads ok)" says nothing to score
        parts.append("Experience: " + job["minExperience"])
    where = [s.strip() for s in (job.get("location") or "").split(" / ") if s.strip()]
    return "\n\n".join(p for p in parts if p), job.get("companyName") or "", where
