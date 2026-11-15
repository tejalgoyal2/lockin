from datetime import datetime, timezone

import pytest
import requests

from scanner import enrich as E
from scanner.config import load_config
from scanner.filters import Filters
from scanner.models import Job
from scanner.score import Scorer

T = datetime(2026, 10, 3, tzinfo=timezone.utc)
CFG = load_config()
FILTERS = Filters.from_config(CFG)
SCORER = Scorer.load()
NOCACHE = E.JDCache(None, 0)


def job(title="Software Engineer", url="https://boards.greenhouse.io/acme/jobs/1", loc="Toronto, ON",
        source="Greenhouse", **kw):
    return Job(company="acme", title=title, location=loc, url=url, source=source, first_seen=T,
               sources={source}, **kw)


class FakeClient:
    def __init__(self, by_id):
        self.by_id = by_id   # greenhouse job id -> JD plain text, or an Exception

    def get_json(self, url, ats_name, **kw):
        val = self.by_id[url.rsplit("/", 1)[-1]]
        if isinstance(val, Exception):
            raise val
        return {"content": f"<p>{val}</p>".replace("\n", "</p><p>")}


def _student(j):
    j.student = True
    return j


def run(cands, held=(), jds=None):
    return E.enrich(list(cands) + [_student(h) for h in held], CFG, FILTERS, SCORER, FakeClient(jds or {}), cache=NOCACHE)


def gh(i, **kw):
    return job(url=f"https://boards.greenhouse.io/acme/jobs/{i}", **kw)


def http_error(code):
    resp = requests.Response()
    resp.status_code = code
    return requests.HTTPError(response=resp)


GOOD = "Requirements:\nPython, SQL, Docker and Git. 0-2 years of experience. Recent graduates welcome."


@pytest.mark.parametrize("jd,reason", [
    ("Python. 5+ years of experience required.", E.R_EXPERIENCE),
    ("Python. 3-5 years of experience.", E.R_EXPERIENCE),
    ("Python. You must be currently enrolled in a degree program.", E.R_ENROLLMENT),
    ("Python. Active security clearance is required.", E.R_CLEARANCE),
])
def test_jd_rules_drop_with_reason(jd, reason):
    res = run([gh(1)], jds={"1": jd})
    assert res.kept == [] and res.drops == {reason: 1}


def test_experience_below_threshold_kept():
    assert len(run([gh(1)], jds={"1": "Python. 1+ year of experience."}).kept) == 1


def test_us_authorization_only_drops_without_canadian_location():
    jd = "Python. Must be authorized to work in the United States."
    assert len(run([gh(1)], jds={"1": jd}).kept) == 1      # Canadian location: rule does not fire
    res = run([gh(1, loc="Austin, TX")], jds={"1": jd})
    assert res.drops == {E.R_US_AUTH: 1}


def test_404_is_not_found_and_other_errors_are_error():
    res = run([gh(1), gh(2)], jds={"1": http_error(404), "2": RuntimeError("boom")})
    assert res.fetch == {"Greenhouse:not_found": 1, "Greenhouse:error": 1}


def test_cache_roundtrip(tmp_path):
    cache = E.JDCache(tmp_path, ttl_days=1)
    client = FakeClient({"1": "Python and SQL."})
    j = gh(1)
    assert E.fetch_one(j, client, cache) == ("ok", "Python and SQL.", "")
    assert E.fetch_one(j, FakeClient({}), cache) == ("ok", "Python and SQL.", "")   # served from cache, no network


def test_fetch_error_details():
    j = gh(1)
    assert E.fetch_one(j, FakeClient({"1": http_error(500)}), NOCACHE)[::2] == ("error", "http 500")
    assert E.fetch_one(j, FakeClient({"1": http_error(404)}), NOCACHE)[::2] == ("not_found", "http 404")
    assert E.fetch_one(j, FakeClient({"1": RuntimeError("x")}), NOCACHE)[::2] == ("error", "RuntimeError")
    assert E.fetch_one(job(url="https://recruiting.paylocity.com/x"), FakeClient({}), NOCACHE)[0] == "unsupported"


def test_student_job_with_future_graduation_date_is_not_rescued():
    jd = "Open to recent graduates. Available to students with an August 2027 or later graduation date."
    res = run([], [gh(1, title="Student, Data Centre")], {"1": jd})
    assert res.kept == [] and res.drops == {E.R_ENROLLMENT: 1}


def test_greenhouse_company_name_is_kept_and_cached(tmp_path):
    class GH:
        def get_json(self, url, ats_name, **kw):
            return {"content": "&lt;p&gt;Python, SQL, Docker, Git.&lt;/p&gt;", "company_name": "Acme Corp"}
    cache = E.JDCache(tmp_path, 1)
    j = gh(1)
    res = E.enrich([j], CFG, FILTERS, SCORER, GH(), cache=cache)
    assert res.kept[0].company_name == "Acme Corp"
    j2 = gh(1)                                           # second run: served from the cache, name preserved
    E.enrich([j2], CFG, FILTERS, SCORER, FakeClient({}), cache=cache)
    assert j2.company_name == "Acme Corp"


def test_simplify_name_wins_over_the_ats_name():
    class GH:
        def get_json(self, url, ats_name, **kw):
            return {"content": "Python, SQL, Docker, Git.", "company_name": "ATS Name"}
    j = gh(1, company_name="Simplify Name")
    E.enrich([j], CFG, FILTERS, SCORER, GH(), cache=NOCACHE)
    assert j.company_name == "Simplify Name"


def test_a_slow_host_gets_its_own_lane_and_does_not_hold_up_the_others():
    import threading
    import time as _time
    started = []

    class Slow(FakeClient):
        host_delays = {"www.jobbank.gc.ca": 5}

        def _hit(self, url):
            started.append((threading.current_thread().name, url, _time.monotonic()))
            if "jobbank" in url:
                _time.sleep(0.2)                                   # the host's turn
            raise requests.HTTPError(response=type("R", (), {"status_code": 500})())

        def get(self, url, ats_name, **kw):
            self._hit(url)

        def get_json(self, url, ats_name, **kw):
            self._hit(url)

    jobs = [job(url=f"https://www.jobbank.gc.ca/jobsearch/jobposting/{i}", source="Job Bank") for i in range(5)]
    jobs += [gh(i) for i in range(20, 26)]
    t0 = _time.monotonic()
    E.fetch_all(jobs, Slow({}), NOCACHE, workers=4)
    fast_done = max(t for _, u, t in started if "jobbank" not in u) - t0
    slow_threads = {n for n, u, _ in started if "jobbank" in u}
    assert len(slow_threads) == 1 and fast_done < 0.5            # one lane for Job Bank; the Greenhouse jobs did not wait for it


LONG_FILLER = "We are a friendly team that values collaboration, ownership and clear communication in everything we do. " * 8
