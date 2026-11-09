"""A hand-curated GitHub list of Canadian junior / new-grad jobs (hanzili/canada_sde_junior_new_grad_position).

The list's README (and its FINANCE.md) hold markdown tables, one row a posting: title (with an `<!--id:N-->` marker and a
fire / sleepy / new emoji), company, a one-line summary, company info, details, location and an `[Apply](<link>)`. Most links
go to LinkedIn, which lockin does not read, so these postings reach the Feed without a description (marked `jd unavailable`);
the title, company, location and link are what the owner needs to open the posting. The list carries no posting date, so a
row counts as first seen when it is first read (`seen.json` keeps it from coming twice).
"""
import re
from datetime import datetime

SOURCE = "Canada List"
_ROW = re.compile(r"^\|(?P<cells>.+)\|\s*$")
_APPLY = re.compile(r"\[Apply\]\(<?([^)>\s]+)>?\)")
_ID = re.compile(r"<!--\s*id:\d+\s*-->")
_EMOJI = re.compile(r"[\U0001F300-\U0001FAFF☀-➿️]+")


def parse(markdown: str, now: datetime) -> list[dict]:
    out = []
    for line in (markdown or "").splitlines():
        apply = _APPLY.search(line)
        m = _ROW.match(line.strip())
        if not (apply and m):
            continue
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", m.group("cells"))]
        if len(cells) < 6:
            continue
        title = re.sub(r"\s+", " ", _EMOJI.sub("", _ID.sub("", cells[0]))).strip()
        company, location = cells[1], cells[5]
        if title and company:
            out.append({"company": company, "company_name": company, "title": title, "location": location,
                        "url": apply.group(1), "source": SOURCE, "first_seen": now, "summary": cells[2]})
    return out


def fetch(urls: list[str], client, now: datetime) -> list[dict]:
    seen, out = set(), []
    for url in urls:
        for rec in parse(client.get(url, SOURCE).text, now):
            if rec["url"] not in seen:
                seen.add(rec["url"])
                out.append(rec)
    return out
