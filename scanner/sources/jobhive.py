"""ats-scrapers' open job dataset ("jobhive", github.com/kalil0321/ats-scrapers, MIT): the hiring systems lockin has no reader for.

The project reads the public job APIs of 64 hiring systems every day and publishes one snapshot per system, free and
without a key, at storage.stapply.ai/jobhive/v1/<system>/jobs.parquet (the manifest lists them). Many Canadian
employers hire through systems lockin cannot read itself: Oracle Recruiting (banks, utilities, universities), Eightfold
(Microsoft), Dayforce and UKG (retail, health, municipal), Rippling, JazzHR, Breezy, Cornerstone, SmartRecruiters
tenants not on the watchlist. This source downloads the snapshots named in `config.yaml`, keeps the rows whose country
is Canada and whose posting date is inside the window, and hands each one's description over (scanner/sources/supplied.py),
because phase 2 has no reader for most of these systems. A row without a posting date is skipped, as everywhere else.

The country code is the dataset's own geocoding and is only a first cut ("Sunnyvale, CA" can come back as Canada): the
location text goes through lockin's own location filter like any other posting.
"""
import csv
import io
import json
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from scanner.net import http_get
from scanner.normalize import parse_iso
from scanner.text import html_to_text

SOURCE = "Jobhive"
BASE = "https://storage.stapply.ai/jobhive/v1"
COLUMNS = ["url", "apply_url", "title", "company", "location", "country_iso", "posted_at", "description", "raw"]
SYSTEMS = ("oracle", "smartrecruiters", "eightfold", "dayforce", "ukg", "rippling", "jazzhr", "breezy", "cornerstone",
           "phenom", "jobvite", "recruitee", "teamtailor", "taleo", "adp", "workable", "gem")
_TAG = re.compile(r"<[a-z/][^>]*>", re.I)
_SITE_WORDS = re.compile(r"^(?:careers|jobs) at\s+|\s*\(CX_\w+\)$|\s+(?:external\s+)?(?:career|careers)(?:\s+site)?$|\s+job opportunities$"
                         r"|\s+career opportunities$", re.I)


class JobhiveError(Exception):
    """A snapshot could not be read."""


def oracle_url(url: str, raw: str) -> str:
    """The dataset's Oracle link opens the job list; rebuild the posting's own page from the host, site and job id."""
    parts = urlsplit(url)
    site = (parse_qs(parts.query).get("site_number") or [""])[0]
    try:
        job = str((json.loads(raw or "{}") or {}).get("oracle_id") or "")
    except ValueError:
        job = ""
    job = job or parts.fragment
    if not (parts.hostname and site and job.isdigit()):
        return url
    return f"https://{parts.hostname}/hcmUI/CandidateExperience/en/sites/{site}/job/{job}"


def clean_company(name: str) -> str:
    """"Careers at Marriott" -> "Marriott", "BNY External Career Site" -> "BNY", "Emgi (CX_1001)" -> "Emgi"."""
    prev = None
    name = (name or "").strip()
    while prev != name:
        prev, name = name, _SITE_WORDS.sub("", name).strip()
    return name


def company_for(name: str, url: str, names: dict[str, str]) -> str:
    """Oracle names many employers only by their tenant code ("Fa Epmd Saasfaprod1", "Eedu"): use the dataset's company
    list for that tenant instead. `names` maps the first label of the careers host to the employer's name."""
    name = clean_company(name)
    label = (urlsplit(url).hostname or "").split(".")[0].lower()
    if label and (not name or name.lower().replace(" ", "-") == label) and clean_company(names.get(label, "")):
        return clean_company(names[label])
    return name


def _when(value) -> datetime | None:
    if not value:
        return None
    try:
        when = parse_iso(str(value))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _text(desc: str) -> str:
    desc = (desc or "").strip()
    return html_to_text(desc) if _TAG.search(desc) else desc


def records(rows: list[dict], cutoff: datetime, names: dict[str, str] | None = None) -> list[dict]:
    """Dataset rows (already Canadian) -> lockin records, newest first; rows older than `cutoff` or undated are left out."""
    out = []
    for r in rows:
        when = _when(r.get("posted_at"))
        title, url = (r.get("title") or "").strip(), (r.get("url") or "").strip()
        if when is None or when < cutoff or not (title and url):
            continue
        url = oracle_url(url, r.get("raw") or "") if ".oraclecloud.com" in url else url.split("#")[0]
        company = company_for(r.get("company") or "", url, names or {})
        out.append({"company": company or "Jobhive employer", "company_name": company, "title": title,
                    "location": (r.get("location") or "").strip() or "Canada", "url": url, "source": SOURCE, "first_seen": when,
                    "jd": _text(r.get("description") or "")})
    out.sort(key=lambda rec: rec["first_seen"], reverse=True)
    return out


def company_names(text: str) -> dict[str, str]:
    """companies.csv (name,slug,url) -> {first label of the careers host: name}."""
    out = {}
    for row in csv.DictReader(io.StringIO(text)):
        label = (urlsplit(row.get("url") or "").hostname or "").split(".")[0].lower()
        if label and row.get("name"):
            out[label] = row["name"]
    return out


def read_snapshot(path: Path) -> list[dict]:
    import pyarrow.parquet as pq           # only this source needs it
    names = set(pq.ParquetFile(path).schema_arrow.names)
    cols = [c for c in COLUMNS if c in names]
    return pq.read_table(path, columns=cols, filters=[("country_iso", "=", "CA")]).to_pylist()


def fetch(systems, since_days: float, now: datetime) -> list[dict]:
    cutoff = now.astimezone(timezone.utc) - timedelta(days=since_days)
    out, seen, failed = [], set(), []
    with tempfile.TemporaryDirectory() as tmp:
        for name in systems:
            path = Path(tmp) / f"{name}.parquet"
            try:
                with http_get(f"{BASE}/{name}/jobs.parquet", stream=True, timeout=300) as resp, open(path, "wb") as fh:
                    for chunk in resp.iter_content(1 << 20):
                        fh.write(chunk)
                names = company_names(http_get(f"{BASE}/{name}/companies.csv").text) if name == "oracle" else {}
                recs = records(read_snapshot(path), cutoff, names)
            except Exception as exc:          # one system's snapshot failing must not cost the others
                failed.append(f"{name} ({type(exc).__name__})")
                continue
            finally:
                path.unlink(missing_ok=True)
            fresh = [r for r in recs if r["url"] not in seen]
            seen.update(r["url"] for r in fresh)
            out += fresh
            print(f"  Jobhive {name}: {len(fresh)} Canadian postings in the window")
    if failed:
        print(f"  Jobhive: not read: {', '.join(failed)}")
        if len(failed) == len(list(systems)):
            raise JobhiveError("no snapshot could be read")
    return out
