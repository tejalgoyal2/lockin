"""The rules that decide what reaches the Feed and what its Score and Exp columns say (scanner/enrich.py, rating.py)."""
from datetime import datetime, timezone

import pytest

from scanner import enrich as E
from scanner import rating as R
from scanner.config import load_config
from scanner.filters import Filters
from scanner.models import Job
from scanner.score import Scorer

T = datetime(2026, 11, 10, tzinfo=timezone.utc)
CFG = load_config()
FILTERS = Filters.from_config(CFG)
SCORER = Scorer.load()
FILLER = "We are a friendly team that values collaboration, ownership and clear communication in everything we do. " * 8
STACK = "Requirements:\n- Python, SQL, React and TypeScript\n- Docker, Git and Azure\n"          # 7 of the owner's tools


def rate(title, jd=STACK, *, loc="Vancouver, BC", student=False, status="ok", **kw):
    """(drop reason or None, the job) after the rules."""
    job = Job(company="acme", title=title, location=loc, url="https://boards.greenhouse.io/acme/jobs/1",
              source="Greenhouse", first_seen=T, sources={"Greenhouse"}, jd=jd if status == "ok" else "",
              jd_status=status, student=student, **kw)
    return E.apply_rules(job, CFG, FILTERS, SCORER), job


# --- experience: the owner's line is 2 years ---------------------------------------------------------

@pytest.mark.parametrize("text, exp", [
    ("0-2 years of experience.", 1), ("No experience required.", 1), ("6 months of experience.", 1),
    ("1+ years of experience.", 2), ("1-5 years of experience.", 2), ("1 to 3 years of experience.", 2),
    ("", 2),                                           # no number: not stated
    ("2 years of experience.", 3), ("2+ years of experience.", 3), ("Minimum two (2) years of experience.", 3),
])
def test_exp_range(text, exp):
    reason, job = rate("Software Developer", STACK + text)
    assert reason is None and job.exp == exp


@pytest.mark.parametrize("text", [
    "3 years of experience.", "3+ years of experience.", "5+ years of experience.", "2-5 years of experience.",
    "2-3 years of experience.", "3-5 years of experience.",
    "5+ years of experience in software engineering. 1+ years experience with SQL.",       # the main requirement decides
])
def test_more_than_two_years_is_dropped(text):
    reason, job = rate("Software Developer", STACK + text)
    assert reason == E.R_EXPERIENCE and job.drop_note.startswith("asks ")


def test_a_title_that_says_who_the_job_is_for_makes_a_one_to_two_year_posting_entry_level():
    assert rate("Junior Software Developer", STACK + "1-2 years of experience.")[1].exp == 1
    assert rate("Software Developer (New Grad)", STACK + "1+ years of experience.")[1].exp == 1
    assert rate("Junior Software Developer", STACK + "2 years of experience.")[1].exp == 3      # 2 years is 2 years
    assert rate("Software Developer", STACK + "New grads welcome.")[1].exp == 1                 # no number, and it says so


# --- is it a tech job? --------------------------------------------------------------------------------

def test_generic_title_needs_two_of_the_owners_tools_in_a_full_description():
    assert rate("Treasury Analyst", FILLER + "Advanced Excel. Troubleshooting. Agile.")[0] == E.R_NOT_TECH   # one tool
    assert rate("Mortgage Specialist", FILLER)[0] == E.R_NOT_TECH
    assert rate("Operations Analyst", FILLER + "SQL and Power BI dashboards.")[0] is None
    assert rate("Learning Specialist", "Help people learn.")[0] == E.R_NOT_TECH       # short, and not one tool
    assert rate("Founding Engineer", "Build our product in Python.")[0] is None       # short: one tool is enough


def test_adjacent_title_needs_one_tool_or_technology():
    assert rate("IT Support Analyst", FILLER + "Troubleshooting for our users.")[0] == E.R_NOT_TECH
    assert rate("IT Support Analyst", FILLER + "Support Intune and Okta.")[0] is None
    assert rate("Systems Analyst", FILLER + "Experience with SAP.")[0] is None        # a gap is still technology
    assert rate("IT Support Analyst", "Help our users.")[0] is None                   # too short to rule it out


