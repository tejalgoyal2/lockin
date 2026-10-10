import json
from datetime import datetime, timezone

import pytest
import requests

from scanner import enrich as E
from scanner import report
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

# Four matched skills, no gaps: Fit 66.7 % (4 / (4 + 2)), above the 60 % the 2-year rule asks for.
STRONG = "Requirements:\nPython, SQL, Docker and Git. {exp}"
# One skill plus gaps: Fit well under 60 %.
WEAK_FIT = "Python. Java, Kafka, Golang and Redis. {exp}"


def job(i=1, title="Software Engineer", loc="Toronto, ON", **kw):
    return Job(company="acme", title=title, location=loc, url=f"https://boards.greenhouse.io/acme/jobs/{i}",
               source="Greenhouse", first_seen=T, sources={"Greenhouse"}, **kw)


class FakeClient:
    def __init__(self, by_id):
        self.by_id = by_id

    def get_json(self, url, ats_name, **kw):
        return {"content": f"<p>{self.by_id[url.rsplit('/', 1)[-1]]}</p>"}


def run(cands, jds, held=()):
    return E.enrich(list(cands), list(held), CFG, FILTERS, SCORER, FakeClient(jds), cache=NOCACHE)


# --- experience tiers -----------------------------------------------------------------

def test_config_values():
    assert CFG["jd"]["min_years_drop"] == 3 and CFG["jd"]["two_year_min_fit"] == 0.6
    assert CFG["jd"]["two_year_signal"] == "2+ yrs"


@pytest.mark.parametrize("exp", ["3+ years of experience.", "Three (3) or more years of experience.",
                                 "a minimum of five years of experience.", "4-6 years of experience."])
def test_three_or_more_years_is_dropped(exp):
    res = run([job()], {"1": STRONG.format(exp=exp)})
    assert res.kept == [] and res.drops == {E.R_EXPERIENCE: 1}


def test_two_years_with_strong_fit_is_kept_with_signal():
    (j,) = run([job()], {"1": STRONG.format(exp="2+ years of experience.")}).kept
    assert "2+ yrs" in j.signals and j.fit_pct == 66.7


def test_two_to_four_years_uses_the_lower_bound_of_two():
    (j,) = run([job()], {"1": STRONG.format(exp="2-4 years of experience.")}).kept
    assert "2+ yrs" in j.signals


def test_two_years_dropped_when_internships_do_not_count():
    res = run([job()], {"1": STRONG.format(exp="2 years of experience (does not include internships or co-ops).")})
    assert res.kept == [] and res.drops == {E.R_TWO_YEAR_INTERN: 1}


def test_two_years_dropped_without_a_fit_percent():
    # three terms only -> Fit n/a ("low signal")
    res = run([job()], {"1": "Python, SQL and Docker. 2 years of experience."})
    assert res.kept == [] and res.drops == {E.R_TWO_YEAR_NO_FIT: 1}


def test_two_years_dropped_below_the_fit_threshold():
    res = run([job()], {"1": WEAK_FIT.format(exp="2 years of experience.")})
    assert res.kept == [] and res.drops == {E.R_TWO_YEAR_LOW_FIT: 1}


def test_two_year_fit_threshold_comes_from_config(monkeypatch):
    cfg = {**CFG, "jd": {**CFG["jd"], "two_year_min_fit": 0.9}}
    res = E.enrich([job()], [], cfg, FILTERS, SCORER, FakeClient({"1": STRONG.format(exp="2 years of experience.")}),
                   cache=NOCACHE)
    assert res.drops == {E.R_TWO_YEAR_LOW_FIT: 1}            # 66.7 % < 90 %


def test_masters_route_uses_its_own_years():
    jd = STRONG.format(exp="Bachelor's with 5 years of experience, or Master's and 2 years of experience.")
    (j,) = run([job()], {"1": jd}).kept
    assert "2+ yrs" in j.signals
    jd3 = STRONG.format(exp="Bachelor's with 5 years of experience, or Master's and 3 years of experience.")
    assert run([job()], {"1": jd3}).drops == {E.R_EXPERIENCE: 1}


def test_one_year_has_no_two_year_signal():
    (j,) = run([job()], {"1": STRONG.format(exp="1+ year of experience.")}).kept
    assert "2+ yrs" not in j.signals


# --- French JD ------------------------------------------------------------------------

