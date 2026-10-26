import pytest

from scanner.score import Scorer, heading_kind, requirements_text


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
    assert fit.fit_pct == round(100 * 3 / 6, 1)   # matched / (matched + gaps + 1) = 3 / (3 + 2 + 1)


def test_section_weights(scorer):
    jd = "About us\nWe use Java.\n\nRequirements:\n- Python\n- SQL\n\nNice to have\n- Kubernetes\n"
    fit = scorer.evaluate(jd)
    assert fit.weights == {"Python": 1.0, "SQL": 1.0, "Kubernetes": 0.5, "Java": 0.5}
    assert fit.fit_pct == 62.5                    # (1 + 1 + 0.5) / (2.5 + 0.5 + 1)


def test_same_terms_score_the_same_with_or_without_a_requirements_heading(scorer):
    # Before: weights 2 / 1 against a fixed smoothing of 2, so a JD with a recognised heading scored higher.
    flat = scorer.evaluate("Python, SQL, Docker and Git.")
    headed = scorer.evaluate("About the role\nBuild things.\n\nMinimum Requirements:\n- Python, SQL, Docker and Git\n")
    assert flat.fit_pct == headed.fit_pct == 80.0          # 4 / (4 + 1)


def test_a_term_only_in_nice_to_have_counts_half_even_without_a_requirements_heading(scorer):
    fit = scorer.evaluate("Build services in Python and SQL.\n\nNice to have\n- Kafka\n- Java\n")
    assert fit.weights == {"Python": 1.0, "SQL": 1.0, "Kafka": 0.5, "Java": 0.5}
    assert fit.fit_pct == 50.0                    # 2 / (2 + 1 + 1)


@pytest.mark.parametrize("heading,kind", [
    ("What you'll bring:", "req"), ("What You Will Bring:", "req"), ("MINIMUM REQUIREMENTS:", "req"),
    ("Required Qualifications", "req"), ("Required Skills and Competencies", "req"), ("Your Background", "req"),
    ("Skills We’re Looking For", "req"), ("What We Are Looking For", "req"), ("The Ideal Candidate:", "req"),
    ("Here are some of the things we are looking for", "req"), ("EDUCATION AND EXPERIENCE", "req"),
    ("PREFERRED REQUIREMENTS:", "nice"), ("Nice to have's", "nice"), ("Bonus points if you bring:", "nice"),
    ("HIGHLY DESIRED SKILLS & EXPERIENCE", "nice"), ("Assets", "nice"), ("It would be great if you had", "nice"),
    ("Key Responsibilities", "other"), ("What you’ll do:", "other"), ("About Us", "other"), ("Benefits", "other"),
    ("Our Digital Expertise", "other"),
    ("- Must have a valid driver's license.", None), ("Experience with Python and SQL", None),
    ("We build data pipelines that power our product.", None),
])
def test_heading_kinds(heading, kind):
    assert heading_kind(heading) == kind


def test_the_hiring_companys_own_product_is_not_a_term(scorer):
    jd = "Requirements:\n- Experience with GitLab CI\n- Python, SQL and Docker\n"
    assert "GitLab" in scorer.evaluate(jd).gaps
    assert "GitLab" not in scorer.evaluate(jd, companies=("gitlab", "GitLab")).gaps


@pytest.mark.parametrize("jd,gaps", [
    ("Requirements:\n- A modern framework such as React, Angular, or Vue\n", []),          # React is owned
    ("Requirements:\n- Backend in C#, Java, Python, or JavaScript/TypeScript\n", []),
    ("Requirements:\n- Cloud warehouses (e.g., Snowflake, Databricks, Redshift, BigQuery)\n", []),
    ("Requirements:\n- Familiarity with PowerBI and/or Tableau\n", []),
    ("Requirements:\n- Data visualization tools (e.g., Looker, Tableau, Power BI)\n", []),
    ("Requirements:\n- Familiarity with Matlab/C++/Python\n", []),
    ("Requirements:\n- Experience with Java and Kafka\n- Python\n", ["Java", "Kafka"]),   # "and": both asked for
    ("Requirements:\n- Python, SQL and Spring Boot\n", ["Spring"]),
])
def test_alternatives_the_owner_has_one_of_are_not_gaps(scorer, jd, gaps):
    assert scorer.evaluate(jd).gaps == gaps


