# lockin

> *Finding a job....*

Job hunting is a full-time job that pays nothing. **lockin** is the intern that does the boring half of it, twice a
day, for free, without ever asking for a coffee.

It reads the public job lists, throws out everything that wants 7 years of experience in a technology invented 4
years ago, scores what is left against your skills, and drops the best new **entry-level and new-grad tech jobs in
Canada** into a Notion board. You tick a box. It never writes a cover letter, never calls a model, never costs a
cent. It runs on GitHub Actions while you do something healthier, like refreshing the same Feed.

Design notes live in [SPEC.md](SPEC.md). Opinions about the job market do not.

**Using the Feed to get hired, and the keys worth adding:** [docs/IDEAS.md](docs/IDEAS.md).

## What a run does

1. Read [Feashliaa/job-board-data](https://github.com/Feashliaa/job-board-data), the
   [SimplifyJobs new-grad list](https://github.com/SimplifyJobs/New-Grad-Positions), the employer boards in
   `watchlist.yaml` (read live, so they are as fresh as the employer's own site; boards the scanner finds by itself
   go in `watchlist.auto.yaml`) and six job sites, Job Bank and the BC Public Service among them (see "Job sites" below).
2. Filter by freshness, Canadian location, company blocklist, language, title and seniority (`config.yaml`).
   "Senior", "Staff", "Principal" and "Director of Vibes" do not make it through.
3. Fetch each JD (Greenhouse, Lever, Ashby, Workday, BambooHR, SmartRecruiters, Phenom, and the job sites), apply the JD rules (3+ years, current
   enrollment, clearance) and score the fit against `skills.yaml` / `gaps.yaml`.
4. Write the top `notion.daily_cap` (500) new jobs to the Feed, skipping anything in `state/seen.json` or
   already in the Feed by Link. At the start of the run, Feed rows whose `done-del` box is ticked (your Notion
   automation ticks it after copying the row to your main database) are trashed; no other row is ever trashed.
5. Commit `state/` and `reports/` back to the repo. `reports/latest.md` is the full candidate list; `reports/dropped.md` lists every job
   the JD rules dropped, by reason (years asked, too few skills, French required ...): read it when you suspect a good job was lost;
   `reports/boards.md` lists the employer boards the scanner added by itself and the ones resting after failed reads;
   `reports/gaps.md` (Mondays) lists the tech terms JDs keep asking for that you do not have yet (a to-learn list that
   writes itself, yay); `reports/feedback.md`
   shows what you actually pick, by Fit band, source and tag (see "Feedback").

It is scheduled twice a day, at 15:57 and 23:57 UTC. GitHub's cron has no time zones, so those slots sit between the
two Pacific offsets (07:57 / 15:57 in winter, 08:57 / 16:57 in summer) and GitHub's usual delay of a few minutes to an
hour lands the runs around 8-9 am and 4-5 pm Pacific. The odd minutes are on purpose: jobs scheduled on the hour are
the ones GitHub delays most, which is also how it feels waiting for a recruiter. GitHub also forgets a scheduled run
now and then; the 7-day window (`freshness.default_days`) makes the next run pick up what the lost one would have
found, and a run only writes jobs not already seen, so nothing comes twice.

## Running it from GitHub (Actions tab)

Nothing needs installing: open the repo on GitHub, **Actions**, pick a workflow, **Run workflow**.

**`scan`** is the real thing (the same run the schedule does). Its inputs:

| Input | Leave blank / default | Use it to |
|---|---|---|
| `dry_run` | ticked | scan and report without touching Notion or `state/seen.json`. **Untick it for a real run**; keep it ticked to look first. |
| `since_days` | `7` (`freshness.default_days`) | widen the net: `14` or `30` after a break, a new source or a fix. Safe any time: `state/seen.json` keeps a job from being written twice, so a wide window only costs a longer run. |
| `max_rows` | `500` (`notion.daily_cap`) | how many new rows this run may write (the best-scoring ones go first; the rest wait for the next run while they are inside the window). `5` to try it, `100` to empty a backlog. |

Typical recipes: *"did the last fix work?"* is `dry_run` ticked, `since_days` 14, then read the log and
`reports/latest.md`. *"I was away for two weeks"* is `dry_run` unticked, `since_days` 14, `max_rows` 100. From a
terminal with the GitHub CLI, the same things:

```bash
gh workflow run scan.yml -f dry_run=true  -f since_days=14                 # look first
gh workflow run scan.yml -f dry_run=false -f since_days=14 -f max_rows=100 # catch up for real
gh run watch                                                               # follow the run
```

**`tools`** are the helpers (nothing is written to the repo, and only `clean-feed` touches Notion; the answer is in the run's Summary and
log). Pick `command` and write `args` the way you would in a terminal:

| `command` | `args` | Answers |
|---|---|---|
| `audit` | `--days 30` (default 14) | Which filter rules stop the fresh Canadian postings, which discarded titles look like jobs you want, which title words the filter does not know. See "The filter audit". |
| `why` | `"General Motors"` (add `--since 21`) | "I saw this employer post a job, why is it not in the Feed?" Names the first rule that stops each of its Canadian postings, or says the employer is not on the watchlist. |
| `clean-feed` | `--dry-run` (then run again without it) | Re-checks the rows already in your Feed against today's rules and trashes the ones that now fail, never a row with Interested / Apply / done-del ticked (Notion keeps trashed rows 30 days). `--rescore` also refreshes Fit % and Tags. |
| `discover` | `"Telus" "Bell Canada"`, or `--workday rbc`, `--phenom jobs.bell.ca/ca/en=Bell`, `--successfactors careers.telus.com=Telus` or `--icims careersen-victoria.icims.com=City of Victoria` | Finds an employer's job board and prints the `watchlist.yaml` line to add. |

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
   | Tags | Multi-select, options: Internship, Students only, New grad, Entry level, Asks 2 yrs, Needs P.Eng, Talent pool, No description, Unclear fit |
   | Area | Select (optional), options in this order: BC, Remote, Alberta, Ontario, Canada. Sorting by Area puts BC first |
   | Location | Text (optional: filled when the column exists) |
   | Posted | Date (optional): the posting date, so the Feed sorts newest first. Rows written before the column existed get it from the date in Company on the next run |
   | Pay | Text (optional): the pay the posting states, e.g. "$70,000–$85,000 a year" (empty when it states none) |
   | JD | Text |
   | I | Checkbox (Interested) |
   | A | Checkbox (Apply) |
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

**Reading the Feed.** Every row has a **Verdict** (Apply first: your skills and level fit; Worth a look: a partial
match or 2 years asked; Long shot: few of your skills, a P.Eng, or no current opening) and a **Why** line (your skills
the posting names, and what it asks that you lack). The Feed's **Info** tab repeats this key in one line. Sort by **Area** (BC, then remote jobs open
to all of Canada, then Alberta and Ontario), then **Fit %**. Jobs only in other provinces are not written at all. **Tags**, at most two:

| Tag | Means |
|---|---|
| Internship | an intern or co-op role |
| Students only | the posting requires current enrollment |
| New grad | the posting asks for new or recent graduates |
| Entry level | it says junior, entry level or 0-2 years |
| Asks 2 yrs | it asks for 2 years of experience: apply if the rest fits |
| Needs P.Eng | it requires an engineering licence |
| Talent pool | it says it is evergreen or not for a current opening: expect to wait |
| No description | the posting's text could not be read: open the link |
| Unclear fit | the text names few of your skills, often not a tech job: lowest priority |

**Fit %** is how many of your skills the posting names, not a prophecy: open the job before you fall in love. **Posted**
is the date the employer published the job (for the Canada List, the day lockin first saw it). **Pay** is what the
posting states.

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

| Source label | What is read | How |
|---|---|---|
| `YC` | Y Combinator's job pages for Canada, Toronto and Vancouver | JSON embedded in the page; postings asking 3+ years are skipped before anything else |
| `Built In` | Built In Vancouver, entry-level cards for engineering and data (up to 3 pages each) | server-rendered cards |
| `Bay Street Oracle` | capital-markets jobs; only postings whose URL words match a tech title are opened | sitemap, then each posting's schema.org data; linked to the employer's own ATS when the scanner reads it |
| `HN` | the monthly "Ask HN: Who is hiring?" thread; top-level comments whose header line (`Company \| Role \| Location`) names a Canadian place and a role (or whose first lines do) | Algolia HN API; a comment counts as new when first read, not when written |
| `Jobicy` | remote jobs open to Canada | Jobicy's public JSON API (no key); the JD comes from the posting page |
| `Adzuna` | Canadian search results for 20 tech keywords | Adzuna's API. Turned off (`enabled: false`): not used |
| `Google Jobs` | Google for Jobs: the job panel Google shows for a search, which gathers LinkedIn, Indeed, Glassdoor, Talent.com and employers' own sites. 2 of 24 tech searches a run, in turn, for British Columbia | SerpApi's Google Jobs API, each posting with its full description. Needs a free key (serpapi.com, 250 searches a month) saved as the secret `SERPAPI_KEY`; skipped without it. The link is the employer's ATS page when Google lists one, so the JD and location come from the employer |
| `Google Alerts` | new pages Google indexes on the ATS sites (`site:jobs.lever.co Vancouver` ...): postings often show up here before LinkedIn | Google Alerts RSS. Turned off (`enabled: false`): not used |
| `Jobhive` | the hiring systems lockin has no reader for: Oracle Recruiting, Eightfold (Microsoft), Dayforce, UKG, Rippling, JazzHR, Breezy, Cornerstone; plus every Greenhouse, Lever, Ashby, BambooHR, iCIMS, SmartRecruiters and Phenom board, not just the watchlist's, and Uber and Apple. About 6,000 Canadian postings a week | ats-scrapers' open dataset (github.com/kalil0321/ats-scrapers, MIT): one Parquet snapshot per system, refreshed daily, free, no key. The Canadian rows inside the window are kept, each with its description; Oracle links are rebuilt to open the posting itself |
| `Site Search` | the recruiter's Boolean trick on 9 ATS sites at once: `site:jobs.ashbyhq.com ("software engineer" OR "software developer" ...) (Vancouver OR "British Columbia" OR Canada)`, past week only; every host x title group, taken in turn | Brave Search API (`BRAVE_SEARCH_API_KEY`, 15 searches a run) or, without it, SerpApi's Google results (`SERPAPI_KEY`, 2 a run). Each result that is a posting is read on the employer's ATS like any other job |
| `Job Bank` | the federal Job Bank (jobbank.gc.ca): each tech occupation (NOC code) and a few keywords, paged 25 at a time, newest first | Job Bank's public results pages. They carry the employers' postings **and the partner sites' ones: CivicJobs.ca (the municipal board: city halls, regional districts), Talent.com, Indeed ...** The Atom feed was dropped: it holds only the employer-posted jobs and 10 per keyword. 5 seconds between requests, as its robots.txt asks |
| `BC Gov` | the BC Public Service (bcpublicservice.hua.hrsmart.com): every external posting, about 80 | one HTML table; the page shows the date opened, so the freshness window works |

All of them read only what the sites publish to any visitor, at the same polite pace as the ATS fetchers, under the
scanner's own name. A site that is down or has changed prints `WARNING <site>: not read (...)` and the run goes on.
A job a site shares with an ATS board is kept once, with the ATS link. To turn one off set `enabled: false` under
`sources.<name>` in `config.yaml`. The Feed's Source column needs the options `YC`, `Built In`, `Bay Street Oracle`,
`HN`, `Job Bank`, `BC Gov`, `Jobicy`, `Adzuna`, `Google Jobs`, `Google Alerts`, `Site Search` and `Jobhive` (and `SmartRecruiters` / `Phenom` / `SuccessFactors` / `iCIMS` for those watchlist boards) to show them (without the option the Source cell stays empty; nothing else changes).

**Municipal jobs.** CivicJobs and CivicInfo BC answer an automated client with a Cloudflare bot challenge (HTTP 403, including
on the RSS feed CivicInfo advertises). That is the sites saying no, so lockin does not go there: no fake browsers, no challenge
solvers. Two honest routes instead: Job Bank republishes CivicJobs.ca postings (the `Job Bank` source above), and the cities,
regional districts, health authorities and Crown corporations are read from their **own** job sites when those are iCIMS or
SuccessFactors (City of Victoria, City of Vancouver, City of Toronto, ICBC ...: see `watchlist.yaml`). Work at a Startup lists the
same postings as the YC pages.

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

**The Recruiter and Hiring Manager links** need nothing: they are LinkedIn people searches you open yourself, signed in.
Rows written before the columns existed get them with Actions, **tools**, command `clean-feed`, args `--people-links`.

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
