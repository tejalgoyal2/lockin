"""Job Bank (Atom feed) and BC Public Service (HTML table): parsing, paging, and the JD pages."""
from datetime import datetime, timezone

import pytest

from scanner.models import Job
from scanner.net import PoliteClient
from scanner.sources import ats, bcgov, jobbank
from scanner.sources.listing import SITE_SOURCES

NOW = datetime(2026, 11, 8, 16, 0, tzinfo=timezone.utc)


def article(title="software developer", where="Vancouver (BC)", employer="Corinex", jid=50678526,
            date="November 06, 2026", via="Job Bank"):
    return (f'<article id="article-{jid}" class="action-buttons"><a href="/jobsearch/jobposting/{jid};jsessionid=ABC.js76?source=searchresults" '
            f'class="resultJobItem"><h3 class="title"><span class="flag"><span class="new"> New </span></span>'
            f'<span class="noctitle"> {title} </span></h3><ul class="list-unstyled"><li class="date">{date} </li>'
            f'<li class="business">{employer}</li><li class="location"><span class="fas fa-map-marker-alt"></span> '
            f'<span class="wb-inv">Location</span> {where} </li><li class="salary">Salary $55 hourly</li>'
            f'<li class="source"><span class="job-source job-source-icon-24"><span class="wb-inv">{via}</span></span> '
            f'<span class="wb-inv">Job number:</span> 117243</li></ul></a></article>')


def page(*articles):
    return "<html><body>" + "".join(articles) + "</body></html>"


# --- Job Bank --------------------------------------------------------------------------------

def test_job_bank_article_becomes_a_record():
    (rec,) = jobbank.parse_results(page(article()))
    assert rec == {"company": "Corinex", "company_name": "Corinex", "title": "Software Developer",
                   "location": "Vancouver, BC", "url": "https://www.jobbank.gc.ca/jobsearch/jobposting/50678526",
                   "source": "Job Bank", "first_seen": datetime(2026, 11, 6, tzinfo=timezone.utc), "via": "Job Bank"}


def test_job_bank_keeps_the_partner_site_a_posting_came_from_municipal_jobs_included():
    (rec,) = jobbank.parse_results(page(article(employer="Capital Regional District", where="Victoria (BC)", via="CivicJobs.ca")))
    assert rec["via"] == "CivicJobs.ca" and rec["company"] == "Capital Regional District" and rec["location"] == "Victoria, BC"


def test_job_bank_province_in_brackets_gets_the_comma_the_location_filter_wants():
    assert jobbank.place("Victoria (BC)") == "Victoria, BC"
    assert jobbank.place("Saint John (NB)") == "Saint John, NB"
    assert jobbank.place("Remote") == "Remote"


def test_job_bank_keeps_a_title_that_already_has_capitals():
    assert jobbank.title_case("Web developer") == "Web Developer"
    assert jobbank.title_case("data analyst") == "Data Analyst"
    assert jobbank.title_case("information technology (IT) analyst") == "Information Technology (IT) Analyst"


def test_job_bank_without_an_employer_is_still_a_record():
    (rec,) = jobbank.parse_results(page(article(employer="")))
    assert rec["company"] == "Job Bank employer" and rec["company_name"] == ""


def test_job_bank_unreadable_page_says_so_and_an_empty_one_is_quiet():
    with pytest.raises(jobbank.JobBankError, match="none readable"):
        jobbank.parse_results('<article id="article-1"><p>nothing</p></article>')
    assert jobbank.parse_results("<html>no results</html>") == []


class ResultsClient:
    """get() answers by the search it is asked: {(kind, value, page): [articles]}."""

    def __init__(self, answers):
        self.answers, self.urls = answers, []

    def get(self, url, ats_label):
        self.urls.append(url)
        from urllib.parse import parse_qs, urlsplit
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        key = ("noc", q["fn21"], int(q["page"])) if "fn21" in q else ("term", q["searchstring"], int(q["page"]))
        return type("R", (), {"text": page(*self.answers.get(key, []))})()


def full(start):
    return [article(jid=start + i) for i in range(jobbank.PAGE)]


def test_each_occupation_is_paged_until_a_short_page_and_repeats_across_searches_are_dropped():
    client = ResultsClient({("noc", "21232", 1): full(1000), ("noc", "21232", 2): full(2000)[:5] + [article(jid=1000)],
                            ("term", "devops", 1): [article(jid=7), article(jid=2000)]})
    out = jobbank.fetch(client, ["21232", "21231"], ["devops"], 10)
    assert len(client.urls) == 4                                   # 21232 pages 1 and 2, 21231 page 1, devops page 1
    assert "fn21=21232" in client.urls[0] and "page=2" in client.urls[1] and "fn21=21231" in client.urls[2]
    assert "searchstring=devops" in client.urls[3] and all("fage=10" in u and "sort=D" in u for u in client.urls)
    assert len(out) == 25 + 5 + 1 and len({r["url"] for r in out}) == len(out)


def test_job_bank_never_asks_more_than_the_query_budget_or_pages():
    client = ResultsClient({("noc", "1", p): full(p * 100) for p in range(1, 30)})
    jobbank.fetch(client, ["1"], [], 10, max_pages=4)
    assert len(client.urls) == 4
    client = ResultsClient({("noc", str(n), 1): full(n * 100) for n in range(1, 7)})
    jobbank.fetch(client, [str(n) for n in range(1, 7)], [], 10, max_queries=3)
    assert len(client.urls) == 3


def test_job_bank_window_becomes_fage_days_within_1_to_30():
    c = ResultsClient({})
    jobbank.fetch(c, [], ["x"], 0.2)
    jobbank.fetch(c, [], ["x"], 99)
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


# --- the curated Canada list --------------------------------------------------------------------------------

LIST = """# 2026 Canadian CS new Grad/Junior Jobs
| Title | Company | Role | Company Info | Details | Location | Apply |
|-------|---------|------|--------------|---------|----------|:-----:|
| 💤 Fresh Graduate – Java Full Stack Developer <!--id:875727899--> | KTek Resourcing | Develop Java features | IT staffing | Full-time | Toronto, Ontario (Onsite) | [Apply](<https://www.linkedin.com/jobs/view/1>) |
| 🔥 Junior Software Developer <!--id:874610920--> | KEV Group | Build React features | K-12 fintech | Full-time · $80K | Toronto, Ontario (Hybrid) | [Apply](https://example.com/jobs/2) |
## Security
_No roles currently_
"""


def test_curated_list_rows_become_records_without_emoji_or_id_markers():
    from scanner.sources import canadalist
    recs = canadalist.parse(LIST, NOW)
    assert [(r["title"], r["company"], r["location"], r["url"]) for r in recs] == [
        ("Fresh Graduate – Java Full Stack Developer", "KTek Resourcing", "Toronto, Ontario (Onsite)", "https://www.linkedin.com/jobs/view/1"),
        ("Junior Software Developer", "KEV Group", "Toronto, Ontario (Hybrid)", "https://example.com/jobs/2")]
    assert all(r["source"] == "Canada List" and r["first_seen"] == NOW for r in recs)
    assert canadalist.parse("no tables here", NOW) == []


def test_curated_list_is_a_site_the_feed_knows_and_two_files_do_not_repeat_a_link():
    from scanner.sources import canadalist
    assert "Canada List" in SITE_SOURCES

    class Files:
        def get(self, url, label):
            return type("R", (), {"text": LIST})()

    assert len(canadalist.fetch(["a", "b"], Files(), NOW)) == 2
