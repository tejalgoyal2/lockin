import argparse
import json
from datetime import datetime, timedelta, timezone

import pytest

from scanner import cli, notion_feed as nf
from scanner.config import load_config
from scanner.models import Job
from scanner.state import SeenStore
from tests.fakes import NAMES, FakeNotion, Resp
from tests.test_notion_feed import NOW, make_job

CFG = load_config()


def args(**kw):
    base = dict(dry_run=False, max_rows=None, show_payloads=5, skip_notion=False, top=None)
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def env(tmp_path):
    notion = FakeNotion()
    client = nf.NotionClient("tok", "2026-03-11", session=notion, sleep=lambda s: None, min_interval=0)
    feed = nf.load_feed(client, "ds-1", NAMES)
    seen = SeenStore(tmp_path / "seen.json")
    return notion, client, feed, seen


def jobs(n=4):
    return [make_job(i, jd="Python, SQL, Docker, Git. " * 200, score=100.0 - i) for i in range(1, n + 1)]


def test_real_run_writes_top_rows_records_seen_and_verifies_readback(env, capsys):
    notion, client, feed, seen = env
    code = cli.notion_step(CFG, args(max_rows=2), client, feed, jobs(), seen, {"rbc": "RBC"}, NOW)
    out = capsys.readouterr().out
    assert code == 0
    assert [p["props"]["Link"] for p in notion.pages.values()] == [
        "https://jobs.example.com/1?utm=x", "https://jobs.example.com/2?utm=x"]
    assert set(SeenStore(seen.path).seen) == {"key1", "key2"}          # saved as each row is written
    assert "wrote 2 rows, 0 failed" in out and "read-back OK" in out and "over_cap" in out


def test_rerun_creates_no_new_rows_and_no_duplicates(env, capsys):
    notion, client, feed, seen = env
    cli.notion_step(CFG, args(), client, feed, jobs(), seen, {}, NOW)          # writes all 4 (cap 40)
    seen2 = SeenStore(seen.path)                                               # as the next run would load it
    code = cli.notion_step(CFG, args(), client, feed, jobs(), seen2, {}, NOW)
    out = capsys.readouterr().out
    assert code == 0 and len(notion.pages) == 4
    assert "0 to write" in out and "'seen': 4" in out
    links = [p["props"]["Link"] for p in notion.pages.values()]
    assert len(links) == len(set(links))


def test_link_already_in_feed_is_skipped_even_when_seen_json_is_empty(env):
    notion, client, feed, seen = env
    notion.add_row("https://jobs.example.com/1?utm=other", NOW.isoformat())
    cli.notion_step(CFG, args(), client, feed, jobs(2), seen, {}, NOW)
    assert sorted(p["props"]["Link"] for p in notion.pages.values() if "created_payload" in p) == [
        "https://jobs.example.com/2?utm=x"]


def test_row_the_owner_moved_out_of_the_feed_does_not_come_back(env):
    notion, client, feed, seen = env
    cli.notion_step(CFG, args(), client, feed, jobs(2), seen, {}, NOW)
    for p in notion.pages.values():           # his automation copies the row to the main DB, deletes it here
        p["in_trash"] = True
    seen2 = SeenStore(seen.path)
    cli.notion_step(CFG, args(), client, feed, jobs(2), seen2, {}, NOW)
    assert all(p["in_trash"] for p in notion.pages.values()) and len(notion.pages) == 2


def test_cleanup_runs_before_writing_and_spares_ticked_rows(env):
    notion, client, feed, seen = env
    old = (NOW - timedelta(days=30)).isoformat()
    plain = notion.add_row("https://a/1", old)
    ticked = notion.add_row("https://a/2", old, apply=True)
    cli.notion_step(CFG, args(max_rows=1), client, feed, jobs(), seen, {}, NOW)
    assert notion.pages[plain]["in_trash"] and not notion.pages[ticked]["in_trash"]


def test_dry_run_prints_payloads_and_writes_nothing(env, capsys):
    notion, client, feed, seen = env
    old = (NOW - timedelta(days=30)).isoformat()
    victim = notion.add_row("https://a/1", old)
    code = cli.notion_step(CFG, args(dry_run=True, show_payloads=2), client, feed, jobs(5), seen, {}, NOW)
    out = capsys.readouterr().out
    assert code == 0
    assert out.count("--- payload") == 2 and '"data_source_id": "ds-1"' in out and "Dry run: nothing written" in out
    assert not [r for r in notion.requests if r["method"] in ("PATCH",) or r["path"] == "/v1/pages"]
    assert not notion.pages[victim]["in_trash"] and not seen.path.exists()


def test_offline_dry_run_uses_a_virtual_feed(capsys):
    feed = nf.virtual_feed(NAMES)
    code = cli.notion_step(CFG, args(dry_run=True, show_payloads=1), None, feed, jobs(2), SeenStore("/nonexistent/s.json"),
                           {"rbc": "RBC"}, NOW)
    out = capsys.readouterr().out
    assert code == 0 and "RBC · oct6" in out and "<NOTION_FEED_DATA_SOURCE_ID>" in out


