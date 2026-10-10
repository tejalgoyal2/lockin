from datetime import datetime, timezone

import pytest
import requests

from scanner import enrich as E
from scanner import feed_clean as FC
from scanner import feed_refresh as FR
from scanner import notion_feed
from scanner.cluster import ClusterResolver
from scanner.config import load_config
from scanner.filters import Filters
from scanner.models import Job
from scanner.score import Scorer
from scanner.sources import ats

T = datetime(2026, 10, 3, tzinfo=timezone.utc)
CFG = load_config()
FILTERS = Filters.from_config(CFG)
SCORER = Scorer.load()
NOCACHE = E.JDCache(None, 0)
GOOD_JD = "<p>Requirements: Python, SQL, Docker and Git. 1 year of experience.</p>"

OLD = "https://bdo.wd3.myworkdayjobs.com/bdo/job/Toronto---Bay-St/DevOps-Engineer---New-Grad--January-2027-_JR7192-1"
OLD_API = "https://bdo.wd3.myworkdayjobs.com/wday/cxs/bdo/bdo/job/Toronto---Bay-St/DevOps-Engineer---New-Grad--January-2027-_JR7192-1"
SEARCH = "https://bdo.wd3.myworkdayjobs.com/wday/cxs/bdo/bdo/jobs"
LIVE_PATH = "/job/Toronto---Bay-St/DevOps-Engineer---New-Grad--January-2027-_JR7194"
LIVE_API = "https://bdo.wd3.myworkdayjobs.com/wday/cxs/bdo/bdo" + LIVE_PATH
TITLE = "DevOps Engineer - New Grad (January 2027)"


def gone(code=403):
    resp = requests.Response()
    resp.status_code = code
    return requests.HTTPError(response=resp)


def detail(location="Toronto, Ontario"):
    return {"jobPostingInfo": {"jobDescription": GOOD_JD, "location": location}}


class WD:
    """A Workday tenant: URL -> JSON (or an exception), and the postings its search returns."""

    def __init__(self, pages, postings=()):
        self.pages, self.postings, self.searches = pages, list(postings), []

    def get_json(self, url, ats_name, **kw):
        val = self.pages[url]
        if isinstance(val, Exception):
            raise val
        return val

    def get_json_browser(self, url, ats_name, home):
        return self.get_json(url, ats_name)

    def post_json(self, url, ats_name, json=None):
        self.searches.append((url, json))
        return {"jobPostings": self.postings}


def posting(title=TITLE, path=LIVE_PATH, where="Toronto, ON, CAN"):
    return {"title": title, "externalPath": path, "locationsText": where}


def wd_job(**kw):
    return Job(company="bdo", title=TITLE, location="Toronto - Bay St", url=OLD, source="Workday", first_seen=T,
               sources={"Workday"}, **kw)


# --- the search -----------------------------------------------------------------------------

def test_search_text_is_the_title_words_without_punctuation():
    assert ats._words(TITLE) == "DevOps Engineer New Grad January 2027"      # "…- New Grad (January 2027)" finds nothing


def test_exact_title_match_with_a_canadian_location_replaces_the_dead_id():
    client = WD({OLD_API: gone(), LIVE_API: detail()}, [posting()])
    r = E.fetch_job(wd_job(), client, NOCACHE, FILTERS)
    assert r.status == "ok" and r.relocation == "matched"
    assert r.live_url == "https://bdo.wd3.myworkdayjobs.com/bdo" + LIVE_PATH
    assert "Python" in r.text and r.locations == ["Toronto, Ontario"]
    (url, body), = client.searches
    assert url == SEARCH and body["searchText"] == "DevOps Engineer New Grad January 2027"


@pytest.mark.parametrize("code", [403, 404])
def test_403_and_404_both_trigger_the_search(code):
    client = WD({OLD_API: gone(code), LIVE_API: detail()}, [posting()])
    assert E.fetch_job(wd_job(), client, NOCACHE, FILTERS).relocation == "matched"


