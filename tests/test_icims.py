"""iCIMS career sites (City of Victoria ...): the search page, the sitemap dates, the posting page, the watchlist entry."""
import json
from datetime import datetime, timezone

import pytest

from scanner import boards
from scanner.models import Job
from scanner.sources import ats, icims
from scanner.sources import watchlist as wl
from scanner.sources.watchlist import Board

NOW = datetime(2026, 11, 8, 16, 0, tzinfo=timezone.utc)
HOST = "careersen-victoria.icims.com"
VICTORIA = Board("icims", HOST, "City of Victoria", "Victoria, BC")


def row(jid=4881, title="Systems Analyst - Enterprise Applications", fields=(("Department", "Information Technology"),)):
    dl = "".join(f'<div class="iCIMS_JobHeaderTag"><dt class="iCIMS_JobHeaderField">{k}</dt>'
                 f'<dd class="iCIMS_JobHeaderData"><span > {v}</span> </dd></div>' for k, v in fields)
    return (f'<li class="iCIMS_JobCardItem"><div class="row"><div class="col-xs-12 title">'
            f'<a href="https://{HOST}/jobs/{jid}/{title.lower().replace(" ", "-")}/job?in_iframe=1" class="iCIMS_Anchor" title="{jid}">'
            f'<span class="sr-only">Job Title</span><h3 > {title}</h3></a></div>'
            f'<div class="col-xs-12 additionalFields"><dl class="iCIMS_JobHeaderGroup">{dl}</dl></div></div></li>')


SITEMAP = (f'<urlset><url><loc>https://{HOST}/jobs/intro</loc></url>'
           f'<url><loc>https://{HOST}/jobs/4881/systems-analyst---enterprise-applications/job</loc>'
           f'<lastmod>2026-11-05T16:31:00-05:00</lastmod></url></urlset>')


def test_row_becomes_a_record_dated_by_the_sitemap_and_located_by_the_entry():
    mod = icims.parse_sitemap(SITEMAP)
    (rec,) = icims.parse_search(HOST, "City of Victoria", "<ul>" + row(title="Systems Analyst---Enterprise Applications") + "</ul>",
                                NOW, mod, "Victoria, BC")
    assert rec["title"] == "Systems Analyst---Enterprise Applications" and rec["location"] == "Victoria, BC"
    assert rec["url"].startswith(f"https://{HOST}/jobs/4881/") and rec["url"].endswith("/job") and "in_iframe" not in rec["url"]
    assert rec["source"] == "iCIMS"
    # a title that is not in the sitemap is dated "now"; the sitemap's own date is used when the url matches
    mod2 = {rec["url"]: datetime(2026, 11, 5, 21, 31, tzinfo=timezone.utc)}
    (again,) = icims.parse_search(HOST, "x", "<ul>" + row(title="Systems Analyst---Enterprise Applications") + "</ul>", NOW, mod2)
    assert again["first_seen"] == datetime(2026, 11, 5, 21, 31, tzinfo=timezone.utc)


def test_header_fields_give_the_location_and_posted_date_when_the_site_shows_them():
    (rec,) = icims.parse_search(HOST, "Hospital", "<ul>" + row(fields=(("Job Locations", "CA-BC-Victoria"), ("Date Posted", "11/3/2026"))) + "</ul>", NOW)
    assert rec["location"] == "Victoria, BC" and rec["first_seen"] == datetime(2026, 11, 3, tzinfo=timezone.utc)
    (rel,) = icims.parse_search(HOST, "Hospital", "<ul>" + row(fields=(("Posted Date", "3 days ago"),)) + "</ul>", NOW)
    assert (NOW - rel["first_seen"]).days == 3


def test_icims_locations_are_country_region_city():
    assert icims.place("CA-BC-Victoria") == "Victoria, BC"
    assert icims.place("CA-ON-Toronto | CA-BC-Vancouver") == "Toronto, ON ; Vancouver, BC"
    assert icims.place("Victoria, BC") == "Victoria, BC" and icims.place("") == ""


def test_unreadable_rows_raise_and_an_empty_page_is_empty():
    with pytest.raises(icims.IcimsError):
        icims.parse_search(HOST, "", '<div class="row"><a class="iCIMS_Anchor" href="/x">x</a></div>', NOW)
    assert icims.parse_search(HOST, "", "<html>none</html>", NOW) == []


class Site:
    def __init__(self, pages):
        self.pages, self.urls = pages, []

    def get(self, url, label):
        self.urls.append(url)
        if url.endswith("sitemap.xml"):
            return type("R", (), {"text": SITEMAP})()
        n = int(url.split("pr=")[1])
        return type("R", (), {"text": "<ul>" + "".join(self.pages.get(n, [])) + "</ul>"})()


def test_pages_are_walked_until_one_adds_nothing():
    site = Site({0: [row(1), row(2)], 1: [row(3)], 2: [row(3)]})            # page 2 only repeats a posting: the end
    recs = wl.fetch_board(VICTORIA, site, now=NOW)
    assert [r["url"].split("/")[4] for r in recs] == ["1", "2", "3"]
    assert [u.split("pr=")[1] for u in site.urls if "pr=" in u] == ["0", "1", "2"]


def ld(description="<p>Python and SQL.</p>", places=({"addressLocality": "Victoria", "addressRegion": "BC"},)):
    data = {"@type": "JobPosting", "datePosted": "2026-11-05", "description": description,
            "jobLocation": [{"@type": "Place", "address": a} for a in places]}
    return f'<html><script type="application/ld+json">{json.dumps(data)}</script></html>'


def test_posting_page_gives_the_description_and_locations_from_its_schema_data():
    text, where = icims.parse_detail(ld())
    assert text == "Python and SQL." and where == ["Victoria, BC"]
    text, _ = icims.parse_detail(ld("&lt;p&gt;Escaped &amp; sent&lt;/p&gt;"))
    assert text == "Escaped & sent"
    with pytest.raises(icims.IcimsError):
        icims.parse_detail("<html></html>")


def test_icims_links_slug_and_entry():
    url = f"https://{HOST}/jobs/4881/systems-analyst/job"
    job = Job(HOST, "Analyst", "Victoria, BC", url + "?in_iframe=1", "iCIMS", NOW)
    assert ats.detect_ats(url) == "iCIMS" and ats.locate(job).api_url == url + "?in_iframe=1"
    assert ats.detect_ats(f"https://{HOST}/jobs/search?ss=1") != "iCIMS"
    assert boards.board_from_url(url) == Board("icims", HOST)
    with pytest.raises(icims.IcimsError):
        icims.check_host("https://x.example.com")


def test_city_only_locations_take_the_province_of_the_entry_and_various_locations_name_no_place():
    labelled = ('<ul><li><div class="row"><div class="col-xs-6 header right"><span class="sr-only field-label">City</span> '
                '<span > Burnaby</span></div><div class="col-xs-12 title"><a href="https://h.icims.com/jobs/9/x/job" class="iCIMS_Anchor">'
                '<h3 > Analyst</h3></a></div></div></li></ul>')
    (rec,) = icims.parse_search("h.icims.com", "Hospital", labelled, NOW, None, "Vancouver, BC")
    assert rec["location"] == "Burnaby, BC"
    various = labelled.replace("Burnaby", "Various Locations")
    (rec,) = icims.parse_search("h.icims.com", "Hospital", various, NOW, None, "Vancouver, BC")
    assert rec["location"] == "Vancouver, BC"                       # nothing stated: the entry's default