def test_alternatives_the_owner_has_none_of_count_as_one_gap(scorer):
    fit = scorer.evaluate("Requirements:\n- Python\n- Kafka or RabbitMQ\n")
    assert fit.gaps == ["Kafka", "RabbitMQ"] and fit.weights["Kafka"] == fit.weights["RabbitMQ"] == 0.5
    assert fit.fit_pct == 33.3                    # 1 / (1 + 1 + 1): one missing requirement, not two (25.0 if two)


def test_a_skill_inside_a_longer_gap_term_is_not_matched(scorer):
    fit = scorer.evaluate("Requirements:\n- Mobile apps in React Native\n- Python\n")
    assert "React" not in fit.matched and fit.gaps == ["React Native"]
    assert "React" in scorer.evaluate("Requirements:\n- React/React Native\n").matched     # React or React Native


def test_one_core_term_is_not_a_fit(scorer):
    jd = "Python. Backend, frontend, DevOps and networking."
    assert scorer.evaluate(jd, 4).fit_pct == 50.0                  # 1 skill + 4 context terms
    f = scorer.evaluate(jd, 4, min_core=2)
    assert f.fit_pct is None and f.n_core == 1 and f.n_terms == 5


def test_requirements_text_stops_at_next_heading():
    jd = "Intro\nWhat you'll need\n- Python\n\nBenefits\n- Dental\n\nQualifications\n- SQL\n"
    assert "Python" in requirements_text(jd) and "Dental" not in requirements_text(jd)
    assert "SQL" in requirements_text(jd)                         # every requirements section, not just the first
    assert requirements_text("no headings here") == ""


def test_no_terms_means_no_fit(scorer):
    fit = scorer.evaluate("Greet customers and stock shelves.")
    assert fit.fit_pct is None and fit.cluster is None and fit.matched == [] and fit.gaps == []


def test_cluster_is_the_one_with_most_matches(scorer):
    assert scorer.evaluate("SQL, Power BI, Databricks, Python").cluster == "data"
    assert scorer.evaluate("PyTorch, TensorFlow, LLM, pandas").cluster == "ml"
    assert scorer.evaluate("Docker, AWS, React, Git").cluster == "software"
    assert scorer.evaluate("CrowdStrike").cluster == "security"


def test_min_terms_gate(scorer):
    jd = "Python, SQL and Docker."
    assert scorer.evaluate(jd).fit_pct == 75.0                     # default: no gate; 3 / (3 + 1), never 100 %
    low = scorer.evaluate(jd, min_terms=4)
    assert low.fit_pct is None and low.n_terms == 3 and low.matched == ["Docker", "Python", "SQL"]
    assert scorer.evaluate(jd + " Java.", min_terms=4).fit_pct == 60.0   # 3 matched + 1 gap = 4 terms; 3 / (4 + 1)


def test_process_tools_count_half(scorer):
    # SPEC §11 #71: Agile, Jira, Confluence, UAT, code review, troubleshooting ... weigh half of a technical skill
    fit = scorer.evaluate("Requirements:\n- Python and SQL\n- Agile, Jira and Confluence\n- UAT\n")
    assert fit.weights == {"Python": 1.0, "SQL": 1.0, "Agile": 0.5, "Jira": 0.5, "Confluence": 0.5, "UAT": 0.5}
    assert fit.fit_pct == 80.0                    # 4 / (4 + 1)
    assert scorer.evaluate("Requirements:\n- Debugging Python services\n").weights["debugging"] == 1.0
    assert scorer.evaluate("Requirements:\n- Troubleshooting and root cause analysis\n").weights["troubleshooting"] == 0.5
