"""The job-site readers (YC, Built In, Bay Street Oracle, Hacker News) on trimmed copies of the real pages."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from scanner import cli
from scanner.config import load_config
from scanner.dedupe import dedupe
from scanner.filters import Filters
from scanner.models import Job
from scanner.sources import ats, baystreet, builtin, hn, yc
from scanner.sources.listing import SITE_SOURCES

FIX = Path(__file__).parent / "fixtures" / "sites"
NOW = datetime(2026, 11, 5, 18, 0, tzinfo=timezone.utc)


def read(name):
    return (FIX / name).read_text(encoding="utf-8")


class Page:
    def __init__(self, text="", data=None, status=200):
        self.text, self._data, self.status_code = text, data, status

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)


class FakeClient:
    """Answers by URL (exact or by substring); records every request."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def _find(self, url):
        self.calls.append(url)
        if url in self.answers:
            return self.answers[url]
        for key, value in self.answers.items():
            if key in url:
                return value
        raise AssertionError(f"unexpected request {url}")

    def get(self, url, ats, **kw):
        page = self._find(url)
        page.raise_for_status()
        return page

    def get_json(self, url, ats, **kw):
        return self._find(url).json()


# --- YC ------------------------------------------------------------------------------------------

def test_yc_list():
    recs = yc.parse_list(read("yc_list.html"), NOW)
    assert len(recs) == 3
    gale = next(r for r in recs if r["company"] == "gale")
    assert gale["url"] == "https://www.ycombinator.com/companies/gale/jobs/9hcjXdD-software-engineer"
    assert gale["source"] == "YC" and gale["company_name"] == "Gale" and gale["min_experience"] == "Any (new grads ok)"
    assert gale["first_seen"] == NOW - timedelta(days=21) and "Vancouver" in gale["location"]


def test_yc_experience_filter_uses_the_employers_own_minimum():
    recs = {r["company"]: r for r in yc.parse_list(read("yc_list.html"), NOW)}
    assert yc.experience_ok(recs["gale"], 3)                       # "Any (new grads ok)"
    assert not yc.experience_ok(recs["amplitude"], 3)              # "5+ years"
    assert yc.experience_ok({"min_experience": "2+ years"}, 3) and not yc.experience_ok({"min_experience": "3+ years"}, 3)


def test_yc_detail_has_description_and_skills_but_no_person_data():
    text, company, where = yc.parse_detail(read("yc_job.html"))
    assert company == "Gale" and where == ["Vancouver, BC, CA", "Vancouver, British Columbia, CA"]
    assert "Skills: PostgreSQL, React, TypeScript, Terraform" in text
    assert "**" not in text and "###" not in text
    assert "Experience:" not in text          # "Any (new grads ok)" is not a requirement: it must not read as a new-grad signal
    assert "avatar" not in text.lower() and "X-Amz" not in text


def test_yc_page_without_data_is_an_error_not_an_empty_list():
    with pytest.raises(yc.YcPageError):
        yc.parse_list("<html><body>nothing here</body></html>", NOW)


def test_yc_fetch_reads_each_page_once_and_dedupes():
    page = Page(read("yc_list.html"))
    client = FakeClient({"/jobs/location/canada": page, "/jobs/location/vancouver": page})
    recs = yc.fetch(["canada", "vancouver"], client, NOW)
    assert len(recs) == 3 and len(client.calls) == 2


# --- Built In -----------------------------------------------------------------------------------

BASE = "https://builtinvancouver.org"


def test_builtin_cards():
    recs = builtin.parse_cards(read("builtin_list.html"), BASE, NOW)
    assert len(recs) == 3
    first = recs[0]
    assert first["source"] == "Built In" and first["url"].startswith(BASE + "/job/") and first["title"]
    assert first["company_name"] and first["location"] and first["first_seen"] is not None
    assert all(r["first_seen"] <= NOW for r in recs)


def test_builtin_markup_change_is_loud():
    broken = '<div id="job-card-1" data-id="job-card"><span>no title, no company</span></div>'
    with pytest.raises(builtin.BuiltInPageError):
        builtin.parse_cards(broken, BASE, NOW)
    assert builtin.parse_cards("<html>no cards at all</html>", BASE, NOW) == []       # an empty page is just empty


