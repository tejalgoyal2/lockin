"""Workday and SmartRecruiters watchlist boards, HN freshness, and the discover / why helpers."""
from datetime import datetime, timedelta, timezone

import pytest

from scanner.config import load_config
from scanner.discover import explain, slug_guesses
from scanner.filters import Filters
from scanner.models import Job
from scanner.sources import ats, hn
from scanner.sources import watchlist as wl
from scanner.sources.watchlist import Board

NOW = datetime(2026, 11, 8, 16, 0, tzinfo=timezone.utc)
TODAY = NOW.replace(hour=0, minute=0, second=0, microsecond=0)


def posting(title, where="Markham, Ontario, Canada", posted="Posted 2 Days Ago", n=1):
    return {"title": title, "externalPath": f"/job/Markham/{title.replace(' ', '-')}_JR-{n}", "locationsText": where,
            "postedOn": posted}


class WorkdayClient:
    """post_json answers like Workday: an unfiltered call lists the facets, a filtered one pages the postings."""

    def __init__(self, facets, postings):
        self.facets, self.postings, self.calls = facets, postings, []

    def post_json(self, url, ats_label, json=None):
        self.calls.append(json)
        if not json["appliedFacets"] and json["limit"] == 1:
            return {"total": len(self.postings), "facets": self.facets, "jobPostings": []}
        page = self.postings[json["offset"]: json["offset"] + json["limit"]]
        return {"total": len(self.postings), "jobPostings": page}


GM = Board("workday", "generalmotors.wd5/Careers_GM", "General Motors")


def test_workday_slug_becomes_api_and_public_urls():
    assert GM.workday == ("generalmotors.wd5.myworkdayjobs.com", "generalmotors", "Careers_GM")
    assert GM.url == "https://generalmotors.wd5.myworkdayjobs.com/wday/cxs/generalmotors/Careers_GM/jobs"


def test_malformed_workday_slug_stops_the_run(tmp_path):
    p = tmp_path / "w.yaml"
    p.write_text("boards:\n  - {ats: workday, slug: generalmotors/Careers_GM}\n", encoding="utf-8")
    with pytest.raises(wl.WatchlistError, match="workday slug"):
        wl.load_boards(p)


@pytest.mark.parametrize("text,days", [("Posted Today", 0), ("Posted Yesterday", 1), ("Posted 3 Days Ago", 3),
                                       ("Posted 30+ Days Ago", 30)])
def test_workday_posted_text(text, days):
    assert wl.workday_posted(text, NOW) == TODAY - timedelta(days=days)


def test_workday_posted_unreadable_is_none():
    assert wl.workday_posted("Reposted", NOW) is None and wl.workday_posted(None, NOW) is None


def test_workday_reads_only_canada_through_a_top_level_country_facet():
    facets = [{"facetParameter": "Location_Country", "values": [{"descriptor": "Canada", "id": "ca1"},
                                                                {"descriptor": "Brazil", "id": "br"}]}]
    client = WorkdayClient(facets, [posting("Software Engineer", n=i) for i in range(25)])
    recs = wl.fetch_board(GM, client, now=NOW)
    assert len(recs) == 25                                   # two pages of 20
    assert client.calls[1]["appliedFacets"] == {"Location_Country": ["ca1"]}
    r = recs[0]
    assert r["url"].startswith("https://generalmotors.wd5.myworkdayjobs.com/Careers_GM/job/")
    assert (r["source"], r["company"], r["first_seen"]) == ("Workday", "generalmotors", TODAY - timedelta(days=2))


def test_workday_finds_a_country_facet_nested_in_a_group():
    nested = [{"facetParameter": "locationMainGroup", "values": [
        {"facetParameter": "locationCountry", "descriptor": "Location Country",
         "values": [{"descriptor": "Canada", "id": "c9"}]},
        {"facetParameter": "locations", "values": []}]}]
    assert wl._canada_facet(nested) == ("locationCountry", ["c9"])


