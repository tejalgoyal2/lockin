"""Rules applied to fetched JD text."""
import re
from dataclasses import dataclass
from datetime import datetime, timezone

_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
          "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15}
_NUM = r"(?:\d{1,3}(?:\.\d+)?|" + "|".join(_WORDS) + r")"
# "(3)", "(3+)", "(3-5)": the digits many postings repeat after a spelled-out number
_PAREN_DIGITS = (r"(?:\s*\(\s*(?:\d{1,2}|" + "|".join(_WORDS) + r")\s*\+?\s*"
                 r"(?:[-–—]\s*(?:\d{1,2}|" + "|".join(_WORDS) + r")\s*\+?\s*)?\))?")
_YEARS = re.compile(
    rf"(?<![\d.,$])(?P<lo>{_NUM}){_PAREN_DIGITS}\s*(?P<plus>\+)?\s*"
    rf"(?:(?:-|–|—|to)\s*(?P<hi>{_NUM}){_PAREN_DIGITS}\s*(?P<plus2>\+)?\s*)?"
    r"(?:(?:or|and)\s+(?:more|greater|above|over)\s+|plus\s+)?"            # "three (3) or more years"
    r"(?:years?|yrs?)\b",
    re.IGNORECASE,
)
# "6 months of experience", and Job Bank's "7 months to less than 1 year"
_MONTHS = re.compile(r"(?<![\d.,$])(\d{1,2})\s*\+?\s*months?['’]?\s+(?:(?:of\s+)?(?:[\w-]+\s+){0,3}?experience|to\s+less\s+than\s+1\s+year)",
                     re.IGNORECASE)
# The posting says no experience is needed. "Experience an asset" and "Will train" are Job Bank's wording for it.
_NO_EXPERIENCE = re.compile(
    r"no\s+(?:prior\s+|previous\s+)?(?:work\s+)?experience\s+(?:is\s+)?(?:required|necessary|needed)"
    r"|^\s*(?:experience\s+an\s+asset|will\s+train)\s*$", re.IGNORECASE | re.MULTILINE)
# "two year college diploma", "12 month contract", "4 years of university": a length of study or of the job, not experience
_NOT_EXPERIENCE_AFTER = re.compile(r"^\W{0,3}(?:of\s+)?(?:community\s+|technical\s+|post[\s-]secondary\s+)?"
                                   r"(?:college|university|degree|diploma|program|certificate|contract|term\b|co-?op|internship|"
                                   r"placement|apprenticeship|warranty|old\b)", re.IGNORECASE)
# "3-5 years of experience preferred", "2 years would be an asset": wanted, not required
_PREFERRED = re.compile(r"\b(?:preferred|preferably|an?\s+asset|assets?\b|nice[\s-]to[\s-]have|a\s+plus|bonus|ideally|desirable|"
                        r"would\s+be\s+(?:great|nice|beneficial))", re.IGNORECASE)
_EXPERIENCE = re.compile(r"experience|expérience", re.IGNORECASE)
_UPPER_BOUND_LEAD = re.compile(r"(?:less than|fewer than|up to|under|maximum of|max\.?|no more than)\s*$", re.IGNORECASE)
_WINDOW = 100          # characters either side of "N years" searched for the word "experience"
_BOILERPLATE_MIN = 20  # "over 100 years in business", "25 years of history": never a job requirement
# "Bachelor's with 5 years, or Master's and 3 years": the Master's route is the owner's route.
_MASTERS = r"(?:master['’]?s?|m\.?\s?sc\.?|\bm\.?s\.?\b|graduate\s+degree|postgraduate)"
_MASTERS_RE = re.compile(_MASTERS, re.IGNORECASE)
_MASTERS_AFTER = re.compile(r"^[^.;\n]{0,28}?(?:with|and|plus|holding|having|if)\s+(?:an?\s+|a\s+)?" + _MASTERS, re.IGNORECASE)
_ENROLLMENT_FIXED = re.compile(
    r"currently\s+enrolled|returning\s+to\s+(?:school|studies)|must\s+be\s+a\s+(?:current\s+)?student"
    r"|enrolled\s+in\s+a\s+co-?op\s+program"
    r"|\b(?:2nd|second|3rd|third)\s+year\s+or\s+(?:later|above)"
    r"|currently\s+pursuing|must\s+be\s+returning", re.IGNORECASE)
_ENROLLED_IN = re.compile(
    r"enrolled\s+in\s+(?P<span>.{0,60}?(?:degree|program|university|college|diploma))", re.IGNORECASE)
