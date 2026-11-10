"""Jobicy (https://jobicy.com): remote jobs open to people in Canada, read through its public JSON API.

`GET /api/v2/remote-jobs?count=100&geo=canada[&industry=<slug>]` lists the newest remote postings whose employer accepts
Canada; the API is free, needs no key and asks for at most one request an hour per query, which two runs a day respect.
A posting's page carries a schema.org `JobPosting` block (JSON-LD) with the full description, which is where the JD is read.
"""
import html
import json
import re
from datetime import datetime

from scanner.normalize import parse_iso
from scanner.text import html_to_text

SOURCE = "Jobicy"
API = "https://jobicy.com/api/v2/remote-jobs"
INDUSTRIES = ("", "dev", "data-science", "cybersecurity", "engineering")      # "" = every industry
_LD = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)


class JobicyError(Exception):
    """The API answer or a posting page is not what it used to be."""


def parse(answer: dict) -> list[dict]:
    jobs = (answer or {}).get("jobs")
    if jobs is None:
        raise JobicyError("no jobs list in the answer: the API changed")
    out = []
    for j in jobs:
        title, url = html.unescape(j.get("jobTitle") or ""), j.get("url")
        if not (title and url):
            continue
        geo = [g.strip() for g in str(j.get("jobGeo") or "").split(",") if g.strip()]
        try:
            when = parse_iso(j["pubDate"]) if j.get("pubDate") else None
        except ValueError:
            when = None
        name = html.unescape(j.get("companyName") or "")
        out.append({"company": name or "Jobicy employer", "company_name": name, "title": title, "url": url,
                    "location": "Remote, Canada" if any(g.lower() == "canada" for g in geo) else ", ".join(geo),
                    "source": SOURCE, "first_seen": when})
    return out


def fetch(client, now: datetime, count: int = 100) -> list[dict]:
    seen, out = set(), []
    for industry in INDUSTRIES:
        url = f"{API}?count={count}&geo=canada" + (f"&industry={industry}" if industry else "")
        for rec in parse(client.get_json(url, SOURCE)):
            if rec["url"] not in seen:
                seen.add(rec["url"])
                out.append(rec)
    return out


def parse_detail(page_html: str) -> str:
    """A posting's page -> the description as plain text (from its JobPosting JSON-LD)."""
    for m in _LD.finditer(page_html or ""):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        for item in (data if isinstance(data, list) else data.get("@graph") or [data]):
            if isinstance(item, dict) and item.get("@type") == "JobPosting" and item.get("description"):
                return html_to_text(html.unescape(item["description"]))
    raise JobicyError("no JobPosting description on the page: the markup changed")
