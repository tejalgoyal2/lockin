# lockin

> *Finding a job....*

Job hunting is a full-time job that pays nothing. **lockin** is the intern that does the boring half of it, twice a
day, for free, without ever asking for a coffee.

It reads the public job lists, throws out everything that wants 7 years of experience in a technology invented 4
years ago, rates what is left against your skills, and drops the new **entry-level tech jobs in BC, Alberta, Ontario
and remote Canada** into a Notion board with two numbers on each: how good a bet it is, and how much experience it
asks for. You tick a box. It never writes a cover letter, never calls a model, never costs a cent. It runs on GitHub
Actions while you do something healthier, like refreshing the same Feed.

How it decides is in [SPEC.md](SPEC.md). Using the Feed to get interviews: [docs/IDEAS.md](docs/IDEAS.md).

## Reading the Feed

| Column | What it says |
|---|---|
| **Name**, **Company** | the job title; the employer and the day it was found (`RBC · nov14`) |
| **Score** | 1-5, how much the job is worth your time. See below |
| **Exp** | the experience the posting asks for: **1** = 0-1 years (or it says new grad / junior / intern), **2** = 1-2 years, or it gives no number, **3** = 2 years (a long shot). Anything asking for more is not in the Feed |
| **Province** | BC, Remote, Alberta, Ontario, Canada (no province named). A job can carry two: `BC` + `Remote` |
| **Posted** | the date the employer published the job |
| **Link**, **JD** | the posting, and its full text |
| **A**, **I**, **done-del** | yours: **A** = Apply, **I** = Interested, **done-del** = finished with the row (the next run removes it) |

**Score** is role + skills + level, read off the posting by fixed rules (no model, so the same posting always gets
the same number):

- **role**: 2 if the title is a role you target (software, data, ML / AI, cloud, security ...), 1 for a neighbouring
  tech role (IT, systems, QA, technical support ...), 0 for a generic title (Analyst, Engineer, Specialist).
- **skills**: 2 if the posting names 4+ of your tools and they are 60 %+ of the tech it asks for, 1 for 2+ tools and
  40 %+. A tool in the job title counts double ("Java Developer" is a Java job). A targeted role whose posting names
  no stack at all gets 1.
- **level**: 1 if it is entry level (Exp = 1).

| Score | Read it as |
|---|---|
| **5** | apply today: a role you target, your stack, entry level |
| **4** | a strong match; the level is not stated or it asks for 1-2 years |
| **3** | a fair match: part of your stack, or a neighbouring role that uses it |
| **2** | a long shot or an unknown: asks for 2 years, or the posting could not be read, or it is a talent pool |
| **1** | probably not worth it: a generic role that barely mentions your tools, or one that needs a P.Eng |

An internship or co-op is kept unless it requires current enrollment, and is a 3 at most unless it says graduates
may apply. Sort by **Score** (high first), then **Exp**, then **Posted**.

Your Notion automation copies a row you tick to your main database and ticks `done-del`; the next run trashes the
row, and `state/seen.json` makes sure the job never comes back to haunt you.

## What a run does