# "enrolled in our benefits plan", "automatically enrolled in the pension program" is HR
# boilerplate about the employee, not a requirement that the applicant be a student.
_NOT_A_STUDY_PROGRAM = re.compile(
    r"\b(?:our|your|its|the\s+company|benefits?|plans?|pension|insurance|rrsp|tfsa|retirement|401\s*k|"
    r"health|dental|vision|wellness|stock|share|savings|onboarding|orientation|training|mentorship|"
    r"payroll|perks?|cpa|cfa|cma|cia|pep)\b", re.IGNORECASE)
_EMPLOYEE_LEAD = re.compile(r"\b(?:will\s+be|automatically|get|be\s+eligible\s+to\s+be|become)\s+$", re.IGNORECASE)
_CLEARANCE = re.compile(r"security\s+clearance", re.IGNORECASE)
_CLEARANCE_REQUIRED = re.compile(r"requir|must|need|obtain|maintain|hold|eligib|active|valid|able to|ability", re.IGNORECASE)
_NEGATION = re.compile(r"\b(?:no|not|without|neither|nor)\b[^.\n]{0,15}$", re.IGNORECASE)
US_AUTH = re.compile(
    r"\bU\.?S\.?\s+citizen(?:ship)?|\bUnited\s+States\s+citizen(?:ship)?"
    r"|(?:authori[sz]ed|eligible|legally\s+(?:able|entitled))\s+to\s+work\s+in\s+the\s+(?:United\s+States|U\.?S\.?A?\.?)\b",
    re.IGNORECASE)
# Explicit eligibility wording ("open to recent graduates", "recent graduates are welcome"). Award / employer-branding
# text ("Best Employers for Recent Graduates", "our student and new graduate programs") is not.
_GRAD = r"(?:recent(?:ly)?\s+graduat\w*|new[\s-]?grad\w*)"
RESCUE_ELIGIBILITY = re.compile(
    rf"open\s+to\s+(?:[\w,'’/&-]+\s+){{0,4}}?{_GRAD}"
    rf"|{_GRAD}(?:\s+(?:and|or)\s+\w+)?\s+(?:are\s+|is\s+)?(?:welcome|eligible|encouraged|invited)"
    rf"|(?:welcome|encourage|invite)s?\s+(?:applications?\s+from\s+)?(?:[\w,'’/&-]+\s+){{0,3}}?{_GRAD}"
    r"|graduated\s+within",
    re.IGNORECASE)
# "graduation date of April 2027 or later" / "2027 or later graduation date": a future graduation
# date means the candidate is still a student, so it is an enrollment requirement. Years up to and
# including the current one ("graduated 2020 or later") are not.
_GRAD_DATE_FORWARD = re.compile(r"graduat\w*[^.\n]{0,50}?\b(20\d\d)\s+or\s+(?:later|after)", re.IGNORECASE)
_GRAD_DATE_BACKWARD = re.compile(r"\b(20\d\d)\s+or\s+(?:later|after)\s+graduat\w*", re.IGNORECASE)


@dataclass(frozen=True)
class Experience:
    min_years: float | None                # the requirement in years; None: the posting states none
    ranges: tuple[tuple[float, float | None], ...] = ()   # (lo, hi) per mention that counts
    masters_route: bool = False            # min_years came from an "or Master's and N years" alternative
    max_years: float | None = None         # the upper end of the mention that set min_years ("2-5 years" -> 5)


def _num(token: str) -> float:
    return float(_WORDS.get(token.lower(), token)) if token else 0.0


def _sentence(text: str, pos: int) -> str:
    start = max(text.rfind(".", 0, pos), text.rfind("\n", 0, pos)) + 1
    ends = [i for i in (text.find(".", pos), text.find("\n", pos)) if i != -1]
    return text[start: min(ends) if ends else len(text)]


def _masters_route(text: str, m: re.Match) -> bool:
    """This "N years" belongs to a Master's route: "Master's and N years", "N years with a Master's".
    "Master's degree OR N years" is the opposite (N is the way around the degree), so it is not."""
    before = text[max(0, m.start() - 90):m.start()]
    before = re.split(r"[.;\n]", before)[-1]                 # stay inside the sentence
    last = None
    for last in _MASTERS_RE.finditer(before):
        pass
    if last is not None and not re.search(r"\bor\b", before[last.end():], re.IGNORECASE):
        return True
    return bool(_MASTERS_AFTER.search(text[m.end():m.end() + 60]))


