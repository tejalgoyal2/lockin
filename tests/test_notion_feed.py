import json
from datetime import datetime, timedelta, timezone

import pytest
import requests

from scanner import notion_feed as nf
from scanner.models import Job
from tests.fakes import NAMES, FakeNotion, Resp, good_schema

NOW = datetime(2026, 10, 7, 16, 0, tzinfo=timezone.utc)
OVERRIDES = {"rbc": "RBC"}


def make_job(i=1, jd="Python, SQL, Docker, Git.", **kw):
    base = dict(company="rbc", title="Software Engineer I", location="Toronto, ON",
                url=f"https://jobs.example.com/{i}?utm=x", source="Workday",
                first_seen=datetime(2026, 10, 6, 9, tzinfo=timezone.utc), sources={"Workday"},
                key=f"key{i}", jd=jd, jd_status="ok", fit_pct=70.0, signals=["junior"], score=80.0 - i)
    base.update(kw)
    return Job(**base)


@pytest.fixture
def notion():
    return FakeNotion()


@pytest.fixture
def client(notion):
    sleeps = []
    c = nf.NotionClient("secret-token", "2026-03-11", session=notion, sleep=sleeps.append, min_interval=0)
    c.sleeps = sleeps
    return c


@pytest.fixture
def feed(client):
    return nf.load_feed(client, "ds-1", NAMES)


# --- schema ---------------------------------------------------------------------------

def test_good_schema_loads_and_resolves_ids(feed):
    assert feed.ids["jd"] == "p_jd" and "Greenhouse" in feed.source_options


def test_missing_property_is_reported_by_name(client, notion):
    del notion.schema["properties"]["JD"]
    with pytest.raises(nf.SchemaError) as e:
        nf.load_feed(client, "ds-1", NAMES)
    assert "missing property 'JD'" in str(e.value)


def test_wrong_type_and_wrong_number_format_and_missing_options_all_listed(client, notion):
    p = notion.schema["properties"]
    p["Link"]["type"] = "rich_text"
    p["Fit %"]["number"]["format"] = "number"
    p["Signals"]["multi_select"]["options"] = [{"name": "junior"}]
    with pytest.raises(nf.SchemaError) as e:
        nf.load_feed(client, "ds-1", NAMES)
    msg = str(e.value)
    assert "'Link' has type 'rich_text', expected 'url'" in msg
    assert "'Fit %' has number format 'number', expected 'percent'" in msg
    assert "'Signals' is missing options: new grad" in msg and "jd unavailable" in msg


def test_unreadable_data_source_gives_a_sharing_hint(client, notion):
    notion.scripted = [Resp(404, {"code": "object_not_found", "message": "Could not find data source"})]
    with pytest.raises(nf.SchemaError) as e:
        nf.load_feed(client, "ds-1", NAMES)
    assert "data source ID, not the database ID" in str(e.value) and "shared with the integration" in str(e.value)
    assert "secret-token" not in str(e.value) and "ds-1" not in str(e.value)


# --- payload --------------------------------------------------------------------------

def test_payload_shape(feed):
    page, sent = nf.build_page(make_job(), feed, OVERRIDES)
    assert page["parent"] == {"type": "data_source_id", "data_source_id": "ds-1"}
    assert "children" not in page                       # body stays empty
    p = page["properties"]
    assert p["Name"]["title"][0]["text"]["content"] == "Software Engineer I"
    assert p["Company"]["rich_text"][0]["text"]["content"] == "RBC · oct6"
    assert p["Link"] == {"url": "https://jobs.example.com/1?utm=x"}
    assert p["Source"] == {"select": {"name": "Workday"}}
    assert p["Fit %"] == {"number": 0.7}                # a fraction, not 70
    assert p["Signals"] == {"multi_select": [{"name": "junior"}]}
    assert sent == "Python, SQL, Docker, Git."
    assert set(p) == {"Name", "Company", "Link", "Source", "Fit %", "Signals", "JD"}


def test_owner_checkboxes_are_never_written(feed):
    props = nf.build_page(make_job(), feed, OVERRIDES)[0]["properties"]
    assert "Interested" not in props and "Apply" not in props
    for extra in ("Matched", "Gaps", "Cluster", "Score"):
        assert extra not in props


def test_fit_empty_when_na_and_fraction_rounding(feed):
    assert nf.build_page(make_job(fit_pct=None), feed, OVERRIDES)[0]["properties"]["Fit %"] == {"number": None}
    assert nf.build_page(make_job(fit_pct=85.7), feed, OVERRIDES)[0]["properties"]["Fit %"] == {"number": 0.857}
    assert nf.build_page(make_job(fit_pct=100.0), feed, OVERRIDES)[0]["properties"]["Fit %"] == {"number": 1.0}


