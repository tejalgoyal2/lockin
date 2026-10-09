# lockin

A zero-LLM, zero-cost daily scanner for **new-grad / entry-level tech jobs in Canada**. It reads two public
job datasets, filters them, fetches each job description from the company's applicant tracking system (ATS),
scores the fit against your skills, and writes the best new jobs to a **Notion "Job Feed"** database for you
to triage. It runs on GitHub Actions. Design: [SPEC.md](SPEC.md).

## How a run works

1. Read [Feashliaa/job-board-data](https://github.com/Feashliaa/job-board-data) and the
   [SimplifyJobs new-grad list](https://github.com/SimplifyJobs/New-Grad-Positions).
2. Filter by freshness, Canadian location, company blocklist, language, title and seniority (`config.yaml`).
3. Fetch each JD (Greenhouse, Lever, Ashby, Workday, BambooHR), apply the JD rules (3+ years, current
   enrollment, clearance) and score the fit against `skills.yaml` / `gaps.yaml`.
4. Write the top `notion.daily_cap` (40) new jobs to the Feed, skipping anything in `state/seen.json` or
   already in the Feed by Link; trash Feed rows older than 14 days that you never ticked.
5. Commit `state/` and `reports/` back to the repo. `reports/latest.md` is the full candidate list;
   `reports/gaps.md` (Mondays) lists the tech terms JDs ask for that you do not list yet.

## Setup

1. **Create the Feed database in Notion** with exactly these properties (names and types matter; the scanner
   refuses to start otherwise and tells you what is wrong):

   | Property | Type |
   |---|---|
   | Name | Title |
   | Company | Text |
   | Link | URL |
   | Source | Select, options: Workday, Greenhouse, Lever, Ashby, BambooHR, Paylocity, Simplify, Watchlist |
   | Fit % | Number, format **Percent** |
   | Signals | Multi-select, options: new grad, recent graduate, 0-2 years, entry level, junior, low signal, jd unavailable |
   | JD | Text |
   | Interested | Checkbox |
   | Apply | Checkbox |

2. **Create a Notion integration** (an internal connection) at <https://www.notion.so/profile/integrations>
   with read, update and insert content capabilities. Copy its secret.
3. **Share the Feed database with the integration** (database menu `...` > Connections). Share nothing else:
   the scanner never needs your main database.
4. **Get the data source ID** of the Feed (database menu `...` > Manage data sources > copy the data source ID). It is not the ID in the database's URL.
5. **Add two repository secrets** (Settings > Secrets and variables > Actions):
   `NOTION_TOKEN` (the integration secret) and `NOTION_FEED_DATA_SOURCE_ID`.
6. **Run it once by hand**: Actions > scan > Run workflow. Leave `dry_run` ticked first (it validates the Feed
   schema and shows what it would write), then run again with `dry_run` unticked and `max_rows` = 5.
   After that the schedule (`0 16 * * *` UTC) takes over. The workflow commits to the branch it runs on; if
   that branch is protected, allow GitHub Actions to push to it.

Your own Notion automation can copy a ticked row to your main database and delete it from the Feed:
`state/seen.json` is what stops that job from being written again.

## Run it locally

```bash
pip install -r requirements-dev.txt
python -m scanner run --dry-run --skip-notion --since 7     # scan, score, print Notion payloads; touches nothing
NOTION_TOKEN=... NOTION_FEED_DATA_SOURCE_ID=... python -m scanner run --dry-run   # also checks the Feed schema
NOTION_TOKEN=... NOTION_FEED_DATA_SOURCE_ID=... python -m scanner run --max-rows 5
pytest
```

Useful flags: `--since N`, `--max-rows N`, `--no-jd` (phase 1 only), `--no-notion`, `--gaps-report always`.

## What you edit

| File | Purpose |
|---|---|
| `config.yaml` | every filter, threshold, Notion property name and the daily cap |
| `skills.yaml` | your skills and their aliases (drives Fit %) |
| `gaps.yaml` | common tech terms you do not have yet (shown as gaps) |
| `company_names.yaml` | display names for the Feed's Company column (`rbc: RBC`) |

## Credits

- [Feashliaa/job-board-aggregator](https://github.com/Feashliaa/job-board-aggregator) and its daily dataset
  [Feashliaa/job-board-data](https://github.com/Feashliaa/job-board-data). The aggregator **code is MIT**; the
  **company lists and data are CC BY-NC 4.0**, so this project is for non-commercial use only.
- [SimplifyJobs/New-Grad-Positions](https://github.com/SimplifyJobs/New-Grad-Positions) for the curated new-grad
  listings.
- ATS endpoint shapes were checked against the MIT-licensed providers in
  [career-ops-hq/career-ops](https://github.com/career-ops-hq/career-ops).
