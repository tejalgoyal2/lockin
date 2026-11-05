from datetime import datetime, timedelta, timezone

import pytest

from scanner import cli, notion_feed as nf
from scanner.feedback import FeedbackStore, fit_band
from tests.fakes import NAMES, FakeNotion

NOW = datetime(2026, 11, 10, 16, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("fit, band", [(None, "n/a"), (0.0, "<60"), (0.599, "<60"), (0.6, "60-69"), (0.699, "60-69"),
                                       (0.7, "70-79"), (0.8, "80-89"), (0.899, "80-89"), (0.9, "90+"), (1.0, "90+")])
def test_fit_bands(fit, band):
    assert fit_band(fit) == band


def row(created=NOW, **kw):
    base = dict(page_id="p", created=created, link="https://x/1", interested=False, apply=False, done_del=True,
                fit=0.82, source="Workday", signals=("recent graduate", "0-2 years", "2+ yrs"))
    base.update(kw)
    return nf.FeedRow(**base)


def test_records_each_dimension_once_per_row(tmp_path):
    st = FeedbackStore(tmp_path / "f.json")
    assert st.record(row(apply=True)) and st.record(row(fit=None, source="Simplify", signals=()))
    assert st.counts["fit"] == {"80-89": [1, 1], "n/a": [1, 0]}
    assert st.counts["source"] == {"Workday": [1, 1], "Simplify": [1, 0]}
    assert st.counts["level"] == {"recent graduate": [1, 1], "none": [1, 0]}      # the strongest level tag only
    assert st.counts["caution"] == {"2+ yrs": [1, 1], "none": [1, 0]}
    assert (st.reviewed, st.picked) == (2, 1)


def test_rows_older_than_the_seed_are_not_counted_twice(tmp_path):
    st = FeedbackStore(tmp_path / "f.json")
    st.seeded_through = "2026-11-04T21:46:37Z"
    assert not st.record(row(created=datetime(2026, 11, 4, 21, 46, 37, tzinfo=timezone.utc)))
    assert not st.record(row(created=datetime(2026, 10, 7, tzinfo=timezone.utc)))
    assert st.record(row(created=datetime(2026, 11, 4, 21, 46, 38, tzinfo=timezone.utc)))
    assert st.reviewed == 1


def test_save_and_reload_round_trip(tmp_path):
    st = FeedbackStore(tmp_path / "f.json")
    st.record(row(interested=True), today=datetime(2026, 11, 10).date())
    st.save()
    again = FeedbackStore(tmp_path / "f.json")
    assert again.counts == st.counts and again.since == "2026-11-10"


def test_committed_seed_is_consistent():
    st = FeedbackStore("state/feedback.json")
    assert st.reviewed == 120 and st.picked == 27 and st.seeded_through
    for dim in st.counts:
        assert sum(v[0] for v in st.counts[dim].values()) == st.reviewed
        assert sum(v[1] for v in st.counts[dim].values()) == st.picked


def test_render_hides_rates_for_thin_bands(tmp_path):
    st = FeedbackStore(tmp_path / "f.json")
    for _ in range(10):
        st.record(row(fit=0.95, apply=True))
    st.record(row(fit=0.5))
    text = st.render()
    assert "| 90+ | 10 | 10 | 100% |" in text and "| <60 | 1 | 0 | - |" in text


# --- the run: count, then trash ----------------------------------------------------------------

@pytest.fixture
def env(tmp_path):
    notion = FakeNotion()
    client = nf.NotionClient("t", "2026-03-11", session=notion, sleep=lambda s: None, min_interval=0)
    return notion, client, nf.load_feed(client, "ds-1", NAMES), tmp_path


def test_trashed_rows_are_counted_and_the_report_is_written(env):
    notion, client, feed, tmp = env
    notion.add_row("https://a/1", NOW.isoformat(), interested=True, apply=True, done_del=True, fit=0.91,
                   source="Greenhouse", signals=["junior"])
    notion.add_row("https://a/2", NOW.isoformat(), done_del=True, fit=0.55, source="Lever")
    notion.add_row("https://a/3", NOW.isoformat(), fit=0.95, source="Lever")            # not done-del: untouched
    st = FeedbackStore(tmp / "state" / "feedback.json")
    assert cli.trash_moved_rows(client, feed, dry_run=False, feedback=st, feedback_report=str(tmp / "fb.md")) == 2
    assert (st.reviewed, st.picked) == (2, 1)
    assert FeedbackStore(tmp / "state" / "feedback.json").reviewed == 2               # saved
    assert "| 90+ | 1 | 1 | - |" in (tmp / "fb.md").read_text(encoding="utf-8")


def test_a_dry_run_counts_nothing(env):
    notion, client, feed, tmp = env
    notion.add_row("https://a/1", NOW.isoformat(), done_del=True, fit=0.9)
    st = FeedbackStore(tmp / "feedback.json")
    cli.trash_moved_rows(client, feed, dry_run=True, feedback=st, feedback_report=str(tmp / "fb.md"))
    assert st.reviewed == 0 and not (tmp / "feedback.json").exists() and not (tmp / "fb.md").exists()
