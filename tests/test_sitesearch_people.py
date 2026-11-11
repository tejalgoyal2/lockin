from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import unquote

import pytest
import requests

from scanner import notion_feed as nf
from scanner import people
from scanner.sources import ats, sitesearch

NOW = datetime(2026, 11, 11, 16, 0, tzinfo=timezone.utc)


class Client:
    def __init__(self, answer=None, exc=None):
        self.answer, self.exc, self.calls = answer, exc, []

    def get_json(self, url, label, **kw):
        self.calls.append((url, kw))
        if self.exc:
            raise self.exc
        return self.answer


BRAVE = {"web": {"results": [
    {"title": "Junior Software Developer @ Delta AI", "url": "https://jobs.ashbyhq.com/delta/3f2c1a9e-1111-2222-3333-444455556666"},
    {"title": "Acme - Data Analyst", "url": "https://jobs.lever.co/acme/0b1c2d3e-aaaa-bbbb-cccc-ddddeeeeffff"},
    {"title": "Job Application for Software Engineer, New Grad at Beta ...",
     "url": "https://job-boards.greenhouse.io/beta/jobs/4455667#app"},
    {"title": "Acme jobs", "url": "https://jobs.lever.co/acme"},                       # the board, not a posting
    {"title": "Careers at Gamma", "url": "https://gamma.bamboohr.com/careers"},
    {"title": "IT Analyst", "url": "https://epsilon.wd3.myworkdayjobs.com/en-US/External/job/Vancouver/IT-Analyst_R1"},
]}}


def test_brave_results_that_are_postings_become_records():
    recs = sitesearch.parse_brave(BRAVE, NOW)
    got = [(r["company"], r["company_name"], r["title"], r["url"]) for r in recs]
    assert got == [
        ("delta", "Delta AI", "Junior Software Developer", "https://jobs.ashbyhq.com/delta/3f2c1a9e-1111-2222-3333-444455556666"),
        ("acme", "Acme", "Data Analyst", "https://jobs.lever.co/acme/0b1c2d3e-aaaa-bbbb-cccc-ddddeeeeffff"),
        ("beta", "Beta", "Software Engineer, New Grad", "https://job-boards.greenhouse.io/beta/jobs/4455667"),
        ("epsilon", "", "IT Analyst", "https://epsilon.wd3.myworkdayjobs.com/en-US/External/job/Vancouver/IT-Analyst_R1"),
    ]
    assert {ats.detect_ats(r["url"]) for r in recs} == {"Ashby", "Lever", "Greenhouse", "Workday"}
    assert all(r["source"] == "Site Search" and r["location"] == "Canada" for r in recs)


def test_queries_cross_every_host_with_every_title_group():
    qs = sitesearch.queries(["jobs.lever.co", "jobs.ashbyhq.com"], ['("a" OR "b")', "c"], "(Vancouver OR Canada)")
    assert qs[0] == 'site:jobs.lever.co ("a" OR "b") (Vancouver OR Canada)' and len(qs) == 4


def test_brave_is_preferred_and_its_key_goes_in_a_header(monkeypatch):
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "bravekey")
    monkeypatch.setenv("SERPAPI_KEY", "serpkey")
    client = Client(BRAVE)
    recs = sitesearch.fetch(client, ["jobs.lever.co"], ["x", "y"], "Canada", NOW, brave_per_run=2)
    assert len(recs) == 4 and len(client.calls) == 2
    url, kw = client.calls[0]
    assert url == sitesearch.BRAVE and kw["headers"]["X-Subscription-Token"] == "bravekey"
    assert kw["params"]["freshness"] == "pw" and kw["params"]["q"].startswith("site:jobs.lever.co")


def test_serpapi_runs_google_with_a_past_week_filter_when_there_is_no_brave_key(monkeypatch):
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    monkeypatch.setenv("SERPAPI_KEY", "serpkey")
    answer = {"organic_results": [{"title": "Acme - Data Analyst",
                                   "link": "https://jobs.lever.co/acme/0b1c2d3e-aaaa-bbbb-cccc-ddddeeeeffff"}]}
    client = Client(answer)
    recs = sitesearch.fetch(client, ["jobs.lever.co"], ["a", "b", "c"], "Canada", NOW, serpapi_per_run=2)
    assert len(client.calls) == 2 and len(recs) == 1
    assert client.calls[0][1]["params"]["tbs"] == "qdr:w" and client.calls[0][1]["params"]["engine"] == "google"


def test_skipped_without_keys_and_errors_never_carry_a_key(monkeypatch, capsys):
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    assert sitesearch.fetch(Client(BRAVE), ["h"], ["t"], "", NOW) == []
    assert "source skipped" in capsys.readouterr().out
    monkeypatch.setenv("SERPAPI_KEY", "sekret")
    bad = Client(exc=requests.HTTPError("401 for url: https://serpapi.com/search.json?api_key=sekret",
                                        response=SimpleNamespace(status_code=401)))
    with pytest.raises(sitesearch.SiteSearchError) as err:
        sitesearch.fetch(bad, ["h"], ["t"], "", NOW)
    assert "sekret" not in str(err.value) and "401" in str(err.value)


# --- people links ------------------------------------------------------------------------------------

def test_two_people_links_per_job_narrowed_to_company_and_team():
    rec = unquote(people.recruiter_link("Sun Life Financial Inc."))
    assert rec.startswith(people.SEARCH) and '"Sun Life Financial"' in rec and "talent acquisition" in rec
    mgr = unquote(people.manager_link("Scotiabank · nov6", "Data Scientist"))
    assert '"Scotiabank"' in mgr and "machine learning" in mgr and "manager" in mgr
    assert "security" in unquote(people.manager_link("Acme", "Security Analyst"))
    assert "IT OR infrastructure" in unquote(people.manager_link("Acme", "IT Support Analyst"))
    assert "engineering" in unquote(people.manager_link("Acme", "Junior Developer"))
    assert people.recruiter_link("") == "" and people.manager_link("", "x") == ""


def test_feed_rows_get_the_links_only_when_the_columns_exist():
    names = {"recruiter": "Recruiter", "manager": "Hiring Manager"}
    feed = nf.Feed("ds", names, ids={"recruiter": "r1", "manager": "m1"})
    props = nf.people_properties(feed, "Acme", "Data Analyst")
    assert set(props) == {"Recruiter", "Hiring Manager"} and props["Recruiter"]["url"].startswith(people.SEARCH)
    assert nf.people_properties(nf.Feed("ds", names), "Acme", "Data Analyst") == {}
