from scanner import feed_clean as FC
from scanner import notion_feed
from scanner.cluster import ClusterResolver
from scanner.company_names import load_overrides
from scanner.config import load_config
from scanner.filters import Filters
from scanner.score import Scorer

CFG = load_config()
FILTERS = Filters.from_config(CFG)
SCORER = Scorer.load()
OVERRIDES = load_overrides()
FOUR = "Python, SQL, Docker and Git."          # 4 matched skills, no gaps: Fit 80 % under matched / (matched + gaps + 1)


def item(title="Software Developer", cell="Bdo · oct8", jd=FOUR, fit=1.0, signals=(), interested=False,
         apply=False, link="https://x.example/job/1", page="p1", done_del=False):
    return FC.FeedItem(page, title, cell.rsplit(" · ", 1)[0], link, "Workday", jd, list(signals), interested, apply,
                       fit, cell, done_del)


def verdicts(items, location="Toronto, ON"):
    clusters = ClusterResolver(CFG)
    return [FC.evaluate(i, location, CFG, FILTERS, SCORER, clusters) for i in items]


def rescore(items, records=None):
    records = records if records is not None else {FC.canonical_url(i.link): FC.Found("Toronto, ON", "ats", "bdo") for i in items}
    return FC.rescore(verdicts(items), records, CFG, FILTERS, SCORER, OVERRIDES)


def test_company_fit_and_signals_are_recomputed_with_the_current_rules():
    it = item(jd="Requirements: Python, SQL, Docker, Git. 2 years of experience.", fit=1.0, signals=["junior"],
              title="Junior Software Developer")
    (c,) = rescore([it])
    assert c.company == ("Bdo · oct8", "BDO · oct8")                  # company_names.yaml
    assert c.fit == (1.0, 0.8)                                       # old formula said 100 %
    assert c.signals == (["junior"], ["junior", "2+ yrs"])


def test_a_row_that_is_already_current_is_not_touched():
    it = item(cell="BDO · oct8", fit=0.8)
    assert rescore([it]) == []


def test_ats_names_are_not_rebuilt_from_a_slug():
    it = item(cell="Tucows Inc. · oct7", fit=0.8)
    recs = {FC.canonical_url(it.link): FC.Found("Toronto, ON", "ats", "tucows")}
    assert rescore([it], recs) == []                                   # not in company_names.yaml: left as it is


def test_the_date_tag_is_kept_and_a_row_without_a_source_record_keeps_its_name():
    it = item(cell="Optrust · sept29", fit=0.8)
    (c,) = rescore([it], {FC.canonical_url(it.link): FC.Found("Toronto, ON", "ats", "optrust")})
    assert c.company == ("Optrust · sept29", "OPTrust · sept29")
    assert rescore([item(cell="Bdo · oct8", fit=0.8)], {}) == []


def test_ticked_unreadable_and_failing_rows_are_skipped():
    rows = [item(interested=True, page="a"), item(apply=True, page="b"), item(interested=None, page="c"),
            item(title="Data Specialist", page="d"),                  # fails a title rule
            item(done_del=True, page="e"), item(done_del=None, page="f")]
    assert rescore(rows) == []


def test_a_row_that_lost_its_terms_goes_to_na_with_low_signal():
    (c,) = rescore([item(jd="Python, SQL and Docker.", fit=0.9, cell="BDO · oct8")])
    assert c.fit == (0.9, None) and c.signals == ([], ["low signal"])


def test_apply_changes_writes_only_what_changed_and_never_the_checkboxes():
    sent = []

    class C:
        def request(self, method, path, json=None, **kw):
            sent.append((method, path, json))
            return {}

    names = CFG["notion"]["properties"]
    feed = notion_feed.Feed("ds", names, {k: k for k in names})
    c = FC.Change(item(), company=("Bdo · oct8", "BDO · oct8"), fit=(1.0, 0.8))
    assert FC.apply_changes(C(), feed, [c], dry_run=True) == 1 and sent == []
    assert FC.apply_changes(C(), feed, [c], dry_run=False) == 1
    (method, path, body), = sent
    assert (method, path) == ("PATCH", "/v1/pages/p1")
    assert set(body["properties"]) == {"Company", "Fit %"}
    assert body["properties"]["Fit %"] == {"number": 0.8}


def test_report_shows_old_and_new_per_row():
    c = FC.Change(item(title="Junior Software Developer", signals=["junior"]), company=("Bdo · oct8", "BDO · oct8"),
                  fit=(1.0, 0.8), signals=(["junior"], ["junior", "2+ yrs"]))
    text = FC.render_changes([c], 5, dry_run=False)
    assert "| Junior Software Developer | Bdo · oct8 → BDO · oct8 | 100.0% → 80.0% | junior → junior, 2+ yrs |" in text
    assert "5 rows with all boxes unticked kept, 1 changed" in text


def test_find_records_takes_the_slug_from_the_ats_and_the_name_from_simplify(monkeypatch):
    url = "https://job-boards.greenhouse.io/2kearlycareers/jobs/1"
    monkeypatch.setattr(FC.feashliaa, "iter_raw", lambda d: iter([{"url": url, "location": "Dublin, Ireland",
                                                                   "company": "2kearlycareers"}]))
    monkeypatch.setattr(FC.simplify, "fetch", lambda cfg: [{"url": url, "company_name": "2K", "title": "t",
                                                            "locations": ["Vancouver, BC, Canada"]}])
    rec = FC.find_records({url}, CFG, feashliaa_dir="unused")[FC.canonical_url(url)]
    assert (rec.location, rec.origin, rec.company, rec.company_name) == ("Dublin, Ireland", "ats", "2kearlycareers", "2K")


def test_a_slug_cell_gets_the_ats_name_and_other_names_are_left_alone():
    tjx = item(cell="Tjx · oct14", fit=0.8, link="https://tjx.wd1.myworkdayjobs.com/tjx_external/job/x/y_REQ1",
               page="t")
    evr = item(cell="Evr · oct16", fit=0.8, link="https://jobs.lever.co/evr/ed1fd146", page="e")
    tucows = item(cell="Tucows Inc. · oct7", fit=0.8, link="https://tucows.wd1.myworkdayjobs.com/x/job/y/z", page="u")
    asked = []

    def name_for(link):
        asked.append(link)
        return {"https://tjx.wd1.myworkdayjobs.com/tjx_external/job/x/y_REQ1": "TJX"}.get(link, "")
    changes = FC.rescore(verdicts([tjx, evr, tucows]), {}, CFG, FILTERS, SCORER, OVERRIDES, name_for=name_for)
    got = {c.item.page_id: c.company for c in changes}
    assert got["t"] == ("Tjx · oct14", "TJX · oct14")                          # from the ATS
    assert got["e"] == ("Evr · oct16", "Elk Valley Resources · oct16")         # company_names.yaml (Lever has no name)
    assert "u" not in got                                                      # an ATS name, not a slug: kept
    assert asked == ["https://tjx.wd1.myworkdayjobs.com/tjx_external/job/x/y_REQ1"]
