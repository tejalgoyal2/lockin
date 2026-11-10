"""Adzuna (https://www.adzuna.ca): a job search engine that gathers Canadian postings from thousands of employers.

Its API is free for personal use but needs a key (https://developer.adzuna.com). lockin reads the key from the environment
(`ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, GitHub Actions secrets) and skips this source, with a note, when they are not set.
`GET /v1/api/jobs/ca/search/<page>?what=<term>&max_days_old=<n>&sort_by=date` answers with 50 results a page: title,
company, place, date and a link to Adzuna's own page, which redirects to the employer's. The API returns only the first
lines of the description (`description`), so the posting reaches the Feed with that as its text; the owner opens the link.
"""
import html
import os
from datetime import datetime

from scanner.normalize import parse_iso

SOURCE = "Adzuna"
API = "https://api.adzuna.com/v1/api/jobs/ca/search"
PAGE = 50
MAX_PAGES = 4


class AdzunaError(Exception):
    """A request to the API failed (the message never carries the key)."""


def credentials() -> tuple[str, str] | None:
    app_id, app_key = os.environ.get("ADZUNA_APP_ID", "").strip(), os.environ.get("ADZUNA_APP_KEY", "").strip()
    return (app_id, app_key) if app_id and app_key else None


def parse(answer: dict) -> list[dict]:
    out = []
    for j in (answer or {}).get("results") or []:
        title, url = html.unescape(j.get("title") or ""), j.get("redirect_url")
        if not (title and url):
            continue
        try:
            when = parse_iso(j["created"]) if j.get("created") else None
        except ValueError:
            when = None
        name = html.unescape((j.get("company") or {}).get("display_name") or "")
        out.append({"company": name or "Adzuna employer", "company_name": name, "title": title, "url": url,
                    "location": html.unescape((j.get("location") or {}).get("display_name") or ""),
                    "source": SOURCE, "first_seen": when, "summary": html.unescape(j.get("description") or "")})
    return out


def fetch(client, terms: list[str], window_days: float, now: datetime, *, max_pages: int = MAX_PAGES) -> list[dict]:
    keys = credentials()
    if not keys:
        print("Adzuna: ADZUNA_APP_ID / ADZUNA_APP_KEY not set, source skipped")
        return []
    seen, out = set(), []
    for term in terms:
        for page in range(1, max_pages + 1):
            url = (f"{API}/{page}?app_id={keys[0]}&app_key={keys[1]}&results_per_page={PAGE}&what={term.replace(' ', '%20')}"
                   f"&max_days_old={max(1, int(window_days))}&sort_by=date&content-type=application/json")
            try:
                recs = parse(client.get_json(url, SOURCE))
            except Exception as exc:               # the key is in the URL, and an HTTP error quotes the URL: never let it out
                raise AdzunaError(f"{type(exc).__name__} on page {page} of {term!r}"
                                  + (f" (HTTP {exc.response.status_code})" if getattr(exc, "response", None) is not None else "")) from None
            for rec in recs:
                if rec["url"] not in seen:
                    seen.add(rec["url"])
                    out.append(rec)
            if len(recs) < PAGE:
                break
    return out
