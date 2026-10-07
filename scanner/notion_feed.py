"""Write the best candidates to the Notion "Job Feed" data source (SPEC §6).

The scanner only ever writes to the Feed. It never writes the owner's `Interested` / `Apply`
checkboxes and never touches a row where either is ticked.
"""
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import requests

from scanner.company_names import company_cell
from scanner.dedupe import canonical_url
from scanner.models import Job

log = logging.getLogger(__name__)

API = "https://api.notion.com"
RICH_TEXT_ITEM_MAX = 2000      # characters per rich_text item (Notion request limit)
ARRAY_MAX = 100                # elements per rich_text array
JD_ITEMS_MAX = 95              # SPEC §6a: ~190k characters, leaving headroom under the array limit
URL_MAX = 2000
PAYLOAD_MAX_BYTES = 450_000    # Notion's hard limit is 500 KB per request
TRUNCATED = "\n\n[truncated]"

# What the Feed must look like. Keys are the logical names used in config.yaml.
EXPECTED_TYPES = {
    "name": "title", "company": "rich_text", "link": "url", "source": "select", "fit": "number",
    "signals": "multi_select", "jd": "rich_text", "interested": "checkbox", "apply": "checkbox",
}
SOURCE_OPTIONS = ("Workday", "Greenhouse", "Lever", "Ashby", "BambooHR", "Paylocity", "Simplify", "Watchlist")
SIGNAL_OPTIONS = ("new grad", "recent graduate", "0-2 years", "entry level", "junior", "low signal",
                  "jd unavailable")


class NotionError(Exception):
    def __init__(self, status: int, code: str, message: str, method: str = "", path: str = ""):
        super().__init__(f"Notion API {method} {path} -> HTTP {status} {code}: {message}".strip())
        self.status, self.code, self.message = status, code, message


class SchemaError(Exception):
    """The Feed's schema does not match what the scanner writes."""


# --- HTTP client ----------------------------------------------------------------------