def test_builtin_detail_takes_only_its_own_body():
    text = builtin.parse_detail(read("builtin_job.html"), "12051723")
    assert text.startswith("The role") and "not this one" not in text
    assert builtin.parse_detail(read("builtin_job.html"), "404") == ""
    assert builtin.job_id(f"{BASE}/job/engineer-payments/12051723") == "12051723" and builtin.job_id("https://x/y") == ""


def test_builtin_walk_stops_at_a_page_with_nothing_fresh():
    page1 = read("builtin_list.html")
    stale = page1.replace("2 Days Ago", "2 Months Ago").replace("Yesterday", "3 Months Ago").replace("Hours Ago", "Months Ago")
    stale = stale.replace("Minutes Ago", "Months Ago").replace("Days Ago", "Months Ago").replace("Today", "3 Months Ago")
    client = FakeClient({"/jobs/x/entry-level?page=2": Page(stale), "/jobs/x/entry-level": Page(page1)})
    builtin.fetch(BASE, ["/jobs/x/entry-level"], 5, client, NOW, window_days=3)
    assert client.calls[-1].endswith("?page=2") and len(client.calls) == 2         # page 3 was never asked for


def test_builtin_walk_is_capped():
    page = Page(read("builtin_list.html"))
    client = FakeClient({"/jobs/": page})
    builtin.fetch(BASE, ["/jobs/a", "/jobs/b"], 2, client, NOW, window_days=10 ** 6)
    assert len(client.calls) == 4                                                   # 2 paths x 2 pages


# --- Bay Street Oracle --------------------------------------------------------------------------

def test_baystreet_sitemap_has_postings_not_categories():
    urls = baystreet.posting_urls(read("bso_sitemap.xml"))
    assert len(urls) == 4 and all("--" in u for u in urls)
    assert "quantitative researcher" in baystreet.slug_words(next(u for u in urls if "quantitative" in u))


def test_baystreet_detail():
    rec = baystreet.parse_detail(read("bso_job.html"), NOW)
    assert rec["company"] == "BMO Capital Markets" and rec["location"] == "Toronto, ON"
    assert rec["first_seen"] == datetime(2026, 8, 25, tzinfo=timezone.utc)
    assert rec["apply_url"].startswith("https://bmo.wd3.myworkdayjobs.com/")          # the employer's, not the site's
    assert rec["description"] and "<p>" not in rec["description"]
    assert baystreet.parse_detail("<html>no posting</html>", NOW) is None


def test_baystreet_fetch_filters_by_url_words_and_prefers_a_readable_ats_link():
    urls = baystreet.posting_urls(read("bso_sitemap.xml"))
    answers = {"/jobs/sitemap.xml": Page(read("bso_sitemap.xml"))}
    answers.update({u: Page(read("bso_job.html")) for u in urls})
    client = FakeClient(answers)
    recs = baystreet.fetch("https://www.baystreetoracle.ca/jobs/sitemap.xml", client, NOW,
                           keep=lambda words: "quantitative" in words or "mergers" in words,
                           supported=lambda u: ats.detect_ats(u) in ats.SUPPORTED)
    assert len(recs) == 2                                       # the other two postings were not requested
    assert all(r["url"].startswith("https://bmo.wd3.myworkdayjobs.com/") for r in recs)
    nothing_supported = baystreet.fetch("https://www.baystreetoracle.ca/jobs/sitemap.xml", client, NOW,
                                        keep=lambda w: "mergers" in w, supported=lambda u: False)
    assert nothing_supported[0]["url"].startswith("https://www.baystreetoracle.ca/jobs/")


def test_baystreet_a_gone_posting_does_not_lose_the_others():
    urls = baystreet.posting_urls(read("bso_sitemap.xml"))
    answers = {"/jobs/sitemap.xml": Page(read("bso_sitemap.xml"))}
    answers.update({u: Page(read("bso_job.html")) for u in urls})
    answers[urls[0]] = Page(status=404)
    recs = baystreet.fetch("https://www.baystreetoracle.ca/jobs/sitemap.xml", FakeClient(answers), NOW,
                           keep=lambda w: True, supported=lambda u: False)
    assert len(recs) == len(urls) - 1
    answers[urls[1]] = Page(status=500)                          # a server error is not "gone": the source fails
    with pytest.raises(requests.HTTPError):
        baystreet.fetch("https://www.baystreetoracle.ca/jobs/sitemap.xml", FakeClient(answers), NOW,
                        keep=lambda w: True, supported=lambda u: False)


