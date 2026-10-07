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


GOOD = "Requirements:\nPython, SQL, Docker and Git. 0-2 years of experience. Recent graduates welcome."


def test_good_job_is_scored_with_fit_cluster_and_signals():
    res = run([gh(1, title="Junior Data Analyst")], jds={"1": GOOD})
    (j,) = res.kept
    assert j.jd_status == "ok" and j.fit_pct == 100.0 and j.cluster == "data"   # title says data
    assert j.signals == ["recent graduate", "0-2 years", "junior"]
    assert j.matched == ["Docker", "Git", "Python", "SQL"]
    assert j.score == 123.0   # Fit 100 + 4 matched x 2 + new-grad boost 15 (recent graduate)


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


def test_fit_needs_four_terms_else_na_and_low_signal():
    three = run([gh(1)], jds={"1": "Python, SQL and Docker."}).kept[0]
    four = run([gh(2)], jds={"2": "Python, SQL, Docker and Git."}).kept[0]
    assert three.fit_pct is None and "low signal" in three.signals
    assert three.matched == ["Docker", "Python", "SQL"]            # terms are still listed
    assert three.score == 6.0                                      # no Fit, but 3 matched x 2
    assert four.fit_pct == 100.0 and "low signal" not in four.signals


def test_gap_terms_count_toward_the_four_term_minimum():
    j = run([gh(1)], jds={"1": "Python and SQL. Java and Kafka are a plus."}).kept[0]
    assert j.fit_pct == 50.0 and "low signal" not in j.signals


def test_low_signal_applies_when_jd_has_no_terms_at_all():
    j = run([gh(1)], jds={"1": "Greet customers."}).kept[0]
    assert j.fit_pct is None and j.signals == ["low signal"] and j.score == 0.0


def test_matched_count_breaks_fit_ties_and_is_capped():
    few = gh(1); many = gh(2); lots = gh(3)
    jds = {"1": "Python, SQL, Docker, Git.",
           "2": "Python, SQL, Docker, Git, AWS, Linux, React, Azure.",
           "3": "Python SQL Docker Git AWS Linux React Azure Terraform Kubernetes Flask Playwright Rust Bash"}
    res = run([few, many, lots], jds=jds)
    scores = {j.url[-1]: j.score for j in res.kept}
    assert scores == {"1": 108.0, "2": 116.0, "3": 120.0}   # cap: at most 10 matched skills count
    assert [j.url[-1] for j in res.kept] == ["3", "2", "1"]


def test_weak_title_with_sparse_jd_is_kept_only_if_something_matches():
    sparse = run([gh(1, title="Technical Specialist", weak_title=True)], jds={"1": "Python and SQL."})
    none = run([gh(2, title="Technical Specialist", weak_title=True)], jds={"2": "Java, Kafka and Go."})
    assert len(sparse.kept) == 1 and sparse.kept[0].fit_pct is None
    assert none.drops == {E.R_WEAK: 1}


def test_strong_title_is_not_dropped_for_low_fit():
    res = run([gh(1, title="Data Analyst")], jds={"1": "Java, Kafka, Go, Kotlin, Scala."})
    assert len(res.kept) == 1 and res.kept[0].fit_pct == 0.0


def test_missing_jd_strong_titles_kept_with_penalty_weak_titles_dropped():
    res = run([job(url="https://recruiting.paylocity.com/recruiting/Jobs/Details/1", source="Paylocity"),
               gh(2, weak_title=True), gh(3, weak_title=True), gh(4, title="Data Analyst")],
              jds={"2": http_error(500), "3": http_error(404), "4": http_error(500)})
    assert sorted(j.title for j in res.kept) == ["Data Analyst", "Software Engineer"]
    assert all(j.score == -10.0 and j.fit_pct is None for j in res.kept)
    assert all(j.signals == ["jd unavailable"] for j in res.kept)
    assert res.drops == {E.R_WEAK_NO_JD: 2}
    assert res.fetch == {"Paylocity:unsupported": 1, "Greenhouse:error": 2, "Greenhouse:not_found": 1}


