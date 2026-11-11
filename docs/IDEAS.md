# Getting hired: what lockin does, what you switch on, what comes next

The goal is interviews, not a big Feed. Every idea here is judged by one question: does it put a good application in
front of a person sooner?

## 1. Switch these on (README, "Keys and secrets", has the click-by-click steps)

| Secret | What it turns on | Cost |
|---|---|---|
| `BRAVE_SEARCH_API_KEY` | **Site Search**: the recruiter's Boolean trick (`site:jobs.ashbyhq.com ("software engineer" OR ...) (Vancouver OR Canada)`) on 9 ATS sites, 15 searches a run, past week only | free monthly credit (~1,000 searches); a card is needed to sign up |
| `SERPAPI_KEY` | **Google Jobs** (Google's job panel: LinkedIn, Indeed, Glassdoor, company sites, each with its full description) and Site Search through Google's own results when there is no Brave key | free plan, 250 searches a month |
| `GOOGLE_ALERTS_FEEDS` | **Google Alerts**: Google runs the same `site:` searches for you and lists new pages as they are indexed | free |
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | **Adzuna**: a Canadian job search engine, 20 tech searches a run | free |

Each one is optional: without it the scan still runs and prints "skipped" for that source.

## 2. Already in place

- 7-day window, two runs a day (8 am and 4 pm Pacific), up to 500 new rows a run, best Score first.
- Sources: a dataset of ~1.4 M ATS postings refreshed daily (Feashliaa: Workday, iCIMS, Greenhouse, Lever, Ashby,
  BambooHR, Paylocity), 200+ employer boards on the watchlist (banks, telcos, BC Crown corporations, cities, Vancouver
  tech), Job Bank (with CivicJobs.ca municipal postings), BC Public Service, YC, Built In Vancouver, HN "Who is hiring",
  Jobicy, Simplify new-grad, Bay Street Oracle.
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
- **Reading LinkedIn or Indeed directly**: they refuse automated readers. Their jobs arrive through Google Jobs, and their
  people through the two links you click yourself.
- **The hanzili Canada new-grad list**: empty upstream ("No roles currently"). The scanner keeps reading it in case it
  returns.
