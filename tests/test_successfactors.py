"""SuccessFactors career sites (Telus, Scotiabank, Rogers ...): the search page, the posting page, the watchlist entry."""
from datetime import datetime, timezone

import pytest

from scanner.models import Job
from scanner.sources import ats, successfactors as sf
from scanner.sources import watchlist as wl
from scanner.sources.watchlist import Board

NOW = datetime(2026, 11, 8, 16, 0, tzinfo=timezone.utc)
TELUS = Board("successfactors", "careers.telus.com", "Telus")


def row(title="Software Developer", where="Burnaby, BC, CA", date="Nov 8, 2026", path="/job/Burnaby-Software-Developer-BC/606044817/"):
    return (f'<tr class="data-row"><td class="colTitle"><span class="jobTitle hidden-phone">'
            f'<a href="{path}" class="jobTitle-link">{title}</a></span></td>'
            f'<td class="colLocation hidden-phone"><span class="jobLocation">\n   {where}\n  </span></td>'
            f'<td class="colDate hidden-phone"><span class="jobDate">{date}\n</span></td></tr>')


def test_row_becomes_a_record():
    (rec,) = sf.parse_search("careers.telus.com", "Telus", "<table>" + row() + "</table>")
    assert rec == {"company": "careers.telus.com", "company_name": "Telus", "title": "Software Developer",
                   "location": "Burnaby, BC, CA",
                   "url": "https://careers.telus.com/job/Burnaby-Software-Developer-BC/606044817/",
                   "source": "SuccessFactors", "first_seen": datetime(2026, 11, 8, tzinfo=timezone.utc)}


def test_unreadable_rows_raise_and_a_page_without_rows_is_empty():
    with pytest.raises(sf.SuccessFactorsError):
        sf.parse_search("h.com", "", '<tr class="data-row"><td>nothing</td></tr>')
    assert sf.parse_search("h.com", "", "<html>no results</html>") == []


def test_dates_in_the_usual_spellings():
    assert sf._day("Nov 8, 2026") == datetime(2026, 11, 8, tzinfo=timezone.utc)
    assert sf._day("November 08, 2026") == datetime(2026, 11, 8, tzinfo=timezone.utc)
    assert sf._day("soon") is None


class Pages:
    """get() answers by the startrow in the URL: pages = {startrow: html}."""

    def __init__(self, pages):
        self.pages, self.urls = pages, []

    def get(self, url, label):
        self.urls.append(url)
        return type("R", (), {"text": self.pages.get(int(url.split("startrow=")[1]), "<html>no results</html>")})()


def table(n, date, first=0):
    return "<table>" + "".join(row(path=f"/job/x-{first + i}/{600000 + first + i}/", date=date) for i in range(n)) + "</table>"


def test_the_walk_continues_after_the_rows_already_read_whatever_the_page_size_and_stops_when_a_page_adds_nothing():
    pages = Pages({0: table(15, "Nov 8, 2026"), 15: table(15, "Nov 7, 2026", 15), 30: table(4, "Nov 6, 2026", 30)})
    assert len(sf.fetch_board("careers.telus.com", "Telus", pages, NOW)) == 34
    assert [u.split("startrow=")[1] for u in pages.urls] == ["0", "15", "30", "34"]       # 15-row pages, then the empty end
    assert "locationsearch=Canada" in pages.urls[0] and "sortDirection=desc" in pages.urls[0]


def test_the_walk_stops_at_a_page_older_than_the_horizon():
    pages = Pages({0: table(25, "Nov 8, 2026"), 25: table(25, "Sep 1, 2026", 25), 50: table(25, "Sep 1, 2026", 50)})
    assert len(sf.fetch_board("careers.telus.com", "Telus", pages, NOW)) == 50 and len(pages.urls) == 2


def test_a_site_that_answers_the_canada_filter_with_nothing_is_asked_again_without_it():
    class NoCanada(Pages):
        def get(self, url, label):
            self.urls.append(url)
            text = "<html>none</html>" if "locationsearch" in url else table(3, "Nov 8, 2026")
            return type("R", (), {"text": text})()

    c = NoCanada({})
    assert len(sf.fetch_board("jobs.toronto.ca/jobsatcity", "Toronto", c, NOW)) == 3
    assert "locationsearch" in c.urls[0] and "locationsearch" not in c.urls[1] and c.urls[1].startswith("https://jobs.toronto.ca/jobsatcity/search/")


