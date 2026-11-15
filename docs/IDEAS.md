# Getting hired: what lockin does, what you switch on, what comes next

The goal is interviews, not a big Feed. Every idea here is judged by one question: does it put a good application in
front of a person sooner?

## 1. Switch these on (README, "Keys and secrets", has the click-by-click steps)

| Secret | What it turns on | Cost |
|---|---|---|
| `BRAVE_SEARCH_API_KEY` | **Site Search**: the recruiter's Boolean trick (`site:jobs.ashbyhq.com ("software engineer" OR ...) (Vancouver OR Canada)`) on 9 ATS sites, 15 searches a run, past week only | Free plan: $5 credit a month (~1,000 searches) |
| `SERPAPI_KEY` | **Google Jobs** (Google's job panel: LinkedIn, Indeed, Glassdoor, company sites, each with its full description) and Site Search through Google's own results when there is no Brave key | free plan, 250 searches a month |

Each one is optional: without it the scan still runs and prints "skipped" for that source.

## 2. Already in place

- Two scans a day at 08:00 and 16:00 BC time, on time (GitHub's own scheduler ran them 3-6 hours late; see README
  "Schedule"), a 7-day window, best jobs first.
- Sources: a dataset of ~1.4 M ATS postings refreshed daily (Feashliaa), 220+ employer boards read live (banks, telcos,
  BC Crown corporations, cities, Vancouver tech), Jobhive (the hiring systems lockin cannot read itself), Job Bank (with
  CivicJobs.ca municipal postings), BC Public Service, YC, Built In Vancouver, HN "Who is hiring", Jobicy, Simplify,
  Bay Street Oracle, Google Jobs and Site Search. The scanner adds new employer boards by itself.
- **Score 1-5** on every row (role + skills + level) and **Exp 1-3** (the experience asked for), so the Feed sorts
  into "apply today" first. Nothing asking for more than 2 years gets in.
- **Province** tags (BC, Remote, Alberta, Ontario, Canada; a job can carry two). Jobs only in other provinces are left out.
- **Internships and co-ops** stay unless they require current enrollment; they score 3 at most unless they say
  graduates may apply.
- Generic titles (Analyst, Specialist, Engineer) are kept only when the description names your tools, so the Feed is
  not half finance and operations jobs.
- Jobs you already applied to never come back (`state/seen.json` and the encrypted applied list).

## 3. How to use the Feed to get interviews

1. **Apply the same day.** Recruiters read applicant lists newest first and stop when they have enough to interview.
   Two scans a day exist so you can be in that first batch: do the 5s and 4s of each scan before the next one.
2. **Sort by Score, then Exp, then Posted.** Ten careful applications a day beat 50 blind ones.
3. **Write to a person.** After applying, find who leads the team on LinkedIn (a 2nd-degree connection is best: ask the
   mutual contact for an intro) and send a 3-line note: the role, one thing you built that matches the JD, a link.
4. **Ask for referrals at companies that keep showing up.**
5. **Use the gap report.** `reports/gaps.md` lists the tech the week's postings ask for that your skills list does not
   have. One small project with the top gap unlocks dozens of postings and raises their Score.
6. **Check the Score against yourself.** `reports/feedback.md` shows your pick rate per Score. If you keep picking 2s
   or skipping 5s, the rules in `scanner/rating.py` are wrong for you: say so and they get changed.

## 4. Next things to build

1. **More employer boards.** Name companies you would love to work for and they go on the watchlist.
2. **Keep the applied list fresh by itself**: today `state/applied.json.enc` is rebuilt by hand from the applications
   database.

## 5. Beyond job boards

- **Vancouver tech meetups and events** (VanJS, Vancouver Python, Women Who Code, BC Tech events, hackathons): most
  first jobs come through a person met there.
- **Programs that pay employers to hire graduates**: TECHNATION Career Ready (wage subsidy for placements). Mention
  eligibility in cover letters when it applies; a subsidy makes a junior hire cheaper.
- **Staffing and contract agencies** that place juniors: a contract role is a paid first line on the resume.
- **Public sector**: BC Public Service, cities, health authorities and Crown corporations hire juniors steadily (already
  on the watchlist).

## Not done, and why

- **Running Google's search page from a script.** Searching by hand is fine. A program is different: google.com/robots.txt
  says `Disallow: /search` to every automated client, Google's terms forbid automated access against those instructions,
  and Google typically answers a script from a cloud server with a CAPTCHA. lockin will not dodge
  that. Brave's and SerpApi's APIs are built to be called by programs, so the same searches go
  through them.
- **Anti-detect browsers** (github.com/topics/antidetect-browser) **and JobSpy-style scrapers**: they work by pretending to
  be a person's browser so a site's bot check lets them through. When a site says no to programs, lockin takes the answer
  and gets the same jobs another way (APIs, open datasets, the employer's own board).
- **Google Alerts and Adzuna**: never switched on, so their readers were removed. Site Search and Google Jobs cover the same ground.
- **zapplyjobs/Canada-Jobs-2027** (Canadian entry-level list, updated every 10 minutes): its links go through Zapply's
  own site and land on its sign-up page, not the posting. Its Greenhouse, Lever and Ashby rows are in Jobhive anyway;
  its Workday rows come from employers the watchlist and the Feashliaa dataset already read.
- **Jobhive's Workday, Google, TikTok, Avature and Pinpoint snapshots**: their Canadian rows carry no posting date, so
  there is no telling a new job from a year-old one.
- **GC Jobs** (the federal public service board): its search runs on server sessions and forms, with no list or feed
  a program can read; federal postings that also go to Job Bank arrive through Job Bank.
- **Ghost-job and scam scores** (several GitHub projects): they score company trust with paid data or an AI call per
  job. lockin keeps to what the posting itself says (the talent pool flag) and to employers' own boards.
- **openroles.today**: a good open dataset, but its robots.txt disallows `/data/` for programs, so it is not read.
- **Reading LinkedIn or Indeed directly**: they refuse automated readers. Their jobs arrive through Google Jobs, and their
  people you look up yourself.
- **The hanzili Canada new-grad list**: empty upstream ("No roles currently"), so its reader was removed.
- **Pay, recruiter and hiring-manager columns, tags and Fit %**: removed from the Feed; Score, Exp and Province carry
  what is needed to decide.
