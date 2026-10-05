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

Stack: Python 3.12, `requests`, `pyyaml`, stdlib only otherwise. `pytest` for tests. No database server.

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
- Keep `active && is_visible`, Canadian locations, category in {Software, AI/ML/Data}. ~11 new Canadian rows/week (all are new-grad-labelled, so give a scoring boost).

### 3c. Direct ATS endpoints (phase 2 = JD fetch; phase 4 = own scraping)
Reference implementations to read before coding (do not copy blindly; check shapes): `Feashliaa/job-board-aggregator/scripts/scraper.py` (MIT) and `career-ops-hq/career-ops/providers/{greenhouse,lever,ashby,workday,bamboohr}.mjs` (MIT).
- Greenhouse: `GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true` (HTML-escaped `content`).
- Lever: `GET https://api.lever.co/v0/postings/{slug}?mode=json` (`descriptionPlain`, `lists`).
- Ashby: `GET https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true` (`descriptionPlain`, `location`, `secondaryLocations`, `isRemote`).
- Workday (undocumented): list `POST https://{tenant}.{wdN}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs` with `{"appliedFacets":{},"limit":20,"offset":0,"searchText":""}` (limit max 20); detail via the `externalPath` returned per job. Feashliaa company id format: `"tenant|wdN|site"`.
- BambooHR: `https://{slug}.bamboohr.com/careers/list` and per-job detail.
- Politeness: sequential per host, ≤5 concurrent per ATS, 0.3 s delay, descriptive User-Agent with repo URL, retries with backoff, respect 429.

## 4. Filters (all in `config.yaml`, not hard-coded)

Location include (case-insensitive regex): Canada, provinces, major cities (Toronto, Vancouver, Montréal/Montreal, Ottawa, Waterloo, Kitchener, Calgary, Edmonton, Victoria, Burnaby, Richmond BC, Surrey, Mississauga, Markham, Kanata, Gatineau, Halifax, Winnipeg, Regina, Saskatoon, Hamilton, London ON, Oakville), suffixes `, ON|BC|AB|QC|MB|SK|NS|NB`, "Remote (Canada)", "Remote in Canada", "Canada - Remote".
Location exclude (known false positives): `Vancouver, Washington`, `, WA`, `Ottawa, Illinois`, `United States`, `USA` unless the same string also lists a Canadian location.

Title include: software, developer, engineer, programmer, data, analytics, machine learning, ML, AI, scientist, devops, SRE, platform, cloud, backend, full stack, frontend, automation, QA, test, security, cyber, database, BI, integration.
Title exclude: senior, sr, staff, principal, lead, manager, director, head of, architect, chief, VP, II, III, IV, intermediate, plus non-software engineering (mechanical, electrical engineer, civil, structural, chemical, process, manufacturing, HVAC, field service, technician, sales).
Student-only exclude (title): intern, internship, co-op, coop, student, PEY, "summer 20xx", "winter 20xx", "fall 20xx" — unless phase-2 JD text says recent graduates are eligible (`recent graduate|new grad|graduated within`).
Company blocklist (aggregators/spam seen in data): jobgether, usasurveyjob, globalhr, tsmg (extendable).
Language: drop French-only titles by default (`analyste|ingénieur|développeur|conseiller|scientifique|spécialiste`), config toggle.

JD rules (phase 2, on fetched text):
- Drop if minimum required experience ≥ 3 years (`(\d+)\s*\+?\s*(?:-|to)?\s*\d*\s*years?` near "experience"; take the smallest number in a range; ignore "years" in company boilerplate like "over 100 years").
- Drop if text requires current enrollment (`currently enrolled|returning to (school|studies)|must be a (current )?student|enrolled in a co-?op program`).
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
| Apply | checkbox | **owner's action** |
| Moved | checkbox | set when copied to main DB |

Page body = full JD text (paragraph blocks, ≤2000 chars per rich-text item, ≤100 blocks per request; split as needed), starting with a line `Job Description:` then the text, then `Source URL: …`.

### 6b. Writing rules
- Write at most `daily_cap` (default 40) highest-Score new jobs per run; everything else goes only to `reports/latest.md`.
- Never write a job whose Job Key already exists in `state/seen.json` or in the Feed.
- Archive (move to trash via API) Feed rows older than `feed_ttl_days` (default 14) where `Apply` is unchecked.
- Notion API: use the current API version and data-source parent as documented at developers.notion.com; read `NOTION_TOKEN` and `NOTION_FEED_DATA_SOURCE_ID` from env (GitHub Actions secrets). Rate limit ≤3 req/s; on 429 sleep `Retry-After`.
- `--dry-run` flag: no Notion writes, print what would be written.

### 6c. Moving to the main database (not this repo's job, documented for context)
Owner's main DB ("data_jobs_fall26"): properties `Company` (title), `Link` (url), `Status` (select: Queued, Ready, Applied, Canceled, Rejected), `Cover Letter` (checkbox), `Batch Tag` (text), `Added` (created time); JD lives in the page body. Ticked Feed rows are copied there as `Status = Queued` either by a Notion button/automation or by the owner's Claude batch run. The scanner does not do this.

## 7. Dedupe

Job Key = `sha1(normalized company + "|" + normalized title + "|" + normalized first location)`; also store the URL. Normalize: lowercase, strip punctuation/whitespace, drop req IDs in parentheses. A job seen from both Feashliaa and Simplify is one job (prefer Simplify for the new-grad signal, Feashliaa/ATS for URL). `state/seen.json` maps key → first_seen date; prune keys older than 90 days. The workflow commits `state/` and `reports/` back to the repo (this also keeps the scheduled workflow from being auto-disabled after 60 days of inactivity in a public repo).

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
Accept: one manual `workflow_dispatch` with `dry_run=false` creates ≤`daily_cap` Feed rows with JD bodies; a second run creates 0 duplicates.

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