# --- Hacker News --------------------------------------------------------------------------------

def comments():
    return json.loads(read("hn_comments.json"))


def test_hn_comment_headers():
    by_id = {c["objectID"]: hn.parse_comment(c) for c in comments()}
    aloe = next(r for r in by_id.values() if r and r["company"] == "Aloe")
    assert aloe["title"] == "AI Research + AI Engineering roles" and "Vancouver" in aloe["location"]
    assert aloe["url"].startswith("https://news.ycombinator.com/item?id=")
    assert next(r for r in by_id.values() if r and r["company"] == "SadServers")["title"] == "Software Engineer"
    assert by_id["1"] is None                                     # Austin, TX
    assert by_id["2"] is None                                     # no "Company | Role | Location" header
    assert all(by_id[c["objectID"]] is None for c in comments() if c["comment_text"].startswith("Matcha.fm"))   # no role


def test_hn_picks_the_hiring_thread_not_the_job_seekers_thread():
    hits = {"hits": [{"objectID": "9", "title": "Ask HN: Who wants to be hired? (November 2026)"},
                     {"objectID": "7", "title": "Ask HN: Who is hiring? (November 2026)"}]}
    client = FakeClient({"author_whoishiring": Page(data=hits)})
    assert hn.latest_thread(client)["objectID"] == "7"
    assert hn.latest_thread(FakeClient({"author_whoishiring": Page(data={"hits": []})})) is None


def test_hn_fetch_reads_top_level_comments_of_the_thread_only():
    hits = {"hits": [{"objectID": "7", "title": "Ask HN: Who is hiring? (November 2026)"}]}
    thread = [dict(c, parent_id=7) for c in comments()[:2]] + [dict(comments()[1], objectID="reply", parent_id=555)]
    client = FakeClient({"author_whoishiring": Page(data=hits),
                         "tags=comment,story_7": Page(data={"hits": thread, "nbPages": 1})})
    recs = hn.fetch(client, NOW)
    assert [r["company"] for r in recs] == ["Aloe"]          # Matcha.fm has no role; the reply (parent 555) is not a posting
    assert not any(r["url"].endswith("reply") for r in recs)


# --- how a job from a site is found and described -------------------------------------------------

@pytest.mark.parametrize("url, site", [
    ("https://www.ycombinator.com/companies/gale/jobs/9hcjXdD-software-engineer", "YC"),
    ("https://news.ycombinator.com/item?id=50348566", "HN"),
    ("https://builtinvancouver.org/job/engineer-payments/12051723", "Built In"),
    ("https://www.baystreetoracle.ca/jobs/x-analyst-toronto--abc", "Bay Street Oracle"),
])
def test_detect_and_locate(url, site):
    assert ats.detect_ats(url) == site and site in ats.SITES
    job = Job(company="c", title="t", location="l", url=url, source=site, first_seen=NOW)
    assert ats.locate(job).ats == site


def test_locate_special_cases():
    job = lambda u: Job(company="c", title="t", location="l", url=u, source="x", first_seen=NOW)
    assert ats.locate(job("https://news.ycombinator.com/item?id=77")).api_url == "https://hn.algolia.com/api/v1/items/77"
    assert ats.locate(job("https://news.ycombinator.com/item?id=abc")) is None
    assert ats.locate(job("https://www.ycombinator.com/companies/gale")) is None          # a company page is not a posting


def test_fetch_full_for_each_site():
    yc_url = "https://www.ycombinator.com/companies/gale/jobs/9hcjXdD-software-engineer"
    d = ats.fetch_full(ats.AtsRef("YC", yc_url), FakeClient({yc_url: Page(read("yc_job.html"))}))
    assert d.company == "Gale" and "PostgreSQL" in d.text and d.locations[0].startswith("Vancouver")
    bi_url = f"{BASE}/job/engineer-payments/12051723"
    d = ats.fetch_full(ats.AtsRef("Built In", bi_url), FakeClient({bi_url: Page(read("builtin_job.html"))}))
    assert d.text.startswith("The role")
    bso_url = "https://www.baystreetoracle.ca/jobs/x--1"
    d = ats.fetch_full(ats.AtsRef("Bay Street Oracle", bso_url), FakeClient({bso_url: Page(read("bso_job.html"))}))
    assert d.company == "BMO Capital Markets" and d.locations == ["Toronto, ON"]
    with pytest.raises(ats.NotFound):
        ats.fetch_full(ats.AtsRef("Bay Street Oracle", bso_url), FakeClient({bso_url: Page("<html>gone</html>")}))
    item = "https://hn.algolia.com/api/v1/items/77"
    d = ats.fetch_full(ats.AtsRef("HN", item), FakeClient({item: Page(data={"text": "Aloe | AI roles | Vancouver<p>We build things."})}))
    assert d.company == "Aloe" and "We build things." in d.text