def test_workday_without_a_country_facet_reads_the_whole_board():
    facets = [{"facetParameter": "locationMainGroup", "values": [{"facetParameter": "locations", "values": []}]}]
    client = WorkdayClient(facets, [posting("Analyst", "TORONTO, Ontario, Canada"), posting("Analyst", "Austin, TX", n=2)])
    recs = wl.fetch_board(GM, client, now=NOW)
    assert len(recs) == 2 and client.calls[1]["appliedFacets"] == {}


def test_workday_tenant_with_no_canadian_value_has_nothing_to_read():
    facets = [{"facetParameter": "locationCountry", "values": [{"descriptor": "Brazil", "id": "br"}]}]
    client = WorkdayClient(facets, [posting("Engineer", "Sao Paulo")])
    # No Canada value: falls back to reading the board, where the location filter drops the Brazilian job.
    assert [r["location"] for r in wl.fetch_board(GM, client, now=NOW)] == ["Sao Paulo"]


def test_workday_n_locations_row_passes_phase_1_unresolved():
    cfg = load_config(None)
    from scanner.pipeline import run_stream
    rec = wl.parse_workday(GM, [posting("Software Engineer", "2 Locations", "Posted Today")], NOW)
    kept, _, _ = run_stream(iter(rec), wl.raw_fields, Filters.from_config(cfg), NOW, 7)
    assert len(kept) == 1 and kept[0].location_pending


def test_workday_public_url_is_located_for_the_jd_fetch():
    url = "https://generalmotors.wd5.myworkdayjobs.com/Careers_GM/job/Markham-Ontario-Canada/Software-Engineer_JR-1"
    job = Job(company="generalmotors", company_name="", title="t", location="x", url=url, source="Workday",
              first_seen=NOW, sources={"Workday"})
    ref = ats.locate(job)
    assert ref.api_url == ("https://generalmotors.wd5.myworkdayjobs.com/wday/cxs/generalmotors/Careers_GM/"
                           "job/Markham-Ontario-Canada/Software-Engineer_JR-1")


# --- SmartRecruiters --------------------------------------------------------------------------

SR = Board("smartrecruiters", "ServiceNow", "ServiceNow")


class SRClient:
    def __init__(self, pages):
        self.pages, self.urls = pages, []

    def get_json(self, url, ats_label, **kw):
        self.urls.append(url)
        return {"content": self.pages.pop(0)}


def test_smartrecruiters_asks_for_canada_and_builds_public_links():
    row = {"id": "744", "name": "Associate Software Engineer", "releasedDate": "2026-11-06T10:00:00.000Z",
           "location": {"city": "Toronto", "region": "ON", "country": "ca", "remote": False}}
    client = SRClient([[row]])
    recs = wl.fetch_board(SR, client, now=NOW)
    assert "country=ca" in client.urls[0]
    assert recs[0]["url"] == "https://jobs.smartrecruiters.com/ServiceNow/744"
    assert recs[0]["location"] == "Toronto, ON, CA" and recs[0]["first_seen"].day == 6


def test_smartrecruiters_jd_and_locations():
    url = "https://jobs.smartrecruiters.com/ServiceNow/744000012345-associate-software-engineer"
    assert ats.detect_ats(url) == "SmartRecruiters"
    job = Job(company="ServiceNow", company_name="", title="t", location="x", url=url, source="SmartRecruiters",
              first_seen=NOW, sources={"SmartRecruiters"})
    assert ats.locate(job).api_url == "https://api.smartrecruiters.com/v1/companies/ServiceNow/postings/744000012345"
    data = {"jobAd": {"sections": {"jobDescription": {"title": "Job Description", "text": "<p>Build <b>Python</b></p>"},
                                   "qualifications": {"title": "Qualifications", "text": "<ul><li>SQL</li></ul>"}}},
            "location": {"city": "Toronto", "region": "ON", "country": "ca"}}
    text = ats._sr_text(data)
    assert "Build" in text and "Qualifications" in text and "SQL" in text
    assert ats._sr_locations(data) == ["Toronto, ON, CA"]


