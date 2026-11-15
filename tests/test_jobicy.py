import json
from datetime import datetime, timezone

import pytest

from scanner.sources import jobicy

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


