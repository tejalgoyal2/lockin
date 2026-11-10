"""iCIMS career sites (careersen-victoria.icims.com, and the many hospitals, cities and companies on iCIMS).

An iCIMS careers site has a public search page, `https://<host>/jobs/search?ss=1&in_iframe=1&pr=<page>`: one row per
posting with its title, link and a few header fields (department, job type, close date, sometimes a location and a posted
date). The site's `sitemap.xml` lists every posting with the time it was last modified, which stands in for the posted date
when the row has none. A posting's page carries a schema.org `JobPosting` (date posted, location, the full description)
in a `<script type="application/ld+json">` block. A watchlist entry is the host: `{ats: icims, slug:
careersen-victoria.icims.com, name: City of Victoria, location: "Victoria, BC"}`; `location` is what a posting that
states none is given (a city's postings are all in that city).

The dataset carries about 250,000 iCIMS postings with no location, so none could ever pass the Canadian filter; reading the
site itself is the only way to see them.
"""
import html
import json
import re
import time
from datetime import datetime, timezone

from scanner.sources.listing import parse_age
from scanner.text import html_to_text

SOURCE = "iCIMS"
MAX_PAGES = 40
BOARD_SECONDS = 300
_HOST = re.compile(r"^[A-Za-z0-9.-]+\.icims\.com$")
_JOB_PATH = re.compile(r"^/jobs/\d+/[^/]*/job/?$")
_ROW = re.compile(r'<div class="row">(?:(?!<div class="row">).)*?class="iCIMS_Anchor".*?(?=<div class="row">|</li>|$)', re.S)
_LINK = re.compile(r'<a href="(https://[^"]+/jobs/\d+/[^"]*/job)[^"]*" class="iCIMS_Anchor"[^>]*>.*?<h3[^>]*>(.*?)</h3>', re.S)
_FIELD = re.compile(r'<dt class="iCIMS_JobHeaderField">(.*?)</dt>\s*<dd class="iCIMS_JobHeaderData">(.*?)</dd>', re.S)
_LABELLED = re.compile(r'<span class="sr-only field-label">(.*?)</span>\s*<span[^>]*>(.*?)</span>', re.S)
_SITEMAP = re.compile(r"<loc>([^<]*?/jobs/\d+/[^<]*?/job)</loc>\s*<lastmod>([^<]+)</lastmod>")
_LD = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)


class IcimsError(Exception):
    """The search page or a posting page is not what it used to be."""


def check_host(slug: str) -> str:
    if not _HOST.match(slug or ""):
        raise IcimsError(f"icims slug must be the careers host, e.g. careersen-victoria.icims.com, got {slug!r}")
    return slug


def is_job_url(url: str) -> bool:
    from urllib.parse import urlsplit
    parts = urlsplit(url or "")
    return parts.netloc.lower().endswith(".icims.com") and bool(_JOB_PATH.match(parts.path))


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment or ""))).strip()


def search_url(host: str, page: int) -> str:
    return f"https://{host}/jobs/search?ss=1&in_iframe=1&pr={page}"


def place(raw: str) -> str:
    """iCIMS writes locations "CA-BC-Victoria" (country-region-city), sometimes several joined by "|" or ";"."""
    parts = []
    for one in re.split(r"[|;]", raw or ""):
        m = re.match(r"^\s*([A-Z]{2})-([A-Z]{2})-(.+?)\s*$", one)
        parts.append(f"{m.group(3)}, {m.group(2)}" if m else one.strip())
    return " ; ".join(p for p in parts if p)


def _with_province(value: str, default_where: str) -> str:
    """A site that lists only the city ("Vancouver") gets the province of the entry's location ("Vancouver, BC" -> BC).
    "Various Locations" and the like name no place."""
    value = (value or "").strip()
    if re.search(r"\b(various|multiple|any|several)\b", value, re.IGNORECASE):
        return ""
    if value and "," not in value and "-" not in value and "," in default_where:
        return f"{value}, {default_where.rsplit(',', 1)[1].strip()}"
    return value


def _when(text: str, now: datetime) -> datetime | None:
    text = _text(text)
    for fmt in ("%m/%d/%Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text.split()[0] if fmt == "%m/%d/%Y" else text, fmt).replace(tzinfo=timezone.utc)
        except (ValueError, IndexError):
            continue
    return parse_age(text, now)


def parse_sitemap(xml_text: str) -> dict[str, datetime]:
    out = {}
    for url, mod in _SITEMAP.findall(xml_text or ""):
        try:
            out[url] = datetime.fromisoformat(mod.strip()).astimezone(timezone.utc)
        except ValueError:
            continue
    return out


def parse_search(host: str, name: str, page_html: str, now: datetime, modified: dict[str, datetime] | None = None,
                 default_where: str = "") -> list[dict]:
    """A search page -> records. Raises IcimsError when it has rows but none can be read."""
    modified = modified or {}
    rows = _ROW.findall(page_html or "")
    out = []
    for row in rows:
        link = _LINK.search(row)
        if not link:
            continue
        url = link.group(1)
        fields = {_text(k).lower(): _text(v) for k, v in _FIELD.findall(row)}
        fields.update({_text(k).lower(): _text(v) for k, v in _LABELLED.findall(row) if _text(k).lower() != "job title"})
        where = _with_province(next((v for k, v in fields.items() if "location" in k or k == "city"), ""), default_where)
        posted = next((v for k, v in fields.items() if "posted" in k or k in ("date", "post date")), "")
        out.append({"company": host, "company_name": name, "title": _text(link.group(2)), "url": url,
                    "location": place(where) or default_where,
                    "source": SOURCE, "first_seen": (_when(posted, now) if posted else None) or modified.get(url) or now})
    if rows and not out:
        raise IcimsError(f"{len(rows)} rows on the page, none readable: the markup changed")
    return out


def fetch_board(host: str, name: str, client, now: datetime, default_where: str = "") -> list[dict]:
    check_host(host)
    try:
        modified = parse_sitemap(client.get(f"https://{host}/sitemap.xml", SOURCE).text)
    except Exception:                                      # the sitemap only dates postings; the search page lists them
        modified = {}
    out, urls, started = [], set(), time.monotonic()
    for page in range(MAX_PAGES):
        if time.monotonic() - started > BOARD_SECONDS:
            break
        new = [r for r in parse_search(host, name, client.get(search_url(host, page), SOURCE).text, now, modified, default_where)
               if r["url"] not in urls]
        if not new:
            break
        urls |= {r["url"] for r in new}
        out += new
    return out


def parse_detail(page_html: str) -> tuple[str, list[str]]:
    """A posting's page -> (description as plain text, locations) from its schema.org JobPosting."""
    for m in _LD.finditer(page_html or ""):
        try:
            data = json.loads(m.group(1), strict=False)
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") == "JobPosting" and item.get("description"):
                places = []
                locs = item.get("jobLocation") or []
                for loc in locs if isinstance(locs, list) else [locs]:
                    addr = (loc or {}).get("address") or {}
                    city, region = addr.get("addressLocality"), addr.get("addressRegion")
                    if city and region:
                        places.append(f"{city}, {region}")
                return html_to_text(item["description"], unescape_first="&lt;" in item["description"]), places
    raise IcimsError("no JobPosting data on the page: the markup changed")
