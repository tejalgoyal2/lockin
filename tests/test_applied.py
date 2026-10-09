import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from scanner import applied
from scanner.applied import AppliedKeyError, AppliedSet, company_keys, link_company_slugs, parse_title
from scanner.company_names import load_overrides
from scanner.models import Job

T1 = datetime(2026, 10, 3, tzinfo=timezone.utc)
OVERRIDES = load_overrides()

# Fake tracker rows only: the real list is private and lives encrypted in state/applied.json.enc.
FAKE_ROWS = [
    ("Widget Engineer (Initech)", "https://jobs.initech.example/jobs/101"),
    ("Platform Engineer New Grad, Data Systems - (Hooli)", "https://jobs.ashbyhq.com/hooli/aaaa-1111"),
    ("Records Analyst II (UBC)", "https://ubc.wd10.myworkdayjobs.com/x/job/y/Records-Analyst-II_JR1"),
    ("Cloud Support Engineer RBC", "https://jobs.rbc.com/ca/en/job/R-1/x"),
    ("Pipeline Analyst (Umbrella)", "https://umbrella.example/careers/opportunities/?gh_jid=111"),
    ("Field Technician/EIT (Planar-ish)", "260909"),
]


def job(company="acme", title="Software Engineer", url="https://boards.greenhouse.io/acme/jobs/1",
        company_name="", location="Toronto, ON"):
    return Job(company=company, company_name=company_name, title=title, location=location, url=url,
               source="Greenhouse", first_seen=T1, sources={"Greenhouse"})


@pytest.fixture
def key():
    return Fernet.generate_key()


def make(tmp_path, key, rows=FAKE_ROWS):
    path = tmp_path / "applied.json.enc"
    applied.encrypt_rows(path, rows, key)
    return AppliedSet.load_encrypted(path, key.decode(), OVERRIDES)


@pytest.mark.parametrize("raw,expected", [
    ("Widget Engineer (Initech)", ("Widget Engineer", "Initech")),
    ("Platform Engineer New Grad, Data Systems - (Hooli)", ("Platform Engineer New Grad, Data Systems", "Hooli")),
    ("SOFTWARE ENGINEER, WIDGETS (Planet", ("SOFTWARE ENGINEER, WIDGETS", "Planet")),
    ("Junior Widget Developer", ("Junior Widget Developer", "")),
    ("Cyber Analyst, Global Security\n(RBC)", ("Cyber Analyst, Global Security", "RBC")),
    ("", ("", "")),
])
def test_parse_title(raw, expected):
    assert parse_title(raw) == expected


@pytest.mark.parametrize("url,expected", [
    ("https://jobs.ashbyhq.com/hooli/aaaa", {"hooli"}),
    ("https://boards.greenhouse.io/acme/jobs/1", {"acme"}),
    ("https://ubc.wd10.myworkdayjobs.com/en-US/staff/job/x", {"ubc"}),
    ("https://acme.bamboohr.com/careers/793", {"acme"}),
    ("https://jobs.rbc.com/ca/en/job/R-1/x", {"rbc"}),
    ("", set()),
])
def test_link_company_slugs(url, expected):
    assert link_company_slugs(url) == expected


def test_company_keys_use_overrides():
    keys = company_keys("Canadian Tire", {"canadiantirecorp": "Canadian Tire"})
    assert {"canadiantire", "canadiantirecorp"} <= keys
    assert company_keys("", {}) == set()


# --- encryption ---------------------------------------------------------------------------

def test_file_on_disk_is_ciphertext_only(tmp_path, key):
    make(tmp_path, key)
    raw = (tmp_path / "applied.json.enc").read_bytes()
    assert raw.startswith(b"gAAAA")                                  # Fernet token
    assert b"Initech" not in raw and b"title" not in raw
    assert [p.name for p in tmp_path.iterdir()] == ["applied.json.enc"]   # no plaintext sidecar


def test_loading_writes_nothing_to_disk(tmp_path, key, monkeypatch):
    make(tmp_path, key)
    before = sorted(p.name for p in tmp_path.iterdir())
    monkeypatch.chdir(tmp_path)
    AppliedSet.load_encrypted(tmp_path / "applied.json.enc", key.decode(), OVERRIDES)
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_missing_key_warns_and_skips(tmp_path, key, capsys):
    make(tmp_path, key)
    for missing in ("", "   ", None):
        s = AppliedSet.load_encrypted(tmp_path / "applied.json.enc", missing, OVERRIDES)
        assert len(s) == 0
        assert s.filter([job()])[0] == [job()]
    err = capsys.readouterr().err
    assert "APPLIED_KEY is not set" in err and "skipping" in err


def test_missing_file_warns_and_skips(tmp_path, key, capsys):
    s = AppliedSet.load_encrypted(tmp_path / "nope.enc", key.decode(), OVERRIDES)
    assert len(s) == 0
    assert "not found" in capsys.readouterr().err


