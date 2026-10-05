"""Rules applied to fetched JD text (SPEC §4 'JD rules')."""
import re
from dataclasses import dataclass

_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
          "eight": 8, "nine": 9, "ten": 10}
_NUM = r"(?:\d{1,3}(?:\.\d+)?|" + "|".join(_WORDS) + r")"
_YEARS = re.compile(
    rf"(?<![\d.])(?P<lo>{_NUM})(?:\s*\(\d{{1,2}}\))?\s*(?P<plus>\+)?\s*"
    rf"(?:(?:-|–|—|to)\s*(?P<hi>{_NUM})(?:\s*\(\d{{1,2}}\))?\s*(?P<plus2>\+)?\s*)?"
    r"(?:years?|yrs?)\b",
    re.IGNORECASE,
)
_EXPERIENCE = re.compile(r"experience|expérience", re.IGNORECASE)
_UPPER_BOUND_LEAD = re.compile(r"(?:less than|fewer than|up to|under|maximum of|max\.?|no more than)\s*$", re.IGNORECASE)
_WINDOW = 100          # characters either side of "N years" searched for the word "experience"
_BOILERPLATE_MIN = 20  # "over 100 years in business", "25 years of history": never a job requirement

ENROLLMENT = re.compile(
    r"currently\s+enrolled|returning\s+to\s+(?:school|studies)|must\s+be\s+a\s+(?:current\s+)?student"
    r"|enrolled\s+in\s+a\s+co-?op\s+program", re.IGNORECASE)
_CLEARANCE = re.compile(r"security\s+clearance", re.IGNORECASE)
_CLEARANCE_REQUIRED = re.compile(r"requir|must|need|obtain|maintain|hold|eligib|active|valid|able to|ability", re.IGNORECASE)
_NEGATION = re.compile(r"\b(?:no|not|without|neither|nor)\b[^.\n]{0,15}$", re.IGNORECASE)
US_AUTH = re.compile(
    r"\bU\.?S\.?\s+citizen(?:ship)?|\bUnited\s+States\s+citizen(?:ship)?"
    r"|(?:authori[sz]ed|eligible|legally\s+(?:able|entitled))\s+to\s+work\s+in\s+the\s+(?:United\s+States|U\.?S\.?A?\.?)\b",
    re.IGNORECASE)
RECENT_GRAD = re.compile(
    r"recent(?:ly)?\s+graduat\w*|new[\s-]?grads?\b|new\s+graduates?|graduated\s+within", re.IGNORECASE)


@dataclass(frozen=True)
class Experience:
    min_years: float | None                # smallest lower bound across all mentions (None = no mention)
    ranges: tuple[tuple[float, float | None], ...] = ()   # (lo, hi) per mention


def _num(token: str) -> float:
    return float(_WORDS.get(token.lower(), token)) if token else 0.0


def parse_experience(text: str) -> Experience:
    """Years-of-experience mentions: "2+ years", "3-5 years", "one to two years of experience".

    A mention only counts when the word "experience" is within `_WINDOW` characters.
    The smallest number in a range is its lower bound; across several mentions the smallest
    lower bound wins ("1+ year SQL, 3+ years Java" asks for 1). "Less than 2 years" style upper
    bounds, numbers >= 20 (company boilerplate) and "1.5"-style fractions below 3 are handled.
    """
    ranges: list[tuple[float, float | None]] = []
    for m in _YEARS.finditer(text or ""):
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
    return Experience(min((lo for lo, _ in ranges), default=None), tuple(ranges))


def requires_enrollment(text: str) -> bool:
    return bool(ENROLLMENT.search(text or ""))


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


def mentions_recent_grad(text: str) -> bool:
    return bool(RECENT_GRAD.search(text or ""))


# --- Notion "Signals" multi-select ------------------------------------------
_SIGNAL_NEW_GRAD = re.compile(r"new[\s-]?grad(?:uate)?s?\b", re.IGNORECASE)
_SIGNAL_RECENT = re.compile(r"recent(?:ly)?\s+graduat\w*|graduated\s+within|university\s+grad|college\s+grad", re.IGNORECASE)
_SIGNAL_ENTRY = re.compile(r"entry[\s-]?level", re.IGNORECASE)
_SIGNAL_JUNIOR = re.compile(r"\bjunior\b|\bjr\b\.?", re.IGNORECASE)
_SIGNAL_NO_EXP = re.compile(r"no\s+(?:prior\s+|previous\s+)?(?:work\s+)?experience\s+(?:is\s+)?(?:required|necessary|needed)", re.IGNORECASE)

SIGNAL_ORDER = ("new grad", "recent graduate", "0-2 years", "entry level", "junior")


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
