"""Phenom People career sites (Bell, Air Canada, OpenText ...): the search endpoint, the posting page, the watchlist entry."""
import json
from datetime import datetime, timezone

import pytest

from scanner.models import Job
from scanner.sources import ats, phenom
from scanner.sources import watchlist as wl
from scanner.sources.watchlist import Board

NOW = datetime(2026, 11, 8, 16, 0, tzinfo=timezone.utc)
BELL = Board("phenom", "jobs.bell.ca/ca/en", "Bell")


def hit(job_id="433002", title="Software Developer", where=("Toronto, Ontario",), posted="2026-11-06T00:00:00.000+0000"):
    return {"jobId": job_id, "reqId": job_id, "title": title, "multi_location": list(where), "location": where[0],
            "postedDate": posted, "dateCreated": "2026-11-06T17:08:21.786+0000"}


def answer(jobs, total=None):
    return {"refineSearch": {"status": 200, "hits": len(jobs), "totalHits": total if total is not None else len(jobs),
                             "data": {"jobs": jobs}}}


def test_slug_splits_into_host_path_and_language():
    assert phenom.split_slug("jobs.bell.ca/ca/en") == ("jobs.bell.ca", "ca/en", "en_ca")
    assert BELL.url == "https://jobs.bell.ca/widgets" and BELL.label == "Phenom"
    for bad in ("jobs.bell.ca", "jobs.bell.ca/en", "https://jobs.bell.ca/ca/en"):
        with pytest.raises(phenom.PhenomError):
            phenom.split_slug(bad)


def test_a_malformed_phenom_slug_stops_the_run(tmp_path):
    p = tmp_path / "w.yaml"
    p.write_text("boards:\n  - {ats: phenom, slug: jobs.bell.ca}\n", encoding="utf-8")
    with pytest.raises(wl.WatchlistError, match="phenom slug"):
        wl.load_boards(p)


def test_search_answer_becomes_records_with_a_public_url_and_all_locations():
    recs, total = phenom.parse_search(BELL.slug, "Bell", answer([hit(where=("Toronto, Ontario", "Ottawa, Ontario")), hit("2", "")], 41))
    assert total == 41 and len(recs) == 1                                   # a posting without a title is skipped
    assert recs[0] == {"company": "jobs.bell.ca/ca/en", "company_name": "Bell", "title": "Software Developer",
                       "url": "https://jobs.bell.ca/ca/en/job/433002", "location": "Toronto, Ontario ; Ottawa, Ontario",
                       "source": "Phenom", "first_seen": datetime(2026, 11, 6, tzinfo=timezone.utc)}


def test_an_answer_without_the_search_block_raises():
    with pytest.raises(phenom.PhenomError, match="refineSearch"):
        phenom.parse_search(BELL.slug, "Bell", {"error": "x"})


class SearchClient:
    def __init__(self, pages):
        self.pages, self.bodies = pages, []

    def post_json(self, url, label, json=None):
        self.bodies.append(json)
        return self.pages[json["from"] // phenom.PAGE]


def test_the_board_is_paged_until_every_hit_is_read_newest_first():
    pages = [answer([hit(str(i)) for i in range(40)], 45), answer([hit(str(100 + i)) for i in range(5)], 45)]
    client = SearchClient(pages)
    recs = wl.fetch_board(BELL, client, now=NOW)
    assert len(recs) == 45 and len(client.bodies) == 2
    assert client.bodies[0]["selected_fields"] == {} and client.bodies[0]["lang"] == "en_ca"
    assert client.bodies[0]["sort"] == {"order": "desc", "field": "postedDate"}


def page_with(job):
    return f'<html><script>var x=1;phApp.ddo = {json.dumps({"jobDetail": {"data": {"job": job}}})};phApp.other = 2;</script></html>'


def test_posting_page_gives_the_description_and_locations():
    text, where = phenom.parse_detail(page_with({"description": "<p>Python and SQL.</p><ul><li>1 year of experience</li></ul>",
                                                 "multi_location": ["Toronto, Ontario", "Toronto, Ontario"], "location": "x"}))
    assert "Python and SQL." in text and "- 1 year of experience" in text and where == ["Toronto, Ontario"]
    with pytest.raises(phenom.PhenomError):
        phenom.parse_detail("<html></html>")
    with pytest.raises(phenom.PhenomError):
        phenom.parse_detail("phApp.ddo = {not json")


def test_a_phenom_posting_link_is_recognised_on_any_host_and_nothing_else_is():
    url = "https://jobs.bell.ca/ca/en/job/433002"
    job = Job("jobs.bell.ca/ca/en", "Dev", "Toronto, Ontario", url, "Phenom", NOW)
    assert ats.detect_ats(url) == "Phenom" and ats.locate(job).ats == "Phenom"
    assert ats.detect_ats("https://careers.aircanada.com/ca/en/job/R1/Dev-Title") == "Phenom"
    for other in ("https://acme.wd5.myworkdayjobs.com/en-US/Careers/job/T/Dev_1", "https://example.com/careers/1",
                  "https://jobs.lever.co/acme/abc", "https://jobs.bell.ca/ca/en/search-results"):
        assert ats.detect_ats(other) != "Phenom"


def test_locations_are_dicts_on_the_posting_page_and_strings_in_the_search_answer():
    page_job = {"description": "<p>x</p>", "multi_location": [{"location": "Verdun, Quebec", "city": "Verdun"},
                                                              {"cityState": "Toronto, Ontario"}]}
    assert phenom.parse_detail(page_with(page_job))[1] == ["Verdun, Quebec", "Toronto, Ontario"]
    recs, _ = phenom.parse_search(BELL.slug, "Bell", answer([{**hit(), "multi_location": [{"location": "Ottawa, Ontario"}]}]))
    assert recs[0]["location"] == "Ottawa, Ontario"
