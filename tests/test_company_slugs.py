from datetime import datetime, timezone

import pytest

from scanner import company_names as cn
from scanner.models import Job

OVERRIDES = cn.load_overrides()


def job(company, company_name=""):
    return Job(company=company, company_name=company_name, title="Software Engineer", location="Toronto, ON",
               url="https://x/1", source="Workday", first_seen=datetime(2026, 10, 6, tzinfo=timezone.utc))


@pytest.mark.parametrize("slug,shown", [
    ("bdo", "BDO"), ("flynncompanies", "Flynn Companies"), ("luminegrp", "Lumine Group"),
    ("intouchinsight", "Intouch Insight"), ("vistavu", "VistaVu Solutions"), ("optrust", "OPTrust"),
])
def test_names_added_after_the_feed_quality_check(slug, shown):
    assert cn.display_company(job(slug), OVERRIDES) == shown


@pytest.mark.parametrize("slug,flag", [
    ("bdo", True),                       # single lowercase word
    ("newcompany", True),
    ("Intouchinsightcorp", True),        # capitalised, no spaces, over 10 characters
    ("Valtech", False),                  # capitalised and short
    ("periodic-labs", False),            # prettifies to two words
    ("Tower Research Capital", False),   # already has spaces
])
def test_looks_like_slug(slug, flag):
    assert cn.looks_like_slug(job(slug), {}) is flag


def test_named_companies_are_not_flagged():
    assert not cn.looks_like_slug(job("bdo"), OVERRIDES)                       # override
    assert not cn.looks_like_slug(job("tucows", company_name="Tucows Inc."), {})   # readable name from the source


def test_slug_names_lists_each_company_once_with_what_is_shown():
    jobs = [job("newcompany"), job("newcompany"), job("bdo"), job("Valtech")]
    assert cn.slug_names(jobs, OVERRIDES) == [("newcompany", "Newcompany")]
