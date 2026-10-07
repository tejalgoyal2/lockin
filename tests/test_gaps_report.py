from datetime import date, datetime, timezone

from scanner import gaps_report as gr
from scanner.models import Job
from scanner.score import Scorer
from scanner.state import GapsStore

TODAY = date(2026, 10, 12)          # a Monday
SCORER = Scorer.load()


def job(i, jd, status="ok", title="Software Engineer", company="acme"):
    return Job(company=company, title=f"{title} {i}", location="Toronto, ON", url=f"https://x/{i}", source="Lever",
               first_seen=datetime(2026, 10, 10, tzinfo=timezone.utc), sources={"Lever"}, key=f"k{i}",
               jd=jd, jd_status=status)


def test_is_report_day_only_on_monday():
    assert gr.is_report_day(date(2026, 10, 12)) and not gr.is_report_day(date(2026, 10, 13))
    assert [gr.is_report_day(date(2026, 10, d)) for d in range(12, 19)] == [True] + [False] * 6


def test_records_every_fetched_job_once_even_if_dropped_later(tmp_path):
    store = GapsStore(tmp_path / "g.json")
    jobs = [job(1, "Java and Kafka"), job(2, "Java and Go: Golang", status="ok"),
            job(3, "", status="error"), job(4, "", status="unsupported")]
    assert gr.record_gaps(store, jobs, SCORER, TODAY, {}) == 2
    assert set(store.jobs) == {"k1", "k2"}                      # no JD, no entry
    assert gr.record_gaps(store, jobs, SCORER, TODAY, {}) == 0   # a second run counts nothing twice
    assert store.jobs["k1"]["gaps"] == ["Java", "Kafka"]


def test_only_gap_terms_are_recorded_not_matched_skills(tmp_path):
    store = GapsStore(tmp_path / "g.json")
    gr.record_gaps(store, [job(1, "Python, SQL and Docker with Kafka")], SCORER, TODAY, {})
    assert store.jobs["k1"]["gaps"] == ["Kafka"]


def seed(store):
    # (key, date, gaps)
    data = [("a", "2026-10-11", ["Java", "Kafka"]), ("b", "2026-10-09", ["Java"]),
            ("c", "2026-10-02", ["Java", "Go"]),             # 10 days old: 30-day window only
            ("d", "2026-09-20", ["Go", "Scala"]),            # 22 days old: 30-day window only
            ("e", "2026-08-01", ["Ruby"])]                   # 72 days old: outside both
    for key, d, gaps in data:
        store.jobs[key] = {"date": d, "title": f"Title {key}", "company": "Acme", "gaps": gaps}


def test_top_gaps_windows_counts_and_examples(tmp_path):
    store = GapsStore(tmp_path / "g.json")
    seed(store)
    rows7, n7 = gr.top_gaps(store, TODAY, 7)
    assert n7 == 2 and rows7 == [("Java", 2, ["Title a (Acme)", "Title b (Acme)"]),
                                 ("Kafka", 1, ["Title a (Acme)"])]
    rows30, n30 = gr.top_gaps(store, TODAY, 30)
    assert n30 == 4
    assert [(t, c) for t, c, _ in rows30] == [("Java", 3), ("Go", 2), ("Kafka", 1), ("Scala", 1)]
    assert "Ruby" not in [t for t, _, _ in rows30]


def test_examples_are_capped_at_three_and_newest_first(tmp_path):
    store = GapsStore(tmp_path / "g.json")
    for i in range(6):
        store.jobs[f"k{i}"] = {"date": f"2026-10-0{i + 1}", "title": f"T{i}", "company": "C", "gaps": ["Java"]}
    (term, n, examples), = gr.top_gaps(store, TODAY, 30)[0]
    assert n == 6 and examples == ["T5 (C)", "T4 (C)", "T3 (C)"]


def test_top_25_limit(tmp_path):
    store = GapsStore(tmp_path / "g.json")
    store.jobs["k"] = {"date": "2026-10-11", "title": "T", "company": "C", "gaps": [f"term{i:02d}" for i in range(40)]}
    assert len(gr.top_gaps(store, TODAY, 7)[0]) == 25


def test_render_has_two_tables_and_is_deterministic(tmp_path):
    store = GapsStore(tmp_path / "g.json")
    seed(store)
    md = gr.render(store, TODAY)
    assert "## Top 25 gap terms, last 7 days (2 jobs with a JD)" in md
    assert "## Top 25 gap terms, last 30 days (4 jobs with a JD)" in md
    assert md.count("| Term | Jobs | Example titles |") == 2
    assert "| Java | 3 |" in md and md == gr.render(store, TODAY)


def test_render_with_empty_state_does_not_crash(tmp_path):
    assert "no gap terms recorded" in gr.render(GapsStore(tmp_path / "g.json"), TODAY)