def test_not_found_exception_triggers_the_search_too():
    client = WD({OLD_API: ats.NotFound("x"), LIVE_API: detail()}, [posting()])
    assert E.fetch_job(wd_job(), client, NOCACHE, FILTERS).relocation == "matched"


@pytest.mark.parametrize("postings,why", [
    ([posting(title="DevOps Engineer - New Grad Card Expansion")], "title is not exact"),
    ([posting(title="DevOps Engineer - New Grad (February 2027)")], "a different word"),
    ([], "nothing found"),
])
def test_no_exact_title_match_leaves_the_job_unavailable(postings, why):
    client = WD({OLD_API: gone(), LIVE_API: detail()}, postings)
    r = E.fetch_job(wd_job(), client, NOCACHE, FILTERS)
    assert r.status == "error" and r.detail == "http 403" and r.relocation == "no_match" and not r.live_url, why


def test_a_match_outside_canada_is_not_taken():
    client = WD({OLD_API: gone(), LIVE_API: detail("Dallas, TX")}, [posting(where="Dallas, TX, USA")])
    r = E.fetch_job(wd_job(), client, NOCACHE, FILTERS)
    assert r.relocation == "no_match" and not r.live_url


def test_title_match_ignores_case_and_punctuation():
    client = WD({OLD_API: gone(), LIVE_API: detail()}, [posting(title="devops engineer – new grad (january 2027)")])
    assert E.fetch_job(wd_job(), client, NOCACHE, FILTERS).relocation == "matched"


def test_no_search_for_a_job_that_is_not_canadian_or_without_filters():
    us = wd_job()
    us.location = "Dallas, TX"
    client = WD({OLD_API: gone()}, [posting()])
    assert E.fetch_job(us, client, NOCACHE, FILTERS).relocation == "" and client.searches == []
    assert E.fetch_job(wd_job(), client, NOCACHE).relocation == "" and client.searches == []


def test_other_errors_do_not_trigger_the_search():
    client = WD({OLD_API: gone(500)}, [posting()])
    assert E.fetch_job(wd_job(), client, NOCACHE, FILTERS).relocation == "" and client.searches == []


def test_fetch_all_swaps_the_url_and_the_cache_remembers_it(tmp_path):
    cache = E.JDCache(tmp_path, 1)
    j = wd_job()
    E.fetch_all([j], WD({OLD_API: gone(), LIVE_API: detail()}, [posting()]), cache, 1, FILTERS)
    assert j.url.endswith("JR7194") and j.jd_status == "ok" and j.relocation == "matched"
    again = wd_job()
    E.fetch_all([again], WD({}), cache, 1, FILTERS)                       # no network: served from the cache
    assert again.url.endswith("JR7194") and again.jd_status == "ok"


# --- Feed rows that were written without a JD -------------------------------------------------

def item(title=TITLE, company="BDO", link=OLD, signals=("new grad", "jd unavailable"), source="Workday", page="p1",
         interested=False, apply=False):
    return FC.FeedItem(page, title, company, link, source, "", list(signals), interested, apply)


class Notion:
    def __init__(self):
        self.patched = []

    def request(self, method, path, json=None, **kw):
        assert method == "PATCH", (method, path)
        self.patched.append((path, json))
        return {}


def feed():
    names = CFG["notion"]["properties"]
    return notion_feed.Feed("ds", names, {k: f"id_{k}" for k in names}, {"Workday"})


def refresh(items, client, dry_run=False):
    notion = Notion()
    rep = FR.refresh(notion, feed(), items, CFG, FILTERS, SCORER, client, dry_run=dry_run)
    return rep, notion


