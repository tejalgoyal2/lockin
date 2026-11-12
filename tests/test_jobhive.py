from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq

from scanner.sources import jobhive

NOW = datetime(2026, 11, 12, 16, 0, tzinfo=timezone.utc)
CUTOFF = datetime(2026, 11, 5, tzinfo=timezone.utc)
ORACLE = "https://fa-epmd-saasfaprod1.fa.ocs.oraclecloud.com/?keyword=&mode=jobs&lang=en&site_number=CX_1#24894"


def row(**kw):
    base = {"url": "https://jobs.smartrecruiters.com/acme/744000160951959", "apply_url": None, "title": "IT Support Analyst",
            "company": "Acme", "location": "Burnaby, BC, Canada", "country_iso": "CA",
            "posted_at": "2026-11-10T00:00:00+00:00", "description": "<p>Help <b>desk</b></p>", "raw": ""}
    return {**base, **kw}


def test_rows_in_the_window_become_records_with_their_description():
    recs = jobhive.records([row(), row(url="https://x.breezy.hr/p/1", posted_at="2026-10-01T00:00:00"),
                            row(url="https://x.breezy.hr/p/2", posted_at=None)], CUTOFF)
    assert len(recs) == 1
    rec = recs[0]
    assert rec["source"] == "Jobhive" and rec["company"] == "Acme" and rec["location"] == "Burnaby, BC, Canada"
    assert "Help" in rec["jd"] and "desk" in rec["jd"] and "<" not in rec["jd"]
    assert rec["first_seen"] == datetime(2026, 11, 10, tzinfo=timezone.utc)


def test_oracle_links_open_the_posting_and_tenant_codes_get_the_employer_name():
    names = {"fa-epmd-saasfaprod1": "Careers at Linamar", "hbbq": "x"}
    rec = jobhive.records([row(url=ORACLE, company="Fa Epmd Saasfaprod1", raw='{"oracle_id": "24894"}')], CUTOFF, names)[0]
    assert rec["url"] == ("https://fa-epmd-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/24894")
    assert rec["company"] == "Linamar"
    assert jobhive.company_for("Nokia", ORACLE, names) == "Nokia"             # a real name is kept
    assert jobhive.clean_company("BNY External Career Site") == "BNY"
    assert jobhive.clean_company("Emgi (CX_1001)") == "Emgi"
    assert jobhive.company_names("name,slug,url\nWTW External Careers Site,eedu,https://eedu.fa.us2.oraclecloud.com/x\n") \
        == {"eedu": "WTW External Careers Site"}


def test_a_snapshot_is_cut_to_its_canadian_rows(tmp_path):
    path = tmp_path / "s.parquet"
    pq.write_table(pa.Table.from_pylist([row(), row(country_iso="US", url="https://x/2")]), path)
    assert [r["url"] for r in jobhive.read_snapshot(path)] == [row()["url"]]


def test_one_failing_snapshot_does_not_cost_the_others(monkeypatch, tmp_path, capsys):
    good = tmp_path / "good.parquet"
    pq.write_table(pa.Table.from_pylist([row()]), good)

    class Resp:
        def __init__(self, data):
            self.data = data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def iter_content(self, n):
            yield self.data

    def fake_get(url, **kw):
        if "/bad/" in url:
            raise OSError("down")
        return Resp(good.read_bytes())

    monkeypatch.setattr(jobhive, "http_get", fake_get)
    recs = jobhive.fetch(["bad", "smartrecruiters"], 7, NOW)
    assert len(recs) == 1 and "not read: bad (OSError)" in capsys.readouterr().out
