"""Rules applied to fetched JD text (SPEC §4 'JD rules')."""
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
    rf"(?<![\d.])(?P<lo>{_NUM}){_PAREN_DIGITS}\s*(?P<plus>\+)?\s*"
    rf"(?:(?:-|–|—|to)\s*(?P<hi>{_NUM}){_PAREN_DIGITS}\s*(?P<plus2>\+)?\s*)?"
    r"(?:(?:or|and)\s+(?:more|greater|above|over)\s+|plus\s+)?"            # "three (3) or more years"
    r"(?:years?|yrs?)\b",
    re.IGNORECASE,
)
_EXPERIENCE = re.compile(r"experience|expérience", re.IGNORECASE)
_UPPER_BOUND_LEAD = re.compile(r"(?:less than|fewer than|up to|under|maximum of|max\.?|no more than)\s*$", re.IGNORECASE)
_WINDOW = 100          # characters either side of "N years" searched for the word "experience"
_BOILERPLATE_MIN = 20  # "over 100 years in business", "25 years of history": never a job requirement
# "Bachelor's with 5 years, or Master's and 3 years": the Master's route is the owner's route.
_MASTERS = r"(?:master['’]?s?|m\.?\s?sc\.?|\bm\.?s\.?\b|graduate\s+degree|postgraduate)"
_MASTERS_RE = re.compile(_MASTERS, re.IGNORECASE)
_MASTERS_AFTER = re.compile(r"^[^.;\n]{0,28}?(?:with|and|plus|holding|having|if)\s+(?:an?\s+|a\s+)?" + _MASTERS, re.IGNORECASE)
# "does not include internships or co-ops", "excluding co-op terms", "internships do not count"
_INTERN = r"(?:intern(?:ship)?s?|co-?ops?|placements?)"
_INTERN_EXCLUDED = re.compile(
    rf"(?:(?:does|do|did)\s*n[o']?t|doesn['’]t|don['’]t|not)\s+(?:include|including|count|counting|consider|considering|qualify)[^.;\n]{{0,60}}?{_INTERN}"
    rf"|(?:exclud\w+|exclusive\s+of|other\s+than|aside\s+from|apart\s+from|not\s+counting)[^.;\n]{{0,40}}?{_INTERN}"
    rf"|{_INTERN}[^.;\n]{{0,50}}?(?:do(?:es)?\s+not|don['’]t|doesn['’]t|(?:is|are)\s+not|isn['’]t|aren['’]t|will\s+not|won['’]t)\s+(?:count|be\s+(?:counted|considered|included|accepted)|qualify|apply)"
    rf"|without\s+(?:counting\s+)?{_INTERN}",
    re.IGNORECASE)

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
# A student-titled job is rescued only by explicit eligibility wording ("open to recent graduates",
# "recent graduates are welcome"). Award / employer-branding text ("Best Employers for Recent
# Graduates", "our student and new graduate programs") must not rescue it.
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
    min_years: float | None                # the requirement: smallest lower bound (Master's route if given)
    ranges: tuple[tuple[float, float | None], ...] = ()   # (lo, hi) per mention
    masters_route: bool = False            # min_years came from an "or Master's and N years" alternative
    excludes_internships: bool = False     # the JD says internships / co-ops do not count as experience


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


def excludes_internships(text: str) -> bool:
    """The JD says internships / co-ops do not count towards the experience it asks for."""
    for m in _INTERN_EXCLUDED.finditer(text or ""):
        context = text[max(0, m.start() - 160): m.end() + 160]
        if re.search(r"experience|years?", context, re.IGNORECASE):
            return True
    return False