# --- HN ---------------------------------------------------------------------------------------

def comment(text, created="2026-11-02T10:00:00Z", oid="1"):
    return {"comment_text": text, "created_at": created, "objectID": oid}


def test_hn_posting_counts_as_first_seen_now_not_when_the_comment_was_written():
    c = comment("Acme | Software Engineer | Toronto, ON | Full-time<p>We build things")
    assert hn.parse_comment(c)["first_seen"].day == 2
    assert hn.parse_comment(c, NOW)["first_seen"] == NOW      # a 3-day window used to drop the whole thread


def test_hn_header_with_a_canadian_place_but_no_role_reads_the_comment():
    c = comment("Strello Health | Toronto (hybrid)<p>We are hiring a software developer to join our team")
    rec = hn.parse_comment(c, NOW)
    assert rec and rec["title"] == "Engineer" and rec["location"].startswith("Toronto")
    assert hn.parse_comment(comment("Credit Genie | NYC | Toronto<p>Marketing and sales roles"), NOW) is None


class HnClient:
    def __init__(self, thread_created):
        self.thread_created = thread_created

    def get_json(self, url, label):
        if "story,author_whoishiring" in url or "tags=story," in url:
            return {"hits": [{"title": "Ask HN: Who is hiring? (November 2026)", "objectID": "9", "created_at": self.thread_created}]}
        return {"hits": [], "nbPages": 0}


def test_hn_ignores_a_stale_thread():
    assert hn.fetch(HnClient("2026-08-01T00:00:00Z"), NOW) == []


# --- discover / why ---------------------------------------------------------------------------

def test_slug_guesses():
    assert slug_guesses("Arc'teryx Inc.")[0] == "arcteryx"
    assert slug_guesses("Thomson Reuters")[:3] == ["thomsonreuters", "thomson-reuters", "thomson"]
    assert slug_guesses("") == []


FILTERS = Filters.from_config(load_config(None))


@pytest.mark.parametrize("title,where,posted,expect", [
    ("Staff Software Engineer", "Markham, Ontario, Canada", NOW - timedelta(days=1), "excluded word"),
    ("Software Engineer", "Austin, TX, United States", NOW - timedelta(days=1), "not Canadian"),
    ("Software Engineer", "Markham, Ontario, Canada", NOW - timedelta(days=20), "older than"),
    ("Cell Operator", "Markham, Ontario, Canada", NOW - timedelta(days=1), "no tech-role"),
    ("Software Engineer", "Markham, Ontario, Canada", NOW - timedelta(days=1), "kept"),
])
def test_explain_names_the_first_rule_that_stops_a_posting(title, where, posted, expect):
    rec = {"title": title, "location": where, "first_seen": posted, "company": "acme"}
    assert expect in explain(rec, FILTERS, NOW, 7)


def test_one_workday_requisition_under_three_site_names_is_one_pending_job():
    from scanner.dedupe import dedupe, pending_key
    base = "https://sunlife.wd3.myworkdayjobs.com/{}/job/Waterloo-Ontario/ServiceNow-Associate-Software-Engineer_JR00129490{}"
    urls = [base.format("experienced", ""), base.format("experienced-jobs", "-1"), base.format("Experienced-Jobs", "")]
    assert len({pending_key(u) for u in urls}) == 1
    jobs = [Job(company="sunlife", company_name="", title="ServiceNow Associate Software Engineer", location="2 Locations",
                url=u, source="Workday", first_seen=NOW - timedelta(days=i), sources={"Workday"}, location_pending=True)
            for i, u in enumerate(urls)]
    out, merges = dedupe(jobs)
    assert len(out) == 1 and merges["fuzzy"] == 2
    other = Job(company="sunlife", company_name="", title="ServiceNow Associate Software Engineer", location="2 Locations",
                url=base.format("experienced", "").replace("129490", "999999"), source="Workday", first_seen=NOW,
                sources={"Workday"}, location_pending=True)
    assert len(dedupe([jobs[0], other])[0]) == 2             # a different requisition is a different job
