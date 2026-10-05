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


def run(cands, held=(), jds=None):
    return E.enrich(list(cands), list(held), CFG, FILTERS, SCORER, FakeClient(jds or {}), cache=NOCACHE)


def gh(i, **kw):
    return job(url=f"https://boards.greenhouse.io/acme/jobs/{i}", **kw)


def http_error(code):
    resp = requests.Response()
    resp.status_code = code
    return requests.HTTPError(response=resp)


GOOD = "Requirements:\nPython and SQL. 0-2 years of experience. Recent graduates welcome."


def test_good_job_is_scored_with_fit_cluster_and_signals():
    res = run([gh(1, title="Junior Data Analyst")], jds={"1": GOOD})
    (j,) = res.kept
    assert j.jd_status == "ok" and j.fit_pct == 100.0 and j.cluster in {"software", "data"}
    assert j.signals == ["recent graduate", "0-2 years", "junior"] and j.matched == ["Python", "SQL"]
    assert j.score == 115.0   # 100 + new-grad boost 15 (recent graduate), Toronto: no BC/remote


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
    assert len(run([gh(1)], jds={"1": "Python. 2+ years of experience."}).kept) == 1


def test_us_authorization_only_drops_without_canadian_location():
    jd = "Python. Must be authorized to work in the United States."
    assert len(run([gh(1)], jds={"1": jd}).kept) == 1      # Canadian location: rule does not fire
    res = run([gh(1, loc="Austin, TX")], jds={"1": jd})
    assert res.drops == {E.R_US_AUTH: 1}


def test_weak_title_dropped_when_no_skill_match_or_low_fit():
    none = run([gh(1, title="Technical Specialist", weak_title=True)], jds={"1": "Greet customers."})
    low = run([gh(2, title="Technical Specialist", weak_title=True)],
              jds={"2": "Python. Java, Kafka, Go, Kotlin, Scala, Spring Boot, Redis."})
    ok = run([gh(3, title="Technical Specialist", weak_title=True)], jds={"3": "Python, SQL, Docker."})
    assert none.drops == {E.R_WEAK: 1} and low.drops == {E.R_WEAK: 1} and len(ok.kept) == 1


def test_strong_title_is_not_dropped_for_low_fit():
    res = run([gh(1, title="Data Analyst")], jds={"1": "Java, Kafka, Go, Kotlin, Scala."})
    assert len(res.kept) == 1 and res.kept[0].fit_pct == 0.0


def test_missing_jd_is_kept_with_penalty_and_weak_titles_survive():
    res = run([job(url="https://recruiting.paylocity.com/recruiting/Jobs/Details/1", source="Paylocity"),
               gh(2, weak_title=True)], jds={"2": http_error(500)})
    assert len(res.kept) == 2 and all(j.score == -10.0 for j in res.kept)
    assert res.fetch == {"Paylocity:unsupported": 1, "Greenhouse:error": 1}
    assert all(j.fit_pct is None for j in res.kept)


def test_404_is_not_found_and_other_errors_are_error():
    res = run([gh(1), gh(2)], jds={"1": http_error(404), "2": RuntimeError("boom")})
    assert res.fetch == {"Greenhouse:not_found": 1, "Greenhouse:error": 1}


def test_student_titled_jobs_rescued_only_when_jd_welcomes_recent_grads():
    held = [gh(1, title="Software Engineer Intern"), gh(2, title="Developer Co-op"),
            gh(3, title="Data Intern"), gh(4, title="Dev Intern")]
    jds = {"1": "Python. Recent graduates are welcome to apply.",
           "2": "Python. Must be enrolled in a co-op program.",
           "3": "Python. Summer role for students.",
           "4": http_error(500)}
    res = run([], held, jds)
    assert [j.title for j in res.kept] == ["Software Engineer Intern"]
    assert res.rescued == 1 and res.held_total == 4
    assert res.drops == {E.R_ENROLLMENT: 1, E.R_STUDENT: 1, E.R_STUDENT_NO_JD: 1}


def test_rescued_student_job_still_faces_other_rules():
    res = run([], [gh(1, title="Software Engineer Intern")],
              {"1": "Recent graduates welcome. 5+ years of experience."})
    assert res.kept == [] and res.drops == {E.R_EXPERIENCE: 1}


def test_scoring_boosts_and_ranking():
    plain = gh(1, title="Software Engineer", loc="Toronto, ON")
    bc = gh(2, title="Software Engineer", loc="Vancouver, BC")
    simp = gh(3, title="Software Engineer", loc="Toronto, ON", new_grad=True)
    jd = "Python and Docker."
    res = run([plain, bc, simp], jds={"1": jd, "2": jd, "3": jd})
    assert {j.url[-1]: j.score for j in res.kept} == {"1": 100.0, "2": 105.0, "3": 110.0}
    assert [j.url[-1] for j in res.kept] == ["3", "2", "1"]


def test_cache_roundtrip(tmp_path):
    cache = E.JDCache(tmp_path, ttl_days=1)
    client = FakeClient({"1": "Python and SQL."})
    j = gh(1)
    assert E.fetch_one(j, client, cache) == ("ok", "Python and SQL.")
    assert E.fetch_one(j, FakeClient({}), cache) == ("ok", "Python and SQL.")   # served from cache, no network


def test_fit_distribution_buckets():
    jobs = []
    for fit in (None, 0.0, 19.9, 20.0, 59.9, 60.0, 100.0):
        j = gh(1); j.fit_pct = fit; jobs.append(j)
    assert dict(E.fit_distribution(jobs)) == {
        "0-19": 2, "20-39": 1, "40-59": 1, "60-79": 1, "80-100": 1, "n/a (no JD or no tech terms)": 1}
