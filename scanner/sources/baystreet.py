"""Bay Street Oracle (https://www.baystreetoracle.ca/jobs): Canadian capital-markets jobs.

The site lists its postings in a sitemap (robots.txt points to it). A posting's URL carries the company, title and
city as words, which is enough to skip the investment-banking majority without a request each; the page of each
remaining posting has a schema.org `JobPosting` (title, employer, city, date posted, description) and the link to
apply on the employer's own site, which is used as the job's link when it is an ATS the scanner can read.
"""
import json
import re

import requests
from collections.abc import Callable
from datetime import datetime, timezone
from urllib.parse import unquote

from scanner.normalize import parse_iso
from scanner.text import html_to_text

SOURCE = "Bay Street Oracle"
_LOC = re.compile(r"<loc>\s*(.*?)\s*</loc>")
_LD = re.compile(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_APPLY = re.compile(r'<a[^>]+href="(https?://[^"]+)"[^>]*>(?:\s|<[^>]+>)*[^<]*\bApply\b', re.I)


def posting_urls(sitemap_xml: str) -> list[str]:
    """The sitemap's posting pages (`/jobs/<company>-<title>-<city>--<uuid>`), not its category pages."""
    return [u for u in _LOC.findall(sitemap_xml or "") if "/jobs/" in u and "--" in u.rsplit("/", 1)[-1]]


def slug_words(url: str) -> str:
    """"…/jobs/bmo-capital-markets-investment-banking-analyst-toronto--2003e5d9-…" -> "bmo capital markets …"."""
    return unquote(url.rsplit("/", 1)[-1].split("--", 1)[0]).replace("-", " ")


def parse_detail(page_html: str, now: datetime) -> dict | None:
    """A posting's page -> a record plus its description, or None when the page has no JobPosting."""
    for block in _LD.findall(page_html or ""):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "JobPosting":
            break
    else:
        return None
    org = data.get("hiringOrganization") or {}
    addr = ((data.get("jobLocation") or {}).get("address")) or {}
    where = ", ".join(x for x in (addr.get("addressLocality"), addr.get("addressRegion")) if x)
    apply_links = [u for u in _APPLY.findall(page_html) if "baystreetoracle.ca" not in u]
    try:
        posted = parse_iso(data["datePosted"]) if data.get("datePosted") else None
    except ValueError:
        posted = None
    return {
        "company": org.get("name") or "", "company_name": org.get("name") or "", "title": data.get("title") or "",
        "location": where or "Canada", "url": data.get("url") or "", "source": SOURCE,
        "first_seen": posted if posted is None or posted.tzinfo else posted.replace(tzinfo=timezone.utc),
        "description": html_to_text(data.get("description") or ""), "apply_url": apply_links[0] if apply_links else "",
    }


def fetch(sitemap_url: str, client, now: datetime, keep: Callable[[str], bool], supported: Callable[[str], bool]
          ) -> list[dict]:
    """Postings whose URL words pass `keep` (a title filter), each read from its page. A posting whose apply link
    is on an ATS the scanner reads (`supported(url)`) is linked there, so the JD fetch and the duplicate check
    work as for any other job from that ATS; otherwise it keeps the Bay Street Oracle page."""
    out = []
    for url in posting_urls(client.get(sitemap_url, SOURCE).text):
        if not keep(slug_words(url)):
            continue
        try:
            page = client.get(url, SOURCE).text
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code in (404, 410):
                continue                       # the sitemap still lists a posting that is gone: skip it, keep the rest
            raise
        rec = parse_detail(page, now)
        if rec is None or not rec["title"]:
            continue
        rec["url"] = rec.pop("apply_url") if rec["apply_url"] and supported(rec["apply_url"]) else (rec["url"] or url)
        rec.pop("apply_url", None)
        rec.pop("description", None)
        out.append(rec)
    return out
