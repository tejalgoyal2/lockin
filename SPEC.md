# SPEC: Canada Entry-Level Job Scanner → Notion Job Feed

Self-contained build spec. Read fully before writing code. Build one phase at a time; after each phase, run it, show the output, and stop.

## 1. Goal

A zero-LLM, zero-cost daily scanner that finds **new entry-level / new-grad tech jobs in Canada** and writes the best matches into a **Notion "Job Feed" database** for human triage. It runs on GitHub Actions in a **public** repo (others may reuse it).

Owner profile (used only for filtering/scoring): new grad (MEng Applied Data Science, Dec 2026), eligible to work in Canada only, open to anywhere in Canada (incl. remote-in-Canada). Targets: software/backend/full-stack/cloud/DevOps, data engineering/analytics, ML/AI, security. Not interested in: senior/II+/lead roles, roles requiring 3+ years, internships/co-ops that require current student enrollment.

Out of scope: applying to jobs, resume generation, LinkedIn/Indeed/Glassdoor scraping (ToS), any LLM calls.

## 2. Architecture

```
GitHub Actions (daily cron, UTC)
  ├─ sources/        fetch raw postings
  │    ├─ feashliaa.py   daily dataset (phase 1)
  │    ├─ simplify.py    SimplifyJobs new-grad listings (phase 1)
  │    └─ ats/*.py       direct ATS fetchers (phase 2 for JD text, phase 4 for full independence)
  ├─ normalize.py    → one Job schema
  ├─ filters.py      title / location / company / JD rules (config-driven)
  ├─ score.py        skills-overlap Fit % + cluster
  ├─ state/seen.json dedupe store (committed back to repo each run)
  ├─ notion_feed.py  write top N to Notion Job Feed; archive stale rows
  └─ reports/latest.md  full candidate list (also the public output)
```

Stack: Python 3.11+ (3.12 in CI; nothing depends on 3.12), `requests`, `pyyaml`, stdlib only otherwise. `pytest` for tests. No database server.

## 3. Data sources (verified 2026-10-04)

### 3a. Feashliaa job-board-data (phase 1, primary)
- Repo: `https://github.com/Feashliaa/job-board-data` (shallow clone, ~141 MB). Updated daily by `Feashliaa/job-board-aggregator` (MIT code; company lists CC BY-NC 4.0 → credit them in README; non-commercial use only).
- Files: `data/metadata.json` (`last_updated`, `total_jobs`), `data/chunks/jobs_chunk_*.json.gz` (58 chunks, each a JSON list of ~25k jobs).
- Job record example:
  `{"ats":"Ashby","title":"AI Platform Engineer","location":"China","skill_level":"mid","company":"0g","is_recruiter":false,"scraped_at":"2026-10-04T13:32:20Z","url":"https://jobs.ashbyhq.com/0g/<id>","salary":{...},"first_seen":"2026-09-11T12:24:28Z"}`
- `skill_level` is title-only (intern/entry/mid/senior); "mid" is the default for untagged titles, so do NOT rely on it to find entry-level roles.
- Their scrape starts 07:33 UTC and can take hours; `last_updated` on 2026-10-04 was 14:13 UTC. Schedule our run at **16:00 UTC** and log `last_updated`; if it is >36 h old, log a warning and continue with other sources.
- Measured 2026-10-04 with the filters in §4 (title + location + exclusions): **~249 new Canadian non-senior tech postings in the last 7 days from 169 companies**; ~3,047 companies currently show at least one Canadian job (BambooHR 1009, Workday 808, Greenhouse 670, Ashby 237, Lever 222, Paylocity 101).
- No JD text in this dataset → phase 2 fetches it.

### 3b. SimplifyJobs New-Grad-Positions (phase 1, secondary)
- Repo: `https://github.com/SimplifyJobs/New-Grad-Positions`, file `.github/scripts/listings.json` (~13 MB).
- Fields: `company_name, title, locations[], url, date_posted (unix), active, is_visible, category (Software | AI/ML/Data | Hardware | Quant | Product), sponsorship, degrees`.
- Keep `active && is_visible`, Canadian locations, category in {Software, AI/ML/Data}. The file also uses the spellings `Software Engineering` and `Data Science, AI & Machine Learning` for the same buckets; all four are accepted (`sources.simplify.categories` in `config.yaml`). ~11 new Canadian rows/week (all are new-grad-labelled, so give a scoring boost).

