"""Hacker News "Ask HN: Who is hiring?" (monthly thread, read through the Algolia HN API).

A posting is a top-level comment whose first line follows the convention `Company | Role | Location | ...`. Only the
first line is read to decide whether a posting is Canadian and what the role is; the comment is the description.
"""
import html
import re
from datetime import datetime

from scanner.normalize import parse_iso
from scanner.text import html_to_text

SOURCE = "HN"
API = "https://hn.algolia.com/api/v1"
_CANADA = re.compile(r"\b(Canada|Canadian|Toronto|Vancouver|Montr[eé]al|Ottawa|Waterloo|Kitchener|Calgary|Edmonton|"
                     r"Winnipeg|Halifax|Ontario|British Columbia|Alberta|Victoria,?\s*BC|[A-Z][a-z]+,\s*(?:ON|BC|AB))\b")
_ROLE = re.compile(r"\b(engineer\w*|developer\w*|programmer\w*|scientist\w*|analyst\w*|researcher\w*|intern\w*|"
                   r"sre|devops|data|ml|ai|machine learning|full[- ]?stack|front[- ]?end|back[- ]?end|"
                   r"new grad\w*|graduate\w*|software|swe|security|founding)\b", re.I)


def latest_thread(client) -> dict | None:
    """The newest "Who is hiring?" story (not "Who wants to be hired?")."""
    data = client.get_json(f"{API}/search_by_date?tags=story,author_whoishiring&hitsPerPage=6", SOURCE)
    for hit in data.get("hits", []):
        if "who is hiring" in (hit.get("title") or "").lower():
            return hit
    return None


def header(comment_html: str) -> str:
    """The comment's first line as plain text."""
    for line in html_to_text(comment_html or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


_BODY_ROLE = re.compile(r"\b(software|developer|engineer|devops|data (?:scientist|engineer|analyst)|machine learning)\b", re.I)
THREAD_MAX_AGE_DAYS = 40


def parse_comment(comment: dict, now: datetime | None = None) -> dict | None:
    """A top-level comment -> a record, or None when its header is not a Canadian posting with a role.

    A header that names a Canadian place but no role ("Strello Health | Toronto", "Stream | Multiple Positions | ...
    Toronto (Canada)") is still a lead: the role is read from the first lines of the comment, and when that names a
    tech role the posting is kept under the generic title "Engineer" (phase 2 decides from the full text).
    With `now`, the posting counts as first seen now: an "Ask HN" comment is written once, for a month, so its
    age says nothing about whether we have shown it; seen.json does that."""
    text = html_to_text(comment.get("comment_text") or "")
    head = header(comment.get("comment_text") or "")
    parts = [p.strip() for p in head.split("|") if p.strip()]
    if len(parts) < 2:
        return None
    where = [p for p in parts[1:] if _CANADA.search(p)]
    roles = [p for p in parts[1:] if _ROLE.search(p) and not _CANADA.search(p) and len(p) < 120]
    if where and not roles and _BODY_ROLE.search(text[:600]):
        roles = ["Engineer"]
    if not (where and roles):
        return None
    company = re.sub(r"\s*\(?https?://\S+\)?", "", parts[0]).strip(" -–")
    if not company or len(company) > 60:
        return None
    try:
        posted = parse_iso(comment["created_at"].replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return None
    if now is not None:
        posted = now
    return {"company": company, "company_name": company, "title": ", ".join(roles)[:150],
            "location": " ; ".join(where), "source": SOURCE,
            "url": f"https://news.ycombinator.com/item?id={comment['objectID']}", "first_seen": posted}


def fetch(client, now: datetime) -> list[dict]:
    thread = latest_thread(client)
    if thread is None:
        return []
    try:
        age = (now - parse_iso(thread["created_at"].replace("Z", "+00:00"))).days
    except (KeyError, ValueError):
        age = 0
    if age > THREAD_MAX_AGE_DAYS:
        return []                                           # the new month's thread is not up yet and this one is stale
    out, page = [], 0
    while True:
        data = client.get_json(f"{API}/search_by_date?tags=comment,story_{thread['objectID']}&hitsPerPage=1000&page={page}",
                               SOURCE)
        for c in data.get("hits", []):
            if str(c.get("parent_id")) == str(thread["objectID"]):
                rec = parse_comment(c, now)
                if rec:
                    out.append(rec)
        page += 1
        if page >= data.get("nbPages", 0):
            return out


def comment_text(item_json: dict) -> str:
    """The Algolia item API's comment -> plain text."""
    return html_to_text(html.unescape(item_json.get("text") or "")) if item_json.get("text") else ""
