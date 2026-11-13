from datetime import date, datetime, timezone

import pytest

from scanner import notion_feed as nf
from scanner import pay
from scanner.company_names import tag_date
from scanner.models import Job
from scanner.sources import jobhive


@pytest.mark.parametrize("text, want", [
    ("The salary range for this role is $70,000 - 85,000 per year.", "$70,000–$85,000 a year"),
    ("For Canada-based roles: The base salary range for this role is CAD $144,000 per year - CAD $160,000 per year.",
     "$144,000–$160,000 a year"),
    ("US base range: $150,000-$180,000 USD. Canada base range: $130,000 - $160,000 CAD.", "$130,000–$160,000 a year"),
    ("The base hourly rate is CAD $24.76 per hour - CAD $25.97 per hour.", "$24.76–$25.97 an hour"),
    ("Pay: $22/hour", "$22 an hour"),
    ("Compensation: $70k–$85k + equity", "$70,000–$85,000 a year"),
    ("between $60,000 and $75,000 annually", "$60,000–$75,000 a year"),
    ("We raised $100M and manage $5 billion. Enjoy a $500 wellness allowance.", ""),     # money, but not the pay
    ("401(k) and a $2,000 learning budget", ""),
    ("Salary: $47.039 to $60.979 per hour", "$47.04–$60.98 an hour"),
    ("Salary: Minimum: $45.15 per hour Maximum: $59.29 per hour", "$45.15–$59.29 an hour"),
    ("Salary Range: $70,473 - $88, 101 per annum", "$70,473–$88,101 a year"),
    ("Competitive compensation of $6000.00 per month for 4 months.", "$6,000 a month"),
    ("a hybrid internship offering $20 per hour for 40 hours per week", "$20 an hour"),
    ("Competitive hourly compensation. $500 sign-up bonus", ""),
    ("AECOM had revenue of $16.1 billion in fiscal year 2025.", ""),
    ("", ""),
])
def test_the_pay_a_description_states(text, want):
    assert pay.from_text(text) == want


def test_pay_given_as_data():
    assert pay.fmt(250000, 300000, "YEAR", "USD") == "$250,000–$300,000 a year (USD)"
    assert pay.fmt(23, 25, None, "CAD") == "$23–$25 an hour"
    assert pay.fmt(None, None) == "" and pay.fmt(5, 6, "HOUR") == ""          # nonsense is left out
    assert jobhive.pay_line({"salary_min": 23.0, "salary_max": 25.0, "salary_currency": "CAD"}) == "$23–$25 an hour"
    assert jobhive.pay_line({"salary_summary": "$42,000 - $45,000 per year"}) == "$42,000–$45,000 a year"
    assert jobhive.pay_line({"salary_min": 40000.0, "salary_currency": "EUR"}) == ""


def test_jobhive_records_carry_the_pay_and_clean_icims_links():
    row = {"url": "https://lenddirect-x.icims.com/jobs/10396/rep/job?in_iframe=1", "title": "Service Representative",
           "company": "lenddirect-x", "location": "Sudbury, ON, CA", "posted_at": "2026-11-10T13:03:00",
           "description": "Hourly wage: $19.50 to $21.00 per hour", "raw": None}
    rec = jobhive.records([row], datetime(2026, 11, 5, tzinfo=timezone.utc))[0]
    assert rec["url"] == "https://lenddirect-x.icims.com/jobs/10396/rep/job"
    assert rec["company_name"] == ""                          # a slug: the Feed shows the prettified name
    assert rec["pay"] == ""                                   # no pay as data; the writer reads it from the JD


def test_a_company_tag_becomes_the_latest_matching_date():
    today = date(2026, 11, 12)
    assert tag_date("RBC · nov6", today) == date(2026, 11, 6)
    assert tag_date("Sun Life · dec20", today) == date(2025, 12, 20)       # no December yet this year
    assert tag_date("Acme · sept3", today) == date(2026, 9, 3)
    assert tag_date("Acme", today) is None and tag_date("Acme · soon5", today) is None