def test_jd_requiring_french_is_dropped_wherever_the_job_is():
    for loc in ("Toronto, ON", "Vancouver, BC", "Remote, Canada"):
        res = run([job(loc=loc)], {"1": STRONG.format(exp="Bilingual (English/French) is required.")})
        assert res.drops == {E.R_FRENCH: 1}, loc


def test_passing_mention_of_french_is_kept():
    assert len(run([job()], {"1": STRONG.format(exp="French is an asset.")}).kept) == 1


# --- Workday "N Locations" ------------------------------------------------------------

def pending(**kw):
    return job(loc="2 Locations", location_pending=True, **kw)


def with_locations(j, locations, status="ok"):
    j.jd_status, j.jd, j.jd_locations = status, STRONG.format(exp=""), locations
    return j


def test_pending_location_resolved_from_additional_locations():
    j = with_locations(pending(), ["Dallas, TX", "Toronto, Ontario, Canada"])
    assert E.apply_rules(j, CFG, FILTERS, SCORER) is None
    assert j.location == "Toronto, Ontario, Canada" and not j.location_pending and not j.location_unresolved
    assert j.key                                                # recomputed from the real location


def test_pending_location_all_quebec_is_dropped():
    j = with_locations(pending(), ["Montréal, Quebec", "Laval, Quebec"])
    assert E.apply_rules(j, CFG, FILTERS, SCORER) == E.R_QUEBEC_ONLY and j.location_dropped


def test_pending_location_with_a_non_quebec_canadian_city_is_kept():
    j = with_locations(pending(), ["Montréal, Quebec", "Toronto, Ontario"])
    assert E.apply_rules(j, CFG, FILTERS, SCORER) is None and j.location == "Toronto, Ontario"


def test_pending_location_outside_canada_is_dropped():
    j = with_locations(pending(), ["Dallas, TX", "Chicago, IL"])
    assert E.apply_rules(j, CFG, FILTERS, SCORER) == E.R_NOT_CANADA and j.location_dropped


@pytest.mark.parametrize("status,locations", [("error", []), ("ok", []), ("not_found", [])])
def test_unresolvable_location_keeps_the_job(status, locations):
    j = with_locations(pending(), locations, status)
    assert E.apply_rules(j, CFG, FILTERS, SCORER) is None
    assert j.location_unresolved and j.location == "2 Locations"


def test_location_dropped_jobs_leave_the_gaps_report_alone():
    from scanner import gaps_report
    from scanner.state import GapsStore
    import tempfile, pathlib
    j = with_locations(pending(), ["Dallas, TX"])
    j.key = "k"
    E.apply_rules(j, CFG, FILTERS, SCORER)
    store = GapsStore(pathlib.Path(tempfile.mkdtemp()) / "gaps.json")
    assert gaps_report.record_gaps(store, [j], SCORER, T.date(), {}) == 0


# --- weak titles still need four skill / gap terms (context terms do not count) ----------

def test_weak_title_needs_four_core_terms_not_context_terms():
    jd = "Python and SQL. Backend, frontend, DevOps and automated testing, with a database."
    res = run([job(title="Technical Specialist", weak_title=True)], {"1": jd})
    assert res.drops == {E.R_WEAK_FEW_TERMS: 1}


# --- Workday detail: locations and the browser retry ------------------------------------

WD_URL = "https://acme.wd1.myworkdayjobs.com/en-US/ext/job/Dallas-TX/Software-Engineer_R1"
WD_API = "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/ext/job/Dallas-TX/Software-Engineer_R1"


def wd_job():
    return Job(company="acme", title="Software Engineer", location="2 Locations", url=WD_URL, source="Workday",
               first_seen=T, sources={"Workday"}, location_pending=True)


def wd_payload(**info):
    return {"jobPostingInfo": {"jobDescription": "<p>Python, SQL, Docker and Git.</p>", **info}}


class WDClient:
    def __init__(self, plain, browser=None):
        self.plain, self.browser, self.calls = plain, browser, []

    def get_json(self, url, ats_name, **kw):
        self.calls.append(("plain", url))
        if isinstance(self.plain, Exception):
            raise self.plain
        return self.plain

    def get_json_browser(self, url, ats_name, home):
        self.calls.append(("browser", url, home))
        if isinstance(self.browser, Exception):
            raise self.browser
        return self.browser


