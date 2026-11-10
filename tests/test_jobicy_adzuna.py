import json
from datetime import datetime, timezone

import pytest

from scanner.sources import adzuna, ats, jobicy
from scanner.sources.listing import SITE_SOURCES

NOW = datetime(2026, 11, 9, tzinfo=timezone.utc)


def test_jobicy_parse_marks_canada_remote():
    ans = {"jobs": [{"jobTitle": "Backend Developer &amp; SRE", "companyName": "Acme", "url": "https://jobicy.com/jobs/1-x",
                     "jobGeo": "USA,  Canada", "pubDate": "2026-11-09T19:30:14+00:00"},
                    {"jobTitle": "", "url": "https://jobicy.com/jobs/2"}]}
    recs = jobicy.parse(ans)
    assert len(recs) == 1 and recs[0]["title"] == "Backend Developer & SRE"
    assert recs[0]["location"] == "Remote, Canada" and recs[0]["first_seen"].year == 2026


def test_jobicy_parse_fails_loudly_when_api_changes():
    with pytest.raises(jobicy.JobicyError):
        jobicy.parse({"oops": 1})


def test_jobicy_detail_reads_json_ld():
    page = ('<script type="application/ld+json">' + json.dumps({"@context": "x", "@type": "JobPosting",
            "description": "&lt;p&gt;Build APIs in Python&lt;/p&gt;"}) + "</script>")
    assert "Build APIs in Python" in jobicy.parse_detail(page)
    with pytest.raises(jobicy.JobicyError):
        jobicy.parse_detail("<html></html>")


def test_adzuna_skipped_without_keys(monkeypatch, capsys):
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    monkeypatch.delenv("ADZUNA_APP_KEY", raising=False)
    assert adzuna.fetch(object(), ["python"], 10, NOW) == []
    assert "skipped" in capsys.readouterr().out


def test_adzuna_parse_and_error_never_leaks_key(monkeypatch):
    recs = adzuna.parse({"results": [{"title": "Data Analyst", "redirect_url": "https://www.adzuna.ca/land/ad/1",
                                       "company": {"display_name": "Acme"}, "location": {"display_name": "Toronto, Ontario"},
                                       "created": "2026-11-08T10:00:00Z", "description": "SQL"}]})
    assert recs[0]["company_name"] == "Acme" and recs[0]["location"] == "Toronto, Ontario"

    monkeypatch.setenv("ADZUNA_APP_ID", "ID123")
    monkeypatch.setenv("ADZUNA_APP_KEY", "SECRETKEY")

    class Boom:
        def get_json(self, url, ats):
            raise RuntimeError(f"failed {url}")

    with pytest.raises(adzuna.AdzunaError) as e:
        adzuna.fetch(Boom(), ["python"], 10, NOW)
    assert "SECRETKEY" not in str(e.value) and "ID123" not in str(e.value)


def test_sites_registered():
    assert {"Jobicy", "Adzuna"} <= set(SITE_SOURCES)
    assert ats.detect_ats("https://jobicy.com/jobs/160682-staff-product-manager-mobile") == "Jobicy"
    assert ats.detect_ats("https://www.adzuna.ca/land/ad/123") == "Adzuna"
