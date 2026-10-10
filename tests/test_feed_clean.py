from datetime import datetime, timezone

from scanner import feed_clean as FC
from scanner import notion_feed
from scanner.cluster import ClusterResolver
from scanner.config import load_config
from scanner.filters import Filters
from scanner.score import Scorer

CFG = load_config()
FILTERS = Filters.from_config(CFG)
SCORER = Scorer.load()
CLUSTERS = ClusterResolver(CFG)
GOOD_JD = "Python, SQL, Docker and Git. 1+ year of experience."


def item(title="Software Engineer", company="Acme", jd=GOOD_JD, link="https://boards.greenhouse.io/acme/jobs/1",
         interested=False, apply=False, signals=(), source="Greenhouse", page="p1"):
    return FC.FeedItem(page, title, company, link, source, jd, list(signals), interested, apply)


def verdict(it, location="Toronto, ON"):
    return FC.evaluate(it, location, CFG, FILTERS, SCORER, CLUSTERS)


def test_clean_rows_pass():
    assert verdict(item()).rule is None


def test_rules_in_the_order_a_new_job_meets_them():
    assert verdict(item(company="mthree Recruiting Portal")).rule == FC.RULE_COMPANY
    assert verdict(item(company="Lifted Solutions")).rule == FC.RULE_COMPANY
    assert verdict(item(title="Développeur de logiciels / Software Developer")).rule == FC.RULE_FRENCH_TITLE
    assert verdict(item(title="Directeur technique - Cloud natif, IA et DevOps")).rule == FC.RULE_TITLE
    assert verdict(item(title="Grafana - Expert Observability Engineer")).rule == FC.RULE_TITLE
    assert verdict(item(title="Data Specialist")).rule == FC.RULE_TITLE
    assert verdict(item(), "Montréal, QC").rule == FC.RULE_QUEBEC
    assert verdict(item(jd="Bilingual (English/French) is required. " + GOOD_JD)).rule == "requires_french"
    assert verdict(item(jd="Python, SQL, Docker, Git. 3+ years of experience.")).rule == "experience_3plus"
    assert verdict(item(jd="Python, SQL, Docker, Git. Five years of experience.")).rule == "experience_3plus"


def test_two_year_rows_need_a_fit_and_internships_to_count():
    ok = "Python, SQL, Docker, Git. 2 years of experience."
    assert verdict(item(jd=ok)).rule is None
    assert verdict(item(jd=ok + " Internships do not count.")).rule == "two_years_excludes_internships"
    assert verdict(item(jd="Python, SQL, Docker. 2 years of experience.")).rule == "two_years_no_fit"
    assert verdict(item(jd="Python. Java, Kafka, Redis, Golang. 2 years of experience.")).rule == "two_years_low_fit"


def test_location_failures_are_reported_not_ruled():
    v = verdict(item(), "Austin, TX")
    assert v.rule is None and v.location_status == "not_canada"
    rep = FC.CleanReport([v, verdict(item(page="p2")), verdict(item(page="p3"), "")])
    assert rep.location_failures() == [v]
    assert len(rep.location_unknown()) == 1                    # no source record, so nothing to check


def test_workday_multi_location_rows_use_the_detail_list():
    it = item(source="Workday", link="https://acme.wd1.myworkdayjobs.com/ext/job/Dallas-TX/x_R1")
    qc = FC.evaluate(it, "2 Locations", CFG, FILTERS, SCORER, CLUSTERS, ["Montréal, Quebec", "Laval, Quebec"])
    assert qc.rule == "location_quebec_only"
    ok = FC.evaluate(it, "2 Locations", CFG, FILTERS, SCORER, CLUSTERS, ["Dallas, TX", "Toronto, Ontario"])
    assert ok.rule is None and ok.location == "Toronto, Ontario"
    unknown = FC.evaluate(it, "2 Locations", CFG, FILTERS, SCORER, CLUSTERS, [])      # cannot resolve: keep
    assert unknown.rule is None


