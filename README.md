# lockin

> *Finding a job....*

Job hunting is a full-time job that pays nothing. **lockin** is the intern that does the boring half of it, twice a
day, for free, without ever asking for a coffee.

It reads the public job lists, throws out everything that wants 7 years of experience in a technology invented 4
years ago, scores what is left against your skills, and drops the best new **entry-level and new-grad tech jobs in
Canada** into a Notion board. You tick a box. It never writes a cover letter, never calls a model, never costs a
cent. It runs on GitHub Actions while you do something healthier, like refreshing the same Feed.

Design notes live in [SPEC.md](SPEC.md). Opinions about the job market do not.

## What a run does

1. Read [Feashliaa/job-board-data](https://github.com/Feashliaa/job-board-data), the
   [SimplifyJobs new-grad list](https://github.com/SimplifyJobs/New-Grad-Positions), the employer boards in
   `watchlist.yaml` (read live, so they are as fresh as the employer's own site; boards the scanner finds by itself
   go in `watchlist.auto.yaml`) and six job sites, Job Bank and the BC Public Service among them (see "Job sites" below).
2. Filter by freshness, Canadian location, company blocklist, language, title and seniority (`config.yaml`).
   "Senior", "Staff", "Principal" and "Director of Vibes" do not make it through.
3. Fetch each JD (Greenhouse, Lever, Ashby, Workday, BambooHR, SmartRecruiters, Phenom, and the job sites), apply the JD rules (3+ years, current
   enrollment, clearance) and score the fit against `skills.yaml` / `gaps.yaml`.
4. Write the top `notion.daily_cap` (40) new jobs to the Feed, skipping anything in `state/seen.json` or
   already in the Feed by Link. At the start of the run, Feed rows whose `done-del` box is ticked (your Notion
   automation ticks it after copying the row to your main database) are trashed; no other row is ever trashed.
5. Commit `state/` and `reports/` back to the repo. `reports/latest.md` is the full candidate list;
   `reports/boards.md` lists the employer boards the scanner added by itself and the ones resting after failed reads;
   `reports/gaps.md` (Mondays) lists the tech terms JDs keep asking for that you do not have yet (a to-learn list that
   writes itself, yay); `reports/feedback.md`
   shows what you actually pick, by Fit band, source and tag (see "Feedback").

It is scheduled twice a day, at 15:57 and 23:57 UTC. GitHub's cron has no time zones, so those slots sit between the
two Pacific offsets (07:57 / 15:57 in winter, 08:57 / 16:57 in summer) and GitHub's usual delay of a few minutes to an
hour lands the runs around 8-9 am and 4-5 pm Pacific. The odd minutes are on purpose: jobs scheduled on the hour are
the ones GitHub delays most, which is also how it feels waiting for a recruiter. GitHub also forgets a scheduled run
now and then; the 10-day window (`freshness.default_days`) makes the next run pick up what the lost one would have
found, and a run only writes jobs not already seen, so nothing comes twice.

## Running it from GitHub (Actions tab)

Nothing needs installing: open the repo on GitHub, **Actions**, pick a workflow, **Run workflow**.

**`scan`** is the real thing (the same run the schedule does). Its inputs:

| Input | Leave blank / default | Use it to |
|---|---|---|
| `dry_run` | ticked | scan and report without touching Notion or `state/seen.json`. **Untick it for a real run**; keep it ticked to look first. |
| `since_days` | `10` (`freshness.default_days`) | widen the net: `14` or `30` after a break, a new source or a fix. Safe any time: `state/seen.json` keeps a job from being written twice, so a wide window only costs a longer run. |
| `max_rows` | `40` (`notion.daily_cap`) | how many new rows this run may write (the best-scoring ones go first; the rest wait for the next run while they are inside the window). `5` to try it, `100` to empty a backlog. |

Typical recipes: *"did the last fix work?"* is `dry_run` ticked, `since_days` 14, then read the log and
`reports/latest.md`. *"I was away for two weeks"* is `dry_run` unticked, `since_days` 14, `max_rows` 100. From a
terminal with the GitHub CLI, the same things:

```bash
gh workflow run scan.yml -f dry_run=true  -f since_days=14                 # look first
gh workflow run scan.yml -f dry_run=false -f since_days=14 -f max_rows=100 # catch up for real
gh run watch                                                               # follow the run
```

**`tools`** are the read-only helpers (nothing is written to Notion or the repo; the answer is in the run's Summary and
log). Pick `command` and write `args` the way you would in a terminal:

| `command` | `args` | Answers |
|---|---|---|
| `audit` | `--days 30` (default 14) | Which filter rules stop the fresh Canadian postings, which discarded titles look like jobs you want, which title words the filter does not know. See "The filter audit". |
| `why` | `"General Motors"` (add `--since 21`) | "I saw this employer post a job, why is it not in the Feed?" Names the first rule that stops each of its Canadian postings, or says the employer is not on the watchlist. |
| `discover` | `"Telus" "Bell Canada"`, or `--workday rbc`, or `--phenom jobs.bell.ca/ca/en=Bell` | Finds an employer's job board and prints the `watchlist.yaml` line to add. |

## Setup

1. **Create the Feed database in Notion** with exactly these properties (names and types matter; the scanner
   refuses to start otherwise and tells you what is wrong):

   | Property | Type |
   |---|---|
   | Name | Title |
   | Company | Text |
   | Link | URL |
   | Source | Select: Workday, Greenhouse, Lever, Ashby, BambooHR, Paylocity, Simplify, Watchlist. Optional extras: YC, Built In, Bay Street Oracle, HN, SmartRecruiters |
   | Fit % | Number, format **Percent** |
   | Signals | Multi-select, options: new grad, recent graduate, 0-2 years, entry level, junior, low signal, jd unavailable (`2+ yrs` and `P.Eng req` are created on first use) |
   | Location | Text (optional: filled when the column exists) |
   | JD | Text |
   | Interested | Checkbox |
   | Apply | Checkbox |
   | done-del | Checkbox |

2. **Create a Notion integration** (an internal connection) at <https://www.notion.so/profile/integrations>
   with read, update and insert content capabilities. Copy its secret.
3. **Share the Feed database with the integration** (database menu `...` > Connections). Share nothing else:
   the scanner never needs your main database, and you do not need a bot reading your life.
4. **Get the data source ID** of the Feed (database menu `...` > Manage data sources > copy the data source ID).
   It is not the ID in the database's URL. Yes, that one trips everybody.
5. **Add two repository secrets** (Settings > Secrets and variables > Actions):
   `NOTION_TOKEN` (the integration secret) and `NOTION_FEED_DATA_SOURCE_ID`. Never commit either, and never paste a
   Notion ID anywhere in the repo.
6. **Run it once by hand**: Actions > scan > Run workflow. Leave `dry_run` ticked first (it validates the Feed
   schema and shows what it would write), then run again with `dry_run` unticked and `max_rows` = 5.
   After that the schedule takes over. The workflow commits to the branch it runs on; if that branch is protected,
   let GitHub Actions push to it.

**Reading the Feed.** A row carries at most two Signals: the strongest level tag (new grad, recent graduate, 0-2 years, entry level,
junior) and the most important warning (2+ yrs, P.Eng req, jd unavailable, low signal). The full set is in
`reports/latest.md`. **Fit %** is a keyword match between the posting and your skills, not a prophecy: open the job
before you fall in love.

Your Notion automation copies a ticked row to your main database and ticks `done-del`; the next run trashes the row,
and `state/seen.json` makes sure the job never comes back to haunt you.

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

## Watchlist: employers we stalk professionally

`watchlist.yaml` lists employers whose Greenhouse, Lever, Ashby, Workday, SmartRecruiters or Phenom board is read directly on every run. Use it for
employers the Feashliaa dataset does not carry (it is built from its author's company lists) and for employers you
want on the morning run: the dataset refreshes once a day, a watchlist board is live. To add one, open the
employer's careers page and look at where "Apply" goes: `boards.greenhouse.io/<slug>` or
`job-boards.greenhouse.io/<slug>`, `jobs.lever.co/<slug>`, `jobs.ashbyhq.com/<slug>`. Add
`- {ats: greenhouse|lever|ashby|smartrecruiters, slug: <slug>, name: Display Name}`; for Workday the slug is
`<tenant>.<wdN>/<site>` (`generalmotors.wd5/Careers_GM`), and only the Canadian postings are asked for. For a Phenom People
careers site (Bell, Air Canada and OpenText run on it) the slug is `<host>/<country>/<language>`
(`jobs.bell.ca/ca/en`). A malformed entry stops the run with a clear message, because a typo should not quietly
un-stalk anyone.

**It grows by itself.** Every Canadian posting links to the employer's own board, and a link on Workday, Greenhouse,
Lever, Ashby or SmartRecruiters names that board exactly. The run counts the boards it meets that are not listed, reads the
busiest 12 once, and appends the ones that really list Canadian jobs to `watchlist.auto.yaml` (at most 150 lines); the
next run reads them. Delete a line you do not want. `watchlist.yaml` wins when a board is in both files.

**It does not waste time.** A board that cannot be read is retried on the next runs; after three failures in a row it
rests for a week, then two, then four (a working read clears it), and a candidate that fails three probes is given up.
`reports/boards.md` lists what is resting and why.

Not covered, and why: employers on SuccessFactors (Telus, Scotiabank, Rogers, Canada Post, Celestica, ICBC), Taleo, iCIMS, UltiPro
or their own site need a different reader. The dataset carries about
250,000 iCIMS postings with no location (the field says "Not specified"), so none can pass the Canadian filter;
employers that use iCIMS (Vancouver Coastal Health among them) are not scanned. A known blind spot, written down
so it stops lurking.

### Finding employers, and finding out why one was missed

```bash
python -m scanner discover "Wealthsimple" "Cohere"                       # guesses the board, prints the watchlist line
python -m scanner discover --workday "generalmotors.wd5/Careers_GM=General Motors"
python -m scanner discover --workday rbc                                 # just the tenant: its robots.txt names the career sites
python -m scanner discover --phenom "jobs.bell.ca/ca/en=Bell"            # a Phenom People careers site
python -m scanner why "General Motors"                                   # every Canadian posting: which rule stops it
python -m scanner audit --days 30                                        # the whole funnel, see below
```

`why` is for the moment you see a job on an employer's site that the Feed does not have. It says whether the employer
is on the watchlist at all (a coverage gap: add it) or which rule drops the posting (title word, location, age).
Neither command writes anything.

### The filter audit

`python -m scanner audit` (or `tools` > `audit` on Actions) reads the sources without any job description and, for every fresh
Canadian posting, records the rule that stopped it, then writes `reports/audit.md`: the funnel (how many postings reach each
rule), the title-exclude words ranked by jobs removed and by jobs they removed *alone* (the only ones changing a rule would bring back),
**near misses** (discarded titles with a "junior / graduate / associate / entry" word, the jobs most likely lost by mistake) and
the commonest words in titles with no tech-role word at all (candidates for `title.strong` / `title.weak`). Run it after
changing a filter, or when a job you wanted never showed up.

## Job sites

| Source label | What is read | How |
|---|---|---|
| `YC` | Y Combinator's job pages for Canada, Toronto and Vancouver | JSON embedded in the page; postings asking 3+ years are skipped before anything else |
| `Built In` | Built In Vancouver, entry-level cards for engineering and data (up to 3 pages each) | server-rendered cards |
| `Bay Street Oracle` | capital-markets jobs; only postings whose URL words match a tech title are opened | sitemap, then each posting's schema.org data; linked to the employer's own ATS when the scanner reads it |
| `HN` | the monthly "Ask HN: Who is hiring?" thread; top-level comments whose header line (`Company \| Role \| Location`) names a Canadian place and a role (or whose first lines do) | Algolia HN API; a comment counts as new when first read, not when written |
| `Job Bank` | the federal Job Bank (jobbank.gc.ca): `sources.jobbank.terms`, each asked for all of Canada, and per province when the 10-posting feed fills | Job Bank's own Atom feed; 5 seconds between requests, as its robots.txt asks; Quebec is never searched |
| `BC Gov` | the BC Public Service (bcpublicservice.hua.hrsmart.com): every external posting, about 80 | one HTML table; the page shows the date opened, so the freshness window works |

All of them read only what the sites publish to any visitor, at the same polite pace as the ATS fetchers, under the
scanner's own name. A site that is down or has changed prints `WARNING <site>: not read (...)` and the run goes on.
A job a site shares with an ATS board is kept once, with the ATS link. To turn one off set `enabled: false` under
`sources.<name>` in `config.yaml`. The Feed's Source column needs the options `YC`, `Built In`, `Bay Street Oracle`,
`HN`, `Job Bank` and `BC Gov` (and `SmartRecruiters` / `Phenom` for those watchlist boards) to show them (without the option the Source cell stays empty; nothing else changes).

**Not read, on purpose:** CivicJobs and CivicInfo BC answer an automated client with a Cloudflare bot challenge
(HTTP 403, including on the RSS feed CivicInfo advertises). That is the sites saying no, so lockin says "fair
enough" and leaves. No fake browsers, no challenge solvers. Work at a Startup lists the same postings as the YC pages.

## Feedback: teaching the robot your taste

Before a `done-del` row is trashed, the run counts it in `state/feedback.json`: reviewed or picked (`Interested` /
`Apply`), by Fit band, source and its two tags. Counts only, no titles or companies. `reports/feedback.md` prints
the pick rate per band. If Fit % predicts your picks, the rate rises with the band; if a band is flat, that is
where Fit % needs work. The first 120 reviewed rows were entered by hand when this started
(`seeded_through` in the file), so they are never counted twice.

## What you edit

| File | Purpose |
|---|---|
| `config.yaml` | every filter, threshold, Notion property name and the daily cap |
| `skills.yaml` | your skills and their aliases (drives Fit %) |
| `gaps.yaml` | common tech terms you do not have yet (shown as gaps) |
| `company_names.yaml` | display names for the Feed's Company column (`rbc: RBC`) |
| `watchlist.yaml` | employer job boards read directly on every run |
| `watchlist.auto.yaml` | boards the scanner added by itself; delete a line to drop one |

## Credits

- [Feashliaa/job-board-aggregator](https://github.com/Feashliaa/job-board-aggregator) and its daily dataset
  [Feashliaa/job-board-data](https://github.com/Feashliaa/job-board-data). The aggregator **code is MIT**; the
  **company lists and data are CC BY-NC 4.0**, so this project is for non-commercial use only. Be nice to the
  person whose data it is.
- [SimplifyJobs/New-Grad-Positions](https://github.com/SimplifyJobs/New-Grad-Positions) for the curated new-grad
  listings.
- ATS endpoint shapes were checked against the MIT-licensed providers in
  [career-ops-hq/career-ops](https://github.com/career-ops-hq/career-ops).

*Built with spite, Python, and a mild fear of the word "synergy".*
