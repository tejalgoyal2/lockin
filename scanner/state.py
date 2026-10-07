"""state/seen.json and state/gaps.json: small JSON stores committed back to the repo (SPEC §7)."""
import json
from datetime import date, timedelta
from pathlib import Path

PRUNE_DAYS = 90


def _load(path: Path, empty: dict) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else empty
    except (FileNotFoundError, ValueError):
        return empty


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)   # atomic: a killed run never leaves a half-written state file


def _prune(entries: dict, today: date, days: int, when) -> dict:
    cutoff = (today - timedelta(days=days)).isoformat()
    return {k: v for k, v in entries.items() if when(v) >= cutoff}


class SeenStore:
    """job key -> date we first wrote it to the Feed. Stops a row the owner moved out of the Feed
    (his automation copies it to the main database and deletes it) from coming back."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.existed = self.path.is_file()
        self.seen: dict[str, str] = _load(self.path, {}).get("seen", {})

    def prune(self, today: date, days: int = PRUNE_DAYS) -> None:
        self.seen = _prune(self.seen, today, days, lambda d: d)

    def add(self, key: str, today: date) -> None:
        self.seen.setdefault(key, today.isoformat())

    def save(self) -> None:
        _save(self.path, {"version": 1, "seen": self.seen})


class GapsStore:
    """job key -> {date, title, company, gaps}: one entry per job whose JD was fetched."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.jobs: dict[str, dict] = _load(self.path, {}).get("jobs", {})

    def prune(self, today: date, days: int = PRUNE_DAYS) -> None:
        self.jobs = _prune(self.jobs, today, days, lambda e: e["date"])

    def record(self, key: str, today: date, title: str, company: str, gaps: list[str]) -> bool:
        """Record a job once. Returns False if the key is already stored."""
        if key in self.jobs:
            return False
        self.jobs[key] = {"date": today.isoformat(), "title": title, "company": company, "gaps": sorted(gaps)}
        return True

    def save(self) -> None:
        _save(self.path, {"version": 1, "jobs": self.jobs})
