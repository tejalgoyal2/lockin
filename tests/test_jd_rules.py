import pytest

from scanner import jd_rules as r


@pytest.mark.parametrize("text,expected", [
    ("2+ years of experience with Python", 2),
    ("3-5 years of experience in software development", 3),
    ("0-2 years of experience", 0),
    ("Minimum of 5 years' experience required", 5),
    ("at least three (3) years of relevant experience", 3),
    ("one to two years of experience", 1),
    ("1 to 3 years experience preferred", None),
    ("Experience: 4 years in a similar role", 4),
    ("1+ year of SQL experience and 3+ years of Java experience", 3),  # the largest lower bound is the requirement
    ("Over 100 years of experience serving our customers", None),    # company boilerplate
    ("We have 25 years of experience in the industry", None),        # >= 20 is never a requirement
    ("Founded 60 years ago, we are a leader", None),                 # no 'experience' nearby
    ("less than 2 years of experience", None),                       # upper bound, not a requirement
    ("up to 3 years of experience", None),
    ("5+ years in software development", None),                      # no 'experience' within the window
    ("1.5 years of experience", 1.5),
    ("No experience required", 0),
    ("", None),
])
def test_parse_experience_min_years(text, expected):
    assert r.parse_experience(text).min_years == expected


def test_parse_experience_keeps_ranges():
    exp = r.parse_experience("0-2 years of experience; 3+ years of AWS experience")
    assert exp.ranges == ((0.0, 2.0), (3.0, None))


@pytest.mark.parametrize("text", [
    "You are currently enrolled in a university program",
    "Candidates must be returning to school in the fall",
    "Returning to studies in September 2027",
    "Must be a current student",
    "must be a student at an accredited university",
    "Applicants must be enrolled in a co-op program",
    "enrolled in a coop program",
    # added after the first live run
    "Enrolled in 2nd year or later of a university computer science degree program",
    "You are enrolled in a degree program at an accredited university",
    "Must be enrolled in an accredited college or university",
    "currently enrolled in a diploma program",
    "Candidates should be in their second year or above",
    "third year or later of an undergraduate degree",
    "Must be currently pursuing a Bachelor's degree",
    "You must be returning to your studies in January",
    "must be returning in the Fall",
])
def test_enrollment_detected(text):
    assert r.requires_enrollment(text)


@pytest.mark.parametrize("text", [
    "Recent graduates are welcome to apply",
    "We enrolled 10,000 customers last year",
    "Student loans repayment benefit",
])
def test_enrollment_not_detected(text):
    assert not r.requires_enrollment(text)


@pytest.mark.parametrize("text", [
    "You will be enrolled in our benefits plan on day one",
    "Employees are automatically enrolled in our group benefits program",
    "enrolled in our benefits plan after 90 days",
    "New hires get enrolled in the company pension program",
    "Eligible to be enrolled in our employee stock purchase program",
    "You'll be enrolled in our onboarding program",
    "enrolled in the health and wellness program",
    "Enrolled in RRSP matching program",
    "Enrolled in the CPA Professional Education Program (PEP)",   # professional designation, seen in real data
])
def test_enrollment_ignores_benefits_boilerplate(text):
    assert not r.requires_enrollment(text)


@pytest.mark.parametrize("text,expected", [
    ("Active security clearance is required", True),
    ("Must be able to obtain a security clearance.", True),
    ("Applicants need a valid security clearance", True),
    ("No security clearance required", False),
    ("Security clearance is not needed for this role", False),
    ("Security clearance is an asset.", False),
    ("We take security seriously", False),
])
def test_clearance(text, expected):
    assert r.requires_clearance(text) is expected


@pytest.mark.parametrize("text,expected", [
    ("Must be a US citizen", True),
    ("Candidates must be U.S. citizens", True),
    ("authorized to work in the United States", True),
    ("Must be authorized to work in the US without sponsorship", True),
    ("eligible to work in Canada", False),
    ("Our US offices are in Austin", False),
])
def test_us_authorization(text, expected):
    assert r.requires_us_authorization(text) is expected


@pytest.mark.parametrize("text", [
    "Open to recent graduates", "This role is open to students and recent graduates",
    "open to new grads", "Recent graduates are welcome to apply", "recent graduates are eligible",
    "New grads welcome", "We welcome recent graduates", "We welcome applications from recent graduates",
    "We encourage new grads to apply", "graduated within the last 12 months",
])
def test_rescue_only_on_explicit_eligibility(text):
    assert r.welcomes_recent_grads(text)


@pytest.mark.parametrize("text", [
    "We are honored to be recognized as Canada's Best Employers for Recent Graduates",
    "Our student and new graduate programs offer a chance to explore Sun Life from the inside.",
    "Ranked a top employer for new grads in 2025",
    "Campus Graduate programs",
    "Your student journey is just the beginning",
    "Transition into permanent roles after graduation",
    "",
])
def test_rescue_ignores_award_and_branding_text(text):
    assert not r.welcomes_recent_grads(text)


@pytest.mark.parametrize("text", [
    "This opportunity is available to students with a August 2027 or later graduation date",
    "Enrolled with a graduation date of April 2027 or later",
    "Expected graduation date: December 2028 or later",
    "graduating in 2027 or later",
    "2028 or later graduation date",
])
def test_future_graduation_date_is_an_enrollment_requirement(text):
    assert r.requires_enrollment(text, current_year=2026)


@pytest.mark.parametrize("text", [
    "Graduated in 2020 or later",              # past years describe a recent grad, not a student
    "graduation date of 2026 or later",        # the current year is not "after" it
    "Founded in 1998; we have grown since 2015 or later",
    "Programs run in 2027 or later",           # no graduation wording
])
def test_past_or_current_graduation_year_is_not(text):
    assert not r.requires_enrollment(text, current_year=2026)


def test_graduation_year_rule_moves_with_the_calendar():
    text = "graduation date of 2027 or later"
    assert r.requires_enrollment(text, current_year=2026)
    assert not r.requires_enrollment(text, current_year=2027)


def test_level_is_read_from_the_title_and_the_description():
    assert r.addresses_new_grads("Software Engineer", "Recent graduates welcome.")
    assert r.addresses_new_grads("2027 Graduate Program, Technology", "") and r.addresses_new_grads("Developer (New Grad)", "")
    assert not r.addresses_new_grads("Software Engineer", "Build things.")
    assert r.junior_level("Jr. Developer", "") and r.junior_level("Associate Software Developer", "")
    assert r.junior_level("Data Analyst I", "") and r.junior_level("Engineer", "This is an entry-level role.")
    assert not r.junior_level("Software Engineer", "You will mentor junior engineers")       # "junior" counts in the title only
    assert r.entry_title("Junior Developer") and r.entry_title("Software Developer (New or Recent Graduate)")
    assert not r.entry_title("Software Developer")


@pytest.mark.parametrize("text, required", [
    ("You must be currently enrolled in a degree program.", True),
    ("You're enrolled in a Computer Science or Engineering degree program", True),
    ("Must be returning to school after the work term", True),
    ("You're completing or have recently finished a degree", False),
    ("Currently pursuing or recently completed a Bachelor's degree", False),
    ("Currently enrolled in a degree program, or a recent graduate", False),
    ("You will be enrolled in our benefits plan", False),
])
def test_a_posting_that_also_takes_graduates_does_not_require_enrollment(text, required):
    assert r.requires_enrollment(text, 2026) is required
