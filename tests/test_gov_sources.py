"""Job Bank (Atom feed) and BC Public Service (HTML table): parsing, paging, and the JD pages."""
from datetime import datetime, timezone

import pytest

from scanner.models import Job
from scanner.net import PoliteClient
from scanner.sources import ats, bcgov, jobbank
from scanner.sources.listing import SITE_SOURCES

NOW = datetime(2026, 11, 8, 16, 0, tzinfo=timezone.utc)


def feed(*entries):
    body = "".join(entries)
    return ('<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom"><title>x</title>'
            f"{body}</feed>")


def entry(title="software developer", where="Vancouver (BC)", employer="Corinex", jid=50678526,
          updated="2026-11-06T13:23:00Z"):
    return (f'<entry><title type="html"><![CDATA[{title}]]></title>'
            f'<link rel="alternate" type="text/html" href="https://www.jobbank.gc.ca/jobsearch/jobposting/{jid}?source=x"/>'
            f"<updated>{updated}</updated>"
            f'<summary type="html"><![CDATA[<strong>Job number:</strong> 1<br /><strong>Location:</strong> {where}  <br />'
            f"<strong>Employer:</strong> {employer}<br /><strong>Salary:</strong> $96,000.00 annually]]></summary></entry>")


# --- Job Bank --------------------------------------------------------------------------------

def test_job_bank_entry_becomes_a_record():
    (rec,) = jobbank.parse_feed(feed(entry()))
    assert rec == {"company": "Corinex", "company_name": "Corinex", "title": "Software Developer",
                   "location": "Vancouver, BC", "url": "https://www.jobbank.gc.ca/jobsearch/jobposting/50678526",
                   "source": "Job Bank", "first_seen": datetime(2026, 11, 6, 13, 23, tzinfo=timezone.utc)}


def test_job_bank_province_in_brackets_gets_the_comma_the_location_filter_wants():
    assert jobbank.place("Victoria (BC)") == "Victoria, BC"
    assert jobbank.place("Saint John (NB)") == "Saint John, NB"
    assert jobbank.place("Remote") == "Remote"


def test_job_bank_keeps_a_title_that_already_has_capitals():
    assert jobbank.title_case("Web developer") == "Web developer"
    assert jobbank.title_case("data analyst") == "Data Analyst"


def test_job_bank_without_an_employer_is_still_a_record():
    (rec,) = jobbank.parse_feed(feed(entry(employer="")))
    assert rec["company"] == "Job Bank employer" and rec["company_name"] == ""


def test_job_bank_feed_that_is_not_xml_or_unreadable_says_so():
    with pytest.raises(jobbank.JobBankError, match="not XML"):
        jobbank.parse_feed("<html>")
    broken = '<entry><title>x</title></entry>'
    with pytest.raises(jobbank.JobBankError, match="none readable"):
        jobbank.parse_feed(feed(broken))
    assert jobbank.parse_feed(feed()) == []                      # an empty feed is a quiet day, not an error


class FeedClient:
    def __init__(self, answers):
        self.answers, self.urls = answers, []

    def get(self, url, ats_label):
        self.urls.append(url)
        key = ("fprov=" + url.split("fprov=")[1].split("&")[0]) if "fprov=" in url else "all"
        term = url.split("searchstring=")[1].split("&")[0]
        body = self.answers.get((term, key), feed())
        return type("R", (), {"text": body})()


def test_a_full_feed_is_asked_again_per_province_and_a_short_one_is_not():
    full = feed(*[entry(jid=1000 + i) for i in range(10)])
    client = FeedClient({("developer", "all"): full, ("developer", "fprov=BC"): feed(entry(jid=5), entry(jid=1000)),
                         ("analyst", "all"): feed(entry(jid=7))})
    out = jobbank.fetch(client, ["developer", "analyst"], 10, provinces=("BC", "AB"))
    urls = " ".join(client.urls)
    assert "searchstring=developer&fage=10&sort=D&fprov=BC" in urls and "fprov=AB" in urls
    assert "analyst&fage=10&sort=D&fprov" not in urls                      # 1 entry: not full, so not split
    assert len(out) == 12 and len({r["url"] for r in out}) == 12            # the repeat of jid 1000 is dropped


def test_job_bank_never_asks_more_than_the_query_budget():
    full = feed(*[entry(jid=i) for i in range(10)])
    client = FeedClient({(t, k): full for t in ("a", "b") for k in ["all"] + [f"fprov={p}" for p in jobbank.PROVINCES]})
    jobbank.fetch(client, ["a", "b"], 10, max_queries=5)
    assert len(client.urls) == 5


def test_job_bank_window_becomes_fage_days_within_1_to_30():
    assert "fage=1&" in jobbank.feed_url("x", 1) and "fage=3" in jobbank.feed_url("x", 3)
    c = FeedClient({})
    jobbank.fetch(c, ["x"], 0.2)
    jobbank.fetch(c, ["x"], 99)
    assert "fage=1&" in c.urls[0] and "fage=30&" in c.urls[1]


JB_PAGE = ('<div class="a"></div><div class="main-job-posting-detail job-posting-detail-requirements ">'
           "<h3>Experience</h3><p>2 years to less than 3 years</p><ul><li>Python</li><li>SQL</li></ul></div>"
           '<div class="job-posting-detail-apply"><p>Apply now</p></div>')


def test_job_bank_page_gives_the_details_block_and_not_the_apply_section():
    text = jobbank.parse_detail(JB_PAGE)
    assert "2 years to less than 3 years" in text and "- Python" in text and "Apply now" not in text


def test_job_bank_page_without_the_block_raises():
    with pytest.raises(jobbank.JobBankError):
        jobbank.parse_detail("<html></html>")


