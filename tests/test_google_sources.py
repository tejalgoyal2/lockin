from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import requests

from scanner import enrich
from scanner.models import Job
from scanner.sources import ats, googlejobs, supplied

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
