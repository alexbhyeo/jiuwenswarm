# Aggregates results/run1..runN/raw_results.json (produced by
# `run_benchmark.py --iterations N`) into two variance CSVs, one per
# condition: for each test sample, input/output/total tokens per trial, plus
# the mean and standard deviation of total tokens across trials.
#
# Run with: uv run python experiment/compile_multi_run.py [--iterations N]

from __future__ import annotations

import argparse
import csv
import json
import statistics
from pathlib import Path

EXPERIMENT_DIR = Path(__file__).parent
RESULTS_DIR = EXPERIMENT_DIR / "results"


def load_iteration(run_dir: Path) -> list[dict]:
    raw_path = run_dir / "raw_results.json"
    if not raw_path.exists():
        return []
    return json.loads(raw_path.read_text(encoding="utf-8"))


def compile_condition(all_iterations: list[list[dict]], condition_name: str, out_path: Path) -> None:
    # by_index[test_index] = {"question": ..., "trials": [record_or_None, ...]}
    by_index: dict[int, dict] = {}
    for records in all_iterations:
        for r in records:
            if r["condition"] != condition_name:
                continue
            entry = by_index.setdefault(r["test_index"], {"question": r["question"], "trials": []})
            entry["trials"].append(r)

    n = len(all_iterations)
    header = ["test_index", "question"]
    header += [f"input_tokens_exp{i}" for i in range(1, n + 1)]
    header += [f"output_tokens_exp{i}" for i in range(1, n + 1)]
    header += [f"total_tokens_exp{i}" for i in range(1, n + 1)]
    header += ["mean_total_tokens", "stdev_total_tokens"]
    # Not part of the requested layout, appended at the end: status per trial,
    # so a 0-token timeout/error isn't silently indistinguishable from a
    # genuinely small real response when reading the mean/stdev.
    header += [f"status_exp{i}" for i in range(1, n + 1)]

    rows = []
    for idx in sorted(by_index):
        entry = by_index[idx]
        trials = entry["trials"]
        # Trials are appended in iteration order (all_iterations is iterated
        # in order), so position i corresponds to run i+1 as long as every
        # iteration produced a record for this test_index (true unless a
        # whole iteration crashed before reaching it).
        input_tokens = [t.get("input_tokens", 0) for t in trials]
        output_tokens = [t.get("output_tokens", 0) for t in trials]
        total_tokens = [t.get("total_tokens", 0) for t in trials]
        statuses = [t.get("status", "missing") for t in trials]

        while len(input_tokens) < n:
            input_tokens.append("")
            output_tokens.append("")
            total_tokens.append("")
            statuses.append("missing")

        numeric_totals = [t for t in total_tokens if isinstance(t, (int, float))]
        mean_total = round(statistics.mean(numeric_totals), 1) if numeric_totals else ""
        stdev_total = round(statistics.stdev(numeric_totals), 1) if len(numeric_totals) > 1 else ""

        rows.append([idx, entry["question"]] + input_tokens + output_tokens + total_tokens
                     + [mean_total, stdev_total] + statuses)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)
    print(f"Wrote {out_path} ({len(rows)} samples x {n} trials)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Compile multi-iteration benchmark results into variance CSVs")
    parser.add_argument("--iterations", type=int, default=5, help="Number of run<N>/ folders to read (default 5)")
    args = parser.parse_args()

    all_iterations = []
    for i in range(1, args.iterations + 1):
        run_dir = RESULTS_DIR / f"run{i}"
        records = load_iteration(run_dir)
        if not records:
            print(f"WARNING: {run_dir}/raw_results.json missing or empty - trial {i} will show as blank/missing")
        all_iterations.append(records)

    compile_condition(all_iterations, "with_a2ui", RESULTS_DIR / "variance_with_a2ui.csv")
    compile_condition(all_iterations, "without_a2ui", RESULTS_DIR / "variance_without_a2ui.csv")


if __name__ == "__main__":
    main()
