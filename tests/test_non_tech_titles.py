"""Non-tech roles that reached the Feed (SPEC §11 #67): title excludes, and a `low signal` job needs a developer
noun in its title."""
import pytest

from scanner import enrich as E
from scanner import feed_clean as FC
from scanner.cluster import ClusterResolver
from scanner.config import load_config
from scanner.filters import Filters
from scanner.score import Scorer
from tests.test_enrich import gh, run

CFG = load_config()
FILTERS = Filters.from_config(CFG)
SCORER = Scorer.load()

LIVE = [   # (company, title) as they appeared in the Feed
    ("Kasko Cattle", "Front End Crew (Cattle Processor)"),
    ("ChampionX", "Journeyperson Electrician Automation Specialist"),
    ("TJX", "Loss Prevention Store Security Agent"),
    ("Giant Tiger", "Automation Cell Operator"),
    ("Ebury", "Business Developer (FX Financial Solutions)"),
    ("Equinix", "Distinguished Engineer, AI Infrastructure"),
    ("City of Hamilton", "Job ID #32279: Security Investigator, Corporate Security"),
    ("Markel", "Associate Underwriter - Cyber & Tech E&O"),
    ("Cineflix", "AI Artist"),
]


@pytest.mark.parametrize("company,title", LIVE)
def test_live_examples_are_excluded_by_title(company, title):
    assert not FILTERS.title_not_excluded(title), title


@pytest.mark.parametrize("company,title", LIVE)
def test_live_examples_are_trashed_by_clean_feed(company, title):
    it = FC.FeedItem("p", title, company, "https://x.example/1", "Workday", "Greet customers.", ["low signal"],
                     False, False)
    v = FC.evaluate(it, "Toronto, ON", CFG, FILTERS, SCORER, ClusterResolver(CFG))
    assert v.rule == FC.RULE_TITLE and it.untouched


@pytest.mark.parametrize("title", [
    "Data Engineer", "Software Developer", "Business Intelligence Developer", "Business Intelligence Analyst",
    "Storage Engineer", "Distributed Systems Engineer", "Operations Engineer I", "Data Platform Engineer",
    "Cloud Operations Analyst", "Security Engineer", "Retail Systems Developer",
])
def test_tech_titles_survive_the_new_excludes(title):
    assert FILTERS.title_not_excluded(title), title


def test_low_signal_job_needs_a_developer_noun_in_its_title():
    sparse = "We value teamwork and punctuality. Python is a plus."             # one term: Fit n/a, `low signal`
    planner = run([gh(1, title="Automation Planner")], jds={"1": sparse})
    engineer = run([gh(2, title="Software Engineer")], jds={"2": sparse})
    analysts = run([gh(3, title="Data Analysts")], jds={"3": sparse})
    assert planner.kept == [] and planner.drops == {E.R_LOW_SIGNAL_TITLE: 1}
    assert len(engineer.kept) == 1 and "low signal" in engineer.kept[0].signals
    assert len(analysts.kept) == 1                                              # plural allowed


def test_a_title_without_a_developer_noun_is_kept_when_fit_is_known():
    jd = "Requirements:\n- Python, SQL, Power BI and Excel\n"
    res = run([gh(1, title="Automation Planner")], jds={"1": jd})
    assert len(res.kept) == 1 and res.kept[0].fit_pct is not None


@pytest.mark.parametrize("title,dev", [("Junior Developer", True), ("Programmer", True), ("ML Scientist", True),
                                        ("Network Analyst I", True), ("Engineering Lead", False),
                                        ("Automation Cell Operator", False), ("AI Artist", False)])
def test_dev_title(title, dev):
    assert E.dev_title(title, CFG) is dev
