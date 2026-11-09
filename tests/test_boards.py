"""Board memory: employers found from posting links, boards that keep failing, watchlist.auto.yaml, the filter audit."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from scanner import audit, boards, discover
from scanner.config import load_config
from scanner.filters import Filters
from scanner.pipeline import run_stream
from scanner.sources import watchlist as wl
from scanner.sources.watchlist import Board

NOW = datetime(2026, 11, 8, 16, 0, tzinfo=timezone.utc)
TODAY = date(2026, 11, 8)


# --- a posting's link names the employer's board ------------------------------------------------

@pytest.mark.parametrize("url,expected", [
    ("https://acme.wd5.myworkdayjobs.com/en-US/Careers/job/Toronto/Dev_JR-1", ("workday", "acme.wd5/Careers")),
    ("https://acme.wd3.myworkdayjobs.com/Ext_Site/job/Remote/Dev_R2", ("workday", "acme.wd3/Ext_Site")),
    ("https://boards.greenhouse.io/hootsuite/jobs/123", ("greenhouse", "hootsuite")),
    ("https://job-boards.greenhouse.io/later/jobs/9", ("greenhouse", "later")),
    ("https://jobs.lever.co/geocomply-2/abc-def", ("lever", "geocomply-2")),
    ("https://jobs.ashbyhq.com/visier/6f1a", ("ashby", "visier")),
    ("https://jobs.smartrecruiters.com/ServiceNow/743999-software-engineer", ("smartrecruiters", "ServiceNow")),
])
def test_board_from_a_posting_link(url, expected):
    b = boards.board_from_url(url)
    assert (b.ats, b.slug) == expected


@pytest.mark.parametrize("url", [
    "", "https://www.example.com/careers/1", "https://pinterestcareers.com/jobs/?gh_jid=123",
    "https://acme.wd5.myworkdayjobs.com/en-US/Careers",                  # the board's home, not a posting
    "https://jobs.lever.co/acme", "https://boards.greenhouse.io/acme", "https://www.jobbank.gc.ca/jobsearch/jobposting/1",
])
def test_links_that_name_no_board_give_none(url):
    assert boards.board_from_url(url) is None


def test_the_pipeline_tap_sees_fresh_canadian_postings_whatever_their_title(tmp_path):
    cfg = load_config()
    store = boards.BoardStore(tmp_path / "boards.json")
    recs = [
        {"title": "Chef", "location": "Toronto, ON", "url": "https://acme.wd5.myworkdayjobs.com/en-US/Careers/job/T/Chef_1"},
        {"title": "Software Developer", "location": "Seattle, WA", "url": "https://jobs.lever.co/usco/1"},
        {"title": "Staff Engineer", "location": "Vancouver, BC", "url": "https://jobs.lever.co/vanco/1"},
    ]

    def fields(r):
        return {"company": "x", "company_name": "", "title": r["title"], "location": r["location"], "url": r["url"],
                "source": "Workday", "first_seen": NOW}

    kept, _, _ = run_stream(iter(recs), fields, Filters.from_config(cfg), NOW, 3, tap=store.tap)
    assert kept == []                                                        # none passes the title filters ...
    assert sorted(boards.key(Board(*k.split("/", 1))) for k in store._harvest) == ["lever/vanco", "workday/acme.wd5/careers"]


# --- a board that keeps failing is left alone, then tried again ---------------------------------------

def test_a_board_is_retried_three_times_then_rests_a_week_then_two_and_works_again_at_once(tmp_path):
    store, b = boards.BoardStore(tmp_path / "b.json"), Board("greenhouse", "gone")
    for day in range(2):
        store.record_read(b, "HTTPError: 404", TODAY + timedelta(days=day))
        assert store.due(b, TODAY + timedelta(days=day + 1))                 # failures 1 and 2: still tried
    store.record_read(b, "HTTPError: 404", TODAY + timedelta(days=2))        # the third in a row
    assert not store.due(b, TODAY + timedelta(days=3)) and store.due(b, TODAY + timedelta(days=9))
    store.record_read(b, "HTTPError: 404", TODAY + timedelta(days=9))        # still dead on the retry: twice as long
    assert not store.due(b, TODAY + timedelta(days=22)) and store.due(b, TODAY + timedelta(days=23))
    assert [k for k, _ in store.dormant(TODAY + timedelta(days=10))] == ["greenhouse/gone"]
    store.record_read(b, "", TODAY + timedelta(days=23))
    assert store.due(b, TODAY + timedelta(days=23)) and store.dormant(TODAY + timedelta(days=24)) == []


def test_a_board_that_always_works_leaves_no_trace(tmp_path):
    store = boards.BoardStore(tmp_path / "b.json")
    store.record_read(Board("lever", "fine"), "", TODAY)
    assert store.health == {}


def test_the_backoff_stops_at_four_weeks():
    store, b = boards.BoardStore("/nonexistent/b.json"), Board("ashby", "x")
    for i in range(12):
        store.record_read(b, "boom", TODAY)
    assert store.health["ashby/x"]["next_try"] == (TODAY + timedelta(days=boards.MAX_BACKOFF_DAYS)).isoformat()


def test_state_survives_a_save_and_a_load(tmp_path):
    p = tmp_path / "s" / "boards.json"
    store = boards.BoardStore(p)
    store.record_read(Board("ashby", "x"), "boom", TODAY)
    store.save()
    assert boards.BoardStore(p).health["ashby/x"]["fails"] == 1


# --- candidates become watchlist.auto.yaml lines ------------------------------------------------------

def tap_many(store, url, n, name=""):
    for _ in range(n):
        store.tap({"url": url, "company_name": name})


def test_candidates_are_the_busiest_unknown_boards_without_added_or_given_up_ones(tmp_path):
    store = boards.BoardStore(tmp_path / "b.json")
    tap_many(store, "https://jobs.lever.co/busy/1", 5, "Busy Co")
    tap_many(store, "https://jobs.lever.co/quiet/1", 1)
    tap_many(store, "https://jobs.lever.co/known/1", 9)
    tap_many(store, "https://jobs.lever.co/added/1", 7)
    tap_many(store, "https://jobs.lever.co/dead/1", 6)
    store.found["lever/added"] = {"status": "added"}
    store.found["lever/dead"] = {"status": "dead"}
    got = store.candidates({"lever/known"}, TODAY, 10)
    assert [(c.board.slug, c.name, c.count) for c in got] == [("busy", "Busy Co", 5), ("quiet", "quiet", 1)]
    assert len(store.candidates(set(), TODAY, 1)) == 1


def test_a_workday_candidate_without_a_name_is_named_after_its_tenant(tmp_path):
    store = boards.BoardStore(tmp_path / "b.json")
    tap_many(store, "https://enbridge.wd3.myworkdayjobs.com/en-US/Enbridge/job/Calgary/Dev_R1", 2)
    assert store.candidates(set(), TODAY, 5)[0].name == "enbridge"


def test_a_candidate_that_cannot_be_read_is_given_up_after_three_probes(tmp_path):
    store = boards.BoardStore(tmp_path / "b.json")
    tap_many(store, "https://jobs.lever.co/flaky/1", 1)
    for i in range(boards.MAX_PROBES):
        (found,) = store.candidates(set(), TODAY, 5)
        store.record_probe(found, "error", TODAY, error="403")
    assert store.found["lever/flaky"]["status"] == "dead" and store.candidates(set(), TODAY, 5) == []


class FakeClient:
    """get_json answers per board URL; an Exception value is raised."""

    def __init__(self, answers):
        self.answers = answers

    def get_json(self, url, ats_label):
        ans = self.answers[url]
        if isinstance(ans, Exception):
            raise ans
        return ans


def lever_posting(title, where):
    return {"text": title, "hostedUrl": "https://jobs.lever.co/x/1", "createdAt": int(NOW.timestamp() * 1000),
            "categories": {"location": where}}


def test_promote_adds_boards_with_canadian_postings_and_remembers_the_rest(tmp_path):
    cfg = load_config()
    store = boards.BoardStore(tmp_path / "b.json")
    for slug in ("canadian", "american", "broken"):
        tap_many(store, f"https://jobs.lever.co/{slug}/1", 3, slug.title())
    client = FakeClient({
        Board("lever", "canadian").url: [lever_posting("Software Developer", "Toronto, ON"), lever_posting("Chef", "Toronto, ON")],
        Board("lever", "american").url: [lever_posting("Software Developer", "Austin, TX")],
        Board("lever", "broken").url: RuntimeError("HTTP 500"),
    })
    auto = tmp_path / "watchlist.auto.yaml"
    added = boards.promote(store, client, Filters.from_config(cfg), NOW, auto_path=auto, limit=10, auto_max=50, min_candidates=1)
    assert [(f.board.slug, h.canadian, h.candidates) for f, h in added] == [("canadian", 2, 1)]
    assert [b.slug for b in wl.load_boards(auto)] == ["canadian"]
    assert store.found["lever/canadian"]["status"] == "added"
    assert store.found["lever/american"]["status"] == "empty" and store.found["lever/broken"]["status"] == "error"
    assert "Canadian" in auto.read_text() and "2 Canadian postings" in auto.read_text()


def test_promote_stops_at_the_limit_the_room_and_in_a_dry_run_writes_nothing(tmp_path):
    cfg = load_config()
    store = boards.BoardStore(tmp_path / "b.json")
    answers = {}
    for slug in "abc":
        tap_many(store, f"https://jobs.lever.co/{slug}/1", 3)
        answers[Board("lever", slug).url] = [lever_posting("Software Developer", "Toronto, ON")]
    auto = tmp_path / "auto.yaml"
    dry = boards.promote(store, FakeClient(answers), Filters.from_config(cfg), NOW, auto_path=auto, limit=10, auto_max=50, dry_run=True,
                         min_candidates=1)
    assert len(dry) == 3 and not auto.exists() and store.found == {}
    real = boards.promote(store, FakeClient(answers), Filters.from_config(cfg), NOW, auto_path=auto, limit=10, auto_max=2, min_candidates=1)
    assert len(real) == 2 and boards.auto_count(auto) == 2                   # room for 2 only


def test_the_auto_file_reads_next_to_the_hand_kept_one_and_the_hand_kept_line_wins(tmp_path):
    hand, auto = tmp_path / "w.yaml", tmp_path / "w.auto.yaml"
    hand.write_text('boards:\n  - {ats: lever, slug: Same, name: "Hand"}\n', encoding="utf-8")
    auto.write_text(boards.AUTO_HEADER + '  - {ats: lever, slug: same, name: "Auto"}\n  - {ats: ashby, slug: new, name: "New"}\n',
                    encoding="utf-8")
    got = wl.load_boards(hand, auto, tmp_path / "missing.yaml")
    assert [(b.ats, b.slug, b.name) for b in got] == [("lever", "Same", "Hand"), ("ashby", "new", "New")]


def test_a_repeat_inside_one_file_is_still_an_error(tmp_path):
    p = tmp_path / "w.yaml"
    p.write_text("boards:\n  - {ats: lever, slug: a}\n  - {ats: lever, slug: A}\n", encoding="utf-8")
    with pytest.raises(wl.WatchlistError, match="twice"):
        wl.load_boards(p)


def test_the_report_lists_added_resting_and_given_up_boards(tmp_path):
    store = boards.BoardStore(tmp_path / "b.json")
    for _ in range(3):
        store.record_read(Board("greenhouse", "gone"), "HTTPError: 404", TODAY)
    store.found["lever/nope"] = {"status": "dead", "probes": 3, "name": "Nope", "error": "403"}
    text = boards.render(store, [], TODAY)
    assert "## Resting (1)" in text and "greenhouse/gone" in text and "lever/nope" in text and "dead after 3 probes" in text


# --- Workday career sites come from the tenant's robots.txt ----------------------------------------------

class RobotsClient:
    def __init__(self, robots):
        self.robots, self.urls = robots, []

    def get(self, url, ats_label):
        self.urls.append(url)
        if url not in self.robots:
            raise RuntimeError("HTTP 422")
        return SimpleNamespace(text=self.robots[url])


def test_workday_sites_are_read_from_allow_disallow_and_sitemap_lines():
    robots = {"https://gm.wd5.myworkdayjobs.com/robots.txt": (
        "Sitemap: https://gm.wd5.myworkdayjobs.com/Careers_GM/siteMap.xml\n"
        "Sitemap: https://gm.wd5.myworkdayjobs.com/Careers_GM_Private/siteMap.xml\n\nUser-agent: *\n"
        "Allow: /Careers_GM/\nAllow: /Careers_TEI/\nDisallow: /refreshFacet/\nDisallow: /ExternalPrivatePosting/")}
    client = RobotsClient(robots)
    assert discover.workday_sites("gm", client) == [("wd5", "Careers_GM"), ("wd5", "Careers_TEI")]
    assert client.urls[-1].startswith("https://gm.wd5") and "wd3" in client.urls[1]       # wd1, wd3, then wd5 found: stops


def test_a_tenant_that_does_not_exist_has_no_sites():
    assert discover.workday_sites("nobody", RobotsClient({})) == []


# --- the filter audit -------------------------------------------------------------------------------------

def fields(title, where="Toronto, ON", company="acme", name="Acme", when=NOW):
    return {"company": company, "company_name": name, "title": title, "location": where, "url": "u", "source": "x",
            "first_seen": when}


def test_exclusion_reasons_name_the_word_that_removed_the_title():
    f = Filters.from_config(load_config())
    assert f.exclusion_reason("Senior Software Developer") == "senior"
    assert f.exclusion_reason("Software Developer") is None
    assert set(f.all_exclusion_reasons("Lead Software Architect")) == {"lead", "architect"}
    assert f.exclusion_reason("Directeur logiciel") == "directeur"


def test_classify_gives_one_verdict_per_title():
    f = Filters.from_config(load_config())
    assert audit.classify("Software Developer", f) == ("kept", [])
    assert audit.classify("Senior Software Developer", f)[0] == "excluded"
    assert audit.classify("Barista", f) == ("no_tech_word", [])
    assert audit.classify("Software Developer Intern", f)[0] == "student"


def test_the_audit_counts_the_funnel_and_the_rules_that_kill_alone():
    a = audit.Audit(Filters.from_config(load_config()), NOW, 14)
    for title in ("Software Developer", "Senior Software Developer", "Lead Software Developer", "Expert Data Analyst",
                  "Barista", "Lead Software Architect"):
        a.add(fields(title, company=title))
    a.add(fields("Software Developer", where="Austin, TX", company="us"))                       # not Canadian
    a.add(fields("Software Developer", when=NOW - timedelta(days=30), company="old"))           # not fresh
    a.add(fields("Software Developer", where="Montreal, QC", company="qc"))                     # Quebec only
    fn = a.funnel
    assert (fn["records"], fn["fresh"], fn["canadian"]) == (9, 8, 6)
    assert (fn["-> kept"], fn["-> excluded"], fn["-> no_tech_word"]) == (1, 4, 1)
    assert a.by_rule["lead"]["all"] == 2 and a.by_rule["lead"]["alone"] == 1
    assert a.by_rule["senior"]["alone"] == 1


def test_near_misses_are_entry_level_titles_the_filters_dropped():
    a = audit.Audit(Filters.from_config(load_config()), NOW, 14)
    a.add(fields("Junior Plumber", company="a"))                      # entry word, no tech word, nothing technical
    a.add(fields("Junior Systems Technologist", company="e"))        # entry word, no tech-role word, but it sounds technical
    a.add(fields("Associate Software Developer Lead", company="b"))  # entry word, excluded by "lead"
    a.add(fields("Senior Associate Developer", company="c"))          # senior: right to lose, not a near miss
    a.add(fields("Software Developer", company="d"))                  # kept
    assert sorted(t for t, *_ in a.near) == ["Associate Software Developer Lead", "Junior Systems Technologist"]
    text = a.render()
    assert "Near misses (2)" in text and "Funnel" in text and "| lead |" in text


def test_unknown_title_words_are_ranked_with_examples():
    a = audit.Audit(Filters.from_config(load_config()), NOW, 14)
    for i in range(3):
        a.add(fields("Firmware Technologist", company=str(i)))
    a.add(fields("Toronto Barista", company="z"))
    assert a.unknown_words["firmware"] == 3 and "toronto" not in a.unknown_words
    assert "| firmware | 3 |" in a.render()


# --- the ranking favours the newest postings -----------------------------------------------------------------

def test_a_posting_from_today_outranks_an_identical_old_one_and_the_bonus_fades_to_nothing():
    from scanner import enrich
    from scanner.models import Job
    cfg = load_config()
    boost = __import__("re").compile(cfg["scoring"]["bc_or_remote_pattern"])
    now = datetime.now(timezone.utc)

    def score(age_days):
        job = Job("acme", "Software Developer", "Toronto, ON", "u", "Greenhouse", now - timedelta(days=age_days))
        job.fit_pct, job.jd_status = 70.0, "ok"
        return enrich._score(job, cfg, boost)

    fresh = cfg["scoring"]["boost"]["fresh"]
    assert score(0) - score(fresh["days"]) == pytest.approx(fresh["bonus"], abs=0.2)
    assert score(fresh["days"]) == score(9) == 70.0                           # no bonus, no penalty, past the fade
    assert score(0) > score(1) > score(2) > score(fresh["days"])


def test_only_boards_with_a_title_the_filters_accept_are_candidates_and_the_most_useful_come_first(tmp_path):
    f = Filters.from_config(load_config())
    store = boards.BoardStore(tmp_path / "b.json", relevant=lambda t: f.title_tier(t) is not None and f.title_not_excluded(t))
    for _ in range(50):
        store.tap({"url": "https://jobs.lever.co/hospital/1", "title": "Registered Nurse"})
    for _ in range(2):
        store.tap({"url": "https://jobs.lever.co/techco/1", "title": "Software Developer"})
    store.tap({"url": "https://jobs.lever.co/bank/1", "title": "Data Analyst"})
    store.tap({"url": "https://jobs.lever.co/bank/2", "title": "Senior Data Analyst"})
    assert [(c.board.slug, c.count) for c in store.candidates(set(), TODAY, 10)] == [("techco", 2), ("bank", 1)]


def test_a_board_whose_canadian_jobs_are_all_filtered_titles_is_not_added(tmp_path):
    cfg = load_config()
    store = boards.BoardStore(tmp_path / "b.json")
    tap_many(store, "https://jobs.lever.co/clinic/1", 2)
    client = FakeClient({Board("lever", "clinic").url: [lever_posting("Registered Nurse", "Toronto, ON")]})
    added = boards.promote(store, client, Filters.from_config(cfg), NOW, auto_path=tmp_path / "a.yaml", limit=5, auto_max=5)
    assert added == [] and store.found["lever/clinic"]["status"] == "empty"


def test_aggregators_and_boards_with_too_few_useful_jobs_are_not_added(tmp_path):
    import re
    cfg = load_config()
    store = boards.BoardStore(tmp_path / "b.json")
    for slug in ("jobgether", "onejob", "twojobs"):
        tap_many(store, f"https://jobs.lever.co/{slug}/1", 3, slug.title())
    dev = lever_posting("Software Developer", "Toronto, ON")
    client = FakeClient({Board("lever", "jobgether").url: [dev, dev], Board("lever", "onejob").url: [dev],
                         Board("lever", "twojobs").url: [dev, lever_posting("Data Analyst", "Ottawa, ON")]})
    added = boards.promote(store, client, Filters.from_config(cfg), NOW, auto_path=tmp_path / "a.yaml", limit=5, auto_max=5,
                           skip=re.compile("jobgether|survey", re.IGNORECASE))
    assert [f.board.slug for f, _ in added] == ["twojobs"]
    assert "lever/jobgether" not in store.found and store.found["lever/onejob"]["status"] == "empty"


def test_a_board_full_of_n_locations_rows_does_not_count_as_canadian():
    f = Filters.from_config(load_config())
    recs = [{"title": "Software Developer", "location": "2 Locations"}] * 20 + [{"title": "Software Developer", "location": "Toronto, ON"}]
    hit = discover._summarise(Board("workday", "x.wd5/y"), recs, f, NOW)
    assert (hit.total, hit.canadian, hit.candidates) == (21, 1, 1)
