import pytest

from scanner.score import Scorer, requirements_text


@pytest.fixture(scope="module")
def scorer():
    return Scorer.load()


def names(fit):
    return set(fit.matched)


@pytest.mark.parametrize("jd,skill", [
    ("Strong C++ skills", "C/C++"),
    ("Experience in C/C++ required", "C/C++"),
    ("We write C# services", "C#"),
    ("Build on the .NET platform", ".NET"),
    ("ASP.NET Core experience", ".NET"),
    ("Familiar with Node.js", "Node.js"),
    ("Own the CI/CD pipeline", "CI/CD"),
    ("Docker and Kubernetes", "Kubernetes"),
    ("PostgreSQL or Postgres", "PostgreSQL"),
    ("Power BI dashboards", "Power BI"),
    ("scikit-learn and pandas", "scikit-learn"),
    ("TypeScript and React", "React"),
])
def test_alias_matching(scorer, jd, skill):
    assert skill in names(scorer.evaluate(jd))


def test_word_boundaries_do_not_leak(scorer):
    fit = scorer.evaluate("JavaScript only. Also MySQL and PostgreSQL.")
    assert "JavaScript" in fit.matched
    assert "Java" in scorer.evaluate("Java developer").gaps
    assert "Java" not in fit.gaps          # JavaScript is not Java
    assert "SQL" not in fit.matched        # MySQL / PostgreSQL do not count as bare SQL
    assert "MySQL" in fit.gaps


def test_c_plus_plus_does_not_match_plain_c(scorer):
    assert "C/C++" not in names(scorer.evaluate("We use C in our coursework; see section C. Not C-suite."))


def test_case_sensitive_words(scorer):
    assert not names(scorer.evaluate("we react quickly to the rest of the team"))
    assert {"React", "REST APIs"} <= names(scorer.evaluate("React and REST"))


def test_fit_percent_and_gaps(scorer):
    fit = scorer.evaluate("Python, SQL, Docker. Java and Kafka a plus.")
    assert fit.matched == ["Docker", "Python", "SQL"] and fit.gaps == ["Java", "Kafka"]
    assert fit.fit_pct == 60.0           # 3 / (3 + 2), nothing in a requirements section


def test_requirements_section_weighted_double(scorer):
    jd = "About us\nWe use Java.\n\nRequirements:\n- Python\n- SQL\n\nNice to have\n- Kubernetes\n"
    fit = scorer.evaluate(jd)
    # Python, SQL in requirements (2 each) + Kubernetes (1) = 5; Java outside requirements = 1
    assert fit.fit_pct == round(100 * 5 / 6, 1)


def test_requirements_text_stops_at_next_heading():
    jd = "Intro\nWhat you'll need\n- Python\n\nBenefits\n- Dental\n"
    assert "Python" in requirements_text(jd) and "Dental" not in requirements_text(jd)
    assert requirements_text("no headings here") == ""


def test_no_terms_means_no_fit(scorer):
    fit = scorer.evaluate("Greet customers and stock shelves.")
    assert fit.fit_pct is None and fit.cluster is None and fit.matched == [] and fit.gaps == []


def test_cluster_is_the_one_with_most_matches(scorer):
    assert scorer.evaluate("SQL, Power BI, Databricks, Python").cluster == "data"
    assert scorer.evaluate("PyTorch, TensorFlow, LLM, pandas").cluster == "ml"
    assert scorer.evaluate("Docker, AWS, React, Git").cluster == "software"
    assert scorer.evaluate("CrowdStrike").cluster == "security"