def test_source_outside_the_feed_options_is_left_empty(feed):
    props = nf.build_page(make_job(source="iCIMS"), feed, OVERRIDES)[0]["properties"]
    assert "Source" not in props         # would otherwise create a new select option silently


def test_signals_include_data_quality_values(feed):
    props = nf.build_page(make_job(signals=["low signal", "jd unavailable"]), feed, OVERRIDES)[0]["properties"]
    assert [s["name"] for s in props["Signals"]["multi_select"]] == ["low signal", "jd unavailable"]


# --- JD chunking ----------------------------------------------------------------------

def test_jd_split_into_2000_char_items_and_roundtrips_exactly():
    jd = ("Requirements: Python.\n\n" * 400)[:9001]
    items, sent = nf.jd_rich_text(jd)
    contents = [i["text"]["content"] for i in items]
    assert len(items) == 5 and all(len(c) <= 2000 for c in contents)
    assert "".join(contents) == jd == sent


def test_chunks_never_split_astral_characters_and_stay_within_limit():
    text = "a" + "😀" * 2500
    chunks = nf.chunk_text(text)
    assert "".join(chunks) == text
    assert all(len(c.encode("utf-16-le")) // 2 <= 2000 for c in chunks)


def test_jd_over_the_limit_is_truncated_with_a_marker():
    items, sent = nf.jd_rich_text("x" * 250_000)
    assert len(items) <= nf.JD_ITEMS_MAX <= 100
    assert sent.endswith("[truncated]") and len(sent) <= nf.JD_ITEMS_MAX * 2000
    assert "".join(i["text"]["content"] for i in items) == sent


def test_empty_jd_gives_no_items_and_nul_bytes_removed():
    assert nf.jd_rich_text("")[0] == []
    assert nf.jd_rich_text("a\x00b")[1] == "ab"


def test_long_link_is_skipped():
    assert nf.skip_reason(make_job(url="https://x.com/" + "a" * 2100)) == "link_too_long"
    assert nf.skip_reason(make_job(url="")) == "no_link"
    assert nf.skip_reason(make_job()) is None


# --- selection / dedupe ---------------------------------------------------------------

def test_select_new_orders_by_score_and_applies_cap_seen_and_feed_links():
    jobs = [make_job(i, score=float(i)) for i in range(1, 8)]      # scores 1..7
    seen = {"key7": "2026-10-01"}                                   # highest score, already written
    feed_links = {"https://jobs.example.com/6"}                     # same Link (tracking params ignored)
    chosen, skipped = nf.select_new(jobs, seen, feed_links, cap=3)
    assert [j.key for j in chosen] == ["key5", "key4", "key3"]
    assert skipped == {"seen": 1, "link_in_feed": 1, "over_cap": 2}


def test_link_check_ignores_tracking_params_but_not_job_ids():
    a = make_job(1, url="https://careers.example.com/jobs/?gh_jid=111")
    b = make_job(2, url="https://careers.example.com/jobs/?gh_jid=222")
    chosen, skipped = nf.select_new([a, b], {}, {"https://careers.example.com/jobs/?gh_jid=111&utm=1"}, cap=5)
    assert [j.key for j in chosen] == ["key2"] and skipped == {"link_in_feed": 1}


# --- cleanup --------------------------------------------------------------------------

def rows_fixture(notion, client, feed):
    old = (NOW - timedelta(days=20)).isoformat().replace("+00:00", "Z")
    fresh = (NOW - timedelta(days=3)).isoformat().replace("+00:00", "Z")
    ids = {
        "old_plain": notion.add_row("https://a/1", old),
        "old_interested": notion.add_row("https://a/2", old, interested=True),
        "old_apply": notion.add_row("https://a/3", old, apply=True),
        "old_both": notion.add_row("https://a/4", old, interested=True, apply=True),
        "fresh_plain": notion.add_row("https://a/5", fresh),
        "edge_13d": notion.add_row("https://a/6", (NOW - timedelta(days=13, hours=23)).isoformat()),
    }
    return ids, nf.list_rows(client, feed)


def test_cleanup_trashes_only_old_rows_with_both_boxes_unticked(notion, client, feed):
    ids, rows = rows_fixture(notion, client, feed)
    stale = nf.stale_rows(rows, NOW, 14)
    assert [r.page_id for r in stale] == [ids["old_plain"]]
    nf.trash_rows(client, stale, dry_run=False)
    assert notion.pages[ids["old_plain"]]["in_trash"] is True
    for k in ("old_interested", "old_apply", "old_both", "fresh_plain", "edge_13d"):
        assert notion.pages[ids[k]]["in_trash"] is False
    trashed = [r for r in notion.requests if r["method"] == "PATCH"]
    assert len(trashed) == 1 and trashed[0]["json"] == {"in_trash": True}


def test_row_with_a_missing_checkbox_value_is_never_trashed():
    old = NOW - timedelta(days=30)
    rows = [nf.FeedRow("p1", old, "https://a", interested=None, apply=False),
            nf.FeedRow("p2", old, "https://b", interested=False, apply=None)]
    assert nf.stale_rows(rows, NOW, 14) == []


def test_dry_run_cleanup_makes_no_patch_requests(notion, client, feed):
    _, rows = rows_fixture(notion, client, feed)
    nf.trash_rows(client, nf.stale_rows(rows, NOW, 14), dry_run=True)
    assert not [r for r in notion.requests if r["method"] == "PATCH"]


def test_list_rows_asks_only_for_the_needed_properties(notion, client, feed):
    rows_fixture(notion, client, feed)
    q = [r for r in notion.requests if r["path"].endswith("/query")][-1]
    assert q["params"] == {"filter_properties[]": ["p_link", "p_int", "p_apply"]}


# --- writing + read-back --------------------------------------------------------------

def test_write_jobs_creates_rows_and_reports_each(notion, client, feed):
    written = []
    res = nf.write_jobs(client, feed, [make_job(1), make_job(2)], OVERRIDES,
                        on_written=lambda job, pid: written.append((job.key, pid)))
    assert [k for k, _ in written] == ["key1", "key2"] and len(res.written) == 2 and not res.failed
    assert len([p for p in notion.pages.values()]) == 2


def test_long_jd_roundtrips_through_the_property_endpoint(notion, client, feed):
    jd = "\n\n".join(f"Paragraph {i}: Python, SQL and Docker." for i in range(400))      # ~14k chars
    assert len(jd) > 4000
    res = nf.write_jobs(client, feed, [make_job(1, jd=jd)], OVERRIDES)
    ok, msg = nf.verify_readback(client, feed, res.written)
    assert ok and f"sent {len(jd)} chars, read {len(jd)} chars" in msg
    gets = [r for r in notion.requests if r["method"] == "GET" and "/properties/" in r["path"]]
    assert len(gets) > 1                                   # paginated read


def test_readback_detects_a_mismatch(notion, client, feed):
    res = nf.write_jobs(client, feed, [make_job(1, jd="x" * 5000)], OVERRIDES)
    notion.pages[res.written[0][1]]["props"]["JD"] = "x" * 4990
    ok, msg = nf.verify_readback(client, feed, res.written)
    assert not ok and "MISMATCH" in msg and "5000" in msg and "4990" in msg


def test_failed_write_is_not_retried_on_5xx_and_aborts_after_three_in_a_row(notion, client, feed):
    notion.scripted = [Resp(503, {"code": "service_unavailable", "message": "x"})] * 10
    res = nf.write_jobs(client, feed, [make_job(i) for i in range(1, 6)], OVERRIDES)
    posts = [r for r in notion.requests if r["method"] == "POST" and r["path"] == "/v1/pages"]
    assert len(posts) == 3 and len(res.failed) == 3 and not res.written    # one attempt each, then stop


def test_a_single_failure_does_not_stop_the_run(notion, client, feed):
    notion.scripted = [Resp(400, {"code": "validation_error", "message": "bad"})]
    res = nf.write_jobs(client, feed, [make_job(1), make_job(2)], OVERRIDES)
    assert [j.key for j, _, _ in res.written] == ["key2"] and [j.key for j, _ in res.failed] == ["key1"]


# --- client: pacing, retries ----------------------------------------------------------

def test_429_waits_retry_after_then_succeeds(notion, client):
    notion.scripted = [Resp(429, {"code": "rate_limited", "message": "slow"}, {"Retry-After": "7"})]
    client.request("GET", "/v1/data_sources/ds-1")
    assert any(7 <= s < 7.3 for s in client.sleeps)
    assert len(notion.calls) == 2


def test_429_is_retried_for_writes_too_but_blocked_is_not(notion, client):
    notion.scripted = [Resp(429, {"code": "rate_limited", "message": "slow"}, {"Retry-After": "1"})]
    client.request("POST", "/v1/pages", json={"properties": {"JD": {"rich_text": []}, "Link": {"url": "u"}}})
    assert len(notion.calls) == 2
    notion.scripted = [Resp(429, {"code": "rate_limited", "message": "no",
                                  "additional_data": {"rate_limit_reason": "public_api_request_blocked"}})]
    with pytest.raises(nf.NotionError):
        client.request("GET", "/v1/data_sources/ds-1")


def test_5xx_retried_for_reads_not_for_writes(notion, client):
    notion.scripted = [Resp(503, {"code": "service_unavailable", "message": "x"})]
    client.request("GET", "/v1/data_sources/ds-1")
    assert len(notion.calls) == 2
    notion.calls.clear()
    notion.scripted = [Resp(503, {"code": "service_unavailable", "message": "x"})]
    with pytest.raises(nf.NotionError):
        client.request("POST", "/v1/pages", json={}, idempotent=False)
    assert len(notion.calls) == 1


def test_gives_up_after_max_attempts():
    notion = FakeNotion()
    notion.scripted = [Resp(429, {"code": "rate_limited", "message": "x"}, {"Retry-After": "0"})] * 10
    c = nf.NotionClient("t", "v", session=notion, sleep=lambda s: None, min_interval=0, max_attempts=3)
    with pytest.raises(nf.NotionError) as e:
        c.request("GET", "/v1/x")
    assert e.value.status == 429 and len(notion.calls) == 3


def test_requests_are_paced_to_three_per_second():
    notion, now, sleeps = FakeNotion(), [0.0], []

    def fake_sleep(s):
        sleeps.append(s)
        now[0] += s
    c = nf.NotionClient("t", "v", session=notion, sleep=fake_sleep, clock=lambda: now[0])   # default 0.34 s gap
    for _ in range(4):
        c.request("GET", "/v1/data_sources/ds-1")
    assert all(abs(s - 0.34) < 1e-9 for s in sleeps) and len(sleeps) == 3


def test_network_error_on_a_write_is_not_retried_and_hides_the_token():
    class Boom:
        calls = 0

        def request(self, *a, **k):
            Boom.calls += 1
            raise requests.ConnectionError("https://api.notion.com ... Bearer secret-token")
    c = nf.NotionClient("secret-token", "v", session=Boom(), sleep=lambda s: None, min_interval=0)
    with pytest.raises(nf.NotionError) as e:
        c.request("POST", "/v1/pages", json={}, idempotent=False)
    assert Boom.calls == 1 and "secret-token" not in str(e.value)


def test_headers_carry_version_and_token_but_payload_does_not(notion, client):
    client.request("GET", "/v1/data_sources/ds-1")
    h = notion.requests[0]["headers"]
    assert h["Notion-Version"] == "2026-03-11" and h["Authorization"] == "Bearer secret-token"


def test_preview_shortens_the_jd(feed):
    page, _ = nf.build_page(make_job(jd="y" * 5000), feed, OVERRIDES)
    shown = nf.preview_page(page, "JD")
    text = json.dumps(shown)
    assert "3 items, 5000 chars total" in text and len(text) < 2500
    assert len(page["properties"]["JD"]["rich_text"]) == 3        # the real payload is untouched


# --- secrets and ids never reach output ----------------------------------------------

REAL_LOOKING_ID = "ae49313a-01cd-4f8e-bd1e-2bf377f44383"


def test_error_text_hides_notion_ids():
    err = nf.NotionError(404, "object_not_found", "x", "GET", f"/v1/data_sources/{REAL_LOOKING_ID}")
    assert REAL_LOOKING_ID not in str(err) and "/v1/data_sources/<id>" in str(err)
    assert nf.redact("/v1/pages/ae49313a01cd4f8ebd1e2bf377f44383/properties/p") == "/v1/pages/<id>/properties/p"


def test_unreadable_feed_error_does_not_contain_the_data_source_id(notion):
    client = nf.NotionClient("tok", "v", session=notion, sleep=lambda s: None, min_interval=0)
    notion.scripted = [Resp(404, {"code": "object_not_found", "message": "nope"})]
    with pytest.raises(nf.SchemaError) as e:
        nf.load_feed(client, REAL_LOOKING_ID, NAMES)
    assert REAL_LOOKING_ID not in str(e.value) and "tok" not in str(e.value).split()


def test_retry_warning_hides_ids_and_never_logs_the_token(notion, caplog):
    c = nf.NotionClient("secret-token", "v", session=notion, sleep=lambda s: None, min_interval=0)
    notion.scripted = [Resp(429, {"code": "rate_limited", "message": "x"}, {"Retry-After": "1"})]
    with caplog.at_level("DEBUG"):
        c.request("GET", f"/v1/data_sources/{REAL_LOOKING_ID}")
    assert "retrying" in caplog.text
    assert REAL_LOOKING_ID not in caplog.text and "secret-token" not in caplog.text


def test_preview_hides_the_data_source_id(feed):
    page, _ = nf.build_page(make_job(), feed, OVERRIDES)
    assert nf.preview_page(page, "JD")["parent"]["data_source_id"] == "<hidden>"
    assert page["parent"]["data_source_id"] == "ds-1"        # the real payload is untouched
