from datetime import datetime, timezone

import pytest

from scanner.models import Job
from scanner.sources import ats

T = datetime(2026, 10, 3, tzinfo=timezone.utc)


def job(url, company="acme", source="Workday"):
    return Job(company=company, title="Software Engineer", location="Toronto, ON", url=url,
               source=source, first_seen=T, sources={source})


class FakeClient:
    """Stands in for PoliteClient: URL → canned JSON, with a call log."""

    def __init__(self, responses):
        self.responses, self.calls = responses, []

    def get_json(self, url, ats_name, **kw):
        self.calls.append(url)
        return self.responses[url]


@pytest.mark.parametrize("url,api_url", [
    ("https://boards.greenhouse.io/robinhood/jobs/8246088?t=gh_src=&gh_jid=8246088",
     "https://boards-api.greenhouse.io/v1/boards/robinhood/jobs/8246088"),
    ("https://job-boards.greenhouse.io/heygen/jobs/5255664007",
     "https://boards-api.greenhouse.io/v1/boards/heygen/jobs/5255664007"),
    ("https://www.pinterestcareers.com/jobs/?gh_jid=8138065",       # custom domain: company = board slug
     "https://boards-api.greenhouse.io/v1/boards/acme/jobs/8138065"),
    ("https://jobs.lever.co/telesat/155c637a-5e9e-4e2d-899c-b44f2b5c2eec",
     "https://api.lever.co/v0/postings/telesat/155c637a-5e9e-4e2d-899c-b44f2b5c2eec"),
    ("https://jobs.lever.co/telesat/155c637a-5e9e-4e2d-899c-b44f2b5c2eec/apply",
     "https://api.lever.co/v0/postings/telesat/155c637a-5e9e-4e2d-899c-b44f2b5c2eec"),
    ("https://jobs.eu.lever.co/acme/abc-123",
     "https://api.eu.lever.co/v0/postings/acme/abc-123"),
    ("https://jobs.ashbyhq.com/vasco/fe861ca6-7a8f-4b3f-a53b-5c0a1273abc0",
     "https://api.ashbyhq.com/posting-api/job-board/vasco"),
    ("https://jobs.ashbyhq.com/Superhuman%20Platform%20Inc/134c282",
     "https://api.ashbyhq.com/posting-api/job-board/Superhuman%20Platform%20Inc"),
    ("https://autodesk.wd1.myworkdayjobs.com/Ext/job/Toronto-ON-CAN/MCP-AI-Developer_26WD99785-1",
     "https://autodesk.wd1.myworkdayjobs.com/wday/cxs/autodesk/Ext/job/Toronto-ON-CAN/MCP-AI-Developer_26WD99785-1"),
    ("https://acme.wd5.myworkdayjobs.com/en-US/careers/job/Montreal/Dev_R1/apply",
     "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/careers/job/Montreal/Dev_R1"),
    ("https://budge.bamboohr.com/careers/73",
     "https://budge.bamboohr.com/careers/73/detail"),
])
def test_locate_builds_endpoint(url, api_url):
    ref = ats.locate(job(url))
    assert ref is not None and ref.api_url == api_url


@pytest.mark.parametrize("url", [
    "https://recruiting.paylocity.com/recruiting/Jobs/Details/4557390",
    "https://apply.workable.com/financeit/j/D3CF97088A/apply",
    "https://careers-48forty.icims.com/jobs/5728/warehouse-associate/job",
    "https://example.com/careers/1",
    "",
])
def test_locate_unsupported(url):
    assert ats.locate(job(url)) is None


def test_ashby_job_id_is_kept():
    assert ats.locate(job("https://jobs.ashbyhq.com/vasco/abc")).job_id == "abc"


def test_greenhouse_content_is_unescaped():
    ref = ats.locate(job("https://boards.greenhouse.io/x/jobs/1"))
    client = FakeClient({ref.api_url: {"content": "&lt;p&gt;Build &amp;amp; ship&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;/ul&gt;"}})
    assert ats.fetch_jd(ref, client) == "Build & ship\n- Python"


def test_lever_combines_description_lists_and_additional():
    ref = ats.locate(job("https://jobs.lever.co/acme/1"))
    client = FakeClient({ref.api_url: {
        "descriptionPlain": "About the job",
        "lists": [{"text": "Requirements:", "content": "<li>Python</li><li>SQL</li>"}],
        "additionalPlain": "We are an equal opportunity employer"}})
    assert ats.fetch_jd(ref, client) == (
        "About the job\n\nRequirements:\n- Python\n- SQL\n\nWe are an equal opportunity employer")


def test_workday_and_bamboohr_shapes():
    wd = ats.locate(job("https://a.wd1.myworkdayjobs.com/S/job/T/X_1"))
    assert ats.fetch_jd(wd, FakeClient({wd.api_url: {"jobPostingInfo": {"jobDescription": "<p>Hi</p><p>There</p>"}}})) == "Hi\n\nThere"
    bb = ats.locate(job("https://x.bamboohr.com/careers/9"))
    assert ats.fetch_jd(bb, FakeClient({bb.api_url: {"result": {"jobOpening": {"description": "<p>Join us</p>"}}}})) == "Join us"


def test_ashby_board_fetched_once_per_company_and_job_picked_by_id():
    board = {"jobs": [{"id": "a", "descriptionPlain": "Job A"}, {"id": "b", "descriptionPlain": "", "descriptionHtml": "<p>Job B</p>"}]}
    ra, rb = (ats.locate(job(f"https://jobs.ashbyhq.com/vasco/{i}")) for i in "ab")
    client, cache = FakeClient({ra.api_url: board}), {}
    ats._ashby_boards.clear()
    assert ats._ashby_text(ra, client, cache) == "Job A"
    assert ats._ashby_text(rb, client, cache) == "Job B"
    assert len(client.calls) == 1
    with pytest.raises(ats.NotFound):
        ats._ashby_text(ats.locate(job("https://jobs.ashbyhq.com/vasco/zzz")), client, cache)
