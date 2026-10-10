"""Apply the current filter rules to the rows already in the Notion Feed (SPEC §11 #57).

`python -m scanner clean-feed [--dry-run]` reads every Feed row (title, company, link, JD, checkboxes),
rebuilds the job, runs the same rules a new job faces, and trashes the rows that fail them. Rows with
Interested or Apply ticked, or a checkbox that cannot be read, are never trashed: they are reported.
Rows that fail the plain Canada location check are listed but never trashed.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from scanner import enrich as enrich_mod
from scanner import notion_feed
from scanner.cluster import ClusterResolver
from scanner.dedupe import canonical_url
from scanner.filters import Filters
from scanner.models import Job
from scanner.normalize import norm_text
from scanner.score import Scorer
from scanner.sources import feashliaa, simplify

# Rule names shown in the report; the phase-2 ones are the drop reasons of scanner.enrich.
RULE_COMPANY = "blocklisted company"
RULE_FRENCH_TITLE = "French title"
RULE_TITLE = "title exclude"
RULE_QUEBEC = enrich_mod.R_QUEBEC_ONLY


@dataclass
class FeedItem:
    page_id: str
    title: str
    company: str                 # as shown in the Feed, without the " · date" tag
    link: str
    source: str
    jd: str
    signals: list[str]
    interested: bool | None
    apply: bool | None
    fit: float | None = None     # the Fit % column (a fraction), as stored
    company_cell: str = ""       # the Company cell as stored, "<name> · <date tag>"


@dataclass
class Verdict:
    item: FeedItem
    rule: str | None             # None: the row passes every rule
    location: str = ""           # "" when the source record was not found
    location_status: str = ""    # filters.location_status result, "" when unknown


@dataclass
class CleanReport:
    verdicts: list[Verdict] = field(default_factory=list)

    def failing(self) -> list[Verdict]:
        return [v for v in self.verdicts if v.rule]

    def trashable(self) -> list[Verdict]:
        """Failing rows whose Interested and Apply boxes are both known and unticked."""
        return [v for v in self.failing() if v.item.interested is False and v.item.apply is False]

    def protected(self) -> list[Verdict]:
        return [v for v in self.failing() if v not in self.trashable()]

    def location_failures(self) -> list[Verdict]:
        """Rows that pass every rule but whose location fails the Canada filter (reported, never trashed)."""
        return [v for v in self.verdicts if not v.rule and v.location_status == "not_canada"]

    def location_unknown(self) -> list[Verdict]:
        return [v for v in self.verdicts if not v.rule and not v.location_status]


# --- reading the Feed -----------------------------------------------------------------

def _plain(items: list[dict]) -> str:
    return "".join((i.get("plain_text") or (i.get("text") or {}).get("content", "")) for i in items or [])


def list_items(client, feed: notion_feed.Feed) -> list[FeedItem]:
    """Every Feed row with the fields the rules need. Long JDs (a rich_text array Notion may cut short)
    are re-read through the property-item endpoint."""
    n, ids = feed.names, feed.ids
    params = {"filter_properties[]": [ids[k] for k in ("name", "company", "link", "source", "signals", "jd",
                                                        "interested", "apply", "fit")]}
    items, cursor = [], None
    while True:
        body = {"page_size": 100, **({"start_cursor": cursor} if cursor else {})}
        data = client.request("POST", f"/v1/data_sources/{feed.data_source_id}/query",
                              json=body, params=params, idempotent=True)
        for page in data.get("results", []):
            props = page.get("properties", {})
            jd_items = (props.get(n["jd"]) or {}).get("rich_text") or []
            jd = (notion_feed.read_property_text(client, page["id"], ids["jd"]) if len(jd_items) >= 25
                  else _plain(jd_items))
            company = _plain((props.get(n["company"]) or {}).get("rich_text"))
            items.append(FeedItem(
                page_id=page["id"],
                title=_plain((props.get(n["name"]) or {}).get("title")),
                company=company.rsplit(" · ", 1)[0],
                link=(props.get(n["link"]) or {}).get("url") or "",
                source=((props.get(n["source"]) or {}).get("select") or {}).get("name", ""),
                jd=jd,
                signals=[o["name"] for o in (props.get(n["signals"]) or {}).get("multi_select", [])],
                interested=(props.get(n["interested"]) or {}).get("checkbox"),
                apply=(props.get(n["apply"]) or {}).get("checkbox"),
                fit=(props.get(n["fit"]) or {}).get("number"),
                company_cell=company,
            ))
        if not data.get("has_more"):
            return items
        cursor = data.get("next_cursor")


# --- where was each job located? -----------------------------------------------------

@dataclass
class Found:
    """What the source data says about one job."""
    location: str = ""
    origin: str = ""             # "ats" (the posting's own location) or "simplify" (its list for the programme)
    company: str = ""            # the ATS slug (Feashliaa)
    company_name: str = ""       # readable name (Simplify)


def find_records(urls: set[str], cfg: dict, feashliaa_dir=None) -> dict[str, Found]:
    """canonical URL -> Found, from the Feashliaa dataset and the Simplify list. The Feashliaa location is the
    ATS's own and wins over Simplify's list for the whole programme; the slug comes from Feashliaa and the
    readable name from Simplify, as when the job was first merged."""
    wanted = {canonical_url(u) for u in urls if u}
    out: dict[str, Found] = {}
    src = cfg["sources"]
    if src["feashliaa"]["enabled"]:
        data_dir = feashliaa_dir or feashliaa.fetch(src["feashliaa"])
        for rec in feashliaa.iter_raw(data_dir):
            url = canonical_url(rec.get("url") or "")
            if url in wanted:
                out[url] = Found(rec.get("location") or "", "ats", rec.get("company") or "")
    if src["simplify"]["enabled"]:
        for rec in simplify.iter_raw(simplify.fetch(src["simplify"])):
            f = simplify.raw_fields(rec)
            url = canonical_url(f["url"])
            if url not in wanted:
                continue
            found = out.setdefault(url, Found(f["location"], "simplify", f["company"]))
            found.company_name = found.company_name or f["company_name"]
    return out


def locations_from(records: dict[str, Found]) -> dict[str, tuple[str, str]]:
    return {url: (r.location, r.origin) for url, r in records.items()}


def find_locations(urls: set[str], cfg: dict, feashliaa_dir=None) -> dict[str, tuple[str, str]]:
    """canonical URL -> (location, origin)."""
    return locations_from(find_records(urls, cfg, feashliaa_dir))


# --- the rules ------------------------------------------------------------------------

def to_job(item: FeedItem, location: str) -> Job:
    return Job(company=item.company, company_name=item.company, title=item.title, location=location,
               url=item.link, source=item.source, first_seen=datetime.now(timezone.utc),
               sources={item.source}, jd=item.jd, jd_status="ok" if item.jd.strip() else "")


def evaluate(item: FeedItem, location: str, cfg: dict, filters: Filters, scorer: Scorer,
             clusters: ClusterResolver, jd_locations: list[str] | None = None, origin: str = "") -> Verdict:
    """The first rule a Feed row breaks, in the order a new job meets them.

    `origin` says where `location` came from: "ats" (the posting's own location) or "simplify" (its list for the
    whole programme). A Simplify row whose ATS location is not Canadian is not a Canadian job."""
    status = filters.location_status(location)[0] if location else ""
    unresolved = status == "unresolved"
    v = Verdict(item, None, location, status)
    if not filters.company_ok(item.company):
        v.rule = RULE_COMPANY
        return v
    if not filters.language_ok(item.title):
        v.rule = RULE_FRENCH_TITLE
        return v
    if not filters.title_not_excluded(item.title):
        v.rule = RULE_TITLE
        return v
    if status == "quebec_only":
        v.rule = RULE_QUEBEC
        return v
    if status == "not_canada" and item.source == "Simplify" and origin == "ats":
        v.rule = enrich_mod.R_ATS_NOT_CANADA
        return v
    job = to_job(item, location or "Canada")      # unknown location: do not let the US-authorization rule guess
    job.weak_title = filters.title_tier(item.title) == "weak"
    if unresolved:
        job.location_pending, job.jd_locations = True, jd_locations or []
    job.jd_status = "ok" if item.jd.strip() else ("error" if "jd unavailable" in item.signals else "")
    reason = enrich_mod.apply_rules(job, cfg, filters, scorer, clusters)
    if reason:
        v.rule = reason
    if unresolved and not job.location_pending:
        v.location, v.location_status = job.location, "ok"
    return v


def clean(items: list[FeedItem], locations: dict[str, tuple[str, str]], cfg: dict, filters: Filters, scorer: Scorer,
          jd_locations: dict[str, list[str]] | None = None) -> CleanReport:
    clusters = ClusterResolver(cfg)
    rep = CleanReport()
    for item in items:
        key = canonical_url(item.link)
        location, origin = locations.get(key, ("", ""))
        rep.verdicts.append(evaluate(item, location, cfg, filters, scorer, clusters, (jd_locations or {}).get(key),
                                     origin))
    return rep


# --- output ---------------------------------------------------------------------------

def _row(v: Verdict) -> str:
    return f"| {v.item.title.replace('|', '/')} | {v.item.company.replace('|', '/')} |"


def render(rep: CleanReport, *, dry_run: bool) -> str:
    out = [f"Feed cleanup ({'dry run: nothing trashed' if dry_run else 'trashing rows'}): "
           f"{len(rep.verdicts)} rows read, {len(rep.failing())} break a rule, "
           f"{len(rep.trashable())} can be trashed, {len(rep.protected())} protected"]
    by_rule: dict[str, list[Verdict]] = defaultdict(list)
    for v in rep.trashable():
        by_rule[v.rule].append(v)
    out += ["", "Rule -> rows removed (Interested and Apply both unticked)", "",
            "| Rule | Role | Company |", "|---|---|---|"]
    for rule, vs in sorted(by_rule.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        for v in vs:
            out.append(f"| {rule} " + _row(v))
    if not by_rule:
        out.append("| none | | |")
    if rep.protected():
        out += ["", "Break a rule but NOT trashed (a checkbox is ticked or unreadable)", "",
                "| Rule | Role | Company |", "|---|---|---|"]
        out += [f"| {v.rule} " + _row(v) for v in rep.protected()]
    fails = rep.location_failures()
    out += ["", f"Remaining rows whose location fails the Canada filter (reported, not trashed): {len(fails)}"]
    if fails:
        out += ["", "| Role | Company | Location |", "|---|---|---|"]
        out += [f"| {v.item.title.replace('|', '/')} | {v.item.company.replace('|', '/')} | {v.location} |"
                for v in fails]
    unknown = rep.location_unknown()
    out += ["", f"Remaining rows whose source record was not found, so the location was not checked: {len(unknown)}"]
    if unknown:
        out += ["", "| Role | Company |", "|---|---|"] + [_row(v) for v in unknown]
    return "\n".join(out) + "\n"


def trash(client, rep: CleanReport, *, dry_run: bool) -> int:
    rows = [notion_feed.FeedRow(v.item.page_id, datetime.now(timezone.utc), v.item.link, False, False)
            for v in rep.trashable()]
    return notion_feed.trash_rows(client, rows, dry_run=dry_run)


# --- rescoring the rows that stay ------------------------------------------------------

@dataclass
class Change:
    item: FeedItem
    company: tuple[str, str] | None = None            # (old cell, new cell)
    fit: tuple[float | None, float | None] | None = None
    signals: tuple[list[str], list[str]] | None = None

    def properties(self, names: dict[str, str]) -> dict:
        props = {}
        if self.company:
            props[names["company"]] = {"rich_text": [{"type": "text", "text": {"content": self.company[1]}}]}
        if self.fit:
            props[names["fit"]] = {"number": self.fit[1]}
        if self.signals:
            props[names["signals"]] = {"multi_select": [{"name": x} for x in self.signals[1]]}
        return props


def _fit_cell(fit: float | None) -> str:
    return "n/a" if fit is None else f"{fit * 100:.1f}%"


def rescore(verdicts: list[Verdict], records: dict[str, Found], cfg: dict, filters: Filters, scorer: Scorer,
            overrides: dict[str, str]) -> list[Change]:
    """Recompute Company, Fit % and Signals for the rows that passed every rule and have both boxes unticked,
    with the current term lists, Fit formula, 2-year tag and company_names.yaml. Only changed values are returned."""
    clusters = ClusterResolver(cfg)
    changes = []
    for v in verdicts:
        it = v.item
        if v.rule or it.interested is not False or it.apply is not False:
            continue
        rec = records.get(canonical_url(it.link)) or Found()
        job = to_job(it, v.location or "Canada")
        job.weak_title = filters.title_tier(it.title) == "weak"
        job.jd_status = "ok" if it.jd.strip() else ("error" if "jd unavailable" in it.signals else "")
        job.company, job.company_name = rec.company or it.company, rec.company_name
        enrich_mod.apply_rules(job, cfg, filters, scorer, clusters)
        ch = Change(it)
        # Company: only company_names.yaml changes a name. Names that came from the ATS ("Tucows Inc.") cannot be
        # rebuilt from the dataset, so they are left alone.
        shown = overrides.get(norm_text(rec.company)) if rec.company else None
        tag = it.company_cell.rsplit(" · ", 1)[1] if " · " in it.company_cell else ""
        if shown:
            new_cell = f"{shown} · {tag}" if tag else shown
            if new_cell != it.company_cell:
                ch.company = (it.company_cell, new_cell)
        new_fit = None if job.fit_pct is None else round(job.fit_pct / 100, 4)
        if (new_fit is None) != (it.fit is None) or (new_fit is not None and abs(new_fit - it.fit) > 0.0002):
            ch.fit = (it.fit, new_fit)
        if sorted(job.signals) != sorted(it.signals):
            ch.signals = (list(it.signals), list(job.signals))
        if ch.company or ch.fit or ch.signals:
            changes.append(ch)
    return changes


def render_changes(changes: list[Change], rescored: int, *, dry_run: bool) -> str:
    out = [f"Rescore ({'dry run: nothing changed' if dry_run else 'updated in place'}): {rescored} rows with both boxes "
           f"unticked kept, {len(changes)} changed", "",
           "| Role | Company | Fit % | Signals |", "|---|---|---|---|"]
    for c in changes:
        company = f"{c.company[0]} → {c.company[1]}" if c.company else c.item.company_cell
        fit = f"{_fit_cell(c.fit[0])} → {_fit_cell(c.fit[1])}" if c.fit else _fit_cell(c.item.fit)
        sig = (f"{', '.join(c.signals[0]) or '-'} → {', '.join(c.signals[1]) or '-'}" if c.signals
               else ", ".join(c.item.signals) or "-")
        out.append(f"| {c.item.title.replace('|', '/')} | {company} | {fit} | {sig} |")
    if not changes:
        out.append("| none | | | |")
    return "\n".join(out) + "\n"


def apply_changes(client, feed, changes: list[Change], *, dry_run: bool) -> int:
    done = 0
    for c in changes:
        if not dry_run:
            client.request("PATCH", f"/v1/pages/{c.item.page_id}", json={"properties": c.properties(feed.names)})
        done += 1
    return done
