# lockin

A zero-LLM, zero-cost scanner (twice a day) for **new-grad / entry-level tech jobs in Canada**. It reads two public
job datasets and a watchlist of employer job boards, filters them, fetches each job description from the company's applicant tracking system (ATS),
scores the fit against your skills, and writes the best new jobs to a **Notion "Job Feed"** database for you
to triage. It runs on GitHub Actions. Design: [SPEC.md](SPEC.md).

## How a run works

1. Read [Feashliaa/job-board-data](https://github.com/Feashliaa/job-board-data), the
   [SimplifyJobs new-grad list](https://github.com/SimplifyJobs/New-Grad-Positions), the employer boards in
   `watchlist.yaml` (read live, so they are as fresh as the employer's own site) and four job sites (see
   "Job sites" below).
2. Filter by freshness, Canadian location, company blocklist, language, title and seniority (`config.yaml`).
3. Fetch each JD (Greenhouse, Lever, Ashby, Workday, BambooHR), apply the JD rules (3+ years, current
   enrollment, clearance) and score the fit against `skills.yaml` / `gaps.yaml`.
4. Write the top `notion.daily_cap` (40) new jobs to the Feed, skipping anything in `state/seen.json` or
   already in the Feed by Link. At the start of the run, Feed rows whose `done-del` box is ticked (your Notion
   automation ticks it after copying the row to your main database) are trashed; no other row is ever trashed.
5. Commit `state/` and `reports/` back to the repo. `reports/latest.md` is the full candidate list;
   `reports/gaps.md` (Mondays) lists the tech terms JDs ask for that you do not list yet; `reports/feedback.md`
   shows what you pick by Fit band, source and tag (see below).

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
   | Signals | Multi-select, options: new grad, recent graduate, 0-2 years, entry level, junior, low signal, jd unavailable (`2+ yrs` is created on first use) |
   | Location | Text (optional: filled when the column exists) |
   | JD | Text |
   | Interested | Checkbox |
   | Apply | Checkbox |
   | done-del | Checkbox |

2. **Create a Notion integration** (an internal connection) at <https://www.notion.so/profile/integrations>
   with read, update and insert content capabilities. Copy its secret.
3. **Share the Feed database with the integration** (database menu `...` > Connections). Share nothing else:
   the scanner never needs your main database.
4. **Get the data source ID** of the Feed (database menu `...` > Manage data sources > copy the data source ID). It is not the ID in the database's URL.
5. **Add two repository secrets** (Settings > Secrets and variables > Actions):
   `NOTION_TOKEN` (the integration secret) and `NOTION_FEED_DATA_SOURCE_ID`.
6. **Run it once by hand**: Actions > scan > Run workflow. Leave `dry_run` ticked first (it validates the Feed
   schema and shows what it would write), then run again with `dry_run` unticked and `max_rows` = 5.
   After that the schedule takes over: twice a day, `17 15 * * *` and `17 23 * * *` UTC (08:17 and 16:17 Pacific,
   UTC-7 all year without DST). A run only writes jobs not yet in `state/seen.json` or the Feed, so the
   second run never duplicates the first. The workflow commits to the branch it runs on; if
   that branch is protected, allow GitHub Actions to push to it.

A row carries at most two Signals: the strongest level tag (new grad, recent graduate, 0-2 years, entry level,
junior) and the most important warning (2+ yrs, P.Eng req, jd unavailable, low signal). The full set is in
`reports/latest.md`.

Your own Notion automation can copy a ticked row to your main database and tick `done-del`; the next run trashes
that row, and `state/seen.json` stops the job from being written again.

## Run it locally

```bash
pip install --require-hashes -r requirements-dev.txt        # Python 3.12; every package pinned by hash
python -m scanner run --dry-run --skip-notion --since 7     # scan, score, print Notion payloads; touches nothing
NOTION_TOKEN=... NOTION_FEED_DATA_SOURCE_ID=... python -m scanner run --dry-run   # also checks the Feed schema
NOTION_TOKEN=... NOTION_FEED_DATA_SOURCE_ID=... python -m scanner run --max-rows 5
pytest
```

Dependencies: edit `requirements.in` / `requirements-dev.in`, then regenerate the pinned files with
`pip-compile --generate-hashes --allow-unsafe --strip-extras requirements.in` (and the same for
`requirements-dev.in`). Dependabot opens weekly PRs for pip and GitHub Actions.

Useful flags: `--since N`, `--max-rows N`, `--no-jd` (phase 1 only), `--no-notion`, `--gaps-report always`.

## Watchlist

`watchlist.yaml` lists employers whose Greenhouse, Lever or Ashby board is read directly on every run. Use it for
employers the Feashliaa dataset does not carry (it is built from its author's company lists) and for employers you
want on the morning run: the dataset refreshes once a day, a watchlist board is live. To add one, open the
employer's careers page and look at where "Apply" goes: `boards.greenhouse.io/<slug>` or
`job-boards.greenhouse.io/<slug>`, `jobs.lever.co/<slug>`, `jobs.ashbyhq.com/<slug>`. Add
`- {ats: greenhouse|lever|ashby, slug: <slug>, name: Display Name}`. A board that cannot be read is reported and
skipped; a malformed entry stops the run with a clear message.

Not covered, and why: employers on Workday, SuccessFactors, Taleo, iCIMS, UltiPro or their own site need a
different reader. Workday employers are already in the dataset when it lists them. The dataset carries about
250,000 iCIMS postings with no location (the field says "Not specified"), so none can pass the Canadian filter;
employers that use iCIMS (Vancouver Coastal Health among them) are not scanned.

## Job sites

| Source label | What is read | How |
|---|---|---|
| `YC` | Y Combinator's job pages for Canada, Toronto and Vancouver | JSON embedded in the page; postings asking 3+ years are skipped before anything else |
| `Built In` | Built In Vancouver, entry-level cards for engineering and data (up to 3 pages each) | server-rendered cards |
| `Bay Street Oracle` | capital-markets jobs; only postings whose URL words match a tech title are opened | sitemap, then each posting's schema.org data; linked to the employer's own ATS when the scanner reads it |
| `HN` | the monthly "Ask HN: Who is hiring?" thread; top-level comments whose header line (`Company \| Role \| Location`) names a Canadian place and a role | Algolia HN API |

All four read only what the sites publish to any visitor, at the same polite pace as the ATS fetchers, under the
scanner's own name. A site that is down or has changed prints `WARNING <site>: not read (...)` and the run goes on.
A job a site shares with an ATS board is kept once, with the ATS link. To turn one off set `enabled: false` under
`sources.<name>` in `config.yaml`. The Feed's Source column needs the options `YC`, `Built In`, `Bay Street Oracle`
and `HN` to show them (without the option the Source cell stays empty; nothing else changes).

Not read: CivicJobs and CivicInfo BC answer an automated client with a Cloudflare bot challenge (HTTP 403, also on
the RSS feed CivicInfo advertises), so they are left alone; Work at a Startup lists the same postings as the YC pages.

## Feedback

Before a `done-del` row is trashed, the run counts it in `state/feedback.json`: reviewed or picked (`Interested` /
`Apply`), by Fit band, source and its two tags. Counts only, no titles or companies. `reports/feedback.md` prints
the pick rate per band. If Fit % predicts your picks, the rate rises with the band; if a band is flat, that is
where Fit % needs work. The first 120 reviewed rows were entered by hand when this started
(`seeded_through` in the file), so those rows are never counted twice.

## What you edit

| File | Purpose |
|---|---|
| `config.yaml` | every filter, threshold, Notion property name and the daily cap |
| `skills.yaml` | your skills and their aliases (drives Fit %) |
| `gaps.yaml` | common tech terms you do not have yet (shown as gaps) |
| `company_names.yaml` | display names for the Feed's Company column (`rbc: RBC`) |
| `watchlist.yaml` | employer job boards read directly on every run |

## Credits

- [Feashliaa/job-board-aggregator](https://github.com/Feashliaa/job-board-aggregator) and its daily dataset
  [Feashliaa/job-board-data](https://github.com/Feashliaa/job-board-data). The aggregator **code is MIT**; the
  **company lists and data are CC BY-NC 4.0**, so this project is for non-commercial use only.
- [SimplifyJobs/New-Grad-Positions](https://github.com/SimplifyJobs/New-Grad-Positions) for the curated new-grad
  listings.
- ATS endpoint shapes were checked against the MIT-licensed providers in
  [career-ops-hq/career-ops](https://github.com/career-ops-hq/career-ops).
