"""Company display names from the ATS (SPEC §11 #68)."""
from datetime import date, timedelta

import pytest

from scanner import ats_names as N
from scanner.company_names import load_overrides, looks_like_slug
from scanner.models import Job

TODAY = date(2026, 10, 26)


class FakeResp:
    def __init__(self, text):
        self.text = text


class FakeClient:
    """Answers like the real endpoints; records every call."""

    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def get_json(self, url, ats):
        self.calls.append(url)
        if self.fail:
            raise OSError("network down")
        if "boards-api.greenhouse.io/v1/boards/bitgo" in url:
            return {"name": "BitGo", "content": "<p>...</p>"}
        if url == "https://cityofhamilton.bamboohr.com/careers/company-info":
            return {"meta": [], "result": {"name": "City of Hamilton", "id": "603347"}}
        if url == "https://bakertillywm.bamboohr.com/careers/company-info":
            return {"meta": [], "result": {"name": "Baker Tilly WM"}}
        if url == "https://firstlightai.bamboohr.com/careers/company-info":
            return {"meta": [], "result": {"name": "Quickplay"}}
        raise AssertionError(url)

    def post_json(self, url, ats, json=None):
        self.calls.append(url)
        assert json["variables"]["organizationHostedJobsPageName"] == "sandboxaq"
        return {"data": {"organization": {"name": "SandboxAQ"}}}

    def get(self, url, ats, **kw):
        self.calls.append(url)
        pages = {"https://tjx.wd1.myworkdayjobs.com/tjx_external": '<meta name="title" property="og:title" content="TJX Careers">',
                 "https://ssctech.wd1.myworkdayjobs.com/ssctechnologies": '<meta property="og:title" content="SS&amp;amp;C Careers">',
                 "https://bdo.wd3.myworkdayjobs.com/bdo": '<meta property="og:title" content="Canada">',
                 "https://gianttiger.wd3.myworkdayjobs.com/gianttiger": '<meta name="title" property="og:title">'}
        return FakeResp(pages[url])


@pytest.mark.parametrize("url,name", [
    ("https://job-boards.greenhouse.io/bitgo/jobs/8886040002", "BitGo"),
    ("https://cityofhamilton.bamboohr.com/careers/2390", "City of Hamilton"),
    ("https://bakertillywm.bamboohr.com/careers/358", "Baker Tilly WM"),
    ("https://firstlightai.bamboohr.com/careers/526", "Quickplay"),
    ("https://jobs.ashbyhq.com/sandboxaq/job-1", "SandboxAQ"),
    ("https://tjx.wd1.myworkdayjobs.com/tjx_external/job/CAN-Winnipeg-MB/Loss-Prevention_REQ167603", "TJX"),
    ("https://ssctech.wd1.myworkdayjobs.com/en-US/ssctechnologies/job/Toronto-Canada/Analyst_R45965", "SS&C"),
    ("https://bdo.wd3.myworkdayjobs.com/bdo/job/Toronto---Bay-St/Consultant_JR7209", ""),          # "Canada" is not BDO
    ("https://gianttiger.wd3.myworkdayjobs.com/gianttiger/job/Johnstown/Operator_JR133523", ""),   # empty og:title
    ("https://jobs.lever.co/lvs1/job-2", ""),                       # Lever: no source
])
def test_name_from_each_ats(url, name, tmp_path):
    store = N.NameStore(tmp_path / "names.json")
    assert N.ats_name(url, store, FakeClient(), TODAY) == name


def test_names_are_stored_and_rechecked_after_a_month(tmp_path):
    url = "https://cityofhamilton.bamboohr.com/careers/2390"
    store, client = N.NameStore(tmp_path / "names.json"), FakeClient()
    N.ats_name(url, store, client, TODAY)
    store.save()
    again = N.NameStore(tmp_path / "names.json")
    assert N.ats_name(url, again, client, TODAY + timedelta(days=29)) == "City of Hamilton" and len(client.calls) == 1
    N.ats_name(url, again, client, TODAY + timedelta(days=31))
    assert len(client.calls) == 2


def test_a_failed_lookup_is_not_stored(tmp_path):
    store = N.NameStore(tmp_path / "names.json")
    assert N.ats_name("https://cityofhamilton.bamboohr.com/careers/1", store, FakeClient(fail=True), TODAY) == ""
    assert store.data == {}


@pytest.mark.parametrize("raw,clean", [("TJX Careers", "TJX"), ("Careers at Acme", "Acme"), ("Interac Corp.", "Interac"),
                                       ("Block 64 Corporation", "Block 64"), ("Canadian Fiber Optics Corp", "Canadian Fiber Optics"),
                                       ("SS&amp;amp;C Careers", "SS&C"), ("Jobs", ""), ("", "")])
def test_clean(raw, clean):
    assert N.clean(raw) == clean


def test_fill_names_only_touches_jobs_without_a_readable_name(tmp_path):
    def job(company, url, name=""):
        return Job(company=company, company_name=name, title="Analyst", location="Toronto, ON", url=url,
                   source="BambooHR", first_seen=None)
    overrides = load_overrides()
    jobs = [job("cityofhamilton", "https://cityofhamilton.bamboohr.com/careers/1"),
            job("bmo", "https://bmo.wd3.myworkdayjobs.com/external/job/x"),                   # company_names.yaml
            job("acme", "https://acme.bamboohr.com/careers/1", name="Acme Inc")]              # name from Simplify
    store, client = N.NameStore(tmp_path / "n.json"), FakeClient()
    assert N.fill_names(jobs, overrides, store, client, TODAY) == 1
    assert jobs[0].company_name == "City of Hamilton" and not looks_like_slug(jobs[0], overrides)
    assert client.calls == ["https://cityofhamilton.bamboohr.com/careers/company-info"]


@pytest.mark.parametrize("slug,name", [
    ("evr", "Elk Valley Resources"), ("lvs1", "Long View"), ("wgl", "AltaGas"), ("web", "Newfold Digital"),
    ("markelcorp", "Markel"), ("gianttiger", "Giant Tiger"), ("championx", "ChampionX"), ("snapchat", "Snap"),
    ("smiledigitalhealth", "Smile Digital Health"), ("lightspeedhq", "Lightspeed"), ("sandboxaq", "SandboxAQ"),
])
def test_names_the_ats_cannot_give_are_in_company_names_yaml(slug, name):
    assert load_overrides()[slug] == name


def test_source_key_and_slug():
    assert N.source_key("https://bdo.wd3.myworkdayjobs.com/en-US/BDO/job/x") == ("Workday", "bdo.wd3.myworkdayjobs.com/BDO")
    assert N.slug_from_url("https://bdo.wd3.myworkdayjobs.com/en-US/BDO/job/x") == "bdo"
    assert N.slug_from_url("https://jobs.ashbyhq.com/method-crm/30be6b56") == "method-crm"
    assert N.slug_from_url("https://www.konrad.com/careers/job/8035997003?gh_jid=8035997003") == ""
