import pytest


@pytest.mark.parametrize("loc", [
    "Toronto, ON", "Toronto, Ontario", "Remote (Canada)", "Canada - Remote", "Canada",
    "CAN - Alberta - Calgary", "Montréal, Quebec", "Victoria, BC", "London, ON",
    "Seattle, WA; Toronto, ON", "San Francisco, Palo Alto, Los Angeles, Toronto",
    "Richmond Hill, ON", "Home-Canada",
])
def test_canadian_locations_kept(filters, loc):
    assert filters.canadian_location(loc) is not None


@pytest.mark.parametrize("loc", [
    "Vancouver, Washington", "Vancouver, WA", "Ottawa, Illinois", "Waterloo, Wisconsin",
    "United States", "USA", "Remote - US", "Victoria, Australia", "London, UK",
    "Halifax, UK", "Seattle, WA", "Not specified", "",
])
def test_non_canadian_locations_dropped(filters, loc):
    assert filters.canadian_location(loc) is None


def test_multi_location_returns_first_canadian_part(filters):
    assert filters.canadian_location("Seattle, WA; Toronto, ON; Calgary, AB") == "Toronto, ON"


@pytest.mark.parametrize("title", [
    "Software Engineer", "Backend Developer", "Data Analyst", "Machine Learning Engineer",
    "AI Engineer", "DevOps Engineer", "Cloud Platform Engineer", "Frontend Developer",
    "Software Engineering Graduate", "Security Analyst", "BI Developer", "SRE",
    "Automation Specialist", "Cyber Security Analyst", "Database Administrator",
    "Full-Stack Engineer", "Analytics Engineer",
])
def test_strong_titles(filters, title):
    assert filters.title_tier(title) == "strong"


@pytest.mark.parametrize("title", [
    "Engineer", "Applications Engineer", "Business Analyst", "Technical Specialist",
    "Test Specialist", "QA Tester", "Integration Engineer", "Research Scientist",
    "Technical Consultant",
])
def test_weak_titles(filters, title):
    assert filters.title_tier(title) == "weak"


@pytest.mark.parametrize("title", ["Barista", "Retail Associate", "Accountant", "Paint Worker"])
def test_non_tech_titles_do_not_match(filters, title):
    assert filters.title_tier(title) is None


@pytest.mark.parametrize("title", [
    "Facilities Engineer", "Engineering Project Coordinator", "Marketing Specialist - AI",
    "Production Supervisor", "Superviseur de production", "Production Team Leader",
    "Administrative Assistant, Data Entry", "Technical Recruiter",
    "ASSISTANT VICE-PRESIDENT, DATA & AI", "Vice President, Engineering", "Vice-President Data Science",
    "AVP, Analytics", "Assistant Vice President - Technology", "Assistant Vice-President",
])
def test_hard_excludes(filters, title):
    assert not filters.title_not_excluded(title)


@pytest.mark.parametrize("title", [
    "Senior Software Engineer", "Sr. Data Analyst", "Staff Engineer", "Principal Developer",
    "Tech Lead", "Engineering Manager", "Director of Engineering", "Head of Data",
    "Solutions Architect", "Software Engineer II", "Data Engineer III", "Developer IV",
    "Intermediate Developer", "VP Engineering", "Mechanical Engineer", "Electrical Engineer",
    "Field Service Engineer", "Sales Engineer", "Security Guard",
])
def test_senior_and_non_software_titles_excluded(filters, title):
    assert not filters.title_not_excluded(title)


@pytest.mark.parametrize("title", [
    "Security Concierge (Part Time Overnights) - Hamilton", "Security Parking Attendant - London",
    "Parking Enforcement Officer", "Security Guard", "Night Guard", "Armed Guard - Security",
    "Security Officer", "Information Security Officer", "Custodian", "Building Custodian, Data Centre",
    "AI Fluency & Innovation Teacher (Grades 11/12)", "Computer Science Teacher", "Lot Attendant",
    "Service Attendant - Software Retail",
])
def test_non_tech_service_roles_are_excluded(filters, title):
    assert not filters.title_not_excluded(title)


@pytest.mark.parametrize("title", [
    "AI Guardrails Engineer", "Guardian Platform Developer", "Security Engineer", "Cyber Security Analyst",
    "Security Operations Analyst", "Software Engineer, Payments Platform Integrations",
    "Teaching Platform Developer", "Application Security Engineer",
])
def test_the_new_excludes_do_not_hit_real_tech_titles(filters, title):
    assert filters.title_not_excluded(title)


def test_vice_president_excludes_do_not_hit_lookalikes(filters):
    for title in ("Service Engineer", "Advice Analyst Developer", "Software Developer"):
        assert filters.title_not_excluded(title)


@pytest.mark.parametrize("title", [
    "Software Engineer", "Junior Developer", "Software Engineer New Grad", "Leadership Program Analyst",
    "Software Engineer in Test", "Data Engineer I", "Internal Tools Engineer",
])
def test_entry_level_and_lookalike_titles_not_excluded(filters, title):
    assert filters.title_not_excluded(title)
    assert filters.not_student_only(title)


@pytest.mark.parametrize("title", [
    "Software Engineer Intern", "Software Development Internship (Summer 2027)", "Co-op Developer",
    "Student Developer", "PEY Software", "Data Analyst - Winter 2027", "Developer (Fall 2026)",
])
def test_student_only_titles_dropped(filters, title):
    assert not filters.not_student_only(title)


def test_company_blocklist(filters):
    assert not filters.company_ok("Jobgether")
    assert not filters.company_ok("USA Survey Job")  # normalised substring match
    assert filters.company_ok("Shopify")


def test_french_only_titles_dropped_but_bilingual_kept(filters):
    assert not filters.language_ok("Ingénieur logiciel")
    assert not filters.language_ok("Analyste de données")
    assert filters.language_ok("Data Analyst / Analyste de données")      # English first: kept
    assert filters.language_ok("Software Engineer")