def test_stale_workday_row_gets_link_jd_fit_and_signals_in_place():
    it = item()
    rep, notion = refresh([it], WD({OLD_API: gone(), LIVE_API: detail()}, [posting()]))
    assert [(i.title, link) for i, link in rep.updated] == [(TITLE, "https://bdo.wd3.myworkdayjobs.com/bdo" + LIVE_PATH)]
    (path, body), = notion.patched
    assert path == "/v1/pages/p1"
    props = body["properties"]
    assert set(props) == {"Link", "JD", "Fit %", "Signals"}                  # nothing else, never the checkboxes
    assert props["Link"]["url"].endswith("JR7194")
    assert props["Fit %"]["number"] == 0.667                                # 4 / (4 + 2)
    assert "jd unavailable" not in [s["name"] for s in props["Signals"]["multi_select"]]
    assert "Python" in props["JD"]["rich_text"][0]["text"]["content"]
    assert it.link.endswith("JR7194") and "jd unavailable" not in it.signals     # the in-memory row follows
    assert rep.locations[FC.canonical_url(it.link)] == ("Toronto, Ontario", "ats")


def test_dry_run_changes_nothing_but_reports():
    rep, notion = refresh([item()], WD({OLD_API: gone(), LIVE_API: detail()}, [posting()]), dry_run=True)
    assert len(rep.updated) == 1 and notion.patched == []


def test_row_without_a_match_is_left_alone_and_listed():
    it = item(title="Associate, Software Engineer, New Grad", company="Capital One")
    rep, notion = refresh([it], WD({OLD_API: gone()}, [posting(title="Associate, Software Engineer, New Grad Card Expansion")]))
    assert rep.no_match == [it] and notion.patched == [] and it.link == OLD and "jd unavailable" in it.signals
    assert "Still `jd unavailable`" in FR.render(rep, dry_run=False) and "Capital One" in FR.render(rep, dry_run=False)


def test_the_old_id_working_again_still_fills_the_row():
    rep, notion = refresh([item()], WD({OLD_API: detail()}))
    assert len(rep.updated) == 1 and notion.patched[0][1]["properties"]["Link"]["url"] == OLD


def test_live_link_that_is_already_a_row_is_not_duplicated():
    other = item(title="Other", link="https://bdo.wd3.myworkdayjobs.com/bdo" + LIVE_PATH, signals=(), page="p2")
    rep, notion = refresh([item(), other], WD({OLD_API: gone(), LIVE_API: detail()}, [posting()]))
    assert len(rep.duplicate) == 1 and notion.patched == []


def test_only_workday_rows_with_the_unavailable_signal_are_looked_at():
    rows = [item(signals=("new grad",)), item(source="Greenhouse", page="p2"), item(page="p3")]
    assert [i.page_id for i in FR.stale_rows(rows)] == ["p3"]


def test_checkbox_state_does_not_matter_to_a_refresh_and_is_never_written():
    rep, notion = refresh([item(interested=True)], WD({OLD_API: gone(), LIVE_API: detail()}, [posting()]))
    assert len(rep.updated) == 1 and "Interested" not in notion.patched[0][1]["properties"]


# --- 2K: a Simplify row follows its ATS location -------------------------------------------------

SIMPLIFY_LOCATIONS = "Vancouver, BC, Canada ; Dublin, Ireland ; United States"
GH_URL = "https://job-boards.greenhouse.io/2kearlycareers/jobs/7992140003"


class GH:
    def __init__(self, location):
        self.location = location

    def get_json(self, url, ats_name, **kw):
        return {"content": GOOD_JD.replace("<p>", "&lt;p&gt;").replace("</p>", "&lt;/p&gt;"),
                "location": {"name": self.location}, "offices": [{"name": self.location}]}


def two_k(sources=("Simplify",), loc=SIMPLIFY_LOCATIONS):
    return Job(company="2K", company_name="2K", title="Engineering Graduate Program", location=loc, url=GH_URL,
               source="Simplify", first_seen=T, sources=set(sources), new_grad=True)


def run_2k(job, ats_location):
    return E.enrich([job], [], CFG, FILTERS, SCORER, GH(ats_location), cache=NOCACHE)


def test_simplify_row_passes_phase_1_on_its_programme_wide_location_list():
    assert FILTERS.location_status(SIMPLIFY_LOCATIONS)[0] == "ok"          # Vancouver is in the list


