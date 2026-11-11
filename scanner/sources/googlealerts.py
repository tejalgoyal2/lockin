"""Google Alerts on the ATS sites: Google's own index, told to watch for new job pages, read as RSS.

The "hidden jobs" trick is a Google search such as `site:jobs.lever.co Vancouver`: every employer's posting on an ATS
is a public page Google indexes, often before it reaches LinkedIn. Running those searches by hand every day is the
chore; a Google Alert runs it for you (google.com/alerts, "Deliver to: RSS feed", "How often: As-it-happens") and
lists each newly indexed page in an Atom feed. Feed readers are what those feeds are for, so lockin reads them the
same way, under its own name, twice a day.

Your alerts' feed addresses are personal, so they live in the GitHub secret `GOOGLE_ALERTS_FEEDS`, one per line,
optionally prefixed with the place the alert searches for: `Vancouver, BC | https://www.google.com/alerts/feeds/...`.
Without a prefix the place is "Canada". Either way the posting's own ATS page decides in phase 2: a job the ATS lists
only outside Canada is dropped there, as for every other source.
"""
import html
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

from scanner.normalize import parse_iso

SOURCE = "Google Alerts"
_ATOM = "{http://www.w3.org/2005/Atom}"
_TAG = re.compile(r"<[^>]+>")
_GREENHOUSE = re.compile(r"^Job Application for (?P<title>.+?) at (?P<company>.+)$", re.I)
_COMPANY_FIRST = re.compile(r"^(?P<company>[^|@]+?) - (?P<title>.+)$")       # Lever, Workable: "Acme - Software Engineer"
_AT = re.compile(r"^(?P<title>.+?) @ (?P<company>.+)$")                       # Ashby: "Software Engineer @ Acme"
_PIPE = re.compile(r"^(?P<title>.+?) \| (?P<company>[^|]+?)(?: \| .*)?$")    # many career sites: "Title | Company"
_SLUG_HOSTS = ("greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "bamboohr.com", "smartrecruiters.com")


class GoogleAlertsError(Exception):
    """A feed is not an Atom feed (the message never carries the feed's address)."""


def feeds() -> list[tuple[str, str]]:
    """[(place, feed url)] from GOOGLE_ALERTS_FEEDS: one per line, `place | url` or just `url` (place "Canada")."""
    out = []
    for line in os.environ.get("GOOGLE_ALERTS_FEEDS", "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        place, _, url = line.rpartition("|") if "|" in line else ("", "", line)
        url = url.strip()
        if url.startswith("https://"):
            out.append((place.strip() or "Canada", url))
    return out


def real_url(link: str) -> str:
    """Google wraps each result: https://www.google.com/url?rct=j&sa=t&url=<the page>&ct=ga&... -> <the page>."""
    parts = urlsplit(link or "")
    if (parts.hostname or "").startswith("www.google.") and parts.path == "/url":
        return (parse_qs(parts.query).get("url") or [""])[0]
    return link or ""


def _plain(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub("", html.unescape(fragment or "")))).strip()


def company_from_url(url: str) -> str:
    """The employer's slug on a multi-tenant ATS (jobs.lever.co/<slug>/...), the tenant on Workday, else the site's name."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    segs = [s for s in parts.path.split("/") if s]
    if host.endswith(_SLUG_HOSTS) and segs:
        return segs[1] if segs[0] in ("embed", "j", "jobs") and len(segs) > 1 and host.endswith("workable.com") else segs[0]
    if host.endswith("myworkdayjobs.com"):
        return host.split(".")[0]
    labels = [label for label in host.split(".") if label not in ("www", "careers", "jobs", "apply", "boards", "job-boards")]
    return labels[0] if labels else host


def split_title(raw: str, url: str) -> tuple[str, str]:
    """(job title, company) from the page title Google shows."""
    text = _plain(raw)
    host = (urlsplit(url).hostname or "").lower()
    pats = [_GREENHOUSE, *([_COMPANY_FIRST] if host.endswith(("lever.co", "workable.com")) else []), _AT, _PIPE]
    for pat in pats:
        m = pat.match(text)
        if m:
            return m.group("title").strip(), m.group("company").strip()
    return text, ""


def parse(feed_xml: str, place: str, now: datetime) -> list[dict]:
    if "<!DOCTYPE" in (feed_xml or "") or "<!ENTITY" in (feed_xml or ""):
        raise GoogleAlertsError("the feed declares a DOCTYPE or entities: not a Google Alerts feed")
    try:
        root = ET.fromstring(feed_xml or "")
    except ET.ParseError as exc:
        raise GoogleAlertsError(f"not an Atom feed ({exc})") from None
    out = []
    for entry in root.iter(f"{_ATOM}entry"):
        link_el = entry.find(f"{_ATOM}link")
        url = real_url(link_el.get("href") if link_el is not None else "")
        title, company = split_title(entry.findtext(f"{_ATOM}title") or "", url)
        if not (url.startswith("http") and title):
            continue
        stamp = entry.findtext(f"{_ATOM}published") or entry.findtext(f"{_ATOM}updated") or ""
        try:
            when = parse_iso(stamp) if stamp else now
        except ValueError:
            when = now
        slug = company_from_url(url)
        out.append({"company": slug or company or "Google Alerts employer", "company_name": company, "title": title,
                    "url": url, "location": place, "source": SOURCE, "first_seen": when.astimezone(timezone.utc)})
    return out


def fetch(client, now: datetime) -> list[dict]:
    configured = feeds()
    if not configured:
        print("Google Alerts: GOOGLE_ALERTS_FEEDS not set, source skipped")
        return []
    seen, out, failed = set(), [], 0
    for n, (place, url) in enumerate(configured, 1):
        try:
            recs = parse(client.get(url, SOURCE).text, place, now)
        except Exception as exc:                  # the address is personal: name the feed by its line number only
            failed += 1
            print(f"WARNING Google Alerts: feed {n} not read ({type(exc).__name__})")
            continue
        for rec in recs:
            if rec["url"] not in seen:
                seen.add(rec["url"])
                out.append(rec)
    if failed == len(configured):
        raise GoogleAlertsError(f"none of the {failed} feeds could be read")
    return out