def parse_experience(text: str) -> Experience:
    """The years of experience a posting requires.

    Mentions are read as digits, words or both ("2+ years", "3-5 years", "Three (3) or more years", "6 months") and
    count only with the word "experience" within `_WINDOW` characters. A posting usually states the overall
    requirement and then smaller ones per tool ("5+ years in software engineering ... 1+ years with a relational
    database"), so the requirement is the LARGEST lower bound: taking the smallest let 5-year jobs through as 1-year
    ones. Not counted: mentions under a nice-to-have heading or in a sentence that says preferred / an asset, upper bounds
    ("less than 2 years"), lengths of study or of a contract ("two year diploma", "12 month contract") and numbers
    >= 20 (company boilerplate). If one mention is an "or Master's and N years" alternative, N is the requirement
    (the owner has a Master's). "No experience required" with no number is 0.
    """
    from scanner.score import sections      # local: score must not depend on jd_rules
    text = text or ""
    nice = [(a, b) for a, b, k in sections(text) if k == "nice"]
    found: list[tuple[float, float | None]] = []
    masters: list[float] = []
    for m in _YEARS.finditer(text):
        lo = _num(m.group("lo"))
        hi = _num(m.group("hi")) if m.group("hi") else None
        if lo >= _BOILERPLATE_MIN or (hi is not None and hi >= _BOILERPLATE_MIN):
            continue
        if _UPPER_BOUND_LEAD.search(text[max(0, m.start() - 15):m.start()]):
            continue
        if _NOT_EXPERIENCE_AFTER.search(text[m.end():m.end() + 40]):
            continue
        if not _EXPERIENCE.search(text[max(0, m.start() - _WINDOW):m.end() + _WINDOW]):
            continue
        if any(a <= m.start() < b for a, b in nice) or _PREFERRED.search(_sentence(text, m.start())):
            continue
        found.append((lo, hi))
        if _masters_route(text, m):
            masters.append(lo)
    for m in _MONTHS.finditer(text):
        found.append((round(int(m.group(1)) / 12, 2), None))
    if not found:
        return Experience(0.0 if _NO_EXPERIENCE.search(text) else None)
    need = min(masters) if masters else max(lo for lo, _ in found)
    tops = [hi for lo, hi in found if lo == need]
    top = None if None in tops else max(tops)
    return Experience(need, tuple(found), bool(masters), top)


def _future_graduation_date(text: str, current_year: int) -> bool:
    return any(int(m.group(1)) > current_year
               for pat in (_GRAD_DATE_FORWARD, _GRAD_DATE_BACKWARD) for m in pat.finditer(text))


_ALSO_GRADUATES = re.compile(
    r"\brecently\s+(?:graduated|completed|finished)|\b(?:have|has|or)\s+(?:recently\s+|already\s+)?(?:graduated|completed|finished)"
    r"|\bor\s+(?:an?\s+|are\s+an?\s+)?(?:recent|new)\s+grad\w*|\bgraduated\s+(?:within|in\s+the\s+(?:last|past))|\balumni\b", re.IGNORECASE)


def requires_enrollment(text: str, current_year: int | None = None) -> bool:
    """The posting is for current students only. A sentence that also takes graduates ("currently pursuing or have
    recently completed a degree") is not such a requirement; benefits boilerplate ("enrolled in our pension plan")
    is not either."""
    text = text or ""
    current_year = current_year or datetime.now(timezone.utc).year
    for m in _ENROLLMENT_FIXED.finditer(text):
        if not _ALSO_GRADUATES.search(_sentence(text, m.start())):
            return True
    if _future_graduation_date(text, current_year):
        return True
    for m in _ENROLLED_IN.finditer(text):
        if _NOT_A_STUDY_PROGRAM.search(m.group("span")):
            continue
        if _EMPLOYEE_LEAD.search(text[max(0, m.start() - 25):m.start()]):
            continue
        if _ALSO_GRADUATES.search(_sentence(text, m.start())):
            continue
        return True
    return False


def requires_clearance(text: str) -> bool:
    """'security clearance' mentioned as a requirement (not negated, not merely preferred)."""
    for m in _CLEARANCE.finditer(text or ""):
        if _NEGATION.search(text[max(0, m.start() - 25):m.start()]):
            continue
        if re.match(r"\W*(?:is\s+|are\s+)?(?:not|no\s+longer)\b", text[m.end():m.end() + 25], re.IGNORECASE):
            continue
        sentence_start = max(text.rfind(".", 0, m.start()), text.rfind("\n", 0, m.start())) + 1
        end = text.find(".", m.end())
        sentence = text[sentence_start: end if end != -1 else len(text)]
        if _CLEARANCE_REQUIRED.search(sentence):
            return True
    return False


