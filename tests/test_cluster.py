import pytest

from scanner.cluster import ClusterResolver
from scanner.config import load_config

R = ClusterResolver(load_config())


@pytest.mark.parametrize("title,cluster", [
    ("Data Analyst", "data"), ("Analytics Engineer", "data"), ("BI Developer", "data"),
    ("Database Administrator", "data"), ("Data Engineer", "data"),
    ("Machine Learning Engineer", "ml"), ("ML Engineer", "ml"), ("AI Engineer", "ml"),
    ("Research Scientist", "ml"),
    ("Security Engineer", "security"), ("Cyber Security Developer", "security"), ("Cyber Analyst", "security"),
    # several keywords: security > ml > data
    ("Data Scientist", "ml"), ("Data & AI Analyst", "ml"), ("AI Security Analyst", "security"),
    ("Data Security Engineer", "security"),
])
def test_title_keyword_decides(title, cluster):
    assert R.from_title(title) == cluster


@pytest.mark.parametrize("title", ["Software Engineer", "Backend Developer", "DevOps Engineer", "Risk Analyst",
                                   "Maintenance Specialist", "Paid Advocate"])
def test_no_keyword_means_none(title):
    assert R.from_title(title) is None


def test_title_beats_jd_terms():
    assert R.resolve("Data Analyst", "software") == "data"
    assert R.resolve("Security Engineer", "data") == "security"


def test_jd_terms_used_only_when_title_has_no_keyword():
    assert R.resolve("Risk Analyst", "data") == "data"
    assert R.resolve("Software Engineer", "ml") == "ml"


def test_default_is_software():
    assert R.resolve("Software Engineer", None) == "software"
    assert R.resolve("Specialist", None) == "software"


def test_word_keywords_do_not_match_inside_words():
    assert R.from_title("Retail Associate") is None       # "ai" inside "retail"
    assert R.from_title("Email Developer") is None
