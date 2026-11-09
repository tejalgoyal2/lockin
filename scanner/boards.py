"""Board memory: the employer boards the scan has met, the ones that broke, and the new ones it found by itself.

Two jobs, both learned from career-ops (discover-new-companies, dead-boards):

* **Finding employers.** Every Canadian posting the scan sees carries a link, and a link on Workday, Greenhouse,
  Lever, Ashby or SmartRecruiters names the employer's board exactly (`acme.wd5.myworkdayjobs.com/en-US/Careers/job/...`
  is the board `acme.wd5/Careers`). Boards not on the watchlist are counted; after the scan the busiest ones
  (by postings whose title passes the title filters) are read once, and a board that really lists such postings is appended to `watchlist.auto.yaml`, which the next run reads
  next to the hand-kept `watchlist.yaml`. The watchlist grows by itself, and the hand-kept file stays yours.
* **Not wasting time.** A board that cannot be read is retried on the next runs; after `MAX_FAILS` failures in a row it
  is left alone for a week (then two, then four) and tried again, and a candidate that cannot be read in `MAX_PROBES`
  probes is given up. A board that works again is back at once.

State lives in `state/boards.json`; nothing here touches Notion.
"""
import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from scanner.sources.watchlist import Board

MAX_FAILS = 3        # reads that fail in a row before a board is left alone
MAX_PROBES = 3       # reads of a candidate board that fail before it is given up
MAX_BACKOFF_DAYS = 28
PROBE_SECONDS = 40   # a candidate is read this long at most: the newest postings tell whether it lists Canadian jobs
_LOCALE = re.compile(r"^[a-z]{2}([-_][A-Za-z]{2})?$")
_WORKDAY_HOST = re.compile(r"^([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com$")
_GREENHOUSE_HOSTS = ("boards.greenhouse.io", "job-boards.greenhouse.io")


def key(board: Board) -> str:
    return f"{board.ats}/{board.slug}".lower()


def board_from_url(url: str) -> Board | None:
    """The employer board a posting's link belongs to; None for a link on any other site (the employer's own,
    iCIMS, Taleo, a job site) or one that names no board (a Greenhouse job embedded in the employer's own pages)."""
    parts = urlsplit(url or "")
    host, segs = parts.netloc.lower(), [s for s in parts.path.split("/") if s]
    m = _WORKDAY_HOST.match(host)
    if m:
        if segs and _LOCALE.match(segs[0]):
            segs = segs[1:]
        if len(segs) >= 3 and any(s in ("job", "details") for s in segs[1:]):
            return Board("workday", f"{m.group(1)}.{m.group(2)}/{segs[0]}")
        return None
    if host in _GREENHOUSE_HOSTS and len(segs) >= 3 and segs[1] == "jobs":
        return Board("greenhouse", segs[0])
    if host.endswith(".icims.com") and len(segs) >= 3 and segs[0] == "jobs" and segs[1].isdigit():
        return Board("icims", host)
    if host == "jobs.lever.co" and len(segs) >= 2:
        return Board("lever", segs[0])
    if host == "jobs.ashbyhq.com" and len(segs) >= 2:
        return Board("ashby", segs[0])
    if host in ("jobs.smartrecruiters.com", "careers.smartrecruiters.com") and len(segs) >= 2 and segs[1][:1].isdigit():
        return Board("smartrecruiters", segs[0])
    return None


@dataclass
class Found:
    board: Board
    name: str
    count: int