def forbidden():
    resp = requests.Response()
    resp.status_code = 403
    return requests.HTTPError(response=resp)


def test_workday_detail_lists_every_location():
    client = WDClient(wd_payload(location="Dallas, TX", additionalLocations=["Toronto, Ontario", "Dallas, TX"]))
    got = ats.fetch_full(ats.locate(wd_job()), client)
    assert got.locations == ["Dallas, TX", "Toronto, Ontario"] and "Python" in got.text
    assert client.calls == [("plain", WD_API)]


def test_workday_403_retries_once_like_a_browser_with_the_career_site_as_referer():
    client = WDClient(forbidden(), browser=wd_payload(location="Toronto, Ontario"))
    got = ats.fetch_full(ats.locate(wd_job()), client)
    assert got.locations == ["Toronto, Ontario"]
    assert client.calls[1] == ("browser", WD_API, "https://acme.wd1.myworkdayjobs.com/ext")


def test_workday_still_403_is_reported_as_jd_unavailable():
    client = WDClient(forbidden(), browser=forbidden())
    r = E.fetch_job(wd_job(), client, NOCACHE)
    assert r.status == "error" and r.detail == "http 403"


def test_other_http_errors_do_not_trigger_the_browser_retry():
    resp = requests.Response()
    resp.status_code = 500
    client = WDClient(requests.HTTPError(response=resp))
    assert E.fetch_job(wd_job(), client, NOCACHE).detail == "http 500"
    assert [c[0] for c in client.calls] == ["plain"]


def test_cache_keeps_locations(tmp_path):
    cache = E.JDCache(tmp_path, ttl_days=1)
    client = WDClient(wd_payload(location="Dallas, TX", additionalLocations=["Toronto, Ontario"]))
    first = E.fetch_job(wd_job(), client, cache)
    again = E.fetch_job(wd_job(), WDClient(RuntimeError("no network")), cache)
    assert first.locations == again.locations == ["Dallas, TX", "Toronto, Ontario"]
    path = next(tmp_path.iterdir())
    path.write_text(json.dumps({"text": "x", "company": ""}))            # entry from before locations were cached
    assert cache.get(WD_URL) is None


# --- the browser retry itself ---------------------------------------------------------

def test_polite_client_browser_retry_primes_cookies_and_sends_origin_and_referer(monkeypatch):
    from scanner import net
    seen = []

    class Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True}

    class Sess:
        def __init__(self):
            self.headers = {}

        def get(self, url, timeout=None, headers=None):
            seen.append((url, {**self.headers, **(headers or {})}))
            return Resp()

    monkeypatch.setattr(net.requests, "Session", Sess)
    c = net.PoliteClient(delay=0)
    assert c.get_json_browser(WD_API, "Workday", "https://acme.wd1.myworkdayjobs.com/ext") == {"ok": True}
    c.get_json_browser(WD_API, "Workday", "https://acme.wd1.myworkdayjobs.com/ext")
    urls = [u for u, _ in seen]
    assert urls == ["https://acme.wd1.myworkdayjobs.com/ext", WD_API, WD_API]       # cookies primed once per host
    api_headers = seen[1][1]
    assert api_headers["Origin"] == "https://acme.wd1.myworkdayjobs.com"
    assert api_headers["Referer"] == "https://acme.wd1.myworkdayjobs.com/ext"
    assert "Mozilla/5.0" in api_headers["User-Agent"] and api_headers["Accept-Language"].startswith("en-US")


# --- report sections --------------------------------------------------------------------

def test_report_lists_forbidden_unresolved_and_slug_names():
    j = job()
    j.score = 1.0
    phase2 = {"drops": {}, "fetch": {}, "fit_dist": [], "held": 0, "rescued": 0,
              "forbidden": __import__("collections").Counter({"bdo.wd3.myworkdayjobs.com": 1}),
              "forbidden_jobs": [("BDO", "DevOps | Engineer", "https://bdo.wd3.myworkdayjobs.com/x")],
              "unresolved_locations": [("Acme", "Software Engineer", WD_URL)],
              "slug_names": [("intouchinsight", "Intouchinsight")]}
    text = report.render([j], 3, T, None, {}, 1, phase2)
    assert "Kept without a JD" in text and "BDO | DevOps \\| Engineer" in text
    assert "unresolved location" in text and WD_URL in text
    assert "raw slugs" in text and "| intouchinsight | Intouchinsight |" in text
