"""What the owner picks and what he dismisses, as counts (state/feedback.json, reports/feedback.md).

The owner reviews the Feed by ticking `Interested` / `Apply` on rows he wants and `done-del` on rows he is done with;
the next run trashes the `done-del` rows. Just before that, each row is counted here by its Fit band, its source and
its two tags: how many were shown, how many were picked. Only counts are kept (no titles, companies or links), so the
file says how well Fit % predicts a pick without recording what was picked. It is the evidence for tuning Fit %.
"""
from datetime import date, datetime, timezone
from pathlib import Path

from scanner.notion_feed import CAUTION_SIGNALS, LEVEL_SIGNALS, has_tag
from scanner.state import _load, _save

FIT_BANDS = ("<60", "60-69", "70-79", "80-89", "90+", "n/a")
DIMENSIONS = (("fit", "Fit %"), ("source", "Source"), ("level", "Level tag"), ("caution", "Warning tag"))
MIN_ROWS = 10        # a band with fewer reviewed rows than this prints no rate: it would be noise


def fit_band(fit: float | None) -> str:
    """The Feed's Fit % cell is a fraction (0.82)."""
    if fit is None:
        return "n/a"
    pct = fit * 100
    for limit, name in ((60, "<60"), (70, "60-69"), (80, "70-79"), (90, "80-89")):
        if pct < limit:
            return name
    return "90+"


def _tags(signals) -> tuple[str, str]:
    level = next((s for s in LEVEL_SIGNALS if has_tag(signals, s)), "none")
    caution = next((s for s in CAUTION_SIGNALS if has_tag(signals, s)), "none")
    return level, caution


class FeedbackStore:
    """dimension -> value -> [reviewed, picked]. `seeded_through` is the creation time of the newest row that is
    already in the counts: older rows are skipped so a count that was entered by hand is never added twice."""

    def __init__(self, path):
        self.path = Path(path)
        data = _load(self.path, {})
        self.counts: dict[str, dict[str, list[int]]] = {d: {k: list(v) for k, v in data.get("counts", {}).get(d, {}).items()}
                                                         for d, _ in DIMENSIONS}
        self.seeded_through: str = data.get("seeded_through", "")
        self.since: str = data.get("since", "")
        self.dirty = False

    def record(self, row, today: date | None = None) -> bool:
        """Count one reviewed row (the caller has just trashed it). False if it predates `seeded_through`."""
        if self.seeded_through and row.created <= datetime.fromisoformat(self.seeded_through.replace("Z", "+00:00")):
            return False
        level, caution = _tags(row.signals)
        for dim, key in (("fit", fit_band(row.fit)), ("source", row.source or "none"), ("level", level),
                         ("caution", caution)):
            cell = self.counts[dim].setdefault(key, [0, 0])
            cell[0] += 1
            cell[1] += 1 if row.picked else 0
        self.since = self.since or (today or datetime.now(timezone.utc).date()).isoformat()
        self.dirty = True
        return True

    @property
    def reviewed(self) -> int:
        return sum(v[0] for v in self.counts["fit"].values())

    @property
    def picked(self) -> int:
        return sum(v[1] for v in self.counts["fit"].values())

    def save(self) -> None:
        _save(self.path, {"version": 1, "since": self.since, "seeded_through": self.seeded_through,
                          "counts": self.counts})
        self.dirty = False

    def render(self) -> str:
        rate_all = self.picked / self.reviewed if self.reviewed else 0
        out = ["# What gets picked", "",
               f"Reviewed rows (ticked `done-del`) since {self.since or 'the start'}: **{self.reviewed}**, picked "
               f"(`Interested` or `Apply`): **{self.picked}** ({rate_all:.0%}). Counts only; a rate needs "
               f"{MIN_ROWS}+ rows. If Fit % predicts your picks, the rate rises down the Fit table.", ""]
        for dim, title in DIMENSIONS:
            keys = self.counts[dim]
            order = FIT_BANDS if dim == "fit" else sorted(keys, key=lambda k: (-keys[k][0], k))
            out += [f"## {title}", "", f"| {title} | Reviewed | Picked | Pick rate |", "|---|---:|---:|---:|"]
            for k in order:
                n, p = keys.get(k, [0, 0])
                rate = f"{p / n:.0%}" if n >= MIN_ROWS else "-"
                out.append(f"| {k} | {n} | {p} | {rate} |")
            out.append("")
        return "\n".join(out)