TILE = ('<ul id="job-tile-list"><li class="job-tile job-id-1 job-row-index-1" data-url="/jobsatcity/job/Toronto-Developer-ON-M5V-0C4/607694717/">'
        '<a class="jobTitle-link fontcolor" data-focus-tile=".x" href="/jobsatcity/job/Toronto-Developer-ON-M5V-0C4/607694717/"> Application Developer </a>'
        '<div id="job-1-desktop-section-date-value">Nov 3, 2026 </div></li></ul>')
CITY_ROW = ('<tr class="data-row"><td class="colTitle"><span class="jobTitle hidden-phone">'
            '<a href="/job/Vancouver-Engineering-Technician-I-Brit-V6A-4K6/1433948200/" class="jobTitle-link">Engineering Technician I</a></span></td>'
            '<td class="colDate"><span class="jobDate">Nov 7, 2026 </span></td></tr>')


def test_tile_layout_and_a_row_without_a_location_column_take_the_place_from_the_link():
    (tile,) = sf.parse_search("jobs.toronto.ca/jobsatcity", "City of Toronto", TILE)
    assert tile["title"] == "Application Developer" and tile["location"] == "Toronto, ON"
    assert tile["url"] == "https://jobs.toronto.ca/jobsatcity/job/Toronto-Developer-ON-M5V-0C4/607694717/"
    assert tile["first_seen"] == datetime(2026, 11, 3, tzinfo=timezone.utc)
    (city,) = sf.parse_search("jobs.vancouver.ca", "City of Vancouver", "<table>" + CITY_ROW + "</table>")
    assert city["location"] == "Vancouver, BC" and city["company_name"] == "City of Vancouver"


def test_place_from_a_link():
    assert sf.location_from_url("/job/Victoria-Planner-BC-V8W-1P6/1/") == "Victoria, BC"
    assert sf.location_from_url("/job/Calgary-Analyst-T2P-0A1/1/") == "Calgary, AB"
    assert sf.location_from_url("/job/Remote-Analyst/1/") == ""


JOB = ('<html><div><span class="jobdescription"><div><p>Build things with Python.</p>'
       '<ul><li>1 year of experience</li></ul><span>nested</span></div></span><span class="other">no</span></div></html>')


def test_posting_page_gives_the_description_only():
    text = sf.parse_detail(JOB)
    assert "Build things with Python." in text and "- 1 year of experience" in text and "nested" in text and "no" not in text.split("nested")[-1]
    with pytest.raises(sf.SuccessFactorsError):
        sf.parse_detail("<html></html>")


def test_a_malformed_successfactors_slug_stops_the_run(tmp_path):
    p = tmp_path / "w.yaml"
    p.write_text("boards:\n  - {ats: successfactors, slug: https://careers.telus.com/search}\n", encoding="utf-8")
    with pytest.raises(wl.WatchlistError, match="successfactors slug"):
        wl.load_boards(p)
    assert TELUS.label == "SuccessFactors" and TELUS.url.startswith("https://careers.telus.com/search/")
    assert sf.split_slug("jobs.toronto.ca/jobsatcity") == ("jobs.toronto.ca", "/jobsatcity")


def test_a_successfactors_posting_link_is_recognised_on_any_host():
    url = "https://jobs.rogers.com/job/Toronto-Developer-ON/605112/"
    job = Job("jobs.rogers.com", "Developer", "Toronto, ON, CA", url, "SuccessFactors", NOW)
    assert ats.detect_ats(url) == "SuccessFactors" and ats.locate(job).ats == "SuccessFactors"
    assert ats.detect_ats("https://jobs.toronto.ca/jobsatcity/job/Toronto-Dev-ON/607694717/") == "SuccessFactors"
    for other in ("https://jobs.rogers.com/search/", "https://acme.wd5.myworkdayjobs.com/en-US/Careers/job/T/Dev_1",
                  "https://example.com/job/x/12"):
        assert ats.detect_ats(other) != "SuccessFactors"
