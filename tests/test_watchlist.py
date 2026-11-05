import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scanner.config import load_config
from scanner.pipeline import run_stream
from scanner.sources import watchlist as wl
from scanner.sources.watchlist import Board

FIX = Path(__file__).parent / "fixtures" / "watchlist"
NOW = datetime(2026, 11, 5, 16, 0, tzinfo=timezone.utc)


def load(name):
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


class FakeClient:
    """Stands in for PoliteClient: answers by URL, or raises."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def get_json(self, url, ats, **kw):
        self.calls.append((url, ats))
        answer = self.answers[url]
        if isinstance(answer, Exception):
            raise answer
        return answer


# --- watchlist.yaml -----------------------------------------------------------------------

def write(tmp_path, text):
    p = tmp_path / "watchlist.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_load_boards(tmp_path):
    p = write(tmp_path, "boards:\n  - {ats: Ashby, slug: acme, name: Acme}\n  - {ats: lever, slug: beta}\n")
    assert wl.load_boards(p) == [Board("ashby", "acme", "Acme"), Board("lever", "beta", "")]


@pytest.mark.parametrize("body", [
    "boards:\n  - {ats: workable, slug: x}\n",                       # unsupported ATS
    "boards:\n  - {ats: lever}\n",                                   # no slug
    "boards:\n  - {ats: lever, slug: x}\n  - {ats: lever, slug: X}\n",  # duplicate
])
def test_bad_entries_are_errors_not_skipped_lines(tmp_path, body):
    with pytest.raises(wl.WatchlistError):
        wl.load_boards(write(tmp_path, body))


def test_missing_file_is_an_error(tmp_path):
    with pytest.raises(wl.WatchlistError):
        wl.load_boards(tmp_path / "nope.yaml")


def test_the_committed_watchlist_is_valid():
    boards = wl.load_boards(Path(__file__).parent.parent / "watchlist.yaml")
    assert boards and len({(b.ats, b.slug.lower()) for b in boards}) == len(boards)


def test_board_urls():
    assert Board("greenhouse", "affirm").url == "https://boards-api.greenhouse.io/v1/boards/affirm/jobs"
    assert Board("lever", "geocomply-2").url == "https://api.lever.co/v0/postings/geocomply-2?mode=json"
    assert Board("ashby", "1password").url == "https://api.ashbyhq.com/posting-api/job-board/1password"


# --- parsing the three real response shapes ---------------------------------------------------

def test_greenhouse_uses_first_published_then_updated_at():
    recs = wl.parse_board(Board("greenhouse", "affirm", "Affirm"), load("greenhouse"))
    assert len(recs) == 3
    assert all(r["source"] == "Greenhouse" and r["company"] == "affirm" and r["company_name"] == "Affirm" for r in recs)
    assert recs[0]["first_seen"].tzinfo is not None
    undated = next(r for r in recs if r["title"] == "Undated Posting")
    assert undated["first_seen"] == datetime(2026, 11, 1, 14, 0, tzinfo=timezone.utc)   # updated_at, 10:00-04:00


def test_lever_epoch_milliseconds_and_all_locations():
    recs = wl.parse_board(Board("lever", "geocomply-2", "GeoComply"), load("lever"))
    first = recs[0]
    assert first["source"] == "Lever" and first["location"] == "Vancouver, BC"
    assert first["first_seen"] == datetime.fromtimestamp(1783631724871 / 1000, tz=timezone.utc)
    assert first["url"].startswith("https://jobs.lever.co/geocomply-2/")


def test_ashby_skips_unlisted_and_joins_secondary_locations():
    recs = wl.parse_board(Board("ashby", "1password"), load("ashby"))
    assert "Unlisted Role" not in [r["title"] for r in recs]
    multi = [r for r in recs if " ; " in r["location"]]
    assert multi, "the fixture has a job with secondary locations"
    assert all(r["first_seen"] is not None and r["url"].startswith("https://jobs.ashbyhq.com/1password/") for r in recs)


def test_records_without_title_or_url_and_unreadable_dates_are_tolerated():
    data = {"jobs": [{"title": "", "absolute_url": "https://x/1"}, {"title": "A", "absolute_url": ""},
                     {"title": "Dev", "absolute_url": "https://x/3", "first_published": "not a date"}]}
    recs = wl.parse_board(Board("greenhouse", "x"), data)
    assert [r["title"] for r in recs] == ["Dev"] and recs[0]["first_seen"] is None


def test_lever_error_object_instead_of_a_list_yields_nothing():
    assert wl.parse_board(Board("lever", "x"), {"ok": False, "error": "Document not found"}) == []


# --- fetching ------------------------------------------------------------------------------

def test_one_dead_board_does_not_stop_the_others():
    boards = [Board("greenhouse", "affirm"), Board("lever", "gone"), Board("ashby", "1password")]
    client = FakeClient({boards[0].url: load("greenhouse"), boards[1].url: RuntimeError("HTTP 404"),
                         boards[2].url: load("ashby")})
    seeded = {}
    records, failed = wl.fetch_all(boards, client, workers=2, seed_ashby=seeded.__setitem__)
    assert [b.slug for b, _ in failed] == ["gone"] and "404" in failed[0][1]
    assert {r["source"] for r in records} == {"Greenhouse", "Ashby"}
    assert list(seeded) == [boards[2].url]            # the Ashby board is handed to the JD fetcher


def test_watchlist_records_flow_through_the_phase_1_filters():
    """Fresh + Canadian + tech title: kept. US-only and stale: dropped. Uses the real config."""
    from scanner.filters import Filters
    filters = Filters.from_config(load_config())
    board = Board("lever", "acme", "Acme")
    day = lambda d: int((NOW - timedelta(days=d)).timestamp() * 1000)
    data = [
        {"text": "Software Developer", "hostedUrl": "https://jobs.lever.co/acme/1", "createdAt": day(1),
         "categories": {"location": "Vancouver, BC", "allLocations": ["Vancouver, BC"]}},
        {"text": "Software Developer", "hostedUrl": "https://jobs.lever.co/acme/2", "createdAt": day(1),
         "categories": {"location": "Austin, TX", "allLocations": ["Austin, TX"]}},
        {"text": "Software Developer", "hostedUrl": "https://jobs.lever.co/acme/3", "createdAt": day(30),
         "categories": {"location": "Toronto, ON", "allLocations": ["Toronto, ON"]}},
        {"text": "Software Developer", "hostedUrl": "https://jobs.lever.co/acme/4", "createdAt": None,
         "categories": {"location": "Toronto, ON", "allLocations": ["Toronto, ON"]}},
    ]
    kept, stages, _ = run_stream(iter(wl.parse_board(board, data)), wl.raw_fields, filters, NOW, 3)
    assert [j.url for j in kept] == ["https://jobs.lever.co/acme/1"]
    job = kept[0]
    assert job.source == "Lever" and job.company == "acme" and job.company_name == "Acme"
    assert dict(stages)["raw"] == 4 and dict(stages)["fresh"] == 2