def parse_experience(text: str) -> Experience:
    """Years-of-experience mentions: "2+ years", "3-5 years", "Three (3) or more years", "two years".

    A mention only counts when the word "experience" is within `_WINDOW` characters. Numbers can be
    digits, words, or both ("Three (3)"). The lower bound of a range is its requirement ("2-4" -> 2,
    "2-12+" -> 2); across several mentions the smallest lower bound wins ("1+ year SQL, 3+ years
    Java" asks for 1). If one mention is an "or Master's and N years" alternative, N is the
    requirement (the owner has a Master's). "Less than 2 years" upper bounds and numbers >= 20
    (company boilerplate) are ignored.
    """
    text = text or ""
    ranges: list[tuple[float, float | None]] = []
    masters: list[float] = []
    for m in _YEARS.finditer(text):
        lo = _num(m.group("lo"))
        hi = _num(m.group("hi")) if m.group("hi") else None
        if lo >= _BOILERPLATE_MIN or (hi is not None and hi >= _BOILERPLATE_MIN):
            continue
        if _UPPER_BOUND_LEAD.search(text[max(0, m.start() - 15):m.start()]):
            continue
        window = text[max(0, m.start() - _WINDOW):m.end() + _WINDOW]
        if not _EXPERIENCE.search(window):
            continue
        ranges.append((lo, hi))
        if _masters_route(text, m):
            masters.append(lo)
    lowest = min((lo for lo, _ in ranges), default=None)
    if masters:
        return Experience(min(masters), tuple(ranges), True, excludes_internships(text))
    return Experience(lowest, tuple(ranges), False, excludes_internships(text))


def _future_graduation_date(text: str, current_year: int) -> bool:
    return any(int(m.group(1)) > current_year
               for pat in (_GRAD_DATE_FORWARD, _GRAD_DATE_BACKWARD) for m in pat.finditer(text))


def requires_enrollment(text: str, current_year: int | None = None) -> bool:
    """Current-enrollment requirement (SPEC §4), without tripping on benefits boilerplate."""
    text = text or ""
    current_year = current_year or datetime.now(timezone.utc).year
    if _ENROLLMENT_FIXED.search(text) or _future_graduation_date(text, current_year):
        return True
    for m in _ENROLLED_IN.finditer(text):
        if _NOT_A_STUDY_PROGRAM.search(m.group("span")):
            continue
        if _EMPLOYEE_LEAD.search(text[max(0, m.start() - 25):m.start()]):
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
    """Explicit eligibility wording that rescues a student-titled job (not employer branding)."""
    return bool(RESCUE_ELIGIBILITY.search(text or ""))


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


# --- Notion "Signals" multi-select ------------------------------------------
_SIGNAL_NEW_GRAD = re.compile(r"new[\s-]?grad(?:uate)?s?\b", re.IGNORECASE)
_SIGNAL_RECENT = re.compile(r"recent(?:ly)?\s+graduat\w*|graduated\s+within|university\s+grad|college\s+grad", re.IGNORECASE)
_SIGNAL_ENTRY = re.compile(r"entry[\s-]?level", re.IGNORECASE)
_SIGNAL_JUNIOR = re.compile(r"\bjunior\b|\bjr\b\.?", re.IGNORECASE)
_SIGNAL_NO_EXP = re.compile(r"no\s+(?:prior\s+|previous\s+)?(?:work\s+)?experience\s+(?:is\s+)?(?:required|necessary|needed)", re.IGNORECASE)

SIGNAL_ORDER = ("new grad", "recent graduate", "0-2 years", "entry level", "junior")
TWO_YEARS = "2+ yrs"                 # requirement is exactly 2 years (kept only with a strong Fit, see enrich)
LOW_SIGNAL = "low signal"            # JD found, but too few tech terms to trust Fit %
JD_UNAVAILABLE = "jd unavailable"    # no JD text could be fetched


def detect_signals(title: str, jd: str, exp: Experience) -> list[str]:
    """Boost signals. `junior` is title-only (JDs say 'mentor junior engineers' for senior roles)."""
    both = f"{title}\n{jd}"
    found = set()
    if _SIGNAL_NEW_GRAD.search(both):
        found.add("new grad")
    if _SIGNAL_RECENT.search(both):
        found.add("recent graduate")
    if _SIGNAL_ENTRY.search(both):
        found.add("entry level")
    if _SIGNAL_JUNIOR.search(title):
        found.add("junior")
    if any(hi is not None and hi <= 2 and lo <= 1 for lo, hi in exp.ranges) or _SIGNAL_NO_EXP.search(jd):
        found.add("0-2 years")
    return [s for s in SIGNAL_ORDER if s in found]
