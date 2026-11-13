# Auto-apply ("Chiron"-style agent): research notes, 2026-11-13

The question: would an agent that fills in job applications (stopping before Submit, the person reviews and clicks)
save time on top of what lockin already does?

## Where the time goes now

- `reports/feedback.md`: about 16 jobs picked a week; Workday is about 42 % of the picks.
- lockin already does the finding and scoring; the resume-tailor skill already tailors by selecting real bullets.
- What is left by hand is mostly the form: a Workday account per employer, re-typing work history the resume reader
  got wrong, dropdowns, and the work-authorization / sponsorship / salary questions.
- Estimate: 20-40 minutes an application (Greenhouse / Lever / Ashby 5-10 minutes of form, Workday 20-30), so about
  2.5-4 hours a week of form filling. A well-working agent might save 1.5-3 hours a week; every form still needs a check,
  and sign-ins, codes and CAPTCHAs stay with the person.

## What exists

- **career-ops** (github.com/santifer/career-ops, MIT): runs in Claude Code; its `apply` mode fills a form field by
  field and stops before Submit. The nearest free version of the idea, on the person's own machine.
- **Simplify Copilot** (free browser extension): autofills Workday, Greenhouse, Lever, iCIMS, SmartRecruiters and more;
  the person reviews and submits. Its paid "Autopilot" (submits for you) has mixed reviews.
- Mass auto-appliers (AIHawk, LazyApply, Sonara ...): poor reported results (thousands of applications for a handful of
  interviews, weak evidence) and account bans (AIHawk's author was banned by LinkedIn).

## Where it breaks, and the risks

- Workday: a separate account per employer, email codes, a form set up differently by each employer, sessions that
  expire. Each hiring system needs its own handler, and every form change breaks it.
- Fraud checks: Ashby flags applicants by device / IP / email signals; Workable tags AI-assisted applications;
  Greenhouse sells bot and mass-application detection. A browser on a rented cloud server applies from a datacenter
  address and can get a real applicant marked as fake.
- Terms: LinkedIn bans automation; Workday's site terms forbid automated scripts; bot-created accounts are the riskiest
  part. Filling a form the person opened, in their own browser, is the least risky.
- Privacy: a server holding the resume, address, work-status answers, dozens of employer passwords and maybe mailbox
  access.
- Wrong answers (sponsorship, salary, start date) are costly and every one must be checked.
- Running an LLM through a consumer subscription inside a third-party agent framework can break that subscription's
  terms; an API key is the allowed route (roughly $15-40 a month at this volume, plus a server).

## Recommendation (only part of it)

1. Use a stop-before-Submit autofill extension in your own browser (Simplify Copilot or similar). Never turn on
   auto-submit.
2. Keep a standard-answers sheet (work authorization and sponsorship worded exactly for your status, salary range per
   province, start date, relocation) and a password manager entry per Workday employer.
3. For a high-value posting, career-ops' `apply` mode (or Claude helping in your own browser) to fill the form while you
   watch and submit.
4. Do not build the hosted agent (server, remote browser, bot-made accounts, own dashboard): weeks of work and constant
   upkeep for 1-3 hours a week, with terms, fraud-flag and password risks, and against lockin's own rule of not
   working around sites that say no to programs.
5. A better build, if wanted: a "prep pack" when a Feed row is ticked Apply (tailored resume plus drafted answers ready
   to paste). It never touches employer sites.

What wins interviews is still applying the same day and writing to the hiring manager and recruiter (docs/IDEAS.md §3);
time saved on forms is worth most when it goes there.
