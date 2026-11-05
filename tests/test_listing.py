from datetime import datetime, timedelta, timezone

import pytest

from scanner.sources.listing import parse_age, years_required

NOW = datetime(2026, 11, 5, 18, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("text, delta", [
    ("2 Days Ago", timedelta(days=2)),
    ("9 days", timedelta(days=9)),
    ("Reposted 10 Hours Ago", timedelta(hours=10)),
    ("13 Minutes Ago", timedelta(minutes=13)),
    ("One Month Ago", timedelta(days=30)),
    ("2 Months Ago", timedelta(days=60)),
    ("about 2 months", timedelta(days=60)),
    ("over 1 year", timedelta(days=365)),
    ("a day", timedelta(days=1)),
    ("Yesterday", timedelta(days=1)),
    ("Reposted Yesterday", timedelta(days=1)),
    ("3 weeks", timedelta(days=21)),
    ("Today", timedelta(0)),
    ("less than a minute", timedelta(0)),
])
def test_parse_age(text, delta):
    assert parse_age(text, NOW) == NOW - delta


@pytest.mark.parametrize("text", ["", None, "Featured", "Hybrid", "Vancouver, BC", "Apply now"])
def test_text_without_an_age_is_none(text):
    assert parse_age(text, NOW) is None


@pytest.mark.parametrize("text, years", [
    ("Any (new grads ok)", None), ("1+ years", 1.0), ("3+ years", 3.0), ("5+ years", 5.0), ("2-4 years", 2.0),
    ("", None), (None, None), ("1 year", 1.0)])
def test_years_required(text, years):
    assert years_required(text) == years
