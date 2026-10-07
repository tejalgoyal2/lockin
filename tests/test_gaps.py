import re

import yaml

from scanner.score import Scorer

REQUESTED = ["Java", "Go", "Kotlin", "Scala", "Ruby", "PHP", "Swift", "Spring", "Angular", "Vue", "Kafka",
             "Airflow", "dbt", "Snowflake", "BigQuery", "Redshift", "Tableau", "Looker", "GCP", "MySQL",
             "Oracle", "Jenkins", "Ansible", "Selenium", "Cypress", "microservices", "distributed systems",
             "unit testing", "Salesforce", "SAP"]


def test_requested_gap_terms_present():
    names = {t.name for t in Scorer.load().gaps}
    assert set(REQUESTED) <= names


def test_gaps_and_skills_do_not_overlap():
    scorer = Scorer.load()
    gap_names = [t.name for t in scorer.gaps]
    assert len(gap_names) == len(set(gap_names)), "duplicate gap entries"
    assert not {n.lower() for n in gap_names} & {t.name.lower() for t in scorer.skills}
    # no alias is claimed by both lists (case-insensitive)
    with open("gaps.yaml", encoding="utf-8") as g, open("skills.yaml", encoding="utf-8") as s:
        gap_aliases = {a.lower() for e in yaml.safe_load(g)["terms"] for a in e["aliases"]}
        skill_aliases = {a.lower() for es in yaml.safe_load(s)["clusters"].values() for e in es for a in e["aliases"]}
    assert not gap_aliases & skill_aliases


def test_new_gap_terms_match_in_a_jd():
    fit = Scorer.load().evaluate("Experience with Salesforce, SAP, Kafka, Snowflake and Spring Boot. Python a plus.")
    assert {"Salesforce", "SAP", "Kafka", "Snowflake", "Spring"} <= set(fit.gaps)
    assert fit.matched == ["Python"]
