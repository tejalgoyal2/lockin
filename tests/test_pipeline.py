from datetime import datetime, timedelta, timezone

from scanner.pipeline import dedupe, run_stream
from scanner.sources import feashliaa, simplify

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


def _f(title, loc="Toronto, ON", company="Acme", days_ago=1, ats="Greenhouse", url="https://x/1"):
    first = (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
    return {"company": company, "title": title, "location": loc, "ats": ats, "url": url, "first_seen": first}


def test_stage_counts_are_sequential(filters):
    recs = [
        _f("Software Engineer"),                        # kept
        _f("Software Engineer", days_ago=10),           # stale
        _f("Software Engineer", loc="Austin, TX"),      # not Canadian
        _f("Software Engineer", company="Jobgether"),   # blocklisted
        _f("Barista"),                                  # not a tech title
        _f("Senior Software Engineer"),                 # senior
        _f("Software Engineer Intern"),                 # student-only
    ]
    jobs, stages = run_stream(recs, feashliaa.raw_fields, filters, NOW, since_days=3)
    assert [j.title for j in jobs] == ["Software Engineer"]
    assert dict(stages) == {
        "raw": 7, "fresh": 6, "location": 5, "company": 4, "language": 4,
        "title_match": 3, "title_not_senior": 2, "not_student_only": 1,
    }


def test_simplify_pre_stage_and_new_grad_flag(filters):
    base = {"company_name": "Acme", "title": "Software Engineer", "locations": ["Toronto, ON, Canada", "SF"],
            "url": "https://s/1", "date_posted": int((NOW - timedelta(days=1)).timestamp()),
            "active": True, "is_visible": True, "category": "Software"}
    inactive = {**base, "active": False}
    hardware = {**base, "category": "Hardware"}
    cats = ["Software", "AI/ML/Data"]
    jobs, stages = run_stream(
        [base, inactive, hardware], simplify.raw_fields, filters, NOW, 3,
        pre_stages=(("active_category", lambda r: simplify.is_listed(r, cats)),))
    assert len(jobs) == 1 and jobs[0].new_grad and jobs[0].location == "Toronto, ON, Canada"
    assert dict(stages)["raw"] == 3 and dict(stages)["active_category"] == 1


def test_dedupe_merges_sources_keeps_ats_url_and_new_grad(filters):
    ats = _f("Software Engineer", url="https://boards.greenhouse.io/acme/1", days_ago=2)
    simp = {"company_name": "Acme", "title": "Software  Engineer", "locations": ["Toronto, ON, Canada"],
            "url": "https://simplify/redirect", "date_posted": int((NOW - timedelta(days=1)).timestamp()),
            "active": True, "is_visible": True, "category": "Software"}
    a, _ = run_stream([simp], simplify.raw_fields, filters, NOW, 3)
    b, _ = run_stream([ats], feashliaa.raw_fields, filters, NOW, 3)
    merged = dedupe(a + b)  # Simplify first, to exercise the URL/source swap
    assert len(merged) == 1
    job = merged[0]
    assert job.url == "https://boards.greenhouse.io/acme/1"
    assert job.source == "Greenhouse" and job.new_grad
    assert job.source_label() == "Greenhouse+Simplify"
    assert job.first_seen == NOW - timedelta(days=2)


def test_dedupe_sorts_newest_first(filters):
    jobs, _ = run_stream([_f("Data Analyst", days_ago=2), _f("Software Engineer", days_ago=1)],
                         feashliaa.raw_fields, filters, NOW, 3)
    assert [j.title for j in dedupe(jobs)] == ["Software Engineer", "Data Analyst"]


def test_staleness_warning(caplog):
    meta = {"last_updated": (NOW - timedelta(hours=40)).isoformat().replace("+00:00", "Z")}
    with caplog.at_level("WARNING"):
        age = feashliaa.check_freshness(meta, 36, NOW)
    assert round(age) == 40 and "old" in caplog.text