class BoardStore:
    """state/boards.json: {"health": {board: {...}}, "found": {board: {...}}}; plus this run's harvest in memory."""

    def __init__(self, path: str | Path, relevant=None):
        """`relevant(title) -> bool`: a title the scan could use (passes the title filters). When given, only boards with
        at least one such posting are candidates, and the ones with most of them come first: a hospital group with
        3,000 Canadian nursing jobs is not worth a watchlist line."""
        self.relevant = relevant
        self.path = Path(path)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            data = {}
        self.health: dict[str, dict] = data.get("health", {})
        self.found: dict[str, dict] = data.get("found", {})
        self.watched: set[str] = set()          # keys of the boards on the watchlist this run (set by the CLI)
        self._harvest: Counter = Counter()
        self._useful: Counter = Counter()
        self._names: dict[str, tuple[Board, str]] = {}

    # -- health: do not waste time on a board that keeps failing ---------------------------------
    def due(self, board: Board, today: date) -> bool:
        h = self.health.get(key(board))
        return not h or not h.get("next_try") or h["next_try"] <= today.isoformat()

    def record_read(self, board: Board, error: str, today: date) -> None:
        """Called once per board per run: `error` is "" when it was read."""
        k = key(board)
        if not error:
            if k in self.health:
                self.health[k] = {"fails": 0, "last_ok": today.isoformat()}
            return
        h = self.health.setdefault(k, {"fails": 0})
        h["fails"] = h.get("fails", 0) + 1
        h["last_fail"], h["error"] = today.isoformat(), error[:120]
        if h["fails"] >= MAX_FAILS:
            days = min(MAX_BACKOFF_DAYS, 7 * 2 ** (h["fails"] - MAX_FAILS))
            h["next_try"] = (today + timedelta(days=days)).isoformat()

    def dormant(self, today: date) -> list[tuple[str, dict]]:
        return sorted((k, h) for k, h in self.health.items()
                      if h.get("next_try") and h["next_try"] > today.isoformat())

    # -- finding employers --------------------------------------------------------------------
    def tap(self, f: dict) -> None:
        """Pipeline hook: one Canadian posting. Counts it for the board its link belongs to."""
        board = board_from_url(f.get("url", ""))
        if board is None:
            return
        k = key(board)
        self._harvest[k] += 1
        if self.relevant is None or self.relevant(f.get("title", "")):
            self._useful[k] += 1
        name = f.get("company_name") or ""
        if k not in self._names or (name and not self._names[k][1]):
            self._names[k] = (board, name)

    def candidates(self, known: set[str], today: date, limit: int) -> list[Found]:
        """Boards met this run that are not on any watchlist, busiest first, skipping the ones already added or given up."""
        out = []
        for k, n in (self._useful if self.relevant else self._harvest).most_common():
            if k in known or n == 0:
                continue
            rec = self.found.get(k, {})
            if rec.get("status") in ("added", "dead"):
                continue
            board, name = self._names[k]
            name = rec.get("name") or name or (board.slug.split(".")[0].split("/")[0] if board.ats == "workday" else board.slug)
            out.append(Found(board, name, n))
            if len(out) >= limit:
                break
        return out

    def record_probe(self, found: Found, status: str, today: date, canadian: int = 0, error: str = "") -> None:
        """status: "added" | "empty" (read, no Canadian posting) | "error"."""
        k = key(found.board)
        rec = self.found.setdefault(k, {"first": today.isoformat(), "probes": 0})
        rec.update(name=found.name, seen=found.count, last=today.isoformat(), canadian=canadian)
        rec["probes"] = rec.get("probes", 0) + (1 if status != "added" else 0)
        rec["status"] = status if status == "added" else ("dead" if rec["probes"] >= MAX_PROBES else status)
        if error:
            rec["error"] = error[:120]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"version": 1, "health": self.health, "found": self.found}, indent=1,
                                  sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(self.path)


# --- watchlist.auto.yaml -----------------------------------------------------------------------

AUTO_HEADER = """# Boards the scanner found by itself (scanner/boards.py): every line is an employer whose Canadian postings the scan met
# through a Workday / Greenhouse / Lever / Ashby / SmartRecruiters link, and whose board was then read and listed Canadian
# jobs. The runs add lines here; delete a line you do not want (it is not added again once it is in state/boards.json),
# or move it to watchlist.yaml to keep it for good. Same format as watchlist.yaml.

boards:
"""


