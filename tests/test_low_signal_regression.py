"""Four real postings that read as "low signal" although their JDs are 4.6k-9.7k characters long.

Root cause: the term lists only knew application-level skills. A systems, consulting or analyst JD
("kernels", "RISC-V", "DevOps", "full-stack", "Excel", "database", "blockchain") scored 1-2 terms, below the
4-term minimum for Fit %. Fixes: a `context` vocabulary in gaps.yaml (counts towards the minimum, never
towards Fit %), "Go" recognised in a language list, and more requirement-section headings
("What you bring", "Who you are"). The fixtures are trimmed excerpts of the public postings.
"""
from pathlib import Path

import pytest

from scanner.score import Scorer, requirements_text

FIX = Path(__file__).parent / "fixtures" / "low_signal"
SCORER = Scorer.load()


def fit(name, min_terms=4):
    return SCORER.evaluate((FIX / f"{name}.txt").read_text(encoding="utf-8"), min_terms)


@pytest.mark.parametrize("name,matched,context", [
    ("guidewire", {"CI/CD", "test automation"}, {"DevOps", "full-stack", "backend", "frontend"}),
    ("optrust", {"SQL", "Power BI", "Excel"}, {"database"}),
    ("tenstorrent", {"C/C++"}, {"computer architecture", "low-level", "operating systems", "performance", "ISA"}),
    ("robinhood", {"Python", "data modeling"}, {"backend", "blockchain", "system design", "cloud"}),
])
def test_jd_terms_found(name, matched, context):
    f = fit(name)
    assert matched <= set(f.matched)
    assert context <= set(f.context)
    assert f.n_terms >= 4 and f.fit_pct is not None          # was: n/a + `low signal`


def go_found(text):
    go = next(t for t in SCORER.gaps if t.name == "Go")
    return go.count_in(text) > 0


def test_go_is_found_in_a_language_list_and_nowhere_else():
    text = (FIX / "robinhood.txt").read_text(encoding="utf-8")
    assert go_found(text)                                       # "languages such as Go, Python, or Java"
    assert "Go" not in fit("robinhood").gaps                    # ... but Python is one of the options (#66)
    for text in ("Go beyond what is expected. Go to market. Let's go!", "We go live in May; go ahead and apply.",
                 "Gopher fans welcome."):
        assert not go_found(text)
    for text in ("Backend languages such as Go, Python or Java.", "Experience with Python, Go.", "Python/Go services",
                 "Written in Golang"):
        assert go_found(text), text


def test_context_terms_count_towards_the_minimum_but_never_move_fit():
    base = SCORER.evaluate("Python, SQL, Docker.", 1)
    more = SCORER.evaluate("Python, SQL, Docker. Backend and frontend with DevOps, a database and networking.", 1)
    assert base.fit_pct == more.fit_pct == 75.0                  # 3 / (3 + 1)
    assert base.n_terms == 3 and more.n_terms == 8 and more.n_core == 3
    assert SCORER.evaluate("Python, SQL, Docker. Backend and frontend.", 4).fit_pct == 75.0
    assert SCORER.evaluate("Python, SQL, Docker.", 4).fit_pct is None


def test_context_terms_are_not_gaps():
    f = fit("guidewire")
    assert "DevOps" not in f.gaps and "DevOps" not in f.matched


def test_more_requirement_headings_are_recognised():
    for heading in ("What you bring", "Who You Are", "What we're looking for", "About you"):
        jd = f"Intro\n\n{heading}\n- Python\n\nBenefits\n- Dental\n"
        assert "Python" in requirements_text(jd), heading


def test_thin_match_cannot_reach_100_percent():
    f = SCORER.evaluate("Python, SQL, Docker, Git, AWS, Linux, React, Azure.", 4)
    assert f.fit_pct == 88.9                                      # 8 / (8 + 1)
    assert SCORER.evaluate("Python.", 1).fit_pct == 50.0             # 1 / (1 + 1)
