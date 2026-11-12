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

- 7-day window, two runs a day (8 am and 4 pm Pacific), up to 500 new rows a run, best Score first.
- Sources: a dataset of ~1.4 M ATS postings refreshed daily (Feashliaa: Workday, iCIMS, Greenhouse, Lever, Ashby,
  BambooHR, Paylocity), 200+ employer boards on the watchlist (banks, telcos, BC Crown corporations, cities, Vancouver
  tech), Job Bank (with CivicJobs.ca municipal postings), BC Public Service, YC, Built In Vancouver, HN "Who is hiring",
  Jobicy, Simplify new-grad, Bay Street Oracle.
- **Jobhive**: ats-scrapers' open dataset (5 M postings, 64 hiring systems, refreshed daily) for the systems lockin cannot
  read itself: Oracle, Eightfold (Microsoft), Dayforce, UKG, Rippling, JazzHR, Breezy, Cornerstone; and every
  Greenhouse, Lever, Ashby, BambooHR and iCIMS board, not only the ones on the watchlist.
- **Pay and Posted columns.** `Pay` shows the range the posting states ("$70,000–$85,000 a year"; Ontario and BC require
  one), the Canadian range when a posting gives a US one too. `Posted` is a real date, so the Feed sorts newest first.
- The scanner finds new employer boards by itself (`watchlist.auto.yaml`), drops nothing for a missing description, and
  flags rather than drops the "2+ years" postings.
- **Two people per job.** Every Feed row has a `Recruiter` and a `Hiring Manager` link: LinkedIn's people search,
  narrowed to the company and the team, closest connections first.

## 3. How to use the Feed to get interviews

1. **Apply the same day.** Recruiters read applicant lists newest first and stop when they have enough to interview. A
   University of Chicago study of job-board data found about 39% of all applications go to postings less than 48 hours
   old. Two runs a day exist so you can be in that first batch.
2. **Sort by Score and work down.** Ten careful applications a day beat 50 blind ones.
3. **Write to the two people.** After applying, open the row's `Hiring Manager` link, pick the person who leads that team
   (a 2nd-degree connection is best: ask the mutual contact for an intro), and send a 3-line note: the role, one thing you
   built that matches the JD, a link. Then the `Recruiter` link: a one-line note that you applied, with the job title.
4. **Ask for referrals at companies with several Feed rows.** If a company shows up 3+ times, the people links will show
   who you know there.
5. **Use the gap report.** `reports/gaps.md` lists the skills the week's job descriptions ask for that your skills list
   does not have (this week: Java, SAP, Salesforce, microservices). One small project with the top gap unlocks dozens of
   postings.

## 4. Next things to build

1. **More employer boards.** Name companies you would love to work for and they go on the watchlist; the scanner also
   keeps adding the busiest boards it meets by itself.
2. **"Posted < 24 h" signal** in the Feed so the freshest jobs stand out at a glance.

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
- **Google Alerts and Adzuna**: switched off by choice (`enabled: false`); the code stays in case they are wanted again.
- **zapplyjobs/Canada-Jobs-2027** (Canadian entry-level list, updated every 10 minutes): its links go through Zapply's
  own site and land on its sign-up page, not the posting. Its Greenhouse, Lever and Ashby rows are in Jobhive anyway;
  its Workday rows come from employers the watchlist and the Feashliaa dataset already read.
- **Jobhive's Workday, Google, TikTok, Avature and Pinpoint snapshots**: their Canadian rows carry no posting date, so
  there is no telling a new job from a year-old one.
- **openroles.today**: a good open dataset, but its robots.txt disallows `/data/` for programs, so it is not read.
- **Reading LinkedIn or Indeed directly**: they refuse automated readers. Their jobs arrive through Google Jobs, and their
  people through the two links you click yourself.
- **The hanzili Canada new-grad list**: empty upstream ("No roles currently"). The scanner keeps reading it in case it
  returns.
