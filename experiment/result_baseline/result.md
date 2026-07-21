# A2UI Token-Usage Benchmark: With vs. Without A2UI

Comparison of `variance_with_a2ui.csv` and `variance_without_a2ui.csv` in this
folder — 30 test cases (`a2ui_benchmark.xlsx`), each run 5 times per
condition (150 trials per condition, 300 total), via the harness in
`experiment/`.

## Token overhead: A2UI costs more, but wildly inconsistently

All figures below are computed from **"ok" trials only** — i.e. trials that
completed without hitting the 180s timeout or erroring. A failed trial's
token count reflects however much was captured before it was cut off, not
the true cost of that turn, so mixing it into an average would be
misleading rather than informative. See "Data quality" below for exactly
how much data this excludes.

| Metric | With A2UI | Without A2UI |
|---|---|---|
| Clean ("ok") trials | 119 / 150 | 99 / 150 |
| Mean total tokens | 108,036 | 75,223 |
| Median total tokens | 75,753 | 28,538 |
| Stdev total tokens | 114,600 | 88,150 |
| Coefficient of variation | 106% | 117% |

- **24 of 29 comparable test cases cost more with A2UI**, 5 cost less.
- Per-sample overhead (each test case's own with/without clean-trial means
  compared): **mean +81.2%, median +66.7%**, stdev of that overhead **113.4%**
  — A2UI is *usually* more expensive, but by a very unpredictable amount
  from one query to the next.
- The gap between the pooled-mean overhead (~44%, from the table above) and
  the median per-sample overhead (66.7%) indicates the effect size is uneven
  across queries, not a flat tax — a handful of large-magnitude cases pull
  the two summaries in different directions.

### Highest overhead (A2UI much more expensive)

All five involve multi-field preference forms (dates, budget, multiple
categories) — the form-generation-plus-validation/repair loop compounds:

| # | Query | With A2UI | Without A2UI | Overhead |
|---|---|---|---|---|
| 24 | Help me buy a bicycle. Ask about my height, riding style, terrain, and budget... | 147,551 | 28,345 | **+421%** |
| 3 | Recommend three hotels in Singapore and compare their prices, ratings... | 248,154 | 58,352 | **+325%** |
| 18 | I'm buying a DSLR camera. Interview me like a salesperson before recommending... | 117,773 | 28,461 | **+314%** |
| 26 | Help me plan a ski trip. Ask about my skiing experience, preferred country... | 81,045 | 28,378 | **+186%** |
| 6 | Find attractions in Kyoto and organize them into a 3-day itinerary. | 192,080 | 70,186 | **+174%** |

### Lowest / negative overhead (A2UI cheaper — rare, but real)

| # | Query | With A2UI | Without A2UI | Overhead |
|---|---|---|---|---|
| 12 | Help me organize a cafe-hopping trip in Singapore. Suggest the most efficient route... | 190,403 | 214,006 | -11% |
| 20 | I'm organizing a company offsite for 30 people. Find suitable venues... | 45,751 | 52,844 | -13% |
| 27 | Find the cheapest MacBook Air on Apple's website, then compare it with offers... | 216,425 | 255,618 | -15% |
| 13 | Find restaurants near Marina Bay Sands that have at least a 4.5-star rating... | 37,778 | 93,254 | **-59%** |
| 9 | Recommend a smartphone for me. Don't suggest anything until you've asked... | 30,211 | 123,731 | **-76%** |

**Interpretation:** A2UI adds overhead when the query needs several
structured preference fields — the form + validation/repair cycle
compounds token cost. It can *save* tokens when the plain-text mode would
otherwise ramble through a lot of exploratory back-and-forth prose to
gather the same information (e.g. #9, #13) that a structured form collects
in one shot.

## Reliability: both conditions are shaky, in different ways

| Status | With A2UI | Without A2UI |
|---|---|---|
| ok | 119 / 150 (79%) | 99 / 150 (66%) |
| timeout | 24 / 150 (16%) | 15 / 150 (10%) |
| error | 7 / 150 (5%) | 36 / 150 (24%) |

Counterintuitively, **A2UI completed cleanly more often**. `without_a2ui`
had far more raw errors — mostly Playwright/browser-level flakiness
(connection resets, page-load timeouts) rather than application failures.
`with_a2ui` timed out more often, consistent with the A2UI repair/retry
stalls investigated earlier in this project (silent LLM-stream stalls
during form generation or repair).

### Least reliable test cases (most combined failures, out of 10 trials each)

| # | Query | Failures |
|---|---|---|
| 13 | Find restaurants near Marina Bay Sands... | **8 / 10** |
| 3 | Recommend three hotels in Singapore... | 7 / 10 |
| 25 | Build me a complete PC for AI development... | 7 / 10 |
| 22 | Search for electric vehicles available in Singapore... | 6 / 10 |
| 27 | Find the cheapest MacBook Air... | 5 / 10 |

Test case #13's comparison above is barely trustworthy — only 2 of 10
total trials succeeded.

## Data quality caveat

Only **84 of 150 possible (test case, trial) pairs (56%)** have a clean
"ok" status on *both* sides in the *same* iteration. The headline "+81%
median overhead" figure is real signal, but any single test case's number
should be checked against its `status_exp1..5` columns in the source CSVs
before being taken at face value — a case with 3+ failures out of 5 trials
per condition is a small, noisy sample.

## Run details

- 5 iterations, both conditions, 30 test cases each = 300 total trials.
- Iteration 1 took ~15 hours (one single test stalled 61 minutes; its
  `without_a2ui` condition failed to restart 3x and was backfilled
  separately). Iterations 2-5 took 49-169 minutes each.
- Source data: `run1/` .. `run5/raw_results.json` (per-iteration), compiled
  into `variance_with_a2ui.csv` / `variance_without_a2ui.csv` via
  `experiment/compile_multi_run.py`.
