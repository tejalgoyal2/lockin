"""Jobs the owner has already applied to: never write them to the Feed again.

The list is private, so the repo only holds `state/applied.json.enc`, a Fernet-encrypted JSON file.
It is decrypted in memory with the key in the APPLIED_KEY environment variable (a repo secret);
plaintext is never written to disk. Each entry is a row title (usually "Role (Company)") and a
canonical job link. A candidate is skipped when

  1. its canonical URL equals an applied link, or
  2. its company matches the applied company (same matching as dedupe, plus company_names.yaml)
     AND the title token Jaccard is >= 0.8.
"""
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken

from scanner.company_names import display_company
from scanner.dedupe import TITLE_JACCARD, canonical_url, company_match, title_jaccard
from scanner.models import Job
from scanner.normalize import norm_text

_TRAILING_PAREN = re.compile(r"\s*[-–—]?\s*\(([^()]*)\)?\s*$")      # "... - (Quora)", "... (RBC)", "... (Planet"
_GENERIC_HOST_LABELS = {"www", "jobs", "careers", "career", "apply", "recruiting", "ats", "secure", "hr",
                        "search", "careersite", "job-boards", "boards", "jobpostings", "en", "ca", "us"}
_SLUG_FIRST_SEGMENT_HOSTS = ("ashbyhq.com", "greenhouse.io", "lever.co")


def parse_title(raw: str) -> tuple[str, str]:
    """('role', 'company') from 'Role (Company)'. Company is '' when the title carries none."""
    text = re.sub(r"\s+", " ", raw or "").strip()
    m = _TRAILING_PAREN.search(text)
    if not m or not m.group(1).strip():
        return text, ""
    return text[:m.start()].strip(" -–—"), m.group(1).strip()


def link_company_slugs(url: str) -> set[str]:
    """Company-ish names a job link gives away: ATS slug, Workday tenant, or the site's own domain."""
    parts = urlsplit(url or "")
    host = parts.netloc.lower()
    if not host:
        return set()
    labels = host.split(".")
    if any(host.endswith(d) for d in _SLUG_FIRST_SEGMENT_HOSTS):
        seg = next((s for s in parts.path.split("/") if s), "")
        return {norm_text(seg)} if seg else set()
    if host.endswith("myworkdayjobs.com") or host.endswith("bamboohr.com"):
        return {norm_text(labels[0])}
    out = set()
    if len(labels) >= 2:
        out.add(norm_text(labels[-2]))
    if labels[0] not in _GENERIC_HOST_LABELS and len(labels) > 2:
        out.add(norm_text(labels[0]))
    return {o for o in out if o and o not in _GENERIC_HOST_LABELS}


def company_keys(name: str, overrides: dict[str, str]) -> set[str]:
    """Every normalised spelling of a company: itself, its display name, and the slugs that map to it."""
    n = norm_text(name)
    if not n:
        return set()
    keys = {n}
    display = overrides.get(n)
    if display:
        keys.add(norm_text(display))
    for slug, shown in overrides.items():          # reverse lookup: "Canadian Tire" -> canadiantirecorp, ...
        if norm_text(shown) in keys:
            keys.add(slug)
    return keys


@dataclass
class AppliedJob:
    title: str
    link: str                                      # canonical, '' when the row has no real URL
    role: str = ""
    company: str = ""
    company_keys: set[str] = field(default_factory=set)

    @classmethod
    def from_entry(cls, title: str, link: str, overrides: dict[str, str]) -> "AppliedJob":
        role, company = parse_title(title)
        keys = set()
        for name in [company, *link_company_slugs(link)]:
            keys |= company_keys(name, overrides)
        return cls(title=title, link=link, role=role, company=company, company_keys=keys)


def canonical_link(link: str) -> str:
    """Canonical form for storing; text that is not a URL (a bare requisition number) becomes ''."""
    return canonical_url(link) if urlsplit(link or "").netloc else ""


class AppliedSet:
    def __init__(self, entries: list[AppliedJob], overrides: dict[str, str]):
        self.entries, self.overrides = entries, overrides
        self._links = {e.link for e in entries if e.link}

    @classmethod
    def from_rows(cls, raw: list[dict], overrides: dict[str, str]) -> "AppliedSet":
        return cls([AppliedJob.from_entry(e["title"], e.get("link", ""), overrides) for e in raw], overrides)

    @classmethod
    def load_encrypted(cls, path: str | Path, key: str, overrides: dict[str, str]) -> "AppliedSet":
        """Decrypt `path` in memory. No key or no file: warn and return an empty set (the check is skipped).
        A key that cannot decrypt the file is a configuration error and raises AppliedKeyError."""
        key = (key or "").strip()
        if not key:
            print("WARNING: APPLIED_KEY is not set; skipping the already-applied check", file=sys.stderr)
            return cls([], overrides)
        try:
            token = Path(path).read_bytes()
        except FileNotFoundError:
            print(f"WARNING: {path} not found; skipping the already-applied check", file=sys.stderr)
            return cls([], overrides)
        try:
            raw = json.loads(Fernet(key.encode()).decrypt(token)).get("applied", [])
        except (InvalidToken, ValueError):          # bad key / corrupt file / key not valid Fernet base64
            raise AppliedKeyError(f"APPLIED_KEY cannot decrypt {path}; fix the secret or re-encrypt the file") from None
        loaded = cls.from_rows(raw, overrides)
        print(f"applied list: {len(loaded)} entries loaded")        # a count only, never an entry
        return loaded

    def __len__(self) -> int:
        return len(self.entries)

    def _job_keys(self, job: Job) -> set[str]:
        keys: set[str] = set()
        for name in (job.company, job.company_name, display_company(job, self.overrides)):
            keys |= company_keys(name, self.overrides)
        return keys

    def match(self, job: Job) -> str | None:
        """'url' or 'company+title' when the job is one the owner already applied to, else None."""
        if canonical_url(job.url) in self._links:
            return "url"
        job_keys = self._job_keys(job)
        for e in self.entries:
            if not any(company_match(a, b) for a in job_keys for b in e.company_keys):
                continue
            # the owner sometimes leaves the company in the title with no brackets ("... Engineer RBC")
            role_tokens = e.role
            for k in job_keys & {norm_text(w) for w in re.findall(r"[A-Za-z0-9]+", e.role)}:
                role_tokens = re.sub(rf"(?i)\b{re.escape(k)}\b", " ", role_tokens)
            if max(title_jaccard(job.title, e.role), title_jaccard(job.title, role_tokens)) >= TITLE_JACCARD:
                return "company+title"
        return None

    def filter(self, jobs: list[Job]) -> tuple[list[Job], dict[str, list[Job]]]:
        """(jobs not yet applied to, {'url': [...], 'company+title': [...]} of the skipped ones)."""
        keep, skipped = [], {"url": [], "company+title": []}
        for job in jobs:
            how = self.match(job)
            (skipped[how] if how else keep).append(job)
        return keep, skipped


class AppliedKeyError(Exception):
    pass


def encrypt_rows(path: str | Path, rows: list[tuple[str, str]], key: bytes) -> None:
    """Write [(title, raw link)] as a Fernet-encrypted file with canonical links. Only ciphertext touches disk."""
    data = {"version": 1, "applied": [
        {"title": re.sub(r"\s+", " ", t).strip(), "link": canonical_link(link)} for t, link in rows]}
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(Fernet(key).encrypt(json.dumps(data, ensure_ascii=False).encode("utf-8")))