def test_weak_title_on_unsupported_ats_is_dropped_too():
    res = run([job(url="https://recruiting.paylocity.com/recruiting/Jobs/Details/1", source="Paylocity",
                   weak_title=True)])
    assert res.kept == [] and res.drops == {E.R_WEAK_NO_JD: 1}


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
    jd = "Python, SQL, Docker and Git."   # 4 terms: Fit 100 + 4 matched x 2 = 108
    res = run([plain, bc, simp], jds={"1": jd, "2": jd, "3": jd})
    assert {j.url[-1]: j.score for j in res.kept} == {"1": 108.0, "2": 113.0, "3": 118.0}
    assert [j.url[-1] for j in res.kept] == ["3", "2", "1"]


def test_cache_roundtrip(tmp_path):
    cache = E.JDCache(tmp_path, ttl_days=1)
    client = FakeClient({"1": "Python and SQL."})
    j = gh(1)
    assert E.fetch_one(j, client, cache) == ("ok", "Python and SQL.", "")
    assert E.fetch_one(j, FakeClient({}), cache) == ("ok", "Python and SQL.", "")   # served from cache, no network


def test_fit_distribution_buckets():
    jobs = []
    for fit in (None, 0.0, 19.9, 20.0, 59.9, 60.0, 100.0):
        j = gh(1); j.fit_pct = fit; jobs.append(j)
    assert dict(E.fit_distribution(jobs)) == {
        "0-19": 2, "20-39": 1, "40-59": 1, "60-79": 1, "80-100": 1, "n/a (no JD or no tech terms)": 1}


def test_cluster_title_first_then_jd_terms_then_software():
    jd_data = "SQL, Power BI, Databricks and Spark."
    res = run([gh(1, title="Software Engineer"), gh(2, title="Machine Learning Engineer"),
               gh(3, title="Software Programmer"), gh(4, title="Software Developer")],
              jds={"1": jd_data, "2": jd_data, "3": "Greet customers.", "4": http_error(404)})
    clusters = {j.url[-1]: j.cluster for j in res.kept}
    # 3: no title keyword and no JD terms -> default; 4: no JD at all -> default
    assert clusters == {"1": "data", "2": "ml", "3": "software", "4": "software"}


def test_http_403_tenants_are_listed_and_strong_titles_kept():
    wd = [job(url=f"https://{t}.wd3.myworkdayjobs.com/site/job/Toronto/Dev_R{i}", source="Workday",
              title="Software Developer") for i, t in enumerate(["bmo", "bmo", "equifax"])]

    class Forbidden:
        def get_json(self, url, ats_name, **kw):
            resp = requests.Response()
            resp.status_code = 403
            raise requests.HTTPError(response=resp)

    res = E.enrich(wd + [job(source="Workday", url="https://acme.wd3.myworkdayjobs.com/s/job/T/X_1",
                             title="Technical Specialist", weak_title=True)],
                   [], CFG, FILTERS, SCORER, Forbidden(), cache=NOCACHE)
    assert len(res.kept) == 3 and all("jd unavailable" in j.signals for j in res.kept)
    assert res.forbidden == {"bmo.wd3.myworkdayjobs.com": 2, "equifax.wd3.myworkdayjobs.com": 1,
                             "acme.wd3.myworkdayjobs.com": 1}
    assert res.drops == {E.R_WEAK_NO_JD: 1}
    assert {j.jd_error for j in res.kept} == {"http 403"}


def test_fetch_error_details():
    j = gh(1)
    assert E.fetch_one(j, FakeClient({"1": http_error(500)}), NOCACHE)[::2] == ("error", "http 500")
    assert E.fetch_one(j, FakeClient({"1": http_error(404)}), NOCACHE)[::2] == ("not_found", "http 404")
    assert E.fetch_one(j, FakeClient({"1": RuntimeError("x")}), NOCACHE)[::2] == ("error", "RuntimeError")
    assert E.fetch_one(job(url="https://recruiting.paylocity.com/x"), FakeClient({}), NOCACHE)[0] == "unsupported"
