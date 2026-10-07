# lockin

Zero-LLM, zero-cost daily scanner for **new-grad / entry-level tech jobs in Canada**.
See [SPEC.md](SPEC.md) for the full design. Status: **Phase 2** (JD fetch, JD rules, scoring). Notion and Actions come in Phase 3.

## Usage

```bash
pip install -r requirements-dev.txt
python -m scanner run --since 7 --dry-run --sample 10   # phase 1+2: writes reports/latest.md, prints counts
python -m scanner run --since 7 --dry-run --no-jd        # phase 1 only (offline after the data download)
pytest
```

Everything tunable lives in `config.yaml`, `skills.yaml` (your skills) and `gaps.yaml` (common terms you lack).

Phase 2 needs outbound HTTPS to the ATS hosts: `boards-api.greenhouse.io`, `api.lever.co`,
`api.ashbyhq.com`, `*.myworkdayjobs.com`, `*.bamboohr.com`. If they are blocked the run still
completes: affected jobs are kept with the no-JD penalty.

## Credits

- Job data: [Feashliaa/job-board-data](https://github.com/Feashliaa/job-board-data)
  (code MIT; company lists CC BY-NC 4.0, so non-commercial use only).
- New-grad listings: [SimplifyJobs/New-Grad-Positions](https://github.com/SimplifyJobs/New-Grad-Positions).