def test_per_host_delay_overrides_the_default(monkeypatch):
    sleeps = []
    monkeypatch.setattr("scanner.net.time.sleep", sleeps.append)
    monkeypatch.setattr("scanner.net.http_get", lambda *a, **k: type("R", (), {"json": lambda s: {}})())
    client = PoliteClient(delay=0.3, host_delays={"www.jobbank.gc.ca": 5})
    client.get("https://www.jobbank.gc.ca/a", "Job Bank")
    client.get("https://www.jobbank.gc.ca/b", "Job Bank")
    client.get("https://other.example/a", "x")
    client.get("https://other.example/b", "x")
    assert len(sleeps) == 2 and 4.0 < sleeps[0] <= 5 and sleeps[1] <= 0.3


# --- BC Public Service ------------------------------------------------------------------------

def row(ministry="Attorney General", req=126729, title="IS 24 - Application Developer", loc="Victoria, BC, CA V9B 6X2<br />",
        opened="11/6/2026"):
    return (f"<tr> <td>{ministry}</td> <td>{req}</td> <td><a href=\"/hr/ats/Posting/view/{req}\"> <span>{title}</span> </a></td>"
            f"<td>GEU</td> <td>Hybrid</td> <td> {loc} </td> <td>{opened}</td> <td>11/22/2026</td> </tr>")


def test_bc_row_becomes_a_record_with_a_clean_location():
    (rec,) = bcgov.parse_list("<table><tbody>" + row() + "</tbody></table>")
    assert rec["title"] == "IS 24 - Application Developer" and rec["location"] == "Victoria, BC"
    assert rec["url"] == "https://bcpublicservice.hua.hrsmart.com/hr/ats/Posting/view/126729"
    assert rec["first_seen"] == datetime(2026, 11, 6, tzinfo=timezone.utc)
    assert rec["company_name"] == "BC Public Service" and rec["source"] == "BC Gov"


def test_bc_locations_lose_postal_code_country_and_primary_marker():
    cell = ("Multiple Locations, BC, CA (Primary)<br />Quesnel, BC, CA V2J 6W6<br />Williams Lake, BC, CA V2G 5M1<br />"
            "Quesnel, BC, CA V2J 6W6<br />")
    assert bcgov.where(cell) == "Multiple Locations, BC; Quesnel, BC; Williams Lake, BC"


def test_bc_table_with_rows_but_no_readable_ones_raises():
    with pytest.raises(bcgov.BcGovError):
        bcgov.parse_list('<tr><td><a href="/hr/ats/Posting/view/1">x</a></td></tr>')
    assert bcgov.parse_list("<html>no table</html>") == []


def test_bc_list_follows_pages_until_a_short_one(monkeypatch):
    monkeypatch.setattr(bcgov, "PAGE_SIZE", 2)

    class Client:
        def __init__(self):
            self.n = 0

        def get(self, url, label):
            self.n += 1
            rows = {1: row(req=1) + row(req=2), 2: row(req=3)}[self.n]
            return type("R", (), {"text": f"<table>{rows}</table>"})()

    assert [r["url"][-1] for r in bcgov.fetch(Client())] == ["1", "2", "3"]


def test_bc_detail_block_is_cut_at_the_next_field():
    page = ('<div x="1"><div id="job-detail"><div>Posting Title</div><div>Data Analyst</div>'
            '<div>Job Summary</div><div>Needs 1 year of experience</div></div>'
            '<div id="job_details_ats_requisition_category_id">IT</div></div>')
    text = bcgov.parse_detail(page)
    assert "Needs 1 year of experience" in text and "IT" not in text.split("experience")[-1]
    with pytest.raises(bcgov.BcGovError):
        bcgov.parse_detail("<p>nothing</p>")


# --- wiring -----------------------------------------------------------------------------------

def test_both_sites_are_job_sites_the_jd_fetch_can_locate():
    assert {"Job Bank", "BC Gov"} <= set(SITE_SOURCES) <= set(ats.SUPPORTED)
    jb = Job("Acme", "Developer", "Vancouver, BC", "https://www.jobbank.gc.ca/jobsearch/jobposting/1", "Job Bank", NOW)
    bc = Job("bcpublicservice", "IS 24", "Victoria, BC",
             "https://bcpublicservice.hua.hrsmart.com/hr/ats/Posting/view/9", "BC Gov", NOW)
    assert ats.locate(jb).ats == "Job Bank" and ats.locate(bc).ats == "BC Gov"


FREE_PAGE = ('<div class="job-posting-details-body col-md-9"><p>sign in</p><h2>Job details</h2>'
             "<div>&lt;p&gt;&lt;strong&gt;RQ1 - Developer&lt;/strong&gt;&lt;/p&gt;&lt;p&gt;At least 2 years of experience with Python.&lt;/p&gt;"
             "&lt;ul&gt;&lt;li&gt;SQL&lt;/li&gt;&lt;/ul&gt;</div>"
             "<div>Location</div><div>Toronto, ON</div><div>Salary</div><div>$88 hourly</div>"
             '</div><div class="job-posting-details-menu">menu</div>')


def test_job_bank_free_text_description_is_unescaped_and_cut_before_the_metadata():
    text = jobbank.parse_detail(FREE_PAGE)
    assert "At least 2 years of experience with Python." in text and "- SQL" in text
    assert "<p>" not in text and "Toronto, ON" not in text and "menu" not in text and "sign in" not in text


def test_job_bank_page_with_an_empty_free_text_block_raises():
    with pytest.raises(jobbank.JobBankError):
        jobbank.parse_detail('<div class="job-posting-details-body"><h2>Job details</h2></div>')
