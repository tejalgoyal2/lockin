# Getting hired: what lockin does, what you switch on, what comes next

The goal is interviews, not a big Feed. Every idea here is judged by one question: does it put a good application in
front of a person sooner?

## 1. Switch these on (10 minutes, all free)

| What | Why it matters | How |
|---|---|---|
| **Google for Jobs** (`SERPAPI_KEY`) | Google's job panel gathers LinkedIn, Indeed, Glassdoor, Talent.com and employers' own sites: the jobs lockin cannot read itself. Each comes with its full description | serpapi.com, sign up (free plan, 250 searches a month), copy the API key, add the repo secret `SERPAPI_KEY` |
| **Google Alerts on the ATS sites** (`GOOGLE_ALERTS_FEEDS`) | The "Google dork" trick from the videos (`site:jobs.lever.co Vancouver`), run by Google for you as soon as a page is indexed. Lever is the one big ATS the open datasets miss | README, section "Google Alerts": 8 alerts, RSS delivery, paste the feed links into one secret |
| **Adzuna** (`ADZUNA_APP_ID`, `ADZUNA_APP_KEY`) | A Canadian job search engine: thousands of employers' postings, 20 tech searches a run | developer.adzuna.com, register an app, add the two secrets |

Nothing else changes: the next scan picks them up. Without them the scan still runs; each prints "skipped".

## 2. Already in place

- 7-day window, two runs a day (8 am and 4 pm Pacific), up to 500 new rows a run, best Score first.
- Sources: a dataset of ~1.4 M ATS postings refreshed daily (Feashliaa: Workday, iCIMS, Greenhouse, Lever, Ashby, BambooHR, Paylocity), 180+
  employer boards on the watchlist (banks, telcos, BC Crown corporations, cities), Job Bank (with CivicJobs.ca municipal
  postings), BC Public Service, YC, Built In Vancouver, HN "Who is hiring", Jobicy, Simplify new-grad, Bay Street Oracle.
- The scanner finds new employer boards by itself (`watchlist.auto.yaml`), drops nothing for a missing description, and
  flags rather than drops the "2+ years" postings.

## 3. How to use the Feed to get interviews

1. **Apply the same day.** Recruiters read applicant lists newest first and stop when they have enough to interview. A
   University of Chicago study of job-board data found about 39% of all applications go to postings less than 48 hours
   old. Two runs a day exist so you can be in that first batch: check the Feed at 9 am and 5 pm.
2. **Sort by Score, apply to the top 10 every day** before reading anything else. Ten tailored applications a day beat 50
   blind ones.
3. **Every application gets a person.** After applying, find the hiring manager or a team member on LinkedIn (company +
   team name + "manager") and send a 3-line note: the role, one concrete thing you built that matches the JD, a link.
   Direct notes and referrals get answered far more often than cold applications.
4. **Ask for referrals at companies with several Feed rows.** If a company shows up 3+ times, someone you know (alumni,
   classmates, bootcamp, meetup contacts) probably works there or knows someone who does. A referral moves you to the top
   of the pile.
5. **Use the gap report.** `reports/gaps.md` lists the skills the week's job descriptions ask for that your skills list
   does not have (this week: Java, SAP, Salesforce, microservices). One small weekend project with the top gap
   unlocks dozens of postings.
6. **Follow up after 7 days** on applications with no answer: a short note to the recruiter or hiring manager.

## 4. Next things to build (ranked by how much they help)

1. **Daily shortlist.** After each run, a "Top 10 today" view or digest: highest Score, posted < 48 h, BC or remote first.
2. **One-click tailored resume and cover letter.** For a row you tick Interested on, draft the tailored resume and cover
   letter from the JD (the resume-tailor and cover-letter skills already exist; this connects them to the Feed).
3. **Hiring-manager finder.** For each top row, the team name from the JD and a ready search link for LinkedIn people at
   that company and team, so the note in 3.3 takes one minute.
4. **Follow-up reminders.** In the Job Applications database: a date column filled 7 days after Apply, and a view of
   what is due.
5. **"Posted < 24 h" signal** in the Feed so the freshest jobs stand out at a glance.
6. **Response tracking.** Which sources, titles and companies answer you: after a month the scanner can rank sources by
   interviews, not just postings.
7. **More employer boards.** Give a list of 20 to 30 companies you would love to work for (Vancouver startups, BC tech,
   public sector); each goes on the watchlist so their new postings arrive within hours.

## 5. Beyond job boards

- **Vancouver tech meetups and events** (VanJS, Vancouver Python, Women Who Code, BC Tech events, hackathons): most
  first jobs come through a person met there.
- **Programs that pay employers to hire graduates**: TECHNATION Career Ready (wage subsidy for placements), and Canada
  Summer Jobs / Youth Employment and Skills Strategy employers. Mention eligibility in cover letters when it applies; a
  subsidy makes a junior hire cheaper.
- **Staffing and contract agencies** that place juniors (they show up in the Feed as "Upstaff", "Apex Systems" ...): a
  contract role is a paid first line on the resume.
- **Public sector**: BC Public Service, cities, health authorities and Crown corporations hire juniors steadily and post
  on their own sites (already on the watchlist).

## Not done, and why

- **Scraping Google, LinkedIn or Indeed directly**: they block automated readers. lockin reads them through Google for Jobs
  (SerpApi) instead, which is permitted and pays for the searches.
- **Paid aggregators** (beyond the free tiers above): not needed yet. Add them only if the Feed runs short of good jobs.
- **The hanzili Canada new-grad list**: it is currently empty upstream ("No roles currently"). The scanner keeps reading
  it and picks it up again if it returns.
