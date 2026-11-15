# SPEC: how lockin decides

One goal: put every new Canadian entry-level tech job the owner could do in front of him within hours of its posting,
with nothing else in the way. No model is called anywhere; every decision is a rule in `config.yaml` or a short
function, and every drop is listed in `reports/dropped.md` with its reason. The full history of earlier decisions is
in git (`git log -- SPEC.md`).

## 1. Pipeline

```
sources ──> phase 1: title + location filters ──> dedupe ──> skip seen ──> phase 2: fetch JD, JD rules, rating ──> Notion Feed
(scanner/sources)   (filters.py, pipeline.py)   (dedupe.py)  (state/seen.json)   (enrich.py, jd_rules.py, score.py, rating.py)   (notion_feed.py)
```

`python -m scanner run` is the whole thing (`scanner/cli.py`). State lives in `state/` and is committed by the
workflow: `seen.json` (jobs already written), `last_slot` (the last scheduled slot scanned), `boards.json`,
`company_names.json`, `gaps.json`, `feedback.json`, `applied.json.enc` (jobs already applied to; Fernet, key in the
secret `APPLIED_KEY`).

## 2. Sources

| Kind | What | Freshness |
|---|---|---|
| Dataset | Feashliaa/job-board-data: Workday, Greenhouse, Lever, Ashby, BambooHR, iCIMS, Paylocity | daily |
| Dataset | Jobhive (ats-scrapers): 24 hiring systems lockin has no reader for, plus every board on the ones it has | daily |
| Lists | SimplifyJobs new-grad and internship lists | hours |
| Live boards | `watchlist.yaml` + `watchlist.auto.yaml`: 220+ employers on Workday, Greenhouse, Lever, Ashby, SmartRecruiters, Phenom, SuccessFactors, iCIMS | live |
| Sites | Job Bank, BC Public Service, YC, Built In Vancouver, Bay Street Oracle, HN "Who is hiring", Jobicy | live |
| Search APIs | Google Jobs (SerpApi), Site Search on 9 ATS hosts (Brave or SerpApi) | live, a few queries a run |

The watchlist grows by itself: every Canadian posting links to its employer's board; the busiest boards not yet
listed are read once and added when they list Canadian jobs that pass the title filters (`scanner/boards.py`). A
board that fails three reads in a row rests for 1, 2, then 4 weeks. Sites that answer programs with a bot challenge
are not read at all.

## 3. Phase 1: title and location (no network)

In order, each counted in `reports/latest.md`:

1. **Fresh**: first seen within `freshness.default_days` (7).
2. **Canadian location** (`location.include`, with known false positives removed: "Vancouver, WA", "Ontario, CA"). A
   title that names a place outside Canada and none inside fails too.
3. **Not Quebec-only**, and in **BC, Alberta, Ontario, remote, or "Canada"** (`location.areas`).
4. **Company** not on the blocklist; **title** not French-only.
5. **Title tier** (`Filters.role_tier`): `core` (a `title.strong` word: software, developer, data, cloud, security,
   ML ...), `adjacent` (a `title.weak` word: IT, systems, QA, technical, embedded ...), `generic` (`title.generic`:
   engineer, analyst, specialist, scientist, graduate program). No word: dropped.
6. **Not senior / not another field** (`title.exclude*`): senior, staff, lead, manager, II / III, mechanical, sales ...
   Domain words such as `coordinator` or `marketing` exclude only a non-core title ("Marketing Data Analyst" stays).
7. Intern / co-op / student titles are flagged, not dropped.

`python -m scanner audit` reports what each of these rules removes and which removals look wrong.

## 4. Phase 2: the JD rules

The description is fetched from the employer's ATS (cached 10 days), or taken from the source when it hands one over.
The posting's own location list replaces the dataset's. Then, first match wins:

| Drop reason | Rule |
|---|---|
| `location_*` | the posting's real places are not Canadian, are all in Quebec, or all in an unwanted province |
| `requires_french` | French or bilingual skills required ("an asset" is not) |
| `requires_enrollment` | for current students only. A sentence that also takes graduates ("completing or have recently finished a degree") is not such a requirement |
| `security_clearance`, `us_work_authorization` | required, not merely mentioned |
| `experience_too_high` | asks for more than `jd.max_years` (2). The requirement is the **largest** lower bound among the mentions that count (a posting states the overall figure, then smaller ones per tool); mentions under a nice-to-have heading, in a "preferred / an asset" sentence, upper bounds, lengths of study or of a contract, and numbers >= 20 do not count; a Master's route sets its own figure. "1-5 years" passes; "2 years" and "2+" pass as long shots; "2-5", "2-3", "3", "3+" are dropped. Not applied to intern titles (banks paste a "4-6 years" template line into them) |
| `not_a_tech_job` | the less the title says, the more the description must: a `core` title that is unmistakable (`jd.relevance.core_always`: software, developer, devops ...) is never doubted; any other core title fails only when a full description has no tech word at all; an `adjacent` title needs one tool or technology; a `generic` title needs 2 of the owner's tools (1 in a description under 600 characters). Process words (Agile, Jira, troubleshooting) are not tools |
| `generic_title_no_description` | no description, a generic title, and a title word from `jd.no_jd_title_exclude` |