def requires_us_authorization(text: str) -> bool:
    return bool(US_AUTH.search(text or ""))


def welcomes_recent_grads(text: str) -> bool:
    """A student-titled posting says graduates may apply: explicit eligibility wording ("open to recent graduates"), or
    a requirement that takes them too ("completing or have recently finished a degree"). Employer branding ("Best
    Employers for Recent Graduates") does not count."""
    return bool(RESCUE_ELIGIBILITY.search(text or "") or _ALSO_GRADUATES.search(text or ""))


# --- French / bilingual requirement --------------------------------------------------------------
# A job that REQUIRES French (or "bilingual (English/French)") is dropped wherever it is located.
# "French is an asset", "our French-speaking clients" and other passing mentions are not enough.
_FR = r"(?:French|fran[cç]ais|bilingue)"
_FRENCH_REQUIRED = re.compile(
    r"\bFrench(?:\s+language)?(?:\s+(?:skills?|proficiency|fluency))?\s+(?:is\s+|are\s+)?(?:required|mandatory|essential|a\s+must|necessary)\b"
    r"|\b(?:require[sd]?|must|need(?:s|ed)?|mandatory|essential)\b[^.;\n]{0,60}?\bFrench\b(?!\s*(?:or|and/or)\b)"
    r"|\b(?:fluen\w+|proficien\w+|profici\w+|communicat\w+|speak\w*|written\s+and\s+(?:spoken|verbal))\s+(?:in|with|of)?\s*(?:both\s+)?(?:English\s+and\s+French|French\s+and\s+English)\b"
    r"|\b(?:fluen\w+|proficien\w+|profici\w+)\s+(?:in|with)\s+French\b"
    r"|\bbilingual\w*\s*[(\[]?\s*(?:in\s+)?(?:English\s*(?:/|-|&|and)\s*French|French\s*(?:/|-|&|and)\s*English)"
    r"|\bbilingual\s+(?:French|English\s+and\s+French)\b"
    r"|\bbilingue\b[^.;\n]{0,30}\b(?:anglais|fran[cç]ais)\b"
    r"|\bbilinguisme\b"
    r"|\bma[iî]trise\s+(?:du|de\s+la|de\s+l['’])\s*(?:fran[cç]ais|langue\s+fran[cç]aise)"
    r"|\bfran[cç]ais\s+(?:requis|exig\w+|obligatoire)\b"
    r"|\b(?:exig\w+|requis|obligatoire)\b[^.;\n]{0,40}\bfran[cç]ais\b",
    re.IGNORECASE)
_SOFT = re.compile(r"\b(?:asset|an?\s+plus|nice[\s-]to[\s-]have|preferred|bonus|advantage|beneficial|considered\s+an?|"
                   r"would\s+be\s+(?:a|an|considered)|not\s+required|not\s+necessary|no\s+french|atout|un\s+plus|souhait\w+)\b",
                   re.IGNORECASE)


def requires_french(text: str) -> bool:
    """The JD requires French or bilingual (English/French) skills. Soft wording ("an asset") does not count."""
    text = text or ""
    for m in _FRENCH_REQUIRED.finditer(text):
        if re.search(r"\b(?:or|and/or)\s+French\b", m.group(), re.IGNORECASE):   # "English or French"
            continue
        if _SOFT.search(_sentence(text, m.start())):
            continue
        return True
    return False


# --- P.Eng licence ---------------------------------------------
_PENG = re.compile(
    r"\bP\.\s?Eng\b\.?|\bPEng\b|"                                   # case-sensitive: never "develop engineering"
    r"(?i:professional\s+engineer(?:ing)?\b(?!\s+(?:services|firm|team|staff)))|"
    r"\b(?:PEO|APEGA|APEGS|EGBC|APEGNB|APEGM)\b|(?i:engineers\s+and\s+geoscientists\s+(?:BC|british\s+columbia))")
_PENG_REQUIRED = re.compile(
    r"(?i)\b(?:must|required|requires?|requirement|mandatory|licen[cs]ed|licen[cs]e|registered|registration|"
    r"eligib\w*|hold|holding|member(?:ship)?\s+(?:in\s+good\s+standing|of|with))\b")
_PENG_OPTIONAL = re.compile(r"(?i)\b(?:asset|preferred|nice\s+to\s+have|a\s+plus|bonus|advantage(?:ous)?|desir(?:ed|able)|"
                            r"would\s+be\s+(?:great|nice))\b")