### 3c. Direct ATS endpoints (phase 2 = JD fetch; phase 4 = own scraping)
Reference implementations to read before coding (do not copy blindly; check shapes): `Feashliaa/job-board-aggregator/scripts/scraper.py` (MIT) and `career-ops-hq/career-ops/providers/{greenhouse,lever,ashby,workday,bamboohr}.mjs` (MIT).
- Greenhouse: `GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true` (HTML-escaped `content`).
- Lever: `GET https://api.lever.co/v0/postings/{slug}?mode=json` (`descriptionPlain`, `lists`).
- Ashby: `GET https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true` (`descriptionPlain`, `location`, `secondaryLocations`, `isRemote`).
- Workday (undocumented): list `POST https://{tenant}.{wdN}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs` with `{"appliedFacets":{},"limit":20,"offset":0,"searchText":""}` (limit max 20); detail via the `externalPath` returned per job. Feashliaa company id format: `"tenant|wdN|site"`.
- BambooHR: `https://{slug}.bamboohr.com/careers/list` and per-job detail.
- Politeness: sequential per host, ≤5 concurrent per ATS, 0.3 s delay, descriptive User-Agent with repo URL, retries with backoff, respect 429.

## 4. Filters (all in `config.yaml`, not hard-coded)

Location include (case-insensitive regex): Canada, provinces, major cities (Toronto, Vancouver, Montréal/Montreal, Ottawa, Waterloo, Kitchener, Calgary, Edmonton, Burnaby, Mississauga, Markham, Kanata, Gatineau, Winnipeg, Regina, Saskatoon, Oakville match on their own; **ambiguous names Victoria, Richmond, Surrey, Hamilton, London, Halifax only match with a province qualifier**, e.g. `Victoria, BC`, `London, ON`, because they also name places in the UK, Australia, NZ and the US), suffixes `, ON|BC|AB|QC|MB|SK|NS|NB`, "Remote (Canada)", "Remote in Canada", "Canada - Remote".
Location exclude (known false positives): `Vancouver, Washington`, `, WA`, `Ottawa, Illinois`, `Waterloo|Markham|Edmonton` + a US state, `United States`, `USA` unless the same string also lists a Canadian location. Implementation: multi-location strings are split on `;`/`|`, false-positive spans are removed, then the include test runs, so `Seattle, WA; Toronto, ON` passes and `Vancouver, WA` does not.

