from datetime import datetime, timezone

import pytest

from scanner import company_names as cn
from scanner.models import Job


def job(company="acme", company_name="", day=datetime(2026, 10, 6, 13, tzinfo=timezone.utc)):
    return Job(company=company, company_name=company_name, title="Software Engineer", location="Toronto, ON",
               url="https://x/1", source="Greenhouse", first_seen=day, sources={"Greenhouse"})


@pytest.mark.parametrize("month,day,tag", [
    (1, 3, "jan3"), (2, 28, "feb28"), (3, 1, "mar1"), (4, 15, "apr15"), (5, 9, "may9"), (6, 30, "jun30"),
    (7, 4, "jul4"), (8, 31, "aug31"), (9, 20, "sept20"), (10, 6, "oct6"), (11, 11, "nov11"), (12, 25, "dec25"),
])
def test_date_tag_months_and_no_leading_zero(month, day, tag):
    assert cn.date_tag(datetime(2026, month, day, tzinfo=timezone.utc)) == tag


def test_company_cell_format():
    overrides = {"rbc": "RBC", "clio": "Clio"}
    assert cn.company_cell(job("rbc"), overrides) == "RBC · oct6"
    assert cn.company_cell(job("clio", day=datetime(2026, 9, 20, tzinfo=timezone.utc)), overrides) == "Clio · sept20"


def test_precedence_override_then_readable_name_then_prettified_slug():
    overrides = {"ubc": "UBC", "generalmotors": "General Motors"}
    assert cn.display_company(job("ubc", company_name="The University of British Columbia"), overrides) == "UBC"
    assert cn.display_company(job("heygen", company_name="HeyGen"), overrides) == "HeyGen"        # Greenhouse/Simplify
    assert cn.display_company(job("periodic-labs"), overrides) == "Periodic Labs"                  # prettified slug
    assert cn.display_company(job("generalmotors"), overrides) == "General Motors"


def test_override_keys_are_normalised():
    assert cn.display_company(job("General-Motors"), {"generalmotors": "General Motors"}) == "General Motors"


@pytest.mark.parametrize("slug,pretty", [
    ("periodic-labs", "Periodic Labs"), ("method_crm", "Method Crm"), ("tailscale", "Tailscale"),
    ("ACCULOGIC LTD", "ACCULOGIC LTD"), ("Motorola", "Motorola"), ("", ""),
])
def test_prettify(slug, pretty):
    assert cn.prettify(slug) == pretty


def test_seeded_overrides_cover_the_examples_and_load():
    o = cn.load_overrides()
    assert o["rbc"] == "RBC" and o["ubc"] == "UBC" and o["generalmotors"] == "General Motors"
    assert o["canadiantirecorporation"] == "Canadian Tire" and o["canadiantirecorp"] == "Canadian Tire"