def test_failed_rows_are_not_marked_seen(env):
    notion, client, feed, seen = env
    original = notion.request

    def flaky(method, url, **kw):
        if url.endswith("/v1/pages") and not getattr(flaky, "failed", False):
            flaky.failed = True
            return Resp(400, {"code": "validation_error", "message": "bad row"})
        return original(method, url, **kw)
    client.session = type("S", (), {"request": staticmethod(flaky)})()
    code = cli.notion_step(CFG, args(), client, feed, jobs(2), seen, {}, NOW)
    assert code == 1 and set(SeenStore(seen.path).seen) == {"key2"}      # key1 will be retried next run


# --- argument / credential handling ----------------------------------------------------

def test_missing_credentials_is_a_usage_error_unless_dry_run(monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    monkeypatch.delenv("NOTION_FEED_DATA_SOURCE_ID", raising=False)
    with pytest.raises(cli.UsageError) as e:
        cli.notion_credentials(args())
    assert "NOTION_TOKEN and NOTION_FEED_DATA_SOURCE_ID" in str(e.value)
    assert cli.notion_credentials(args(dry_run=True)) == ("", "")


def test_main_exits_2_without_credentials_before_scanning(monkeypatch, capsys):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    monkeypatch.delenv("NOTION_FEED_DATA_SOURCE_ID", raising=False)
    assert cli.main(["run", "--since", "1"]) == 2
    assert "must be set" in capsys.readouterr().err


def test_main_rejects_no_jd_when_writing(monkeypatch, capsys):
    monkeypatch.setenv("NOTION_TOKEN", "t")
    monkeypatch.setenv("NOTION_FEED_DATA_SOURCE_ID", "d")
    assert cli.main(["run", "--no-jd"]) == 2 and "needs JD text" in capsys.readouterr().err


def test_schema_problem_exits_2_with_the_list_and_writes_nothing(monkeypatch, capsys):
    notion = FakeNotion()
    del notion.schema["properties"]["Apply"]
    notion.schema["properties"]["Link"]["type"] = "title"
    monkeypatch.setenv("NOTION_TOKEN", "t")
    monkeypatch.setenv("NOTION_FEED_DATA_SOURCE_ID", "ds-1")
    real = nf.NotionClient
    monkeypatch.setattr(nf, "NotionClient", lambda token, version: real(
        token, version, session=notion, sleep=lambda s: None, min_interval=0))
    assert cli.main(["run"]) == 2
    err = capsys.readouterr().err
    assert "missing property 'Apply'" in err and "'Link' has type 'title'" in err
    assert not [r for r in notion.requests if r["method"] in ("POST", "PATCH")]


def test_top_makes_a_rerun_an_exact_repeat_with_zero_new_rows(env, capsys):
    notion, client, feed, seen = env
    cli.notion_step(CFG, args(top=2, max_rows=2), client, feed, jobs(5), seen, {}, NOW)
    assert len(notion.pages) == 2
    capsys.readouterr()
    cli.notion_step(CFG, args(top=2, max_rows=2), client, feed, jobs(5), SeenStore(seen.path), {}, NOW)
    out = capsys.readouterr().out
    assert len(notion.pages) == 2 and "0 to write" in out and "'seen': 2" in out


def test_link_check_alone_blocks_a_rerun_when_seen_json_is_lost(env, capsys):
    notion, client, feed, seen = env
    cli.notion_step(CFG, args(top=2, max_rows=2), client, feed, jobs(5), seen, {}, NOW)
    capsys.readouterr()
    cli.notion_step(CFG, args(top=2, max_rows=2), client, feed, jobs(5), SeenStore(seen.path.parent / "gone.json"), {}, NOW)
    out = capsys.readouterr().out
    # the Link check finds both rows and records them in the (new) seen.json, so they count as seen
    assert len(notion.pages) == 2 and "0 to write" in out and "2 jobs already in the Feed recorded" in out


def test_jobs_already_in_the_feed_are_remembered_so_they_cannot_return_later(env, capsys):
    notion, client, feed, seen = env
    notion.add_row("https://jobs.example.com/1?utm=other", NOW.isoformat())
    cli.notion_step(CFG, args(top=1, max_rows=1), client, feed, jobs(3), seen, {}, NOW)
    assert "recorded in seen.json" in capsys.readouterr().out
    assert set(SeenStore(seen.path).seen) == {"key1"} and len(notion.pages) == 1       # nothing was written
    for p in notion.pages.values():           # the owner's automation moves the row out of the Feed
        p["in_trash"] = True
    cli.notion_step(CFG, args(top=1, max_rows=1), client, feed, jobs(3), SeenStore(seen.path), {}, NOW)
    assert len(notion.pages) == 1             # still not re-created


def test_dry_run_does_not_touch_seen_json_for_feed_matches(env):
    notion, client, feed, seen = env
    notion.add_row("https://jobs.example.com/1?utm=other", NOW.isoformat())
    cli.notion_step(CFG, args(dry_run=True), client, feed, jobs(2), seen, {}, NOW)
    assert not seen.path.exists()