def test_rows_with_a_ticked_or_unreadable_checkbox_are_never_trashed():
    bad = [item(title="Data Specialist", page="a"),
           item(title="Data Specialist", page="b", interested=True),
           item(title="Data Specialist", page="c", apply=True),
           item(title="Data Specialist", page="d", interested=None)]
    rep = FC.CleanReport([verdict(i) for i in bad])
    assert [v.item.page_id for v in rep.trashable()] == ["a"]
    assert [v.item.page_id for v in rep.protected()] == ["b", "c", "d"]


class Notion:
    """Scripted session for the Notion client: one query page, then records PATCH calls."""

    def __init__(self, pages):
        self.pages, self.patched = pages, []

    def request(self, method, url, headers=None, json=None, params=None, timeout=None):
        path = url.split("api.notion.com", 1)[1]
        resp = type("R", (), {"status_code": 200, "headers": {}, "json": None})()
        if method == "POST" and path.endswith("/query"):
            resp.json = lambda: {"results": self.pages, "has_more": False}
        elif method == "PATCH":
            self.patched.append((path, json))
            resp.json = lambda: {}
        else:
            raise AssertionError((method, path))
        return resp


def page(pid, title, company, link, jd, interested=False, apply=False, signals=()):
    rt = lambda s: [{"type": "text", "plain_text": s, "text": {"content": s}}]
    return {"id": pid, "properties": {
        "Name": {"title": rt(title)}, "Company": {"rich_text": rt(company)}, "Link": {"url": link},
        "Source": {"select": {"name": "Greenhouse"}}, "Signals": {"multi_select": [{"name": s} for s in signals]},
        "JD": {"rich_text": rt(jd)}, "Interested": {"checkbox": interested}, "Apply": {"checkbox": apply}}}


def feed():
    names = CFG["notion"]["properties"]
    return notion_feed.Feed("ds", names, {k: f"id_{k}" for k in names})


def test_list_items_and_trash_only_rows_that_fail_and_are_unticked():
    sess = Notion([
        page("good", "Software Engineer", "Acme · oct6", "https://boards.greenhouse.io/acme/jobs/1", GOOD_JD),
        page("bad", "Data Specialist", "McKesson · oct8", "https://boards.greenhouse.io/mck/jobs/2", GOOD_JD),
        page("kept", "Directeur technique", "Valtech · oct7", "https://boards.greenhouse.io/val/jobs/3", GOOD_JD,
             interested=True),
    ])
    client = notion_feed.NotionClient("secret", "2026-03-11", session=sess, sleep=lambda s: None)
    items = FC.list_items(client, feed())
    assert [(i.title, i.company) for i in items] == [("Software Engineer", "Acme"), ("Data Specialist", "McKesson"),
                                                      ("Directeur technique", "Valtech")]
    rep = FC.clean(items, {}, CFG, FILTERS, SCORER)
    assert FC.trash(client, rep, dry_run=True) == 1 and sess.patched == []
    assert FC.trash(client, rep, dry_run=False) == 1
    assert sess.patched == [("/v1/pages/bad", {"in_trash": True})]


def test_render_has_a_table_per_rule_and_never_prints_ids():
    rep = FC.CleanReport([verdict(item(title="Data Specialist", company="McKesson", page="secret-page-id")),
                          verdict(item(title="Directeur", page="x2", interested=True)),
                          verdict(item(), "Austin, TX")])
    text = FC.render(rep, dry_run=True)
    assert "| title exclude | Data Specialist | McKesson |" in text
    assert "NOT trashed" in text and "Directeur" in text
    assert "Austin, TX" in text and "secret-page-id" not in text and "dry run" in text


def test_feed_row_company_loses_its_date_tag():
    sess = Notion([page("p", "Software Engineer", "Tower Research Capital · oct9", "https://x/1", GOOD_JD)])
    client = notion_feed.NotionClient("secret", "2026-03-11", session=sess, sleep=lambda s: None)
    assert FC.list_items(client, feed())[0].company == "Tower Research Capital"
