from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import requests

from scanner import enrich
from scanner.models import Job
from scanner.sources import ats, googlealerts, googlejobs, supplied

NOW = datetime(2026, 11, 11, 16, 0, tzinfo=timezone.utc)


class Resp:
    def __init__(self, text="", data=None, status=200):
        self.text, self._data, self.status_code = text, data, status

    def json(self):
        return self._data


class Client:
    def __init__(self, answer=None, text="", exc=None):
        self.answer, self.text, self.exc, self.calls = answer, text, exc, []

    def get_json(self, url, ats_name, **kw):
        self.calls.append((url, kw))
        if self.exc:
            raise self.exc
        return self.answer

    def get(self, url, ats_name, **kw):
        self.calls.append((url, kw))
        if self.exc:
            raise self.exc
        return Resp(self.text)


# --- Google for Jobs (SerpApi) ------------------------------------------------------------------------

ANSWER = {"jobs_results": [
    {"title": "Junior Software Developer", "company_name": "Acme &amp; Co", "location": "Vancouver, BC, Canada",
     "via": "LinkedIn", "description": "Build things in Python. " * 30,
     "job_highlights": [{"title": "Qualifications", "items": ["Python", "SQL"]}],
     "detected_extensions": {"posted_at": "2 days ago"},
     "apply_options": [{"title": "LinkedIn", "link": "https://ca.linkedin.com/jobs/view/1"},
                       {"title": "Greenhouse", "link": "https://job-boards.greenhouse.io/acme/jobs/123"}]},
    {"title": "Data Analyst", "company_name": "Beta", "location": "Anywhere", "description": "SQL and dashboards.",
     "extensions": ["5 hours ago", "Full-time"],
     "apply_options": [{"title": "Indeed", "link": "https://ca.indeed.com/viewjob?jk=1"},
                       {"title": "Beta Careers", "link": "https://careers.beta.ca/jobs/9"}]},
    {"title": "IT Analyst", "company_name": "Gamma", "location": "Burnaby, BC",
     "apply_options": [{"title": "LinkedIn", "link": "https://ca.linkedin.com/jobs/view/2"}]},
    {"title": "", "company_name": "No title"},
]}


def prefer(url):
    return ats.detect_ats(url) in ats.SUPPORTED and ats.detect_ats(url) not in ats.SITES


def test_parse_picks_the_ats_link_then_the_employer_then_any():
    recs = googlejobs.parse(ANSWER, NOW, prefer)
    assert [r["url"] for r in recs] == ["https://job-boards.greenhouse.io/acme/jobs/123", "https://careers.beta.ca/jobs/9",
                                        "https://ca.linkedin.com/jobs/view/2"]
    first = recs[0]
    assert first["company_name"] == "Acme & Co" and first["source"] == "Google Jobs"
    assert first["first_seen"] == NOW - timedelta(days=2)
    assert "Qualifications\n- Python\n- SQL" in first["jd"]
    assert recs[1]["first_seen"] == NOW - timedelta(hours=5)
    assert recs[2]["first_seen"] == NOW                       # no age given: counts as new


def test_terms_rotate_between_runs_and_cover_every_term():
    terms = [f"t{i}" for i in range(10)]
    asked = set()
    for day in range(3):
        for hour in (16, 23):
            got = googlejobs.terms_for_run(terms, NOW.replace(hour=hour) + timedelta(days=day), 4)
            assert len(got) == 4
            asked |= set(got)
    assert asked == set(terms)


def test_skipped_without_a_key(monkeypatch, capsys):
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    client = Client(ANSWER)
    assert googlejobs.fetch(client, ["x"], "Canada", NOW) == [] and not client.calls
    assert "SERPAPI_KEY not set" in capsys.readouterr().out


def test_fetch_asks_google_jobs_for_canada_and_never_leaks_the_key(monkeypatch):
    monkeypatch.setenv("SERPAPI_KEY", "sekret123")
    client = Client(ANSWER)
    recs = googlejobs.fetch(client, ["junior developer", "data analyst"], "British Columbia, Canada", NOW, per_run=2,
                            prefer=prefer)
    assert len(recs) == 3                                     # the same postings for both terms: kept once
    params = client.calls[0][1]["params"]
    assert params["engine"] == "google_jobs" and params["gl"] == "ca" and params["location"] == "British Columbia, Canada"
    resp = SimpleNamespace(status_code=401)
    bad = Client(exc=requests.HTTPError("401 for url: https://serpapi.com/search.json?api_key=sekret123", response=resp))
    with pytest.raises(googlejobs.GoogleJobsError) as err:
        googlejobs.fetch(bad, ["x"], "Canada", NOW)
    assert "sekret123" not in str(err.value) and "401" in str(err.value) and err.value.__cause__ is None


def test_an_empty_answer_is_not_an_error_but_a_real_error_is(monkeypatch):
    monkeypatch.setenv("SERPAPI_KEY", "k")
    empty = Client({"error": "Google hasn't returned any results for this query."})
    assert googlejobs.fetch(empty, ["x"], "Canada", NOW) == []
    with pytest.raises(googlejobs.GoogleJobsError, match="Invalid API key"):
        googlejobs.fetch(Client({"error": "Invalid API key."}), ["x"], "Canada", NOW)


