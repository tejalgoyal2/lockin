import hashlib
import re
from datetime import datetime, timezone

_PAREN = re.compile(r"\([^)]*\)")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def norm_text(s: str) -> str:
    """Lowercase, drop parenthesised bits (req IDs), strip everything but a-z0-9."""
    s = _PAREN.sub(" ", s or "").lower()
    return _NON_ALNUM.sub("", s)


def norm_location(s: str) -> str:
    """City-level key: the part before the first comma."""
    return norm_text((s or "").split(",")[0])


def job_key(company: str, title: str, location: str) -> str:
    raw = "|".join((norm_text(company), norm_text(title), norm_location(location)))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def parse_iso(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def from_unix(ts: int | float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def clean_title(title: str) -> str:
    return re.sub(r"\s+", " ", title or "").strip()