FEED = nf.Feed("ds", {"name": "Name", "company": "Company", "link": "Link", "signals": "Signals", "jd": "JD",
                      "fit": "Fit %", "source": "Source", "posted": "Posted", "pay": "Pay"},
               ids={"posted": "p_posted", "pay": "p_pay"})


def job(**kw):
    base = dict(company="rbc", title="Analyst", location="Toronto, ON", url="https://x/1", source="Workday",
                first_seen=datetime(2026, 11, 6, 9, tzinfo=timezone.utc), jd="Salary range: $60,000 - $70,000 a year")
    return Job(**{**base, **kw})


def test_rows_get_a_sortable_date_and_the_pay():
    props, _ = nf.build_properties(job(), FEED, {})
    assert props["Posted"] == {"date": {"start": "2026-11-06"}}
    assert props["Pay"]["rich_text"][0]["text"]["content"] == "$60,000–$70,000 a year"
    props, _ = nf.build_properties(job(pay="$23–$25 an hour", jd=""), FEED, {})
    assert props["Pay"]["rich_text"][0]["text"]["content"] == "$23–$25 an hour"
    assert "Pay" not in nf.build_properties(job(jd="no figures"), FEED, {})[0]
    bare = nf.Feed("ds", FEED.names)                               # the Feed has neither column yet
    assert not {"Posted", "Pay"} & set(nf.build_properties(job(), bare, {})[0])


class Client:
    def __init__(self):
        self.calls = []

    def request(self, method, path, **kw):
        self.calls.append((method, path, kw.get("json")))
        return {}


def test_older_rows_get_their_date_once_from_the_company_tag():
    when = datetime(2026, 11, 1, tzinfo=timezone.utc)
    rows = [nf.FeedRow("p1", when, "https://a", False, False, company="RBC · nov6"),
            nf.FeedRow("p2", when, "https://b", False, False, company="TD · nov7", posted="2026-11-07"),
            nf.FeedRow("p3", when, "https://c", False, False, company="BMO"),
            nf.FeedRow("p4", when, "https://d", False, False, done_del=True, company="CIBC · nov2")]
    client = Client()
    assert nf.date_rows(client, FEED, rows, date(2026, 11, 12), dry_run=True) == 1 and client.calls == []
    assert nf.date_rows(client, FEED, rows, date(2026, 11, 12), dry_run=False) == 1
    assert client.calls == [("PATCH", "/v1/pages/p1", {"properties": {"Posted": {"date": {"start": "2026-11-06"}}}})]
    assert nf.date_rows(client, nf.Feed("ds", FEED.names), rows, date(2026, 11, 12), dry_run=False) == 0


def test_a_bare_remote_row_in_canada_reads_remote_canada():
    assert jobhive._where("Remote") == "Remote, Canada" and jobhive._where("") == "Canada"
    assert jobhive._where("Remote - US") == "Remote - US" and jobhive._where("Toronto, ON") == "Toronto, ON"


@pytest.mark.parametrize("text, want", [
    ("This is an evergreen requisition and does not represent a current vacancy.", True),
    ("This posting is for building a talent pool for upcoming roles.", True),
    ("This job posting is not for a current vacancy.", True),
    ("Not the right fit? Join our talent community!", False),                 # the usual footer
    ("This competition may be used to fill future vacancies.", False),        # a real job that also fills others
    ("We are hiring for an immediate opening.", False),
])
def test_talent_pool_postings_are_flagged(text, want):
    from scanner import jd_rules
    assert jd_rules.talent_pool(text) is want


def test_only_wanted_provinces_reach_the_feed(filters):
    from scanner.pipeline import run_stream
    from scanner.sources import listing
    now = datetime(2026, 11, 12, tzinfo=timezone.utc)
    recs = [{"company": "acme", "title": "Software Developer", "location": where, "url": f"https://x/{i}",
             "source": "Job Bank", "first_seen": now} for i, where in
            enumerate(["Regina, SK", "Regina, SK; Vancouver, BC", "Calgary, AB", "Winnipeg, MB"])]
    jobs, stages, _ = run_stream(recs, listing.raw_fields, filters, now, since_days=7)
    assert sorted((j.area, j.location) for j in jobs) == [("Alberta", "Calgary, AB"), ("BC", "Vancouver, BC")]
    assert dict(stages)["wanted_area"] == 2


