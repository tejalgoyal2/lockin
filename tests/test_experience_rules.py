import pytest

from scanner import jd_rules as J


@pytest.mark.parametrize("text,lo", [
    ("Three (3) or more years of experience in software development", 3),
    ("3 or more years of experience", 3),
    ("at least three years of experience", 3),
    ("a minimum of five years of professional experience", 5),
    ("minimum of five (5) years' experience", 5),
    ("two years of relevant experience", 2),
    ("Two (2) years of experience", 2),
    ("3 (three) years of experience", 3),
    ("2-4 years of experience", 2),
    ("2–4 years of experience", 2),
    ("2 – 12+ years of experience", 2),
    ("two to four years of experience", 2),
    ("Two (2) to four (4) years of experience", 2),
    ("one to two years of experience", 1),
    ("5+ years of experience", 5),
    ("three plus years of experience", 3),
    ("1+ year of experience with SQL and 3+ years of experience with Java", 1),     # smallest lower bound
    ("Experience: 4 or more years", 4),
])
def test_years_as_digits_words_or_both(text, lo):
    assert J.parse_experience(text).min_years == lo


@pytest.mark.parametrize("text", [
    "less than 2 years of experience",
    "over 100 years of experience in the market",           # company boilerplate
    "We have been in business for 25 years",                # no "experience" nearby
    "Experience with Python.",
])
def test_no_requirement_found(text):
    assert J.parse_experience(text).min_years is None


def test_masters_alternative_sets_the_requirement():
    a = J.parse_experience("Bachelor's degree with 5 years of experience, or Master's degree and 3 years of experience")
    assert a.min_years == 3 and a.masters_route
    b = J.parse_experience("5 years of experience, or 2 years of experience with a Master's degree")
    assert b.min_years == 2 and b.masters_route
    c = J.parse_experience("Master's degree in Computer Science or 4 years of related experience")
    assert c.min_years == 4 and not c.masters_route
    d = J.parse_experience("3 years of experience. Master's preferred.")
    assert d.min_years == 3 and not d.masters_route


@pytest.mark.parametrize("text,expected", [
    ("2 years of experience (this does not include internships or co-ops)", True),
    ("Minimum 2 years experience; internships and co-ops do not count.", True),
    ("2 years of professional experience, excluding internships.", True),
    ("2+ years of experience, not counting co-op terms", True),
    ("Experience of 2 years. Internship experience does not qualify.", True),
    ("2 years of experience, including internships and co-ops", False),
    ("Internships available. 2 years of experience.", False),
    ("We run an internship program and a co-op program for students.", False),     # nothing about experience
])
def test_internships_excluded_from_experience(text, expected):
    assert J.parse_experience(text).excludes_internships is expected


YES_FRENCH = [
    "Bilingual (English/French) is required for this role.",
    "Fluency in both English and French is required.",
    "French is required.",
    "French language skills are mandatory.",
    "Maîtrise du français exigée.",
    "Bilinguisme requis (français et anglais).",
    "Français requis.",
    "Must be fluent in French and English.",
    "You must speak French with clients.",
    "Candidates must be bilingual (English/French).",
    "Proficiency in English and French.",
    "Bilingual French/English required.",
    "Poste bilingue anglais/français.",
]
NO_FRENCH = [
    "French is an asset.",
    "Knowledge of French is a plus.",
    "Bilingualism (English/French) is an asset.",
    "We serve French-speaking customers across Canada.",
    "We hold events in French and English at our Montreal office.",
    "Must be fluent in English or French.",
    "French language skills preferred.",
    "Ability to work in French is a plus.",
    "Required: Python. French is nice to have.",
    "Python, SQL and Docker.",
]


@pytest.mark.parametrize("text", YES_FRENCH)
def test_french_required(text):
    assert J.requires_french(text)


@pytest.mark.parametrize("text", NO_FRENCH)
def test_passing_mention_of_french_is_not_enough(text):
    assert not J.requires_french(text)
