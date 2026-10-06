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
- **Weak** (generic words only: engineer, analyst, specialist, test, technical, scientist, integration, QA): kept but tagged `weak_title`. Phase 2 drops a `weak_title` job if its JD matches zero skills or Fit % < 20, or if no JD could be fetched (see §11 #13). Strong-title jobs without a JD stay with the no-JD penalty.
- A title with any strong term is strong even if it also has a weak term.

Title exclude: senior, sr, staff, principal, lead, manager, director, head of, architect, chief, VP, vice president / vice-president, AVP, assistant vice, II, III, IV, intermediate, plus non-software engineering (mechanical, electrical engineer, civil, structural, chemical, process engineer, manufacturing, HVAC, field service, technician, sales) and non-tech roles seen in the data (security guard, clerk, facilities, coordinator, marketing, supervisor, superviseur, team leader, administrative, recruiter).
Student-only exclude (title): intern, internship, co-op, coop, student, PEY, "summer 20xx", "winter 20xx", "fall 20xx" — unless phase-2 JD text says recent graduates are eligible (`recent graduate|new grad|graduated within`).
Company blocklist (aggregators/spam seen in data): jobgether, usasurveyjob, globalhr, tsmg (extendable).
Language: drop French-only titles by default (`analyste|ingénieur|développeur|conseiller|scientifique|spécialiste`), config toggle. A title that also contains an English role word (engineer, developer, analyst, ...) is treated as bilingual and kept.

JD rules (phase 2, on fetched text):
- Drop if minimum required experience ≥ 3 years (`(\d+)\s*\+?\s*(?:-|to)?\s*\d*\s*years?` near "experience"; take the smallest number in a range; ignore "years" in company boilerplate like "over 100 years").
- Drop if text requires current enrollment (`currently enrolled|returning to (school|studies)|must be a (current )?student|enrolled in a co-?op program|enrolled in .{0,60}(degree|program|university|college|diploma)|(2nd|second|3rd|third) year or (later|above)|currently pursuing|must be returning`). The `enrolled in ...` pattern is skipped for benefits/HR boilerplate ("enrolled in our benefits plan", pension, wellness, onboarding, CPA/PEP programs).
- Drop if `security clearance` required, or `US citizen`/`authorized to work in the United States` without Canadian location.
- Boost signals → `Signals` multi-select: `new grad`, `recent graduate`, `0-2 years`, `entry level`, `junior`.

Freshness: only jobs first seen in the last 3 days on a normal run (`--since` CLI flag to override; first run uses 14 days).

## 5. Scoring

`skills.yaml` (public, owner-maintained): skill → aliases, grouped by cluster `software | data | ml | security`. Seed it from the owner's skills (to be pasted by the owner): Python, JavaScript/TypeScript, C#, Rust, SQL, PowerShell, Bash, C/C++, React, Next.js, Node.js, .NET, Flask, REST APIs, MCP, PostgreSQL/Supabase, SQL Server, MongoDB, Azure (Data Factory, SQL MI, Entra ID), Databricks, Unity Catalog, Spark (if listed), Power BI, Power Apps/Automate, Docker, Kubernetes, Terraform, AWS, Cloudflare Workers, Git/GitHub, CI/CD, Playwright, PyTorch, TensorFlow, scikit-learn, pandas, NumPy, LLM/Anthropic API, Gemini API, CrowdStrike/LogScale, Linux. Owner will extend.

- Extract JD terms = all `skills.yaml` aliases found in the JD (word-boundary match; handle `C++`, `C#`, `.NET`, `Node.js`, `CI/CD`).
- Also extract a small list of common tech terms NOT in the owner's skills (`gaps.yaml`: Java, Go/Golang, Kotlin, Scala, MySQL, GCP, microservices, distributed systems, Kafka, Airflow, dbt, Snowflake, unit testing, …) so gaps are visible.
- `Fit % = matched / (matched + gaps)`, requirements section weighted ×2 if detectable ("Requirements|Qualifications|What you'll need|Must have").
- `Cluster` = cluster with most matched terms.
- `Score` (for ranking only) = Fit % + signal boosts (new grad +15, Simplify source +10, BC/remote +5) − penalties (no JD text −10).

## 6. Notion

Two databases exist/will exist in the owner's workspace. **The scanner only ever writes to the Job Feed database.** Never touch the main application database.

### 6a. Job Feed database (create once; owner shares it with the integration)
| Property | Type | Notes |
|---|---|---|
| Name | title | `Company — Role` |
| Company | text | |
| Role | text | |
| Link | url | application URL |
| Location | text | |
| Source | select | Workday, Greenhouse, Lever, Ashby, BambooHR, Paylocity, Simplify, Watchlist |
| First Seen | date | |
| Fit % | number (percent) | |
| Cluster | select | software, data, ml, security |
| Signals | multi-select | new grad, recent graduate, 0-2 years, entry level, junior |
| Matched | text | top matched skills |
| Gaps | text | JD terms not in skills.yaml |
| Job Key | text | dedupe key (see §7) |
| JD | text | **full job description text** (see below) |
| Apply | checkbox | owner's action; read-only for the scanner |

The JD goes in the `JD` text property, NOT the page body, because the owner's Notion automation copies properties (not body content) into the main database. Write it as a rich_text array split into ≤2000-character items (Notion API limit per item; keep the array ≤100 items, so truncate beyond ~190k chars with a `[truncated]` marker). Normalize HTML to plain text with paragraph breaks preserved (`\n\n`). Page body stays empty.

All Notion property names live in `config.yaml` (`notion.properties.<field>: "<Notion name>"`), because the owner creates the databases by hand. On startup, fetch the Feed schema and fail with a clear message listing any missing/mistyped property before writing anything.

### 6b. Writing rules
- Write at most `daily_cap` (default 40) highest-Score new jobs per run; everything else goes only to `reports/latest.md`.
- Never write a job whose Job Key already exists in `state/seen.json` or in the Feed.
- Archive (move to trash via API) Feed rows older than `feed_ttl_days` (default 14) where `Apply` is unchecked.
- Notion API: use the current API version and data-source parent as documented at developers.notion.com; read `NOTION_TOKEN` and `NOTION_FEED_DATA_SOURCE_ID` from env (GitHub Actions secrets). Rate limit ≤3 req/s; on 429 sleep `Retry-After`.
- `--dry-run` flag: no Notion writes, print what would be written.

### 6c. Moving to the main database (not this repo's job, documented for context)
Owner's main DB ("data_jobs_fall26"): `Company` (title), `Link` (url), `Status` (select: Queued, Ready, Applied, Canceled, Rejected), `Cover Letter` (checkbox), `Batch Tag` (text), `Added` (created time), plus a `JD` text property added by the owner. A Notion automation built by the owner copies a Feed row into the main DB as `Queued` when he chooses to apply. This repo never reads or writes the main DB, and the integration is shared with the Feed DB only.

## 7. Dedupe

Job Key = `sha1(normalized company + "|" + normalized title + "|" + normalized first location)`; also store the URL. Normalize: lowercase, strip punctuation/whitespace, drop req IDs in parentheses; location is reduced to its city (text before the first comma). Within a run, duplicates are merged in two passes (`scanner/dedupe.py`):
1. **Canonical URL**: lowercase host, drop fragment, trailing slash and query string, **except job-identifying params** (`gh_jid`, `jid`, `job_id`, `reqid`, ...). Dropping every query param merged distinct jobs on real data (e.g. pinterestcareers.com serves every Greenhouse job from `/jobs/?gh_jid=<id>`).
2. **Fuzzy**: same city AND company names match (one normalized name is a prefix of the other with the shorter ≥ 3 chars, or `difflib` ratio ≥ 0.85) AND title-token Jaccard ≥ 0.8. The same-city condition keeps the original §7 location component, so one role posted in two cities stays two rows.

A job seen from both Feashliaa and Simplify is one job (prefer Simplify for the new-grad signal, Feashliaa/ATS for URL and source). Known limitation: aliased titles ("Model Context Protocol/AI Developer" vs "MCP/AI Developer") fall below the Jaccard threshold and are not merged. `state/seen.json` maps key → first_seen date; prune keys older than 90 days. The workflow commits `state/` and `reports/` back to the repo (this also keeps the scheduled workflow from being auto-disabled after 60 days of inactivity in a public repo).

## 8. GitHub Actions

`.github/workflows/scan.yml`: `schedule: cron "0 16 * * *"` (UTC) + `workflow_dispatch` with inputs `since_days`, `dry_run`. Steps: checkout → setup-python 3.12 → pip install → run `python -m scanner run` → commit `state/` and `reports/` with `[skip ci]`. `permissions: contents: write`. Timeout 60 min. Secrets: `NOTION_TOKEN`, `NOTION_FEED_DATA_SOURCE_ID`. Secrets are not available to forks' PRs, so the public repo is safe; never print the token.

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
| 12 | Student-titled jobs held by phase 1 get their JD fetched; they are kept (and flagged `recent graduate`) only if the JD matches `recent graduate|new grad|graduated within` and no enrollment requirement | Implements the §4 "unless the JD says recent graduates are eligible" clause |
| 13 | `weak_title` jobs with no JD (unsupported ATS, fetch failure, or 404) are **dropped** (`weak_title_no_jd`); strong-title jobs with no JD are kept with the −10 penalty. Supersedes the earlier "keep weak titles with the penalty" choice | A generic title alone ("Specialist", "Engineer") is too weak to justify a row; nothing else can vouch for it. |
| 14 | `skills.yaml` includes Spark ("Spark (if listed)" in §5). Words that are also plain English (React, REST, Spark, Flask, Rust, JS) match case-sensitively | Avoids "react quickly", "the rest of the team" |
| 15 | JD fetches are cached on disk by URL for 7 days (`.cache/jd`, gitignored) | Politeness on re-runs |
| 16 | Title excludes add `vice president`, `vice-president`, `AVP`, `assistant vice` | "Assistant Vice-President, Data & AI" (BDC) passed the title filter in the first live run; `VP` alone does not match it |
| 17 | Enrollment rule also matches `enrolled in .{0,60}(degree\|program\|university\|college\|diploma)`, `(2nd\|second\|3rd\|third) year or (later\|above)`, `currently pursuing`, `must be returning`; the `enrolled in` pattern ignores benefits/HR boilerplate (our/your/the company, benefits, plan, pension, insurance, wellness, stock, onboarding, training, CPA/CFA/PEP, and "will be / automatically / get enrolled") | Sun Life's "Enrolled in 2nd year or later of a university ... degree program" co-op was rescued as a student role. Checked against the 427 cached JDs: 18 newly matched, 16 genuine student requirements, 2 were CPA-designation boilerplate (now excluded) |
| 18 | `gaps.yaml` expanded: everything you listed (Java ... SAP; all but Salesforce and SAP were already there) plus ~30 more technologies (ServiceNow, MATLAB, Perl, Flutter, Laravel, FastAPI, Maven/Gradle, Hive, Trino, Flink, Informatica, SSIS, GitLab, Helm, Nginx, ...). Concepts the owner arguably has (ETL, data modeling) are deliberately not gaps. A test enforces that no gap duplicates a skill | More gap terms make Fit % mean "overlap with what the JD actually asks for" instead of "100% of the 2 terms we recognised" |