def test_supplied_description_is_used_when_the_page_cannot_be_read(tmp_path):
    supplied.clear()
    job = Job(company="Gamma", title="IT Analyst", location="Burnaby, BC", url="https://ca.linkedin.com/jobs/view/2",
              source="Google Jobs", first_seen=NOW)
    cache = enrich.JDCache(str(tmp_path), 10)
    assert enrich.fetch_job(job, Client(), cache).status == "unsupported"
    supplied.put(job.url, "Support our users. " * 50)
    r = enrich.fetch_job(job, Client(), cache)
    assert r.status == "ok" and r.text.startswith("Support our users.")
    supplied.clear()


# --- Google Alerts ------------------------------------------------------------------------------------

FEED = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:idx="urn:atom-extension:indexing">
 <id>tag:google.com,2005:reader/user/0/state/com.google/alerts/1</id>
 <title>Google Alert - site:jobs.lever.co Vancouver</title>
 <updated>2026-11-11T10:00:00Z</updated>
 <entry>
  <id>tag:google.com,2013:googlealerts/feed:1</id>
  <title type="html">Acme - Junior &lt;b&gt;Software&lt;/b&gt; Developer</title>
  <link href="https://www.google.com/url?rct=j&amp;sa=t&amp;url=https://jobs.lever.co/acme/1a2b-3c4d&amp;ct=ga&amp;cd=X&amp;usg=Y"/>
  <published>2026-11-11T09:00:00Z</published>
  <content type="html">We are hiring in &lt;b&gt;Vancouver&lt;/b&gt;</content>
 </entry>
 <entry>
  <title type="html">Job Application for Data Analyst at Beta Health</title>
  <link href="https://www.google.com/url?rct=j&amp;sa=t&amp;url=https://job-boards.greenhouse.io/betahealth/jobs/77&amp;ct=ga"/>
  <published>2026-11-10T20:00:00Z</published>
 </entry>
 <entry>
  <title type="html">Machine Learning Engineer @ Delta AI</title>
  <link href="https://www.google.com/url?rct=j&amp;sa=t&amp;url=https://jobs.ashbyhq.com/delta/abc&amp;ct=ga"/>
  <published>2026-11-10T08:00:00Z</published>
 </entry>
 <entry>
  <title type="html">Software Engineer - Backend</title>
  <link href="https://www.google.com/url?rct=j&amp;sa=t&amp;url=https://epsilon.wd3.myworkdayjobs.com/External/job/Vancouver/X_1&amp;ct=ga"/>
  <published>2026-11-10T08:00:00Z</published>
 </entry>
</feed>"""


def test_alert_feed_entries_become_postings_on_the_employers_own_page():
    recs = googlealerts.parse(FEED, "Vancouver, BC", NOW)
    got = [(r["company"], r["company_name"], r["title"], r["url"]) for r in recs]
    assert got == [
        ("acme", "Acme", "Junior Software Developer", "https://jobs.lever.co/acme/1a2b-3c4d"),
        ("betahealth", "Beta Health", "Data Analyst", "https://job-boards.greenhouse.io/betahealth/jobs/77"),
        ("delta", "Delta AI", "Machine Learning Engineer", "https://jobs.ashbyhq.com/delta/abc"),
        ("epsilon", "", "Software Engineer - Backend", "https://epsilon.wd3.myworkdayjobs.com/External/job/Vancouver/X_1"),
    ]
    assert all(r["location"] == "Vancouver, BC" and r["source"] == "Google Alerts" for r in recs)
    assert recs[0]["first_seen"] == datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc)
    assert {ats.detect_ats(r["url"]) for r in recs} == {"Lever", "Greenhouse", "Ashby", "Workday"}


def test_feeds_come_from_the_secret_with_an_optional_place(monkeypatch):
    monkeypatch.setenv("GOOGLE_ALERTS_FEEDS", "Vancouver, BC | https://www.google.com/alerts/feeds/1/2\n\n# note\n"
                                              "https://www.google.com/alerts/feeds/1/3\nnot a url")
    assert googlealerts.feeds() == [("Vancouver, BC", "https://www.google.com/alerts/feeds/1/2"),
                                    ("Canada", "https://www.google.com/alerts/feeds/1/3")]


def test_alerts_skipped_without_feeds_and_a_bad_feed_never_prints_its_address(monkeypatch, capsys):
    monkeypatch.delenv("GOOGLE_ALERTS_FEEDS", raising=False)
    assert googlealerts.fetch(Client(text=FEED), NOW) == []
    assert "GOOGLE_ALERTS_FEEDS not set" in capsys.readouterr().out
    monkeypatch.setenv("GOOGLE_ALERTS_FEEDS", "https://www.google.com/alerts/feeds/private/1\n"
                                              "https://www.google.com/alerts/feeds/private/2")

    class Mixed(Client):
        def get(self, url, ats_name, **kw):
            if url.endswith("/1"):
                raise requests.HTTPError("404 for url: " + url)
            return Resp(FEED)

    assert len(googlealerts.fetch(Mixed(), NOW)) == 4
    out = capsys.readouterr().out
    assert "feed 1 not read" in out and "private" not in out
    with pytest.raises(googlealerts.GoogleAlertsError):
        googlealerts.fetch(Client(exc=requests.ConnectionError("boom")), NOW)


def test_a_feed_with_entities_is_refused():
    with pytest.raises(googlealerts.GoogleAlertsError):
        googlealerts.parse('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><feed/>', "Canada", NOW)