A job with no readable description is otherwise kept (Score at most 2): the owner can open the link.

## 5. Fit (`scanner/score.py`)

`skills.yaml` lists the owner's skills with aliases and context-aware regexes (R, C, Go, Excel the product, React the
library). `gaps.yaml` lists common tech he does not have, plus generic engineering vocabulary that marks a
description as technical without counting for or against him.

**Fit = matched / (matched + gaps + 1)**, each term weighted by where the posting names it: 1 in a requirements
section (or anywhere, when the posting has no such section), 0.5 elsewhere or under a nice-to-have heading, 2 in the
job title, and half again for process skills. A list of alternatives ("React, Angular or Vue") is no gap when he has
one of them and one gap when he has none. The employer's own product is ignored (GitLab at GitLab). The `+ 1` keeps a
thin match from reading as 100 %.

Fit is an input, not a column: a fraction of term matches says little on its own ("Patient Support Specialist" once
showed 71 %), so it only counts together with how many tools matched and what the role is.

## 6. Score and Exp (`scanner/rating.py`)

**Exp**: 1 = asks for under a year, or says new grad / junior / intern (a title that says so makes a "1-2 years"
posting a 1); 2 = 1-2 years, or no number; 3 = 2 years.

**Score** = role + skills + level, 1 to 5:

| Part | Points |
|---|---|
| role | core 2, adjacent 1, generic 0 (1 when skills = 2: the description is plainly his work) |
| skills | 2: 4+ tools matched and Fit >= 60 %. 1: 2+ tools and Fit >= 40 %; or a core title whose description names at most 2 tech terms (no stack stated, so nothing to hold against it). 0 otherwise |
| level | 1 when Exp = 1 |

Caps: Exp 3, no description, or a talent pool: at most 2. P.Eng required: 1. An intern / co-op role: at most 3 unless
it says graduates may apply.

Checked against a week of real postings (1,628 candidates, 2026-11-15): 237 kept, down from 383 under the old rules,
the difference being generic titles whose descriptions named none or one of his tools; of the jobs he had applied to
that week and that were in the set, all but one scored 3 or more, most 4 or 5. `reports/feedback.md` keeps checking:
it counts reviewed and picked rows per Score, and the pick rate should rise with the Score.

## 7. Notion

Feed columns: Name, Company, Score, Exp, Province (multi-select), Posted, Link, JD, and the owner's A / I / done-del.
The scanner validates the schema before scanning, never writes the three checkboxes, writes the best jobs first
(Score, then Exp, then newest) up to `notion.daily_cap`, and reads one long JD back to verify it. Rows with `done-del`
ticked are counted in `state/feedback.json` and trashed at the start of the next run; `seen.json` keeps them from
returning. Notion IDs and the token never appear in logs or the repo.

## 8. Dedupe

Canonical URL first (tracking parameters dropped, ATS job ids kept), then same city + similar company + title token
Jaccard >= 0.8. An ATS row wins over a list or job-site row; province tags are merged. A Workday "N Locations" row is
keyed by tenant + requisition id until its real location is read.

## 9. Schedule

Slots at 15:00 and 23:00 UTC (08:00 and 16:00 in BC, UTC-7 all year). The workflow's cron fires every two hours;
`scanner/schedule.py` turns a trigger into "wait for the next slot" (when it is at most 5.5 h away and not yet
scanned), "run now" (a slot missed by up to 4 h) or "nothing to do". The scan job is serialized, checks
`state/last_slot` again after its checkout, and records the slot only after a scan that worked, so a failed scan is
retried by the next trigger.

## 10. Rules of the house

- Read only what a site publishes to any visitor; honour robots.txt, crawl delays and bot challenges.
- Packages pinned by hash; actions pinned by commit.
- No personal data, Notion ID or token in the repo or the logs.
