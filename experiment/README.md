# A2UI Token-Usage Benchmark

Measures how many tokens A2UI (interactive form generation) adds on top of a
plain-text conversation, using the 30 test queries in `a2ui_benchmark.xlsx`.

## What it does

For each of the two conditions (`with_a2ui`, `without_a2ui`):

1. Restarts the jiuwenswarm backend with `JIUWENSWARM_A2UI_ENABLED` set
   accordingly (env var overrides `config.yaml`'s `a2ui.enabled`).
2. Opens a fresh browser session per test case and sends the query through the
   real web UI (not a raw API call — this exercises the exact same path a user
   hits).
3. Waits for the "处理中" indicator to clear (or the per-test timeout).
4. Parses `agent_server.log` for every `[JiuWenSwarmDeepAdapter] llm_usage
   chunk` line logged in that test's time window, aggregated by
   `call_purpose`:
   - `main` — the primary response.
   - `a2ui_repair` — a retry the finalizer makes when the model's first
     `<a2ui-json>` output fails schema validation.
   - `a2ui_retry_plain` — a full plain-text retry when repair itself gives up.

   (This is the tagging added in `jiuwenswarm/server/runtime/a2ui/runtime/call_purpose.py`
   and its three call sites in `interface.py`/`interface_deep.py`.)

## Prerequisites

- Nothing else needs to be running — the script restarts the backend itself
  between conditions. If you have a jiuwenswarm instance running that you care
  about, stop it first; this script will kill it.
- Playwright's Chromium browser must be installed once:
  `uv run --with playwright playwright install chromium`

## Running

```bash
cd D:\git_projects\jiuwenswarm

# Smoke test first: 2 questions, both conditions (~a few minutes)
uv run --with playwright --with psutil python experiment/run_benchmark.py --limit 2

# Full run: all 30 questions x 2 conditions
uv run --with playwright --with psutil python experiment/run_benchmark.py

# Just one condition (e.g. re-run only without_a2ui after a config change)
uv run --with playwright --with psutil python experiment/run_benchmark.py --condition without_a2ui
```

`--with playwright --with psutil` installs those two packages into an ad-hoc
environment for this run only — nothing is added to the project's
`pyproject.toml`. `yaml`/`requests`/`openpyxl` are already present in the
jiuwenswarm venv.

## Output

Written to `results/` (gitignored raw data, keep or discard as you like):

- `raw_results.json` — every test's full record, checkpointed after each test
  case so a crash mid-run doesn't lose progress.
- `benchmark_results.xlsx` — two sheets:
  - `results`: one row per (test case, condition) with token totals broken
    down by `call_purpose`.
  - `comparison`: one row per test case with `with_a2ui` vs `without_a2ui`
    totals side by side and the delta.
- `summary.csv` — flat mirror of the `results` sheet for quick grepping.

## Timeouts and failure handling

`config.yaml`'s `per_test_timeout_seconds` (default 180s) bounds each test —
A2UI generation, repair, or a dispatched `browser_agent` search can
occasionally stall (see project history: a 13-minute silent stall was
observed once under heavy session load). A test that hits the timeout is
recorded with `status=timeout` and a token count of whatever was logged up to
that point; the run continues to the next test rather than hanging
indefinitely.

## Files

```
experiment/
  config.yaml          # server/benchmark/output settings
  run_benchmark.py     # orchestrator
  lib/
    log_parser.py      # parses agent_server.log, aggregates by call_purpose
    process_manager.py # restarts the backend with a given A2UI setting
    browser_client.py  # drives one query through the web UI via Playwright
    xlsx_io.py          # reads test cases, writes the results workbook
  a2ui_benchmark.xlsx  # input test cases (not modified by the script)
  results/             # output (created at runtime)
```