Title tiers (two-tier include, see `title.strong` / `title.weak` in `config.yaml`):
- **Strong** (pass outright): software, developer, programmer, data, machine learning / ML, AI, cloud, devops, SRE, platform, backend, frontend, full stack, security, cyber, database, BI, analytics, automation.
- **Weak** (generic words only: engineer, analyst, specialist, test, technical, scientist, integration, QA): kept but tagged `weak_title`. Phase 2 keeps a `weak_title` job only if its JD has **at least 4 distinct terms (matched + gaps)**, matches at least one skill and has Fit % >= 20; it is dropped when no JD could be fetched (§11 #13, #22). Strong-title jobs without a JD stay with the no-JD penalty.
- A title with any strong term is strong even if it also has a weak term.

Title exclude: senior, sr, staff, principal, lead, manager, director, head of, architect, chief, VP, vice president / vice-president, AVP, assistant vice, II, III, IV, intermediate, plus non-software engineering (mechanical, electrical engineer, civil, structural, chemical, process engineer, manufacturing, HVAC, field service, technician, sales) and non-tech roles seen in the data (security guard, clerk, facilities, coordinator, marketing, supervisor, superviseur, team leader, administrative, recruiter).
Student-only exclude (title): intern, internship, co-op, coop, student, PEY, "summer 20xx", "winter 20xx", "fall 20xx" — unless the phase-2 JD has **explicit eligibility wording** ("open to recent graduates", "recent graduates are welcome/eligible", "we welcome new grads", "graduated within ..."). Award or employer-branding text ("Best Employers for Recent Graduates", "our student and new graduate programs") does not count, and a JD that requires current enrollment is never rescued.
Company blocklist (aggregators/spam seen in data): jobgether, usasurveyjob, globalhr, tsmg (extendable).
Language: drop French-only titles by default (`analyste|ingénieur|développeur|conseiller|scientifique|spécialiste`), config toggle. A title that also contains an English role word (engineer, developer, analyst, ...) is treated as bilingual and kept.

JD rules (phase 2, on fetched text):
- Drop if minimum required experience ≥ 3 years (`(\d+)\s*\+?\s*(?:-|to)?\s*\d*\s*years?` near "experience"; take the smallest number in a range; ignore "years" in company boilerplate like "over 100 years").
- Drop if text requires current enrollment (`currently enrolled|returning to (school|studies)|must be a (current )?student|enrolled in a co-?op program|enrolled in .{0,60}(degree|program|university|college|diploma)|(2nd|second|3rd|third) year or (later|above)|currently pursuing|must be returning`), or states a **graduation date after the current year** ("graduation date of April 2027 or later", "2027 or later graduation date"). The `enrolled in ...` pattern is skipped for benefits/HR boilerplate ("enrolled in our benefits plan", pension, wellness, onboarding, CPA/PEP programs).
- Drop if `security clearance` required, or `US citizen`/`authorized to work in the United States` without Canadian location.
- Boost signals → `Signals` multi-select: `new grad`, `recent graduate`, `0-2 years`, `entry level`, `junior`. Two data-quality values share the field: `low signal` (JD found but < 4 tech terms, so Fit % is n/a) and `jd unavailable` (no JD text could be fetched).

Freshness: only jobs first seen in the last 3 days on a normal run (`--since` CLI flag to override). The first run, defined as one where `state/seen.json` does not exist yet, uses 14 days (`freshness.first_run_days`).

## 5. Scoring

`skills.yaml` (public, owner-maintained): skill → aliases, grouped by cluster `software | data | ml | security`. Seed it from the owner's skills (to be pasted by the owner): Python, JavaScript/TypeScript, C#, Rust, SQL, PowerShell, Bash, C/C++, React, Next.js, Node.js, .NET, Flask, REST APIs, MCP, PostgreSQL/Supabase, SQL Server, MongoDB, Azure (Data Factory, SQL MI, Entra ID), Databricks, Unity Catalog, Spark (if listed), Power BI, Power Apps/Automate, Docker, Kubernetes, Terraform, AWS, Cloudflare Workers, Git/GitHub, CI/CD, Playwright, PyTorch, TensorFlow, scikit-learn, pandas, NumPy, LLM/Anthropic API, Gemini API, CrowdStrike/LogScale, Linux. Owner will extend.

- Extract JD terms = all `skills.yaml` aliases found in the JD (word-boundary match; handle `C++`, `C#`, `.NET`, `Node.js`, `CI/CD`).
- Also extract a small list of common tech terms NOT in the owner's skills (`gaps.yaml`: Java, Go/Golang, Kotlin, Scala, MySQL, GCP, microservices, distributed systems, Kafka, Airflow, dbt, Snowflake, unit testing, …) so gaps are visible.
- `Fit % = matched / (matched + gaps)`, requirements section weighted ×2 if detectable ("Requirements|Qualifications|What you'll need|Must have"). **Fit % is only reported when at least 4 distinct terms (matched + gaps) are found** (`scoring.min_terms_for_fit`); otherwise Fit is `n/a` and the job gets the `low signal` signal. Matched/gap terms are still listed.
- `Cluster`: the **title decides first** (`cluster.title_keywords` in `config.yaml`; checked security → ml → data, first hit wins): security/cyber → security; ML/AI/machine learning/scientist → ml; data/analytics/BI → data. With no title keyword, the cluster with the most matched JD terms; with neither, `software`. Cluster is therefore never empty.
- `Score` (for ranking only) = Fit % (0 when n/a) + matched skills × 2 (counting at most 10) + signal boosts (new grad +15, Simplify source +10, BC/remote +5) − penalties (no JD text −10).

## 6. Notion

Two databases exist/will exist in the owner's workspace. **The scanner only ever writes to the Job Feed database.** Never touch the main application database.

### 6a. Job Feed database (create once; owner shares it with the integration)
The Feed has exactly these properties; no others exist. Matched, Gaps, Cluster and Score stay in `reports/latest.md`.

| Property | Type | Value written by the scanner |
|---|---|---|
| Name | title | The job role as listed, e.g. `Software Engineer I` |
| Company | text | `<Company> · <date>`, e.g. `RBC · oct6`, `Clio · sept20`. The date is the job's first-seen date (UTC): lowercase month + day, no space, no leading zero; months `jan feb mar apr may jun jul aug sept oct nov dec` |
| Link | url | Application URL |
| Source | select | Workday, Greenhouse, Lever, Ashby, BambooHR, Paylocity, Simplify, Watchlist. A source outside these options is left empty (never creates a new option) |
| Fit % | number, percent format | A fraction (`0.7` = 70%); empty when Fit is n/a |
| Signals | multi-select | new grad, recent graduate, 0-2 years, entry level, junior, low signal, jd unavailable (options already exist) |
| JD | text | Full plain-text JD |
| Interested | checkbox | The owner's. Never written, never read for anything but cleanup |
| Apply | checkbox | The owner's. Never written, never read for anything but cleanup |

**Company name.** Resolved as: `company_names.yaml` override (owner-curated, e.g. `rbc: RBC`, `generalmotors: General Motors`) > readable name from Simplify (`company_name`) or the Greenhouse response (`company_name`) > the slug prettified (`periodic-labs` -> `Periodic Labs`).

**JD.** The JD goes in the `JD` text property, NOT the page body, because the owner's Notion automation copies properties (not body content) into the main database. It is written as a rich_text array split into items of at most 2000 characters (UTF-16 units, never splitting a code point); the array is kept to 95 items, so a JD beyond ~190k characters is truncated with a `[truncated]` marker. HTML is normalised to plain text with paragraph breaks preserved (`\n\n`). The page body stays empty.

**Schema check.** All property names live in `config.yaml` (`notion.properties`). Before the slow scan and before anything is written, the scanner retrieves the Feed data source and fails (exit 2) with a message listing every missing property, wrong type, non-percent `Fit %` format and missing select/multi-select option.

### 6b. Writing rules
- Write at most `notion.daily_cap` (default 40) highest-Score new jobs per run (`--max-rows` overrides); everything else stays in `reports/latest.md`.
- Skip a job whose Job Key is in `state/seen.json`, or whose Link (canonical URL: tracking params ignored, job-id params kept) matches any Feed row. The owner's Notion automation copies a ticked row to his main database and then deletes it from the Feed, so `seen.json` is what stops it coming back. A key is added to `seen.json` the moment its row is created, and the file is saved after every row.
- Cleanup: trash (`in_trash: true`) Feed rows whose Notion created time is older than `notion.feed_ttl_days` (default 14) and where **both** `Interested` and `Apply` are unticked. A row with either box ticked, or with a checkbox missing from the response, is never touched.
- API: `Notion-Version: 2026-03-11` (`notion.api_version`), pages created under `parent: {type: data_source_id}`, data source queried with `filter_properties`. Requests are paced to at most 3 per second. 429 and 529 are retried after `Retry-After` (not `public_api_request_blocked`); 500/502/503/504 and network errors are retried only for reads, never for page creation (it may have succeeded; the Link check prevents a duplicate next run). Three consecutive failed writes abort the write step.
- `--dry-run`: no Notion writes or trashing and no `seen.json` update; prints the first 5 payloads (JD shortened for display). With credentials it still reads the Feed (schema check, Link dedupe, what would be trashed); `--skip-notion` avoids contacting Notion at all.
- After a real run the scanner reads one written JD back through the paginated property-item endpoint and compares it with what it sent (prefers one over 4,000 characters); a mismatch exits 1.
- `NOTION_TOKEN` and `NOTION_FEED_DATA_SOURCE_ID` come from the environment (GitHub Actions secrets). The token is never printed or logged.

### 6d. Gaps report (no Notion)
Each run records, in `state/gaps.json`, the gap terms (`gaps.yaml`) of every job whose JD was fetched, including jobs the filters later dropped, once per job key; entries older than 90 days are pruned. On Mondays (UTC) the run overwrites `reports/gaps.md` with two tables: the top 25 gap terms of the last 7 days and of the last 30 days (term, job count, three example titles). `--gaps-report always|never|auto` overrides the Monday rule.

### 6c. Moving to the main database (not this repo's job, documented for context)
Owner's main DB ("data_jobs_fall26"): `Company` (title), `Link` (url), `Status` (select: Queued, Ready, Applied, Canceled, Rejected), `Cover Letter` (checkbox), `Batch Tag` (text), `Added` (created time), plus a `JD` text property added by the owner. A Notion automation built by the owner copies a Feed row into the main DB as `Queued` when he chooses to apply. This repo never reads or writes the main DB, and the integration is shared with the Feed DB only.

## 7. Dedupe

Job Key = `sha1(normalized company + "|" + normalized title + "|" + normalized first location)`; also store the URL. Normalize: lowercase, strip punctuation/whitespace, drop req IDs in parentheses; location is reduced to its city (text before the first comma). Within a run, duplicates are merged in two passes (`scanner/dedupe.py`):
1. **Canonical URL**: lowercase host, drop fragment, trailing slash and query string, **except job-identifying params** (`gh_jid`, `jid`, `job_id`, `reqid`, ...). Dropping every query param merged distinct jobs on real data (e.g. pinterestcareers.com serves every Greenhouse job from `/jobs/?gh_jid=<id>`).
2. **Fuzzy**: same city AND company names match (one normalized name is a prefix of the other with the shorter ≥ 3 chars, or `difflib` ratio ≥ 0.85) AND title-token Jaccard ≥ 0.8. The same-city condition keeps the original §7 location component, so one role posted in two cities stays two rows.

A job seen from both Feashliaa and Simplify is one job (prefer Simplify for the new-grad signal, Feashliaa/ATS for URL and source). Known limitation: aliased titles ("Model Context Protocol/AI Developer" vs "MCP/AI Developer") fall below the Jaccard threshold and are not merged. `state/seen.json` maps key → first_seen date; prune keys older than 90 days. The workflow commits `state/` and `reports/` back to the repo (this also keeps the scheduled workflow from being auto-disabled after 60 days of inactivity in a public repo).

## 8. GitHub Actions

`.github/workflows/scan.yml`: `schedule: cron "0 16 * * *"` (UTC) + `workflow_dispatch` with inputs `dry_run` (default true), `max_rows`, `since_days`. Scheduled runs always write. Steps: checkout → setup-python 3.12 → pip install → run `python -m scanner run` → commit `state/` and `reports/` with `[skip ci]` (also when the run failed part-way, so rows already written stay in `seen.json`). Runs are serialised with a `concurrency` group. `permissions: contents: write`. Timeout 60 min. Secrets: `NOTION_TOKEN`, `NOTION_FEED_DATA_SOURCE_ID`. Secrets are not available to forks' PRs, so the public repo is safe; never print the token.

## 9. Phases and acceptance checks

**Phase 1: dry-run candidate list (no Notion, no JD fetch).**
Sources 3a + 3b → normalize → title/location/company filters → dedupe → `reports/latest.md` (table: First Seen, Company, Role, Location, Source, URL) + counts per stage printed.
Accept: `python -m scanner run --since 7 --dry-run` completes in <10 min locally, prints stage counts, and the 7-day candidate count is in the same ballpark as §3a (~200–300). Spot-check 20 rows: all Canadian, none senior.

**Phase 2: JD fetch + JD rules + scoring.**
Fetch JD for each phase-1 candidate via §3c by ATS (skip unsupported, keep with penalty). Apply §4 JD rules and §5 scoring. Report gains Fit %, Cluster, Signals, Gaps, drop reasons count.
Accept: unit tests for years-of-experience parsing (≥10 cases incl. "2+ years", "3-5 years", "over 100 years", "0-2 years"), enrollment detection, alias matching (C++, C#, .NET, CI/CD). Report shows drop-reason counts.

**Phase 3: Notion + Actions.**
`notion_feed.py` per §6, workflow per §8, README with setup steps (create integration, share Feed DB, add secrets) and credit to Feashliaa + SimplifyJobs.
Accept: one manual `workflow_dispatch` with `dry_run=false` creates ≤`daily_cap` Feed rows whose `JD` property holds the full text (verify one JD longer than 4,000 chars round-trips intact by reading it back via the API and comparing lengths); a second run creates 0 duplicates; a missing property produces the startup error, not a partial write.

**Phase 4: independence (own scraping).**
Build `companies/canada.json` = companies that had ≥1 Canadian job in the last 30 days of Feashliaa data, plus `companies/watchlist.yaml` (owner-added: e.g., employers outside the dataset). Scrape these boards directly daily via §3c (Workday with `searchText` per role keyword to stay small). Feashliaa data then only feeds weekly company discovery; if it is stale/unavailable, scanning continues.
Accept: with Feashliaa disabled by config, a run still produces candidates; runtime <45 min.

**Phase 5 (optional): public page.** GitHub Pages rendering `reports/latest.md` (Canadian entry-level tech jobs, last 14 days) for other job seekers. No personal data on it.

## 10. Rules

- No LLM calls, no paid services, no scraping of LinkedIn/Indeed/Glassdoor.
- Everything tunable lives in `config.yaml`, `skills.yaml`, `gaps.yaml`.
- No personal data in the repo beyond the skills list (no resume, email, phone).
- Keep functions small and tested; network calls behind thin clients so tests use fixtures.
- After each phase: run it, paste the stage counts and 10 sample rows, then stop for review.

## 11. Decisions log (deviations from the original draft)

| # | Decision | Why |
|---|---|---|
| 1 | Python 3.11+ instead of 3.12 | Dev environment ships 3.11; nothing needs 3.12 |
| 2 | Simplify accepts four category spellings | The file uses `Software Engineering` and `Data Science, AI & Machine Learning` alongside the two in §3b |
| 3 | Ambiguous city names require a province qualifier; Waterloo/Markham/Edmonton US-state exclusions | `Halifax, UK`, `Victoria, Australia`, `Waterloo, Wisconsin` etc. leaked through |
| 4 | Two-tier title match (strong/weak) + extra hard excludes | Generic words (`engineer`, `test`) let Facilities/Controls/Marketing roles through; weak titles are decided by JD fit in phase 2 |
| 5 | Two-pass dedupe; URL pass keeps job-id query params; fuzzy pass stays within a city | See §7. Plain query stripping merged distinct Pinterest/Stripe jobs on real data |
| 6 | Student-titled jobs are held aside in phase 1 instead of discarded | §4 lets phase 2 rescue them when the JD says recent graduates are eligible |
| 7 | `reports/latest.md` is committed from phase 1 on | It is the public output (§2) |
| 8 | Phase 2 JD fetch uses per-job endpoints: Greenhouse `/v1/boards/{slug}/jobs/{id}`, Lever `/v0/postings/{slug}/{id}`, Workday `/wday/cxs/{tenant}/{site}{externalPath}`, BambooHR `/careers/{id}/detail`; Ashby has no per-job endpoint, so its company board is fetched once and cached. Paylocity, iCIMS, Workable and unknown hosts are unsupported: kept with the no-JD penalty | One request per candidate instead of downloading whole boards (§3c lists the board endpoints; the per-job forms avoid pulling thousands of unrelated postings). Simplify-only rows are routed by the ATS in their URL |
| 9 | Minimum experience: a "N years" mention counts only if "experience" is within 100 characters; a range uses its lower bound; across several mentions the **smallest** lower bound wins; numbers >= 20 and "less than/up to N years" are ignored; drop at >= 3 (`jd.max_experience_years`) | Silently dropping a good job costs more than showing one extra. Known gap: "5+ years in software" with no nearby "experience" is not caught |
| 10 | Clearance: drop only when "security clearance" is not negated and its sentence has a requirement word (required, must, obtain, hold, ...). "No clearance required" / "is an asset" are kept | Avoids dropping jobs that merely mention clearance |
| 11 | The US-work-authorization drop only fires when the job has no Canadian location | Literal reading of §4. Every phase-1 candidate has one, so it is currently a safety net |
| 12 | Student-titled jobs held by phase 1 get their JD fetched; they are kept (and flagged `recent graduate`) only if the JD has explicit eligibility wording (see §4 and #23) and no enrollment requirement | Implements the §4 "unless the JD says recent graduates are eligible" clause |
| 13 | `weak_title` jobs with no JD (unsupported ATS, fetch failure, or 404) are **dropped** (`weak_title_no_jd`); strong-title jobs with no JD are kept with the −10 penalty. Supersedes the earlier "keep weak titles with the penalty" choice | A generic title alone ("Specialist", "Engineer") is too weak to justify a row; nothing else can vouch for it. |
| 14 | `skills.yaml` includes Spark ("Spark (if listed)" in §5). Words that are also plain English (React, REST, Spark, Flask, Rust, JS) match case-sensitively | Avoids "react quickly", "the rest of the team" |
| 15 | JD fetches are cached on disk by URL for 7 days (`.cache/jd`, gitignored) | Politeness on re-runs |
| 16 | Title excludes add `vice president`, `vice-president`, `AVP`, `assistant vice` | "Assistant Vice-President, Data & AI" (BDC) passed the title filter in the first live run; `VP` alone does not match it |
| 17 | Enrollment rule also matches `enrolled in .{0,60}(degree\|program\|university\|college\|diploma)`, `(2nd\|second\|3rd\|third) year or (later\|above)`, `currently pursuing`, `must be returning`; the `enrolled in` pattern ignores benefits/HR boilerplate (our/your/the company, benefits, plan, pension, insurance, wellness, stock, onboarding, training, CPA/CFA/PEP, and "will be / automatically / get enrolled") | Sun Life's "Enrolled in 2nd year or later of a university ... degree program" co-op was rescued as a student role. Checked against the 427 cached JDs: 18 newly matched, 16 genuine student requirements, 2 were CPA-designation boilerplate (now excluded) |
| 18 | `gaps.yaml` expanded: everything you listed (Java ... SAP; all but Salesforce and SAP were already there) plus ~30 more technologies (ServiceNow, MATLAB, Perl, Flutter, Laravel, FastAPI, Maven/Gradle, Hive, Trino, Flink, Informatica, SSIS, GitLab, Helm, Nginx, ...). Concepts the owner arguably has (ETL, data modeling) are deliberately not gaps. A test enforces that no gap duplicates a skill | More gap terms make Fit % mean "overlap with what the JD actually asks for" instead of "100% of the 2 terms we recognised" |
| 19 | Fit % needs >= 4 distinct terms (matched + gaps), else `n/a` + `low signal` signal; a weak-title job is still dropped for zero matches, but sparse (n/a) Fit is not treated as < 20. Score adds +2 per matched skill, capped at 10 skills | "100%" from two recognised terms is noise (many sample rows read 100% with 1-2 matches). Matched count separates a 2-skill match from an 8-skill one |
| 20 | Cluster comes from the title first (security > ml > data by precedence, so "Data Scientist" is ml); JD terms only when the title has no keyword; otherwise `software`. A plain "Software Engineer" title has no keyword, so its cluster follows the JD terms (e.g. a PostgreSQL/SQL-heavy JD gives `data`) | Cluster by JD term count alone labelled a Tailscale networking SWE role `data` from two SQL mentions. Reading of "everything else → software": it is the final fallback, not an override of JD terms. If you want every software-ish title (software/developer/engineer/devops/cloud/platform/backend/frontend) to be `software` outright, that is a one-line addition to `cluster.title_keywords` |
| 21 | A strong-title job whose JD cannot be fetched (HTTP 403/5xx, 404, unsupported ATS) is kept with the −10 penalty and the `jd unavailable` signal; the run summary and report list the hosts that answered HTTP 403 (Workday tenants return `permission denied` for some postings regardless of User-Agent). The Notion `Signals` select therefore needs the options `low signal` and `jd unavailable` (phase 3) | Makes missing data visible in the feed instead of an unexplained blank Fit % |
| 22 | A `weak_title` job is dropped unless its JD has >= 4 distinct terms (matched + gaps); reason `weak_title_few_terms`. After that, the earlier rule applies (>= 1 matched skill and Fit % >= 20, else `weak_title_low_fit`). Supersedes the part of #19 that kept weak titles with sparse JDs | 24 weak-title jobs ("Pricing Analyst", "Retail Operations Specialist", "Technical Writer") survived on one or two matched terms |
| 23 | Student-title rescue needs explicit eligibility wording (`open to ... recent graduates`, `recent graduates are welcome/eligible`, `we welcome ... new grads`, `graduated within`). Branding or award text no longer rescues. Supersedes the `recent graduate\|new grad\|graduated within` match in #12 | Sun Life's "Best Employers for Recent Graduates" line rescued a student role |
| 24 | A graduation date after the current year ("graduation date of April 2027 or later", "2027 or later graduation date") is an enrollment requirement; years up to the current one are not. The year is read from the clock at run time | The Sun Life student role asked for "a August 2027 or later graduation date", which no earlier pattern matched |
| 25 | Cluster is **not written to Notion**. The `Cluster` column stays in `reports/latest.md` only; no further cluster work. Supersedes the `Cluster` property in §6a | Owner's decision |
| 26 | Notion API version `2026-03-11` (current per developers.notion.com on 2026-10-07): pages are created with `parent.type = data_source_id`, the Feed is read with `POST /v1/data_sources/{id}/query` (+ `filter_properties`), rows are trashed with `in_trash: true` (`archived` was removed in this version), long text is read back with the paginated property-item endpoint | §6b said "use the current version as documented" |
| 27 | The Feed schema is the nine properties in §6a (Name, Company, Link, Source, Fit %, Signals, JD, Interested, Apply). Role, Location, First Seen, Job Key, Cluster, Matched, Gaps and Score are not Notion properties; `Name` is the plain role and the date moved into `Company` | Owner's decision. Supersedes the §6a draft and #25 (cluster) |
| 28 | `Company` = `<name> · <first-seen date, UTC>`. Name precedence: `company_names.yaml` > Simplify `company_name` or Greenhouse `company_name` > prettified slug. Workday's `hiringOrganization` is ignored (it is often a legal entity such as "Autodesk Canada Co.") and Lever, Ashby and BambooHR give no name. `company_names.yaml` is seeded from the 14-day report of 2026-10-07; a few entries are guesses to check (`myview` = Loblaw, `cw`, `isc`, `fccfac`) | Owner's format. The override map wins so legal names can be shortened |
| 29 | The schema check also verifies that `Fit %` has number format `percent` and that every Source and Signals option the scanner can write exists. A Source outside the Feed's options (e.g. iCIMS) is left empty instead of silently creating a new option | "Schema is exactly ..." |
| 30 | `seen.json` holds the keys of rows actually written (key -> date written) and is saved after every row; jobs skipped by the cap or by JD rules are re-evaluated next run. Because the cap truncates, a rerun with the same cap writes the next-best jobs, never the same ones | Keeps a row the owner moved to the main DB from returning, and survives a killed run |
| 31 | Cleanup never touches a row where either checkbox is ticked or where a checkbox value is missing from the response. It runs before writing, and in dry runs it only reports | "Never touch a row with either box ticked" |
| 32 | Page creation is never retried after a 5xx or network error; 3 consecutive failed writes abort the write step; the exit code is 1 if any row failed or the read-back differs | Notion's guidance: a write that returns 5xx may have succeeded. The Link check makes the next run safe |
| 33 | Gaps are recorded for every job whose JD was fetched, student-titled held jobs included, once per job key (the first time it is seen, by run date). Window counts use that recording date. Dry runs still record gaps | "Counting each job once" |
| 34 | `workflow_dispatch` input `dry_run` defaults to **true** (scheduled runs always write); extra inputs `max_rows` and `since_days` | A manual click should not write by accident |
| 35 | A job whose Link is already a Feed row is also recorded in `seen.json` (not only rows the scanner wrote itself). Found in the live test: if the owner deletes a row whose key was never saved, the job would come back | Covers a killed run and rows created before `seen.json` existed |
| 36 | `--top N` (testing aid) restricts the Notion step to the N highest-scoring jobs, so a rerun with identical flags repeats the same set and "0 new rows" is a meaningful check. The commit step of `scan.yml` retries `pull --rebase` + `push` five times with backoff | The cap otherwise makes a rerun write the next-best jobs. GitHub answered one push with a transient HTTP 500 during the live test |
| 37 | Live acceptance (2026-10-07, GitHub Actions, real Feed): schema check passed; 5 rows written; the 7,275-character Quora JD read back with identical length and content; a rerun with the same flags wrote 0 rows (`seen: 5`); a rerun with `seen.json` deleted wrote 0 rows (`link_in_feed: 5`); the Feed ended with 5 rows, 5 distinct Links, none ticked | §9 phase 3 acceptance. Done on the PR branch with a temporary workflow that was deleted before merge |

