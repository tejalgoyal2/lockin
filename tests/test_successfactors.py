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
    def __init__(self, pages):
        self.pages, self.urls = pages, []

    def get(self, url, label):
        self.urls.append(url)
        n = int(url.split("startrow=")[1]) // sf.PAGE
        return type("R", (), {"text": self.pages[n]})()


def full_page(date, first=0):
    return "<table>" + "".join(row(path=f"/job/x-{first + i}/{600000 + first + i}/", date=date) for i in range(sf.PAGE)) + "</table>"


def test_the_walk_stops_at_a_short_page_or_a_page_older_than_the_horizon():
    short = Pages([full_page("Nov 8, 2026"), "<table>" + row(path="/job/y/700000/") + "</table>"])
    assert len(sf.fetch_board("careers.telus.com", "Telus", short, NOW)) == sf.PAGE + 1
    old = Pages([full_page("Nov 8, 2026"), full_page("Sep 1, 2026", 100), full_page("Sep 1, 2026", 200)])
    assert len(sf.fetch_board("careers.telus.com", "Telus", old, NOW)) == 2 * sf.PAGE and len(old.urls) == 2
    assert "locationsearch=Canada" in old.urls[0] and "sortDirection=desc" in old.urls[0]


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


def test_a_successfactors_posting_link_is_recognised_on_any_host():
    url = "https://jobs.rogers.com/job/Toronto-Developer-ON/605112/"
    job = Job("jobs.rogers.com", "Developer", "Toronto, ON, CA", url, "SuccessFactors", NOW)
    assert ats.detect_ats(url) == "SuccessFactors" and ats.locate(job).ats == "SuccessFactors"
    for other in ("https://jobs.rogers.com/search/", "https://acme.wd5.myworkdayjobs.com/en-US/Careers/job/T/Dev_1",
                  "https://example.com/job/x/12"):
        assert ats.detect_ats(other) != "SuccessFactors"