1. Read [Feashliaa/job-board-data](https://github.com/Feashliaa/job-board-data) (1.4 M ATS postings, refreshed daily),
   the [SimplifyJobs](https://github.com/SimplifyJobs/New-Grad-Positions) new-grad and internship lists, the 220+
   employer boards in `watchlist.yaml` / `watchlist.auto.yaml` (read live) and the job sites below.
2. Filter by freshness (7 days), Canadian location (BC, Alberta, Ontario, remote; not Quebec-only), company
   blocklist, language, title and seniority (`config.yaml`). "Senior", "Staff", "Principal" and "Director of Vibes" do
   not make it through.
3. Fetch each description and apply the JD rules: dropped if it asks for more than 2 years ("2-5 years", "3+"; "1-5
   years" passes), requires French, a security clearance or current enrollment, or does not show a tech job (the less
   the title says, the more of your tools the description must name).
4. Rate what is left (Score, Exp, Province) and write the new jobs to the Feed, best first, skipping anything in
   `state/seen.json`, already in the Feed by Link, or on the encrypted list of jobs you applied to. Rows whose
   `done-del` box is ticked are trashed first; no other row is ever trashed by a run.
5. Commit `state/` and `reports/` back to the repo: `reports/latest.md` (every kept job with what its Score was built
   from), `reports/dropped.md` (every job the JD rules dropped, by reason: read it when you suspect a good job was
   lost), `reports/boards.md` (employer boards added or resting), `reports/gaps.md` (Mondays: the tech the week's
   postings ask for that you do not list) and `reports/feedback.md` (what you pick, by Score: if the pick rate does not
   rise with the Score, the rules in `scanner/rating.py` need work).

**Schedule.** Scans run at **08:00 and 16:00 in British Columbia** (UTC-7 all year), in time to apply the same day.
GitHub starts scheduled workflows hours late on this repo (2.5 to 6.5 h, measured), so the workflow is triggered
every two hours and the first trigger to reach a slot waits for it, scans on time and records the slot in
`state/last_slot`; the triggers behind it stop in seconds. A slot nobody reached is still run up to 4 hours late, and
the 7-day window covers anything worse.

## Running it from GitHub (Actions tab)

**`scan`** is the real thing. Run it by hand with these inputs:

| Input | Default | Use it to |
|---|---|---|
| `dry_run` | ticked | scan and report without touching Notion or `state/seen.json`. **Untick it for a real run** |
| `since_days` | `7` | widen the net: `14` or `30` after a break. `state/seen.json` keeps a job from being written twice |
| `max_rows` | `500` | how many new rows this run may write (best first) |

```bash
gh workflow run scan.yml -f dry_run=true  -f since_days=14                 # look first
gh workflow run scan.yml -f dry_run=false -f since_days=14                 # catch up for real
gh run watch                                                               # follow the run
```

**`tools`** are the helpers (the answer is in the run's Summary and log):

| `command` | `args` | Answers |
|---|---|---|
| `audit` | `--days 30` (default 14) | Which title rules stop the fresh Canadian postings, which discarded titles look like jobs you want, which title words the filter does not know. Run it after changing a filter |
| `why` | `"General Motors"` | "I saw this employer post a job, why is it not in the Feed?" Names the rule that stops each of its Canadian postings, or says the employer is not on the watchlist |
| `discover` | `"Telus"`, `--workday rbc`, `--phenom jobs.bell.ca/ca/en=Bell`, `--successfactors careers.telus.com=Telus`, `--icims HOST=Name` | Finds an employer's job board and prints the `watchlist.yaml` line to add |
| `wipe-feed` | `--dry-run` first | A fresh start: trashes every Feed row with no box ticked (Notion keeps them 30 days). Then empty `state/seen.json` (`{"seen": {}}`) and run `scan` |

## Setup

1. **Create the Feed database in Notion** with exactly these properties (the scanner refuses to start otherwise and
   says what is wrong):

   | Property | Type |
   |---|---|
   | Name | Title |
   | Company | Text |
   | Score | Number |
   | Exp | Number |
   | Province | Multi-select, options in this order: BC, Remote, Alberta, Ontario, Canada |
   | Posted | Date |
   | Link | URL |
   | JD | Text |
   | A | Checkbox (Apply) |
   | I | Checkbox (Interested) |
   | done-del | Checkbox |

2. **Create a Notion integration** at <https://www.notion.so/profile/integrations> with read, update and insert
   content capabilities, and **share the Feed database with it** (database menu `...` > Connections). Share nothing
   else: you do not need a bot reading your life.
3. **Add two repository secrets** (Settings > Secrets and variables > Actions): `NOTION_TOKEN` (the integration
   secret) and `NOTION_FEED_DATA_SOURCE_ID` (database menu `...` > Manage data sources > copy the data source ID; it
   is not the ID in the database's URL. Yes, that one trips everybody). Never commit either.
4. **Run it once by hand**: Actions > scan > Run workflow, `dry_run` ticked (it checks the Feed and shows what it
   would write), then again unticked.

## Run it locally

```bash
pip install --require-hashes -r requirements-dev.txt        # Python 3.12; every package pinned by hash
python -m scanner run --dry-run --skip-notion --since 7     # scan and rate; touches nothing
NOTION_TOKEN=... NOTION_FEED_DATA_SOURCE_ID=... python -m scanner run --dry-run   # also checks the Feed schema
pytest
```

Dependencies: edit `requirements.in` / `requirements-dev.in`, then regenerate the pinned files with
`pip-compile --generate-hashes --allow-unsafe --strip-extras requirements.in` (and the same for
`requirements-dev.in`). Dependabot opens weekly PRs for pip and GitHub Actions.

## Watchlist: employers we stalk professionally

`watchlist.yaml` lists employers whose Greenhouse, Lever, Ashby, Workday, SmartRecruiters or Phenom board is read directly on every run. Use it for
employers the Feashliaa dataset does not carry (it is built from its author's company lists) and for employers you
want on the morning run: the dataset refreshes once a day, a watchlist board is live. To add one, open the
employer's careers page and look at where "Apply" goes: `boards.greenhouse.io/<slug>` or
`job-boards.greenhouse.io/<slug>`, `jobs.lever.co/<slug>`, `jobs.ashbyhq.com/<slug>`. Add
`- {ats: greenhouse|lever|ashby|smartrecruiters, slug: <slug>, name: Display Name}`; for Workday the slug is
`<tenant>.<wdN>/<site>` (`generalmotors.wd5/Careers_GM`), and only the Canadian postings are asked for. For a Phenom People
careers site (Bell, Air Canada and OpenText run on it) the slug is `<host>/<country>/<language>`
(`jobs.bell.ca/ca/en`). A SuccessFactors careers site is its host (`careers.telus.com`, or with its path: `jobs.toronto.ca/jobsatcity`); an iCIMS one is its host
(`careersen-victoria.icims.com`). A city's own site lists no location, so an entry can carry one: `location: "Victoria, BC"` is given to every posting that states none. A malformed entry stops the run with a clear message, because a typo should not quietly
un-stalk anyone.

**It grows by itself.** Every Canadian posting links to the employer's own board, and a link on Workday, Greenhouse,
Lever, Ashby or SmartRecruiters names that board exactly. The run counts the boards it meets that are not listed, reads the
busiest 12 once, and appends the ones that really list Canadian jobs to `watchlist.auto.yaml` (at most 150 lines); the
next run reads them. Delete a line you do not want. `watchlist.yaml` wins when a board is in both files.

**It does not waste time.** A board that cannot be read is retried on the next runs; after three failures in a row it
rests for a week, then two, then four (a working read clears it), and a candidate that fails three probes is given up.
`reports/boards.md` lists what is resting and why.

Not covered, and why: employers on Taleo, UltiPro, Oracle Recruiting or their own site need a different reader (Taleo: the Corporation of Delta and some other municipalities; its
list is drawn by JavaScript, which lockin does not run). Tell `why` or the audit about a missed one and it can be added.

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

| Source | What is read |
|---|---|
| YC | Y Combinator's job pages for Canada, Toronto and Vancouver |
| Built In | Built In Vancouver's entry-level cards for engineering and data |
| Bay Street Oracle | capital-markets jobs whose URL names a tech title |
| HN | the monthly "Ask HN: Who is hiring?" thread: comments that name a Canadian place and a role |
| Jobicy | remote jobs open to Canada (public JSON API) |
| Google Jobs | Google's job panel (LinkedIn, Indeed, Glassdoor, company sites) through SerpApi: 2 of 24 searches a run, for British Columbia. Needs `SERPAPI_KEY` |
| Site Search | the recruiter's Boolean trick on 9 ATS sites (`site:jobs.ashbyhq.com ("software engineer" OR ...) (Vancouver OR Canada)`), past week, through Brave Search (`BRAVE_SEARCH_API_KEY`, 15 searches a run) or SerpApi |
| Jobhive | [ats-scrapers](https://github.com/kalil0321/ats-scrapers)' open dataset (MIT): the hiring systems lockin has no reader for (Oracle, Eightfold, Dayforce, UKG, Rippling ...) and every Greenhouse, Lever, Ashby, BambooHR and iCIMS board, not just the watchlist's |
| Job Bank | the federal Job Bank: each tech occupation (NOC code) and a few keywords, newest first; it also carries CivicJobs.ca (municipal) and other partner sites' postings |
| BC Gov | the BC Public Service's own board |

All of them read only what the sites publish to any visitor, at a polite pace, under the scanner's own name. A site
that is down or has changed prints `WARNING <site>: not read (...)` and the run goes on. A job a site shares with an
ATS board is kept once, with the ATS link. Turn one off with `enabled: false` under `sources.<name>` in `config.yaml`.
A site that answers programs with a bot challenge (CivicJobs, LinkedIn, Indeed) is saying no, so lockin does not go
there: their jobs arrive through Job Bank, Google Jobs and the employers' own boards.

## Keys and secrets (all optional, all free)

Every source below is skipped, with a note in the log, until its secret exists. A secret is added the same way each time:
GitHub, the `lockin` repo, **Settings**, **Secrets and variables**, **Actions**, **New repository secret**, then the name
exactly as written and the value. Never paste a key into a file, an issue or a chat: only into that box.

| Secret | Turns on | Where to get it |
|---|---|---|
| `BRAVE_SEARCH_API_KEY` | Site Search, 15 searches a run | api-dashboard.search.brave.com: sign up, confirm the email, on the **Search** plan pick **Free** ($5 of credit every month, about 1,000 searches) and click **Activate**, **API Keys**, **Add API key**, copy it |
| `SERPAPI_KEY` | Google Jobs (2 searches a run), and Site Search when there is no Brave key | serpapi.com: **Register** (free plan, 250 searches a month), confirm the email, **Api Key** in the dashboard, copy it |

To check: Actions, **scan**, **Run workflow** with *Dry run* ticked. The log shows `Site Search: 15 of 45 searches this run,
through Brave`, `Google Jobs: N postings` instead of "skipped".

## What you edit

| File | Purpose |
|---|---|
| `config.yaml` | every filter, the experience limit, the relevance rule, the Feed's property names |
| `skills.yaml` | your skills and their aliases (the tools a posting is matched against) |
| `gaps.yaml` | common tech terms you do not have yet (they count against the fit) |
| `company_names.yaml` | display names for the Feed's Company column (`rbc: RBC`) |
| `watchlist.yaml` | employer job boards read directly on every run |
| `watchlist.auto.yaml` | boards the scanner added by itself; delete a line to drop one |
| `scanner/rating.py` | the Score and Exp rules, in 80 lines |

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
