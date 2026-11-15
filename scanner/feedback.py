"""What the owner picks and what he dismisses, as counts (state/feedback.json, reports/feedback.md).

The owner reviews the Feed by ticking `I` (Interested) / `A` (Apply) on rows he wants and `done-del` on rows he is done
with; the next run trashes the `done-del` rows. Just before that, each row is counted here by its Score, its Exp range
and its Province tags: how many were shown, how many were picked. Only counts are kept (no titles, companies or
links). If Score is doing its job, the pick rate rises with it; a Score whose rate is out of line is the place to
look at scanner/rating.py.
"""
from datetime import date, datetime, timezone
from pathlib import Path

from scanner.state import _load, _save

DIMENSIONS = (("score", "Score"), ("exp", "Exp"), ("province", "Province"))
VERSION = 2          # 1 counted the Fit % bands and tags of the old Feed columns
MIN_ROWS = 10        # fewer reviewed rows than this prints no rate: it would be noise


class FeedbackStore:
    """dimension -> value -> [reviewed, picked]."""

    def __init__(self, path):
        self.path = Path(path)
        data = _load(self.path, {})
        if data.get("version") != VERSION:
            data = {}
        self.counts: dict[str, dict[str, list[int]]] = {d: {k: list(v) for k, v in data.get("counts", {}).get(d, {}).items()}
                                                         for d, _ in DIMENSIONS}
        self.since: str = data.get("since", "")
        self.dirty = False

    def record(self, row, today: date | None = None) -> None:
        """Count one reviewed row (the caller has just trashed it)."""
        keys = [("score", str(row.score or "none")), ("exp", str(row.exp or "none"))]
        keys += [("province", p) for p in (row.provinces or ("none",))]
        for dim, key in keys:
            cell = self.counts[dim].setdefault(key, [0, 0])
            cell[0] += 1
            cell[1] += 1 if row.picked else 0
        self.since = self.since or (today or datetime.now(timezone.utc).date()).isoformat()
        self.dirty = True

    @property
    def reviewed(self) -> int:
        return sum(v[0] for v in self.counts["score"].values())

    @property
    def picked(self) -> int:
        return sum(v[1] for v in self.counts["score"].values())

    def save(self) -> None:
        _save(self.path, {"version": VERSION, "since": self.since, "counts": self.counts})
        self.dirty = False

    def render(self) -> str:
        rate_all = self.picked / self.reviewed if self.reviewed else 0
        out = ["# What gets picked", "",
               f"Reviewed rows (ticked `done-del`) since {self.since or 'the start'}: **{self.reviewed}**, picked "
               f"(`I` or `A`): **{self.picked}** ({rate_all:.0%}). Counts only; a rate needs {MIN_ROWS}+ rows. "
               "If Score is doing its job, the pick rate rises with it.", ""]
        for dim, title in DIMENSIONS:
            keys = self.counts[dim]
            out += [f"## {title}", "", f"| {title} | Reviewed | Picked | Pick rate |", "|---|---:|---:|---:|"]
            for k in sorted(keys, reverse=dim == "score"):
                n, p = keys[k]
                out.append(f"| {k} | {n} | {p} | {f'{p / n:.0%}' if n >= MIN_ROWS else '-'} |")
            out.append("")
        return "\n".join(out)