def test_wrong_or_malformed_key_is_a_clear_error(tmp_path, key):
    make(tmp_path, key)
    path = tmp_path / "applied.json.enc"
    for bad in (Fernet.generate_key().decode(), "not-a-fernet-key"):
        with pytest.raises(AppliedKeyError) as exc:
            AppliedSet.load_encrypted(path, bad, OVERRIDES)
        assert bad not in str(exc.value) and "Initech" not in str(exc.value)


def test_corrupt_file_is_a_clear_error(tmp_path, key):
    path = tmp_path / "applied.json.enc"
    path.write_bytes(b"gAAAAnot-a-real-token")
    with pytest.raises(AppliedKeyError):
        AppliedSet.load_encrypted(path, key.decode(), OVERRIDES)


def test_repo_holds_ciphertext_not_plaintext():
    assert not Path("state/applied.json").exists()
    raw = Path("state/applied.json.enc").read_bytes()
    assert raw.startswith(b"gAAAA")
    with pytest.raises(ValueError):
        json.loads(raw)
    assert "state/applied.json\n" in Path(".gitignore").read_text()


# --- matching -----------------------------------------------------------------------------

def test_match_by_url_ignores_tracking_params(tmp_path, key):
    s = make(tmp_path, key)
    assert s.match(job(company="initech", title="Something Else",
                       url="https://Jobs.initech.example/jobs/101/?utm_source=x")) == "url"


def test_job_id_params_keep_different_postings_apart(tmp_path, key):
    s = make(tmp_path, key)
    other = job(company="umbrella", title="Totally Different Role",
                url="https://umbrella.example/careers/opportunities/?gh_jid=222")
    assert s.match(other) is None
    assert s.match(job(company="umbrella", title="Totally Different Role",
                       url="https://umbrella.example/careers/opportunities/?gh_jid=111")) == "url"


def test_same_role_with_different_url_suffix_matches_on_company_and_title(tmp_path, key):
    s = make(tmp_path, key)
    feed_job = job(company="hooli", company_name="Hooli",
                   title="Platform Engineer New Grad - Data Systems",
                   url="https://jobs.ashbyhq.com/hooli/aaaa-1111/application?embed=true")
    assert s.match(feed_job) == "company+title"        # the /application suffix defeats the URL match


def test_company_alias_via_company_names(tmp_path, key):
    s = make(tmp_path, key)
    assert s.match(job(company="universityofbritishcolumbia", company_name="UBC",
                       title="Records Analyst II", url="https://elsewhere.example/1")) == "company+title"


def test_title_with_company_left_in_title(tmp_path, key):
    s = make(tmp_path, key)
    assert s.match(job(company="rbc", title="Cloud Support Engineer",
                       url="https://elsewhere.example/2")) == "company+title"


def test_same_title_other_company_is_kept(tmp_path, key):
    s = make(tmp_path, key)
    assert s.match(job(company="globex", title="Widget Engineer", url="https://elsewhere.example/3")) is None


def test_same_company_other_title_is_kept(tmp_path, key):
    s = make(tmp_path, key)
    assert s.match(job(company="initech", title="Senior Product Designer", url="https://elsewhere.example/4")) is None
    assert s.match(job(company="initech", title="Widget Engineer Intern Winter",
                       url="https://elsewhere.example/5")) is None      # Jaccard 0.6 < 0.8


def test_non_url_link_is_stored_empty_and_never_matches(tmp_path, key):
    s = make(tmp_path, key)
    assert [e.link for e in s.entries if e.title.startswith("Field")] == [""]
    assert s.match(job(company="other", title="Something", url="")) is None


def test_filter_splits_and_does_not_mutate(tmp_path, key):
    s = make(tmp_path, key)
    jobs = [job(company="initech", title="x", url="https://jobs.initech.example/jobs/101"),
            job(company="hooli", title="Platform Engineer New Grad, Data Systems", url="https://e.example/1"),
            job(company="acme", title="Software Engineer", url="https://e.example/2")]
    before = list(jobs)
    kept, skipped = s.filter(jobs)
    assert [j.company for j in kept] == ["acme"]
    assert [j.company for j in skipped["url"]] == ["initech"]
    assert [j.company for j in skipped["company+title"]] == ["hooli"]
    assert jobs == before


def test_saved_entries_hold_only_title_and_canonical_link(tmp_path, key):
    path = tmp_path / "a.enc"
    applied.encrypt_rows(path, [("Role  (Co)", "https://x.com/Job/1/?utm_source=a#top")], key)
    assert json.loads(Fernet(key).decrypt(path.read_bytes())) == {
        "version": 1, "applied": [{"title": "Role (Co)", "link": "https://x.com/Job/1"}]}
