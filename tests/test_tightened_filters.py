from datetime import datetime, timedelta, timezone

import pytest

from scanner.pipeline import run_stream
from scanner.sources import feashliaa


# --- location: Quebec only ------------------------------------------------------------

@pytest.mark.parametrize("loc,status", [
    ("Montréal, QC", "quebec_only"),
    ("Montreal, Quebec, Canada", "quebec_only"),
    ("Québec City, Québec", "quebec_only"),
    ("Laval, QC", "quebec_only"),
    ("Gatineau, QC", "quebec_only"),
    ("Longueuil, QC", "quebec_only"),
    ("Sherbrooke, Quebec", "quebec_only"),
    ("Lévis, QC", "quebec_only"),
    ("Saint-Laurent, QC", "quebec_only"),
    ("Quebec", "quebec_only"),
    ("Montreal, QC ; Gatineau, QC", "quebec_only"),             # every location is in Quebec
    ("Seattle, WA ; Montreal, QC", "quebec_only"),              # the only Canadian location is in Quebec
    ("Toronto, ON ; Montreal, QC", "ok"),                       # a non-Quebec location is kept
    ("Montreal, QC | Vancouver, BC", "ok"),
    ("Remote, Canada", "ok"),
    ("Canada - Remote ; Montreal, QC", "ok"),
    ("Remote (Canada)", "ok"),
    ("Ottawa, ON - Gatineau, QC", "ok"),
    ("Ottawa-Gatineau", "ok"),
    ("Montreal, QC, Toronto, ON", "ok"),
    ("Vancouver, BC", "ok"),
    ("Austin, TX", "not_canada"),
    ("2 Locations", "unresolved"),
    ("12 Locations", "unresolved"),
])
def test_location_status(filters, loc, status):
    assert filters.location_status(loc)[0] == status


def test_canadian_location_prefers_a_non_quebec_part(filters):
    assert filters.canadian_location("Montreal, QC ; Toronto, ON") == "Toronto, ON"
    assert filters.canadian_location("2 Locations") is None


# --- language: French first segment ----------------------------------------------------

@pytest.mark.parametrize("title,ok", [
    ("Développeur de logiciels / Software Developer", False),
    ("développeur de systèmes d’affaires / Business Systems Developer", False),
    ("Analyste de données / Data Analyst", False),
    ("Ingénieur(e) en vérification de logiciel QC/ Software Verification Engineer QC", False),
    ("Software Developer / Développeur de logiciels", True),
    ("Data Analyst / Analyste de données", True),
    ("Software Engineer", True),
    ("CI/CD Engineer", True),
    ("Frontend/Backend Developer", True),
    ("Ingénieur logiciel", False),
])
def test_french_first_segment_of_a_bilingual_title(filters, title, ok):
    assert filters.language_ok(title) is ok


# --- titles ---------------------------------------------------------------------------

@pytest.mark.parametrize("title", [
    "Directeur technique - Cloud natif, IA et DevOps", "DIRECTEUR, Plateforme", "Directrice des données",
    "Director of Engineering", "Expert Observability Engineer", "Grafana - Expert Observability Engineer",
    "PhD Researcher, AI Research", "Customer Support Specialist", "Support Specialist, Data",
    "Data Specialist", "Flexible Work - Help Improve AI - English Speakers", "Help Improve AI",
    "English Speaker Wanted", "Data Annotation Analyst", "Data Labeling Engineer", "AI Trainer",
    "Spécialiste des données Directrice adjointe",
])
def test_new_title_excludes(filters, title):
    assert not filters.title_not_excluded(title)


@pytest.mark.parametrize("title", [
    "Support Engineer", "Customer Support Developer (Databases)", "Technical Support Engineer",
    "Software Developer", "Data Engineer", "Experts Exchange Platform Engineer".replace("Experts ", "Experienced "),
    "Phdata Engineer",           # "phd" only as a whole word
])
def test_support_engineers_and_developers_are_not_excluded(filters, title):
    assert filters.title_not_excluded(title)


def test_title_exclude_is_accent_insensitive(filters):
    assert not filters.title_not_excluded("Directeur")
    assert not filters.title_not_excluded("DIRECTEUR")
    assert not filters.title_not_excluded("Dîrectrice".replace("î", "i"))


# --- company blocklist ------------------------------------------------------------------

@pytest.mark.parametrize("company", [
    "mthree", "mthree Recruiting Portal", "MTHREE", "mthree Inc.", "Mthree-Recruiting",
    "Lifted Solutions", "lifted solutions", "LiftedanUpworkCompany", "Lifted an Upwork Company",
])
def test_blocked_companies(filters, company):
    assert not filters.company_ok(company)


@pytest.mark.parametrize("company", ["Lifted", "Liftoff", "Three Mile", "Shopify"])
def test_other_companies_are_not_blocked(filters, company):
    assert filters.company_ok(company)


# --- pipeline -------------------------------------------------------------------------

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


def rec(title="Software Engineer", loc="Toronto, ON", company="Acme", url="https://x/1"):
    first = (NOW - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    return {"company": company, "title": title, "location": loc, "ats": "Workday", "url": url, "first_seen": first}


def test_quebec_only_stage_and_workday_multi_location_pass_through(filters):
    recs = [
        rec(url="https://x/ok"),
        rec(loc="Montréal, QC", url="https://x/qc"),
        rec(loc="Toronto, ON ; Montreal, QC", url="https://x/both"),
        rec(loc="2 Locations", url="https://acme.wd1.myworkdayjobs.com/ext/job/Dallas-TX/Software-Engineer_R1"),
        rec(loc="Austin, TX", url="https://x/us"),
        rec(title="Développeur de logiciels / Software Developer", url="https://x/fr"),
        rec(company="mthree Recruiting Portal", url="https://x/mthree"),
        rec(title="Data Specialist", url="https://x/ds"),
    ]
    jobs, stages, _ = run_stream(recs, feashliaa.raw_fields, filters, NOW, since_days=3)
    assert sorted(j.url for j in jobs) == sorted([
        "https://x/ok", "https://x/both", "https://acme.wd1.myworkdayjobs.com/ext/job/Dallas-TX/Software-Engineer_R1"])
    st = dict(stages)
    assert st["location"] == 7 and st["not_quebec_only"] == 6       # Austin out at "location", Montréal at the next stage
    pending = [j for j in jobs if j.location_pending]
    assert [j.location for j in pending] == ["2 Locations"]
    assert {j.url: j.location for j in jobs if not j.location_pending}["https://x/both"] == "Toronto, ON"


def test_pending_workday_jobs_are_not_merged_or_keyed_by_the_placeholder_location():
    from scanner.dedupe import dedupe
    from scanner.models import Job

    def wd(i):
        return Job(company="acme", title="Software Engineer", location="2 Locations", source="Workday",
                   url=f"https://acme.wd1.myworkdayjobs.com/ext/job/City-{i}/Software-Engineer_R{i}",
                   first_seen=NOW, sources={"Workday"}, location_pending=True)

    out, merges = dedupe([wd(1), wd(2)])
    assert len(out) == 2 and merges == {"url": 0, "fuzzy": 0}
    assert out[0].key != out[1].key