def auto_count(path: str | Path) -> int:
    try:
        return len(re.findall(r"^\s*- \{ats:", Path(path).read_text(encoding="utf-8"), re.M))
    except FileNotFoundError:
        return 0


def append_auto(path: str | Path, board: Board, name: str, canadian: int, today: date) -> None:
    p = Path(path)
    if not p.is_file():
        p.write_text(AUTO_HEADER, encoding="utf-8")
    label = json.dumps(name or board.slug, ensure_ascii=False)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(f"  - {{ats: {board.ats}, slug: {board.slug}, name: {label}}}"
                 f"        # {canadian} Canadian postings when found, {today.isoformat()}\n")


def known_keys(boards: list[Board]) -> set[str]:
    return {key(b) for b in boards}



def promote(store: BoardStore, client, filters, now, *, auto_path, limit: int, auto_max: int, dry_run: bool = False,
            workers: int = 6, min_candidates: int = 2, skip: re.Pattern | None = None):
    """Read the busiest boards met this run that are not on the watchlist; the ones that list Canadian postings with a
    title the filters accept go to `auto_path`. Returns [(Found, Hit)] for the boards added (or that a dry run would add)."""
    from scanner import discover
    from scanner.sources import watchlist
    today = now.date()
    room = max(0, auto_max - auto_count(auto_path))
    added = []

    def read(found: Found):
        try:
            return found, watchlist.fetch_board(found.board, client, now=now, budget=PROBE_SECONDS), ""
        except Exception as exc:                               # 403, 404, 5xx: counts as a probe; MAX_PROBES gives up
            return found, [], f"{type(exc).__name__}: {exc}"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        picked = [c for c in store.candidates(store.watched, today, limit * 3)
                  if not (skip and (skip.search(c.board.slug) or skip.search(c.name)))][:limit]
        results = list(pool.map(read, picked))
    for found, records, error in results:
        if len(added) >= room:
            break
        if error:
            if not dry_run:
                store.record_probe(found, "error", today, error=error)
            continue
        hit = discover._summarise(found.board, records, filters, now)
        if hit.candidates < min_candidates:                      # too few Canadian jobs a title filter would let through
            if not dry_run:
                store.record_probe(found, "empty", today)
            continue
        if not dry_run:
            append_auto(auto_path, found.board, found.name, hit.canadian, today)
            store.record_probe(found, "added", today, canadian=hit.canadian)
        added.append((found, hit))
    return added


def render(store: BoardStore, added, today: date) -> str:
    lines = ["# Boards", "",
             "What the scanner learned about employer boards (state/boards.json). Boards it found by itself are in "
             "`watchlist.auto.yaml`.", ""]
    lines += [f"## Added this run ({len(added)})", ""]
    lines += [f"- `{key(f.board)}` {f.name}: {h.canadian} Canadian postings, {h.candidates} pass the title filters"
              for f, h in added] or ["- none"]
    resting = store.dormant(today)
    lines += ["", f"## Resting ({len(resting)})", "",
              f"Failed {MAX_FAILS}+ reads in a row; tried again after the date shown (a working read clears it).", ""]
    lines += [f"- `{k}`: {h.get('fails', 0)} failures, back on {h['next_try']} ({h.get('error', '')})" for k, h in resting] or ["- none"]
    waiting = [(k, r) for k, r in sorted(store.found.items()) if r.get("status") not in ("added",)]
    lines += ["", f"## Candidates not added ({len(waiting)})", "",
              f"A board is given up after {MAX_PROBES} probes that find nothing to add.", ""]
    lines += [f"- `{k}` {r.get('name', '')}: {r.get('status')} after {r.get('probes', 0)} probes {r.get('error', '')}".rstrip()
              for k, r in waiting] or ["- none"]
    return "\n".join(lines) + "\n"