def test_the_area_is_written_and_tags_read_back_under_either_name():
    feed = nf.Feed("ds", {**FEED.names, "area": "Area"}, ids={"area": "p_area"})
    assert nf.build_properties(job(area="BC"), feed, {})[0]["Area"] == {"select": {"name": "BC"}}
    assert "Area" not in nf.build_properties(job(area=""), feed, {})[0]
    assert nf.has_tag(["No description"], "jd unavailable") and nf.has_tag(["jd unavailable"], "jd unavailable")
    assert not nf.has_tag(["Entry level"], "jd unavailable")


def test_simplify_internship_rows_are_not_new_grad(filters, monkeypatch):
    from scanner.pipeline import run_stream
    from scanner.sources import simplify
    now = datetime(2026, 11, 12, tzinfo=timezone.utc)
    row = {"company_name": "Acme", "title": "Software Developer", "locations": ["Vancouver, BC, Canada"],
           "url": "https://x/1", "date_posted": int(now.timestamp()), "active": True, "is_visible": True, "category": "Software"}
    lists = {"new": [row], "intern": [{**row, "url": "https://x/2", "title": "Software Developer Intern"}]}
    monkeypatch.setattr(simplify, "http_get", lambda url, **kw: type("R", (), {"json": lambda self: list(lists[url])})())
    rows = simplify.fetch({"listings_url": "new", "internships_url": "intern"})
    jobs, _, held = run_stream(rows, simplify.raw_fields, filters, now, since_days=7)
    assert [j.new_grad for j in jobs] == [True] and [(j.title, j.new_grad, j.student) for j in held] == [
        ("Software Developer Intern", False, True)]


def test_a_successfactors_page_states_its_own_posting_date():
    from scanner.sources import successfactors as sf
    assert sf.posted_on_page('<meta itemprop="datePosted" content="Tue Oct 20 00:00:00 UTC 2026">') == \
        datetime(2026, 10, 20, tzinfo=timezone.utc)
    assert sf.posted_on_page('<span itemprop="datePosted" content="2026-11-09">') == datetime(2026, 11, 9, tzinfo=timezone.utc)
    assert sf.posted_on_page("<html></html>") is None


def test_workday_rows_without_location_text_use_their_bullets_or_the_canada_filter():
    from scanner.sources import watchlist as wl
    assert wl._workday_where({"bulletFields": ["Toronto", "Ontario", "JREQ204795"]}, True) == "Toronto ; Ontario ; Canada"
    assert wl._workday_where({"bulletFields": ["Ann Arbor; Toronto", "Michigan; Ontario", "JREQ1"]}, False) \
        == "Ann Arbor; Toronto ; Michigan; Ontario"
    assert wl._workday_where({}, True) == "Canada" and wl._workday_where({}, False) == ""
    assert wl._workday_where({"locationsText": "2 Locations"}, True) == "2 Locations"


@pytest.mark.parametrize("fit, signals, want", [
    (75.0, [], "Apply first"),
    (75.0, ["2+ yrs"], "Worth a look"),
    (45.0, ["new grad"], "Apply first"),
    (45.0, [], "Worth a look"),
    (20.0, [], "Long shot"),
    (None, ["low signal"], "Long shot"),
    (None, ["internship", "jd unavailable"], "Worth a look"),
    (90.0, ["P.Eng req"], "Long shot"),
])
def test_every_row_gets_a_verdict(fit, signals, want):
    assert nf.verdict(job(fit_pct=fit, signals=signals)) == want


def test_the_why_line_shows_the_evidence():
    assert nf.why(job(fit_pct=80.0, matched=["Python", "SQL"], gaps=["Tableau"], signals=["2+ yrs"])) == \
        "You have: Python, SQL · Missing: Tableau · asks 2 years"
    assert nf.why(job(fit_pct=None, signals=["low signal"], matched=["Excel"])).startswith("Names few of your skills")
    assert nf.why(job(signals=["jd unavailable"])) == "Could not read the posting: open the link"
    assert nf.display_signals(["internship", "students only"]) == ["Internship"]
