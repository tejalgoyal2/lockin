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
    ("1+ year of experience with SQL and 3+ years of experience with Java", 3),     # the largest lower bound
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


@pytest.mark.parametrize("text, lo, hi", [
    ("5+ years of experience in software engineering. 1+ years experience with a relational database", 5, None),
    ("1-5 years of experience", 1, 5),
    ("2-5 years of experience", 2, 5),
    ("2+ years of experience", 2, None),
    ("At least two years of professional experience.\nPreferred qualifications:\n- Three to five years of industry experience", 2, None),
    ("Minimum 3 years industrial experience.\n1 - 3 years millwright experience, preferred", 3, None),
    ("Two year community college diploma.\nExperience: Three to four years' related experience.", 3, 4),
    ("Salary: $75,000 - $85,000 year (dependent on experience)", None, None),        # "000 year" is not a requirement
    ("12 month contract. Experience with Python.", None, None),
    ("6 months of co-op experience", 0.5, None),
    ("Experience\n\n7 months to less than 1 year", 0.58, None),                       # Job Bank's wording
    ("Experience\n\n1 year to less than 2 years", 1, None),
    ("Experience\n\nExperience an asset\n", 0, None),
    ("Experience\n\nWill train\n", 0, None),
])
def test_the_requirement_is_the_largest_lower_bound_that_is_really_asked_for(text, lo, hi):
    e = J.parse_experience(text)
    assert (e.min_years, e.max_years) == (lo, hi)
