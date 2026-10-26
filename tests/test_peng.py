"""`P.Eng req` Signal (SPEC §11 #72): a JD that requires a P.Eng licence is tagged, never dropped."""
import pytest

from scanner import enrich as E
from scanner.jd_rules import requires_peng
from tests.test_enrich import gh, run

HYDRO = ("Requirements:\n- Undergraduate degree in Engineering\n"
         "- Professional Engineer, licensed or able to be licensed in the province of Ontario\n"
         "- Familiarity with PowerBI and/or Tableau, Python, SQL\n")


@pytest.mark.parametrize("jd", [
    HYDRO,
    "Qualifications:\n- P.Eng. designation\n- Python and SQL\n",
    "You must hold a P.Eng licence with PEO.",
    "- Registered as a Professional Engineer (P.Eng) with APEGA or eligible to apply",
    "Membership in good standing with EGBC is required.",
])
def test_peng_required(jd):
    assert requires_peng(jd), jd


@pytest.mark.parametrize("jd", [
    "Requirements:\n- P.Eng or EIT with APEGA\n",                                    # EIT accepted
    "Qualifications:\n- Engineer-in-Training (EIT) or P.Eng.\n",
    "We provide the training and skills needed to achieve your P.Eng. designation.",  # a perk
    "Professional registration with EGBC is considered an asset.",                    # optional
    "You will develop engineering software in C++ and mentor engineers.",            # "develop engineering"
    "Our professional engineering services team supports clients.",
    "Requirements:\n- Python, SQL\n",
])
def test_peng_not_required(jd):
    assert not requires_peng(jd), jd


def test_a_peng_job_is_kept_with_the_signal():
    res = run([gh(1, title="Operations Business Intelligence Engineer")], jds={"1": HYDRO})
    (j,) = res.kept
    assert "P.Eng req" in j.signals and res.drops == {}