class NotionClient:
    """Minimal Notion REST client: paced to <= 3 req/s, honours Retry-After, never logs the token.

    Retries follow Notion's rules: 429 / 529 always (except `public_api_request_blocked`);
    500/502/503/504 and network errors only for idempotent calls (GET, DELETE, queries).
    A write that fails with a 5xx is NOT retried: it may have succeeded, and the next run's
    Link check against the Feed prevents a duplicate.
    """

    def __init__(self, token: str, version: str, *, session=None, min_interval: float = 0.34,
                 max_attempts: int = 6, timeout: int = 60, sleep=time.sleep, clock=time.monotonic):
        self._headers = {"Authorization": f"Bearer {token}", "Notion-Version": version,
                         "Content-Type": "application/json"}
        self.session = session or requests.Session()
        self.min_interval, self.max_attempts, self.timeout = min_interval, max_attempts, timeout
        self._sleep, self._clock, self._last = sleep, clock, -1e9

    def _pace(self) -> None:
        wait = self.min_interval - (self._clock() - self._last)
        if wait > 0:
            self._sleep(wait)
        self._last = self._clock()

    def request(self, method: str, path: str, *, json=None, params=None, idempotent: bool | None = None) -> dict:
        idempotent = method in ("GET", "DELETE") if idempotent is None else idempotent
        for attempt in range(self.max_attempts):
            self._pace()
            try:
                resp = self.session.request(method, API + path, headers=self._headers, json=json,
                                            params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                if idempotent and attempt < self.max_attempts - 1:
                    self._sleep(min(2 ** attempt, 30))
                    continue
                raise NotionError(0, type(exc).__name__, "network error", method, path) from None
            if resp.status_code < 300:
                return resp.json()
            body = _json_or_empty(resp)
            code, message = body.get("code", ""), body.get("message", "")
            reason = (body.get("additional_data") or {}).get("rate_limit_reason", "")
            retryable = ((resp.status_code in (429, 529) and reason != "public_api_request_blocked")
                         or (idempotent and resp.status_code in (500, 502, 503, 504)))
            if not retryable or attempt == self.max_attempts - 1:
                raise NotionError(resp.status_code, code, message, method, path)
            retry_after = resp.headers.get("Retry-After")
            delay = float(retry_after) if retry_after else min(2 ** attempt, 30)
            log.warning("Notion %s %s -> %s; retrying in %.1fs", method, path, resp.status_code, delay)
            self._sleep(delay + random.uniform(0, 0.25))
        raise AssertionError("unreachable")


def _json_or_empty(resp) -> dict:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


# --- schema ---------------------------------------------------------------------------

@dataclass
class Feed:
    """Resolved Feed: the data source id plus the property names/ids the scanner uses."""
    data_source_id: str
    names: dict[str, str]                       # logical name -> Notion property name
    ids: dict[str, str] = field(default_factory=dict)   # logical name -> Notion property id
    source_options: set[str] = field(default_factory=set)


def validate_schema(schema: dict, names: dict[str, str]) -> list[str]:
    """Return human-readable problems (empty list = the Feed matches)."""
    props = schema.get("properties", {})
    problems = []
    for logical, expected in EXPECTED_TYPES.items():
        name = names[logical]
        prop = props.get(name)
        if prop is None:
            problems.append(f"missing property {name!r} (expected type {expected})")
            continue
        if prop.get("type") != expected:
            problems.append(f"property {name!r} has type {prop.get('type')!r}, expected {expected!r}")
            continue
        if logical == "fit" and (prop.get("number") or {}).get("format") != "percent":
            problems.append(f"property {name!r} has number format {(prop.get('number') or {}).get('format')!r}, "
                            "expected 'percent'")
        for logical_opts, wanted in (("source", SOURCE_OPTIONS), ("signals", SIGNAL_OPTIONS)):
            if logical == logical_opts:
                have = {o["name"] for o in (prop.get(expected) or {}).get("options", [])}
                missing = [o for o in wanted if o not in have]
                if missing:
                    problems.append(f"property {name!r} is missing options: {', '.join(missing)}")
    return problems


def load_feed(client: NotionClient, data_source_id: str, names: dict[str, str]) -> Feed:
    """Fetch the Feed schema and fail with a clear message before anything is written."""
    try:
        schema = client.request("GET", f"/v1/data_sources/{data_source_id}")
    except NotionError as exc:
        hint = (" (check that NOTION_FEED_DATA_SOURCE_ID is the data source ID, not the database ID, "
                "and that the Feed is shared with the integration)" if exc.status in (400, 403, 404) else "")
        raise SchemaError(f"cannot read the Feed data source {data_source_id}: {exc}{hint}") from None
    problems = validate_schema(schema, names)
    if problems:
        raise SchemaError("Notion Feed schema does not match:\n  - " + "\n  - ".join(problems))
    props = schema["properties"]
    return Feed(
        data_source_id=data_source_id,
        names=names,
        ids={k: props[v]["id"] for k, v in names.items()},
        source_options={o["name"] for o in props[names["source"]]["select"]["options"]},
    )


# --- payload --------------------------------------------------------------------------

def _utf16_len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def chunk_text(text: str, max_units: int = RICH_TEXT_ITEM_MAX) -> list[str]:
    """Split into pieces of at most `max_units` UTF-16 units, never splitting a code point.

    Concatenating the pieces gives back `text` exactly (the length round-trip test relies on it).
    """
    chunks, buf, units = [], [], 0
    for ch in text:
        w = 2 if ord(ch) > 0xFFFF else 1
        if units + w > max_units:
            chunks.append("".join(buf))
            buf, units = [], 0
        buf.append(ch)
        units += w
    if buf:
        chunks.append("".join(buf))
    return chunks


def jd_rich_text(jd: str, max_items: int = JD_ITEMS_MAX) -> tuple[list[dict], str]:
    """The JD as rich_text items, truncated with a marker if it exceeds `max_items` items.

    Returns (items, text_actually_sent).
    """
    jd = (jd or "").replace("\x00", "")
    cap = max_items * RICH_TEXT_ITEM_MAX
    if _utf16_len(jd) > cap:
        keep = chunk_text(jd, cap - _utf16_len(TRUNCATED))[0]
        jd = keep + TRUNCATED
    chunks = chunk_text(jd)
    return [{"type": "text", "text": {"content": c}} for c in chunks], jd


def _rich(text: str) -> list[dict]:
    return [{"type": "text", "text": {"content": c}} for c in chunk_text(text)][:ARRAY_MAX]


def build_properties(job: Job, feed: Feed, overrides: dict[str, str]) -> tuple[dict, str]:
    """Notion `properties` for one job, plus the JD text actually sent.

    Never includes the owner's Interested / Apply checkboxes.
    """
    n = feed.names
    jd_items, jd_sent = jd_rich_text(job.jd)
    props = {
        n["name"]: {"title": _rich(job.title)[:1] or [{"type": "text", "text": {"content": ""}}]},
        n["company"]: {"rich_text": _rich(company_cell(job, overrides))},
        n["link"]: {"url": job.url},
        n["signals"]: {"multi_select": [{"name": s} for s in job.signals]},
        n["jd"]: {"rich_text": jd_items},
    }
    if job.source in feed.source_options:
        props[n["source"]] = {"select": {"name": job.source}}
    props[n["fit"]] = {"number": None if job.fit_pct is None else round(job.fit_pct / 100, 4)}
    return props, jd_sent


def build_page(job: Job, feed: Feed, overrides: dict[str, str]) -> tuple[dict, str]:
    props, jd_sent = build_properties(job, feed, overrides)
    return {"parent": {"type": "data_source_id", "data_source_id": feed.data_source_id},
            "properties": props}, jd_sent       # no `children`: the page body stays empty


def skip_reason(job: Job) -> str | None:
    if not job.url:
        return "no_link"
    if len(job.url) > URL_MAX:
        return "link_too_long"
    return None


# --- reading the Feed -----------------------------------------------------------------

@dataclass
class FeedRow:
    page_id: str
    created: datetime
    link: str
    interested: bool | None     # None: property absent from the response
    apply: bool | None


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def list_rows(client: NotionClient, feed: Feed) -> list[FeedRow]:
    """Every row in the Feed (paginated), fetching only Link / Interested / Apply."""
    n, ids = feed.names, feed.ids
    params = {"filter_properties[]": [ids["link"], ids["interested"], ids["apply"]]}
    rows, cursor = [], None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        data = client.request("POST", f"/v1/data_sources/{feed.data_source_id}/query",
                              json=body, params=params, idempotent=True)
        for page in data.get("results", []):
            props = page.get("properties", {})
            rows.append(FeedRow(
                page_id=page["id"],
                created=_parse_ts(page["created_time"]),
                link=(props.get(n["link"]) or {}).get("url") or "",
                interested=(props.get(n["interested"]) or {}).get("checkbox"),
                apply=(props.get(n["apply"]) or {}).get("checkbox"),
            ))
        if not data.get("has_more"):
            return rows
        cursor = data.get("next_cursor")


# --- selection, cleanup, writing -------------------------------------------------------

def select_new(jobs: list[Job], seen: dict[str, str], feed_links: set[str], cap: int
               ) -> tuple[list[Job], dict[str, int]]:
    """Highest-score jobs not already in seen.json or the Feed (by Link), at most `cap`.

    Returns (to_write, skipped counts by reason).
    """
    canon_links = {canonical_url(link) for link in feed_links}
    skipped: dict[str, int] = {}
    out: list[Job] = []
    for job in sorted(jobs, key=lambda j: (-j.score, -j.first_seen.timestamp())):
        reason = None
        if job.key in seen:
            reason = "seen"
        elif canonical_url(job.url) in canon_links:
            reason = "link_in_feed"
        else:
            reason = skip_reason(job)
        if reason:
            skipped[reason] = skipped.get(reason, 0) + 1
        elif len(out) < cap:
            out.append(job)
        else:
            skipped["over_cap"] = skipped.get("over_cap", 0) + 1
    return out, skipped


def already_in_feed(jobs: list[Job], feed_links: set[str]) -> list[Job]:
    """Jobs whose Link is already a Feed row. Remembered in seen.json so that, once the owner's
    automation moves the row out of the Feed, the job does not come back."""
    canon = {canonical_url(link) for link in feed_links}
    return [j for j in jobs if canonical_url(j.url) in canon]


def stale_rows(rows: list[FeedRow], now: datetime, ttl_days: int) -> list[FeedRow]:
    """Rows created more than `ttl_days` ago where BOTH checkboxes are known and unticked.

    A row with either box ticked, or with a checkbox missing from the response, is never returned.
    """
    cutoff = now - timedelta(days=ttl_days)
    return [r for r in rows if r.created < cutoff and r.interested is False and r.apply is False]


def trash_rows(client: NotionClient, rows: list[FeedRow], *, dry_run: bool) -> int:
    done = 0
    for row in rows:
        if dry_run:
            log.info("[dry-run] would trash %s", row.page_id)
        else:
            client.request("PATCH", f"/v1/pages/{row.page_id}", json={"in_trash": True})
        done += 1
    return done


@dataclass
class WriteResult:
    written: list[tuple[Job, str, str]] = field(default_factory=list)   # (job, page_id, jd_sent)
    failed: list[tuple[Job, str]] = field(default_factory=list)         # (job, error)


def write_jobs(client: NotionClient, feed: Feed, jobs: list[Job], overrides: dict[str, str],
               on_written=lambda job, page_id: None, max_consecutive_failures: int = 3) -> WriteResult:
    """Create one Feed row per job. `on_written` runs right after each success (to save seen.json)."""
    res, streak = WriteResult(), 0
    for job in jobs:
        page, jd_sent = build_page(job, feed, overrides)
        try:
            created = client.request("POST", "/v1/pages", json=page, idempotent=False)
        except NotionError as exc:
            log.error("could not write %s | %s: %s", job.company, job.title, exc)
            res.failed.append((job, str(exc)))
            streak += 1
            if streak >= max_consecutive_failures:
                log.error("aborting after %d consecutive failures", streak)
                break
            continue
        streak = 0
        res.written.append((job, created["id"], jd_sent))
        on_written(job, created["id"])
    return res


# --- read-back check ------------------------------------------------------------------

def read_property_text(client: NotionClient, page_id: str, property_id: str) -> str:
    """Full text of a rich_text property via the paginated property-item endpoint."""
    parts, cursor = [], None
    while True:
        params = {"page_size": 100, **({"start_cursor": cursor} if cursor else {})}
        data = client.request("GET", f"/v1/pages/{page_id}/properties/{property_id}", params=params)
        for item in data.get("results", []):
            rt = item.get("rich_text") or {}
            parts.append(rt.get("plain_text") or (rt.get("text") or {}).get("content", ""))
        if not data.get("has_more"):
            return "".join(parts)
        cursor = data.get("next_cursor")


def verify_readback(client: NotionClient, feed: Feed, written: list[tuple[Job, str, str]],
                    min_chars: int = 4000) -> tuple[bool, str]:
    """Read one written JD back and compare lengths (and content). Picks the first JD > min_chars,
    else the longest. Returns (ok, message)."""
    candidates = [w for w in written if w[2]]
    if not candidates:
        return True, "read-back skipped: no rows with a JD were written"
    long_ones = [w for w in candidates if len(w[2]) > min_chars]
    job, page_id, sent = long_ones[0] if long_ones else max(candidates, key=lambda w: len(w[2]))
    got = read_property_text(client, page_id, feed.ids["jd"])
    ok = got == sent
    msg = (f"read-back {'OK' if ok else 'MISMATCH'}: {job.company} | {job.title}: sent {len(sent)} chars, "
           f"read {len(got)} chars" + ("" if long_ones else f" (no JD over {min_chars} chars was written)"))
    return ok, msg


# --- dry-run preview ------------------------------------------------------------------

def virtual_feed(names: dict[str, str]) -> Feed:
    """A Feed built from config alone, for dry runs that do not contact Notion."""
    return Feed(data_source_id="<NOTION_FEED_DATA_SOURCE_ID>", names=names,
                ids={k: f"<{v} id>" for k, v in names.items()}, source_options=set(SOURCE_OPTIONS))


def preview_page(page: dict, jd_prop: str) -> dict:
    """The payload with the (very long) JD property shortened for printing."""
    props = dict(page["properties"])
    items = props[jd_prop]["rich_text"]
    total = sum(len(i["text"]["content"]) for i in items)
    first = items[0]["text"]["content"] if items else ""
    shown = [{"type": "text", "text": {"content": first[:120] + ("..." if len(first) > 120 else "")}}]
    if len(items) > 1:
        shown.append({"_": f"+{len(items) - 1} more rich_text items"})
    props[jd_prop] = {"rich_text": shown, "_summary": f"{len(items)} items, {total} chars total"}
    return {**page, "properties": props}
