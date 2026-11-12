"""The pay a posting states, as one short line for the Feed's Pay column ("$70,000–$85,000 a year").

Ontario (since 2026) and British Columbia (since 2023) require a pay range in public job postings, and many employers
elsewhere publish one, so most Canadian descriptions carry it. `from_text` finds the range in a description; a source
that already has the pay as data (the Jobhive dataset) formats it with `fmt`. Where a posting gives a US and a
Canadian range, the Canadian one is kept. Nothing is guessed: no figure, no Pay.
"""
import re

_AMOUNT = r"(?:CA?D\s*)?(?:C?\$|CAD\s*\$?)\s?(\d{1,3}(?:,\d{3})+(?:\.\d{1,3})?|\d+(?:\.\d{1,3})?)\s?([kK])?(?:\s*CAD)?"
_AMOUNT2 = r"(?:CA?D\s*)?(?:C?\$|CAD\s*\$?)?\s?(\d{1,3}(?:,\d{3})+(?:\.\d{1,3})?|\d+(?:\.\d{1,3})?)\s?([kK])?(?:\s*CAD)?"   # "$70,000 - 85,000"
_PERIOD = r"(?:\s*(?:per|an?|/)\s*(?:hour|hr|year|yr|annum|month))?"
_RANGE = re.compile(_AMOUNT + _PERIOD + r"\s*(?:-|–|—|to|and|,?\s*max(?:imum)?\s*:?)\s*" + _AMOUNT2 + r"(?!\s*(?:million|billion|[mb]\b))",
                    re.IGNORECASE)
_SINGLE = re.compile(_AMOUNT + r"(?!\s*(?:million|billion|[mb]\b|\+))", re.IGNORECASE)
_HOURLY = re.compile(r"^\W{0,3}(?:per|an?|/)\s*(?:hour|hr)\b|^\W{0,3}hourly", re.IGNORECASE)
_MONTHLY = re.compile(r"^\W{0,3}(?:per|an?|/)\s*(?:month|mo)\b|^\W{0,3}monthly", re.IGNORECASE)
_YEARLY = re.compile(r"^\W{0,3}(?:per|an?|/)\s*(?:year|yr|annum)\b|^\W{0,3}annual(?:ly)?", re.IGNORECASE)
_PAY_WORDS = re.compile(r"salary|pay\b|pay\s+(?:range|rate|scale|band)|compensation|wage|hourly|rate\b|base\b|"
                        r"remuneration|earn", re.IGNORECASE)
_CANADA = re.compile(r"\bCAD\b|canad|C\$|ontario|toronto|british columbia|vancouver|alberta|quebec|montr", re.IGNORECASE)
_US = re.compile(r"\bUSD\b|US\$|\bU\.?S\.? (?:base|based|range|candidates|locations)|united states|california|new york|"
                 r"washington|colorado|seattle|san francisco", re.IGNORECASE)


def _num(digits: str, k: str | None) -> float:
    n = float(digits.replace(",", ""))
    return n * 1000 if k else n


def _period(lo: float, after: str) -> str | None:
    if _HOURLY.search(after):
        return "hour"
    if _MONTHLY.search(after):
        return "month"
    if _YEARLY.search(after):
        return "year"
    if 12 <= lo < 300:
        return "hour"
    if 20_000 <= lo <= 600_000:
        return "year"
    return None


_PLAUSIBLE = {"hour": (12, 500), "month": (1_000, 40_000), "year": (20_000, 900_000)}


def _money(n: float) -> str:
    return f"${n:,.2f}" if round(n % 1, 2) else f"${n:,.0f}"


def fmt(lo: float | None, hi: float | None, period: str | None = None, currency: str = "") -> str:
    """'$70,000–$85,000 a year', '$24.76 an hour', '$150,000–$180,000 a year (USD)'. Empty when it makes no sense."""
    lo, hi = lo or hi, hi or lo
    if not lo or hi < lo:
        return ""
    period = (period or "").lower()
    period = ("hour" if period.startswith("h") else "month" if period.startswith("mo") else
              "year" if period.startswith(("y", "a")) else _period(lo, ""))
    if period is None or not _PLAUSIBLE[period][0] <= lo <= _PLAUSIBLE[period][1] or hi > 3 * lo:
        return ""
    money = _money(lo) if lo == hi else f"{_money(lo)}–{_money(hi)}"
    tail = " (USD)" if (currency or "").upper() == "USD" else ""
    return f"{money} {'a year' if period == 'year' else 'an hour' if period == 'hour' else 'a month'}{tail}"


def from_text(text: str) -> str:
    """The pay range stated in a job description, formatted by `fmt`; '' when none is stated."""
    text = re.sub(r"(\d),\s(\d{3})\b", r"\1,\2", re.sub(r"\s+", " ", text or ""))     # "$88, 101"
    found = []
    for m in _RANGE.finditer(text):
        lo, hi = _num(m.group(1), m.group(2) or m.group(4)), _num(m.group(3), m.group(4))
        found.append((m.start(), m.end(), lo, hi, True))
    taken = [(s, e) for s, e, *_ in found]
    for m in _SINGLE.finditer(text):
        if any(s <= m.start() < e for s, e in taken):
            continue
        before, after = text[max(0, m.start() - 60):m.start()], text[m.end():m.end() + 20]
        if _PAY_WORDS.search(before) or _HOURLY.search(after) or _YEARLY.search(after):   # a lone figure counts only
                                                    # when the text says it is the pay, or what period it is for
            n = _num(m.group(1), m.group(2))
            found.append((m.start(), m.end(), n, n, False))
    best, best_rank = "", None
    for start, end, lo, hi, is_range in found:
        before, after = text[max(0, start - 120):start], text[end:end + 40]
        period = ("hour" if _HOURLY.search(after) else "month" if _MONTHLY.search(after) else
                  "year" if _YEARLY.search(after) else None)
        currency = "USD" if re.search(r"\bUSD\b|US\$", text[max(0, start - 15):end + 8]) else ""
        line = fmt(lo, hi, period, currency)
        if not line:
            continue
        near = text[max(0, start - 15):end + 8]
        tag = 3 if re.search(r"\bCAD\b|C\$", near) else -3 if currency else 0
        ca = max((m.end() for m in _CANADA.finditer(before)), default=-1)     # the country named last before the
        us = max((m.end() for m in _US.finditer(before)), default=-1)         # figure is the one it is for
        rank = tag + (2 if ca > us else -2 if us > ca else 0) + bool(_PAY_WORDS.search(before)) + is_range
        if best_rank is None or rank > best_rank:
            best, best_rank = line, rank
    return best
