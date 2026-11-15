from datetime import datetime, timezone

from scanner.feedback import FeedbackStore
from scanner.notion_feed import FeedRow

NOW = datetime(2026, 11, 15, tzinfo=timezone.utc)


def row(score, exp, provinces, picked=False):
    return FeedRow("p", NOW, "https://a", interested=picked, apply=False, done_del=True, score=score, exp=exp,
                   provinces=tuple(provinces))


def test_a_row_is_counted_once_per_dimension_and_once_per_province_tag(tmp_path):
    fb = FeedbackStore(tmp_path / "f.json")
    fb.record(row(5, 1, ["BC", "Remote"], picked=True))
    fb.record(row(5, 2, ["Ontario"]))
    fb.record(row(None, None, []))
    assert fb.counts["score"] == {"5": [2, 1], "none": [1, 0]}
    assert fb.counts["exp"] == {"1": [1, 1], "2": [1, 0], "none": [1, 0]}
    assert fb.counts["province"] == {"BC": [1, 1], "Remote": [1, 1], "Ontario": [1, 0], "none": [1, 0]}
    assert (fb.reviewed, fb.picked) == (3, 1)


def test_save_and_reload_round_trip_and_an_old_file_starts_again(tmp_path):
    fb = FeedbackStore(tmp_path / "f.json")
    fb.record(row(4, 1, ["BC"], picked=True), today=NOW.date())
    fb.save()
    again = FeedbackStore(tmp_path / "f.json")
    assert again.counts == fb.counts and again.since == "2026-11-15"
    (tmp_path / "old.json").write_text('{"version": 1, "counts": {"fit": {"90+": [3, 1]}}, "since": "2026-10-06"}')
    assert FeedbackStore(tmp_path / "old.json").reviewed == 0        # the Fit % bands of the old columns are not carried over


def test_render_lists_scores_best_first_and_hides_rates_for_thin_rows(tmp_path):
    fb = FeedbackStore(tmp_path / "f.json")
    for i in range(12):
        fb.record(row(5, 1, ["BC"], picked=i < 6))
    fb.record(row(2, 3, ["Ontario"]))
    text = fb.render()
    assert "| 5 | 12 | 6 | 50% |" in text and "| 2 | 1 | 0 | - |" in text
    assert text.index("| 5 | 12") < text.index("| 2 | 1 | 0")
