# lockin

Zero-LLM, zero-cost daily scanner for **new-grad / entry-level tech jobs in Canada**.
See [SPEC.md](SPEC.md) for the full design. Status: **Phase 1** (dry-run candidate list).

## Usage

```bash
pip install -r requirements-dev.txt
python -m scanner run --since 7 --dry-run   # writes reports/latest.md, prints stage counts
pytest
```

Everything tunable lives in `config.yaml`.

## Credits

- Job data: [Feashliaa/job-board-data](https://github.com/Feashliaa/job-board-data)
  (code MIT; company lists CC BY-NC 4.0, so non-commercial use only).
- New-grad listings: [SimplifyJobs/New-Grad-Positions](https://github.com/SimplifyJobs/New-Grad-Positions).