_EIT = re.compile(r"\bEIT\b|\bE\.I\.T\.?|(?i:engineer[\s-]+in[\s-]+training|engineering\s+intern\b)")


def requires_peng(jd: str) -> bool:
    """The JD asks for a P.Eng licence (or eligibility for one): a mention under a requirements heading, or one next
    to a requirement word ("licensed", "must", "registered", "eligible"). Not when an EIT is accepted anywhere in
    the JD, and not for perks ("training to achieve your P.Eng. designation")."""
    from scanner.score import sections      # local: score must not depend on jd_rules
    jd = jd or ""
    if _EIT.search(jd):
        return False
    req = [(a, b) for a, b, k in sections(jd) if k == "req"]
    for m in _PENG.finditer(jd):
        line_start = jd.rfind("\n", 0, m.start()) + 1
        line_end = jd.find("\n", m.end())
        line = jd[line_start: line_end if line_end != -1 else len(jd)]
        if _PENG_OPTIONAL.search(line):
            continue                                     # "registration with EGBC is considered an asset"
        if any(a <= m.start() < b for a, b in req) or _PENG_REQUIRED.search(line):
            return True
    return False


# --- talent pool / evergreen postings ------------------------------------------
# A posting that says it is not for a current opening ("evergreen requisition", "this posting is to build a talent
# pool") rarely leads to an interview soon. Flagged, never dropped. The "join our talent community" footer many
# employers add to every posting does not count, nor does "also used to fill future vacancies".
_POOL = re.compile(
    r"\bevergreen\s+(?:requisition|posting|req|role|position|opening|job)s?\b"
    r"|\b(?:this|the)\s+(?:job\s+)?(?:posting|requisition|position|role|opportunity|advertisement)\s+is\s+(?:an?\s+)?"
    r"(?:evergreen|pipeline|pooling|talent[\s-]pool|(?:for|intended\s+(?:for|to))\s+(?:build(?:ing)?|creat\w+)\s+(?:a|our)\s+"
    r"(?:talent\s+pool|pool|pipeline|candidate\s+pool))"
    r"|\b(?:this|the)\s+(?:job\s+)?(?:posting|requisition|advertisement)\s+(?:is|does)\s+not\s+(?:for\s+|represent\s+)?"
    r"(?:an?\s+)?(?:current|immediate|specific|existing|active)\s+(?:vacanc|opening|position|role)"
    r"|\bthere\s+(?:is|are)\s+(?:currently\s+)?no\s+(?:current\s+|immediate\s+)?(?:vacanc|opening|position)",
    re.IGNORECASE)


def talent_pool(jd: str) -> bool:
    """The posting says it is evergreen or for a talent pool, not for a current opening."""
    return bool(_POOL.search(jd or ""))


# --- level -------------------------------------------------------------------------------
_NEW_GRAD = re.compile(r"new[\s-]?grad(?:uate)?s?\b|recent(?:ly)?\s+graduat\w*|graduated\s+within|university\s+grad|college\s+grad"
                       r"|\bgraduate\s+(?:program|programme|role|rotation|developer|engineer|analyst|scientist)|\bcampus\b|early[\s-]career",
                       re.IGNORECASE)
_JUNIOR_TITLE = re.compile(r"\bjunior\b|\bjr\b\.?|entry[\s-]?level|\bassociate\b|\bapprentice\b|\btrainee\b"
                           r"|\b(?:engineer|developer|analyst|scientist|specialist|administrator|programmer|tester|technologist)\s+(?:I|1)\b",
                           re.IGNORECASE)
_ENTRY_JD = re.compile(r"entry[\s-]?level", re.IGNORECASE)


def addresses_new_grads(title: str, jd: str) -> bool:
    """The title or the description speaks to new or recent graduates."""
    return bool(_NEW_GRAD.search(f"{title}\n{jd}"))


def junior_level(title: str, jd: str) -> bool:
    """A junior title (Junior, Associate, "Analyst I"), or a description that calls the job entry level. "Junior" is
    read in the title only: descriptions say "mentor junior engineers" for senior roles."""
    return bool(_JUNIOR_TITLE.search(title or "") or _ENTRY_JD.search(jd or ""))


def entry_title(title: str) -> bool:
    """The title itself says who the job is for: new grad, junior, associate, "Engineer I"."""
    return bool(_NEW_GRAD.search(title or "") or _JUNIOR_TITLE.search(title or ""))