def test_simplify_row_whose_ats_posting_is_in_dublin_is_dropped():
    res = run_2k(two_k(), "Dublin, Ireland")
    assert res.kept == [] and res.drops == {E.R_ATS_NOT_CANADA: 1}


@pytest.mark.parametrize("ats_location", ["Vancouver, BC, Canada", "Remote", "Hybrid", "Remote - Dublin"])
def test_simplify_row_is_kept_when_the_ats_is_canadian_or_vague(ats_location):
    assert len(run_2k(two_k(), ats_location).kept) == 1


def test_ats_location_in_quebec_only_drops_a_simplify_row():
    assert run_2k(two_k(), "Montréal, QC").drops == {E.R_QUEBEC_ONLY: 1}


def test_rows_not_listed_by_simplify_are_left_to_the_phase_1_location():
    j = two_k(sources=("Greenhouse",), loc="Vancouver, BC")
    assert len(run_2k(j, "Dublin, Ireland").kept) == 1


@pytest.mark.parametrize("payload,expected", [
    ({"location": {"name": "Dublin, Ireland"}, "offices": [{"name": "Dublin, Ireland"}, {"name": "Toronto"}]},
     ["Dublin, Ireland", "Toronto"]),
])
def test_greenhouse_detail_lists_its_locations(payload, expected):
    class C:
        def get_json(self, url, ats_name, **kw):
            return {"content": "", **payload}
    j = Job(company="2kearlycareers", title="x", location="", url=GH_URL, source="Greenhouse", first_seen=T)
    assert ats.fetch_full(ats.locate(j), C()).locations == expected


def test_lever_and_ashby_and_bamboo_detail_locations():
    lever = Job(company="a", title="x", location="", url="https://jobs.lever.co/acme/abc", source="Lever", first_seen=T)

    class L:
        def get_json(self, url, ats_name, **kw):
            return {"descriptionPlain": "d", "categories": {"location": "Toronto", "allLocations": ["Toronto", "Dublin"]}}
    assert ats.fetch_full(ats.locate(lever), L()).locations == ["Toronto", "Dublin"]

    ashby = Job(company="a", title="x", location="", url="https://jobs.ashbyhq.com/acme/job-1", source="Ashby", first_seen=T)

    class A:
        def get_json(self, url, ats_name, **kw):
            return {"jobs": [{"id": "job-1", "descriptionPlain": "d", "location": "Toronto",
                              "secondaryLocations": [{"location": "Dublin"}]}]}
    assert ats.fetch_full(ats.locate(ashby), A()).locations == ["Toronto", "Dublin"]

    bamboo = Job(company="a", title="x", location="", url="https://acme.bamboohr.com/careers/7", source="BambooHR",
                 first_seen=T)

    class B:
        def get_json(self, url, ats_name, **kw):
            return {"result": {"jobOpening": {"description": "d", "location": {"city": "Waterloo", "state": "Ontario"}}}}
    assert ats.fetch_full(ats.locate(bamboo), B()).locations == ["Waterloo, Ontario"]


def test_feed_row_for_2k_is_trashed_because_its_ats_location_is_dublin():
    it = FC.FeedItem("p", "Engineering Graduate Program", "2K", GH_URL, "Simplify", "Python, SQL, Docker, Git.", [],
                     False, False)
    v = FC.evaluate(it, "Dublin, Ireland", CFG, FILTERS, SCORER, ClusterResolver(CFG), origin="ats")
    assert v.rule == E.R_ATS_NOT_CANADA
    rep = FC.CleanReport([v])
    assert rep.trashable() == [v] and rep.location_failures() == []


def test_a_simplify_only_location_that_is_not_canadian_is_reported_not_trashed():
    it = FC.FeedItem("p", "Engineering Graduate Program", "2K", GH_URL, "Simplify", "Python, SQL, Docker, Git.", [],
                     False, False)
    v = FC.evaluate(it, "Dublin, Ireland", CFG, FILTERS, SCORER, ClusterResolver(CFG), origin="simplify")
    assert v.rule is None and v.location_status == "not_canada"