def test_core_title_is_doubted_only_when_it_is_ambiguous_and_the_description_has_no_tech_word():
    assert rate("Event Security - Part-Time", FILLER)[0] == E.R_NOT_TECH
    assert rate("Inventory - Field Data Collector", FILLER)[0] == E.R_NOT_TECH
    assert rate("Software Developer", FILLER)[0] is None            # Job Bank postings name no tools
    assert rate("Data Analyst", FILLER + "Maintain the database.")[0] is None


@pytest.mark.parametrize("jd, reason", [
    (STACK + "You must be currently enrolled in a degree program.", E.R_ENROLLMENT),
    (STACK + "Active security clearance is required.", E.R_CLEARANCE),
    (STACK + "Fluency in French is required.", E.R_FRENCH),
])
def test_other_drops(jd, reason):
    assert rate("Software Developer", jd)[0] == reason


# --- student roles: the owner has graduated ------------------------------------------------------------

def test_an_internship_is_kept_unless_it_requires_enrollment_and_scores_at_most_3_until_it_says_graduates_may_apply():
    assert rate("Software Developer Intern", STACK + "Must be returning to school after the term.", student=True)[0] == E.R_ENROLLMENT
    reason, plain = rate("Software Developer Intern", STACK, student=True)
    assert reason is None and plain.exp == 1 and plain.rating == 3 and R.INTERN in plain.notes
    _, open_ = rate("Software Developer Intern", STACK + "You're completing or have recently finished a degree.", student=True)
    assert open_.rating == 5 and R.GRADS_OK in open_.notes
    # a bank's intern posting carries its template "4-6 years" line: not a reason to drop a student role
    assert rate("AI Developer Co-op", STACK + "Typically between 4 - 6 years of relevant experience.", student=True)[0] is None


# --- Score ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("title, jd, score", [
    ("Junior Software Developer", STACK, 5),                                   # core 2 + skills 2 + entry 1
    ("Software Developer", STACK, 4),                                          # no level stated
    ("Software Developer", "Requirements:\n- Python and SQL\n- Java and Kafka\n", 3),        # 2 tools, 40 % of the stack
    ("Java Developer", "Requirements:\n- Python and SQL\n- Java, Spring, Kafka and Go\n", 2),  # the title's stack is a gap
    ("Software Developer", FILLER, 3),                                         # names no stack: not held against it
    ("IT Administrator", STACK, 3),                                            # adjacent 1 + skills 2
    ("Solutions Engineer", STACK, 3),                                          # generic, but the description is his work
    ("Operations Analyst", FILLER + "SQL and Power BI dashboards.", 1),        # generic, half vouched for
    ("Software Developer", STACK + "2+ years of experience.", 2),              # 2 years: a long shot, whatever the fit
    ("Software Developer", STACK + "Must be a licensed P.Eng in good standing.", 1),
    ("Software Developer", STACK + "This is an evergreen requisition.", 2),
])
def test_score(title, jd, score):
    reason, job = rate(title, jd)
    assert reason is None and job.rating == score, (job.role, job.fit, job.hard, job.notes)


def test_no_description_is_kept_as_a_low_score_unless_the_title_is_generic_and_plainly_not_tech():
    reason, job = rate("Software Developer", status="error")
    assert reason is None and job.rating == 2 and R.NO_JD in job.notes
    assert rate("Junior Software Developer", status="error")[1].rating == 2        # nothing confirms the fit
    assert rate("Business Analyst", status="error")[1].rating == 1
    assert rate("Warehouse Specialist", status="error")[0] == E.R_NO_JD_GENERIC


# --- provinces in phase 2 --------------------------------------------------------------------------------

def test_the_postings_own_places_decide_the_province_tags():
    reason, job = rate("Software Developer", loc="Toronto, ON", jd_locations=["Remote - British Columbia", "Toronto, ON"])
    assert reason is None and job.provinces == ["BC", "Remote", "Ontario"] and job.location == "Remote - British Columbia"
    assert rate("Software Developer", loc="Canada", jd_locations=["Winnipeg, MB"])[0] == E.R_OTHER_AREA
    assert rate("Software Developer (Remote)", loc="Calgary, AB")[1].provinces == ["Remote", "Alberta"]
    assert rate("Software Developer", loc="Canada")[1].provinces == ["Canada"]


def test_kept_jobs_come_best_first():
    a, b, c = rate("Software Developer")[1], rate("Junior Software Developer")[1], rate("IT Administrator")[1]
    assert [j.rating for j in sorted([a, c, b], key=E.rank_key)] == [5, 4, 3]
