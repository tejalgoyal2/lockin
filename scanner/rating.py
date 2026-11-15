"""The Feed's two judgement columns: Exp (1-3, the experience the posting asks for) and Score (1-5, how much the job
is worth the owner's time).

Neither is a model's opinion: both are read off the posting by fixed rules, so the same posting always gets the same
numbers and every number can be explained from the three things it is built on.

Score = role + skills + level, cut down by what makes an application unlikely to pay off:

  role    2  the title names a role the owner targets (software, data, ML / AI, cloud, security ...)
          1  a neighbouring tech role (IT, systems, QA, technical support, embedded ...), or a generic title whose
             description is plainly the owner's kind of work (skills = 2)
          0  a generic title (Analyst, Engineer, Specialist) that the description only half vouches for
  skills  2  the posting names 4+ of the owner's tools and they are 60 %+ of the tech it asks for
          1  2+ of the owner's tools and 40 %+ of the tech it asks for; or a targeted role whose posting names
             almost no tech at all (it does not say what the stack is, so it cannot count against the job)
          0  anything else
  level   1  entry level: asks for under a year, or says new grad / junior / intern
          0  asks for 1-2 years, or states nothing

  5 points -> 5, 4 -> 4, 3 -> 3, 2 -> 2, fewer -> 1. Then the caps: a posting that asks for 2 years is at most a 2,
  one with no readable description at most a 2 (nothing confirms the fit), a talent pool at most a 2, and one that
  requires a P.Eng licence is a 1. An intern / co-op role is at most a 3 unless it says graduates may apply.
"""
from scanner.models import Job

# What the rules noticed about a posting (Job.notes). Shown in reports/latest.md, never in the Feed.
NEW_GRAD = "new grad"            # the title or description addresses new / recent graduates
JUNIOR = "junior"                # the title says junior / entry level / associate, or the description says entry level
INTERN = "internship"            # an intern / co-op / student title
GRADS_OK = "open to graduates"   # ... whose description says graduates may apply
TWO_YEARS = "asks 2 years"
PENG = "needs P.Eng"
POOL = "talent pool"             # evergreen posting, no current opening
NO_JD = "no description"         # the posting could not be read
THIN = "thin description"        # read, but it names almost no tech: the fit cannot be measured

ROLE_POINTS = {"core": 2, "adjacent": 1, "generic": 0}
STRONG_FIT, STRONG_SKILLS = 0.60, 4
SOME_FIT, SOME_SKILLS = 0.40, 2
THIN_TERMS = 2                   # a description naming this many skill / gap terms or fewer is thin
ENTRY_NOTES = (NEW_GRAD, JUNIOR, INTERN)


def exp_range(min_years: float | None, notes, entry_title: bool = False) -> int:
    """1 = asks for 0-1 years (or addresses new grads / juniors / interns), 2 = 1-2 years, or no number at all,
    3 = 2 years. A posting that asks for more never reaches the Feed (jd.max_years). A title that says new grad,
    junior or intern makes a "1-2 years" posting a 1: the title says who the job is for."""
    if min_years is None:
        return 1 if any(n in notes for n in ENTRY_NOTES) else 2
    if min_years < 1 or (entry_title and min_years < 2):
        return 1
    return 2 if min_years < 2 else 3


def skill_points(job: Job) -> int:
    if THIN in job.notes or job.fit is None:
        return 1 if job.role == "core" and NO_JD not in job.notes else 0
    if job.fit >= STRONG_FIT and job.hard >= STRONG_SKILLS:
        return 2
    return 1 if job.fit >= SOME_FIT and job.hard >= SOME_SKILLS else 0


def score(job: Job) -> int:
    """1-5 for a job whose role, fit, notes and exp are filled (scanner.enrich)."""
    skills = skill_points(job)
    role = ROLE_POINTS.get(job.role, 0)
    if job.role == "generic" and skills == 2:
        role = 1
    value = max(1, min(5, role + skills + (1 if job.exp == 1 else 0)))
    if PENG in job.notes:
        return 1
    if job.exp == 3 or NO_JD in job.notes or POOL in job.notes:
        value = min(value, 2)
    if INTERN in job.notes and GRADS_OK not in job.notes:
        value = min(value, 3)          # a student role that does not say graduates may apply
    return value
