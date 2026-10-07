import json
from datetime import date

from scanner.state import GapsStore, SeenStore

TODAY = date(2026, 10, 7)


def test_seen_roundtrip_prune_and_first_date_wins(tmp_path):
    path = tmp_path / "state" / "seen.json"
    s = SeenStore(path)
    assert not s.existed and s.seen == {}
    s.add("a", date(2026, 7, 1))           # 98 days old
    s.add("b", date(2026, 7, 9))           # 90 days old: kept
    s.add("c", TODAY)
    s.add("c", date(2026, 12, 31))         # a later add never overwrites the first date
    s.save()
    s2 = SeenStore(path)
    assert s2.existed and s2.seen == {"a": "2026-07-01", "b": "2026-07-09", "c": "2026-10-07"}
    s2.prune(TODAY, 90)
    assert set(s2.seen) == {"b", "c"}


def test_state_files_are_valid_json_and_written_atomically(tmp_path):
    s = SeenStore(tmp_path / "seen.json")
    s.add("k", TODAY)
    s.save()
    assert json.loads((tmp_path / "seen.json").read_text())["seen"] == {"k": "2026-10-07"}
    assert not list(tmp_path.glob("*.tmp"))


def test_corrupt_or_missing_state_starts_empty(tmp_path):
    (tmp_path / "seen.json").write_text("{not json")
    assert SeenStore(tmp_path / "seen.json").seen == {}
    assert GapsStore(tmp_path / "missing.json").jobs == {}


def test_gaps_store_records_each_job_once_and_prunes(tmp_path):
    g = GapsStore(tmp_path / "gaps.json")
    assert g.record("k1", date(2026, 7, 1), "Old Job", "Acme", ["Java"])
    assert g.record("k2", TODAY, "New Job", "Acme", ["Kafka", "Java"])
    assert not g.record("k2", date(2026, 10, 8), "New Job again", "Acme", ["Go"])      # once per job
    assert g.jobs["k2"]["gaps"] == ["Java", "Kafka"] and g.jobs["k2"]["date"] == "2026-10-07"
    g.prune(TODAY, 90)
    assert set(g.jobs) == {"k2"}
    g.save()
    assert set(GapsStore(tmp_path / "gaps.json").jobs) == {"k2"}
