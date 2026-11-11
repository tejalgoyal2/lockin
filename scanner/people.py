"""Two people to write to about a job: a recruiter and the hiring manager, as LinkedIn people searches.

lockin does not read LinkedIn (it refuses automated readers); it writes two links into the Feed row that open
LinkedIn's own people search, already narrowed to the company and the team. LinkedIn ranks the answers by how close
each person is to you (1st, 2nd degree first), which is exactly the order to write in, and shows who could refer you.

The team comes from the job title: "Data Analyst" -> data, "Security Analyst" -> security, "IT Support" -> IT,
anything else -> engineering.
"""
import re
from urllib.parse import quote

SEARCH = "https://www.linkedin.com/search/results/people/?keywords="
_TEAMS = (
    (re.compile(r"\b(?:security|cyber|soc)\b", re.I), '(security OR cybersecurity)'),
    (re.compile(r"\b(?:machine learning|ml|ai|scientist)\b", re.I), '("machine learning" OR AI OR "data science")'),
    (re.compile(r"\b(?:data|analytics|bi|business intelligence|database)\b", re.I), '(data OR analytics)'),
    (re.compile(r"\b(?:it|support|help ?desk|service desk|systems administrator|network|infrastructure)\b", re.I),
     '(IT OR infrastructure OR "technical support")'),
    (re.compile(r"\b(?:qa|quality|test|testing)\b", re.I), '(QA OR quality OR engineering)'),
    (re.compile(r"\b(?:devops|sre|site reliability|cloud|platform)\b", re.I), '(platform OR cloud OR DevOps OR infrastructure)'),
)
_DEFAULT_TEAM = '(engineering OR software OR development)'
_SUFFIX = re.compile(r"\s*·.*$|,?\s+(?:inc|ltd|llc|corp|corporation|limited|co)\.?$", re.I)


def team(title: str) -> str:
    for pat, words in _TEAMS:
        if pat.search(title or ""):
            return words
    return _DEFAULT_TEAM


def _company(name: str) -> str:
    return _SUFFIX.sub("", (name or "").replace('"', "")).strip()


def recruiter_link(company: str) -> str:
    """People at the company who recruit: recruiters, talent acquisition, talent partners."""
    name = _company(company)
    if not name:
        return ""
    return SEARCH + quote(f'"{name}" (recruiter OR "talent acquisition" OR "talent partner" OR "technical recruiter")')


def manager_link(company: str, title: str) -> str:
    """People at the company who lead the team the job is on: managers, leads, directors."""
    name = _company(company)
    if not name:
        return ""
    return SEARCH + quote(f'"{name}" {team(title)} (manager OR lead OR director OR head)')
