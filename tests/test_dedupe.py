from datetime import datetime, timezone

import pytest

from scanner.dedupe import canonical_url, company_match, dedupe, title_jaccard
from scanner.models import Job

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 10, 3, tzinfo=timezone.utc)


def job(company="Acme", title="Software Engineer", location="Toronto, ON", url="https://x.com/1",
        source="Greenhouse", first_seen=T1, **kw):
    return Job(company=company, title=title, location=location, url=url, source=source,
               first_seen=first_seen, sources={source}, **kw)


@pytest.mark.parametrize("raw,expected", [
    ("https://Boards.Greenhouse.io/acme/jobs/1?gh_src=x&t=y", "https://boards.greenhouse.io/acme/jobs/1"),
    ("https://jobs.lever.co/acme/abc/", "https://jobs.lever.co/acme/abc"),
    ("https://acme.wd1.myworkdayjobs.com/Ext/job/Toronto/Dev_R1?x=1#top",
     "https://acme.wd1.myworkdayjobs.com/Ext/job/Toronto/Dev_R1"),
    ("https://x.com/Path/CaseKept", "https://x.com/Path/CaseKept"),
    # job-id params survive; tracking params next to them do not
    ("https://www.pinterestcareers.com/jobs/?gh_jid=8138065&utm_source=x",
     "https://www.pinterestcareers.com/jobs?gh_jid=8138065"),
    ("https://boards.greenhouse.io/robinhood/jobs/8246088?t=gh_src=&gh_jid=8246088",
     "https://boards.greenhouse.io/robinhood/jobs/8246088?gh_jid=8246088"),
])
def test_canonical_url(raw, expected):
    assert canonical_url(raw) == expected


@pytest.mark.parametrize("a,b,expected", [
    ("Motorola", "motorolasolutions", True),          # prefix
    ("Motorola Solutions, Inc.", "Motorola", True),
    ("Expedia Group", "expedia", True),
    ("Shopify", "Shopify Inc", True),
    ("Acme Corp", "Acme Corporation", True),
    ("Stripe", "Strip", True),                       # ratio 0.91
    ("Stripe", "Square", False),
    ("", "Acme", False),
])
def test_company_match(a, b, expected):
    assert company_match(a, b) is expected


def test_company_prefix_rule_is_loose_by_design():
    # "meta" is a prefix of "metabase", so the company test alone says match;
    # same city + title Jaccard >= 0.8 are what keep unrelated employers apart.
    assert company_match("Meta", "Metabase")


def test_title_jaccard():
    assert title_jaccard("Software Engineer - Emergency Call Handling",
                         "Software Engineer, Emergency Call Handling") == 1.0
    assert title_jaccard("Software Engineer", "Data Analyst") == 0.0
    assert title_jaccard("Junior Software Engineer", "Software Engineer") == pytest.approx(2 / 3)


def test_pass_a_url_merge_ignores_query_and_trailing_slash():
    a = job(url="https://boards.greenhouse.io/acme/jobs/1?t=gh_src", title="Software Engineer")
    b = job(url="https://Boards.greenhouse.io/acme/jobs/1/", title="Totally different title")
    jobs, merges = dedupe([a, b])
    assert len(jobs) == 1 and merges == {"url": 1, "fuzzy": 0}


def test_pass_a_keeps_distinct_jobs_behind_one_path():
    # Real data: pinterestcareers.com serves every Greenhouse job from /jobs/?gh_jid=<id>.
    a = job(company="pinterest", title="University Grad Machine Learning Engineer", url="https://www.pinterestcareers.com/jobs/?gh_jid=8138065")
    b = job(company="pinterest", title="University Grad Software Engineer", url="https://www.pinterestcareers.com/jobs/?gh_jid=8138049")
    jobs, merges = dedupe([a, b])
    assert len(jobs) == 2 and merges["url"] == 0


def test_pass_b_motorola_simplify_vs_ats():
    simp = job(company="Motorola", title="Junior Software Engineer - Emergency Call Handling",
               location="Gatineau, QC, Canada", url="https://simplify/redirect/1", source="Simplify",
               first_seen=T0, new_grad=True)
    ats = job(company="motorolasolutions", title="Junior Software Engineer, Emergency Call Handling",
              location="Gatineau, Canada, More...", url="https://motorolasolutions.wd5.myworkdayjobs.com/J/1",
              source="Workday", first_seen=T1)
    jobs, merges = dedupe([simp, ats])
    assert merges == {"url": 0, "fuzzy": 1}
    (merged,) = jobs
    assert merged.source == "Workday" and merged.url.endswith("/J/1")
    assert merged.new_grad and merged.sources == {"Simplify", "Workday"}
    assert merged.first_seen == T0
    assert merged.source_label() == "Workday+Simplify"


def test_pass_b_does_not_merge_different_cities():
    jobs, merges = dedupe([job(url="https://x/1", location="Toronto, ON"),
                           job(url="https://x/2", location="Vancouver, BC")])
    assert len(jobs) == 2 and merges["fuzzy"] == 0


def test_pass_b_does_not_merge_dissimilar_titles():
    jobs, _ = dedupe([job(url="https://x/1", title="Software Engineer"),
                      job(url="https://x/2", title="Data Analyst")])
    assert len(jobs) == 2


def test_known_miss_title_aliases_not_merged():
    # "Model Context Protocol/AI Developer" vs "MCP/AI Developer": Jaccard 0.33 < 0.8.
    jobs, _ = dedupe([job(company="Autodesk", title="Model Context Protocol/AI Developer",
                          url="https://s/1", source="Simplify"),
                      job(company="autodesk", title="MCP/AI Developer", url="https://a/1")])
    assert len(jobs) == 2


def test_merge_weak_title_only_if_both_weak_and_keys_assigned():
    jobs, _ = dedupe([job(url="https://x/1", weak_title=True), job(url="https://x/1?q=1", weak_title=False)])
    assert jobs[0].weak_title is False and len(jobs[0].key) == 40