# --- duplicates across sources --------------------------------------------------------------------

def mk(source, company="xero", title="Engineer - Payments", location="Vancouver", url="https://a/1"):
    return Job(company=company, title=title, location=location, url=url, source=source, first_seen=NOW, sources={source})


def test_a_site_posting_is_absorbed_by_its_ats_twin_whatever_the_city_string():
    ats_row = mk("Ashby", location="CAN: Vancouver (333 Seymour St)", url="https://jobs.ashbyhq.com/xero/1")
    site_row = mk("Built In", location="Seymour Heights, North Vancouver, BC, CAN", url="https://builtinvancouver.org/job/x/9")
    for order in ([ats_row, site_row], [site_row, ats_row]):
        out, merges = dedupe(order)
        assert [j.source for j in out] == ["Ashby"] and out[0].sources == {"Ashby", "Built In"}
        assert merges["fuzzy"] == 1


def test_a_site_posting_without_an_ats_twin_stays():
    other = mk("Ashby", company="stripe", title="Data Scientist", url="https://jobs.ashbyhq.com/stripe/1")
    out, _ = dedupe([other, mk("Built In", url="https://builtinvancouver.org/job/x/9"),
                     mk("Built In", title="Marketing Coordinator", url="https://builtinvancouver.org/job/y/10")])
    assert sorted(j.source for j in out) == ["Ashby", "Built In", "Built In"]


def test_the_same_url_from_the_dataset_and_a_site_keeps_the_ats_row():
    url = "https://bmo.wd3.myworkdayjobs.com/External/job/Toronto/Analyst_R1"
    for order in ([mk("Bay Street Oracle", url=url), mk("Workday", url=url)], [mk("Workday", url=url), mk("Bay Street Oracle", url=url)]):
        out, _ = dedupe(order)
        assert len(out) == 1 and out[0].source == "Workday" and out[0].sources == {"Workday", "Bay Street Oracle"}


def test_every_site_label_is_known_to_the_feed():
    from scanner import notion_feed as nf
    assert set(SITE_SOURCES) == set(nf.SITE_SOURCE_OPTIONS) == set(ats.SITES)
    assert set(SITE_SOURCES).isdisjoint(nf.SOURCE_OPTIONS)        # not required of an existing Feed


# --- a site that is down or changed does not stop the run ------------------------------------------

def test_a_failing_site_is_reported_and_the_others_still_run(monkeypatch, capsys):
    cfg = load_config()
    for name in ("feashliaa", "simplify", "watchlist", "baystreet"):
        cfg["sources"][name]["enabled"] = False
    for name in ("builtin", "jobbank", "bcgov", "canadalist"):
        cfg["sources"][name]["enabled"] = False
    monkeypatch.setattr(yc, "fetch", lambda *a, **k: (_ for _ in ()).throw(yc.YcPageError("no data-page JSON in the page")))
    rec = {"company": "acme", "company_name": "Acme", "title": "Software Developer", "location": "Toronto, ON",
           "url": "https://news.ycombinator.com/item?id=1", "source": "HN", "first_seen": NOW - timedelta(hours=3)}
    monkeypatch.setattr(hn, "fetch", lambda *a, **k: [rec])
    jobs, held, blocks, _ = cli.collect(cfg, SimpleNamespace(), Filters.from_config(cfg), NOW, 3)
    out = capsys.readouterr().out
    assert "WARNING YC: not read (YcPageError" in out and "HN: 1 postings" in out
    assert [j.title for j in jobs] == ["Software Developer"] and "HN" in blocks and "YC" not in blocks
