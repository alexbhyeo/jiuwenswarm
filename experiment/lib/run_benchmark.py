# A2UI token-usage benchmark: runs every question in a2ui_benchmark.xlsx against
# the jiuwenswarm web UI once with A2UI enabled and once with it disabled,
# recording token usage (broken down by call_purpose: main / a2ui_repair /
# a2ui_retry_plain) for each run. See README.md for usage.
#
# Run with: uv run --with playwright --with psutil python experiment/run_benchmark.py

from __future__ import annotations

import argparse
import functools
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

print = functools.partial(print, flush=True)  # noqa: A001 - force real-time output when redirected to a file

sys.path.insert(0, str(Path(__file__).parent))
from lib import log_parser, process_manager, xlsx_io  # noqa: E402
from lib.browser_client import new_page, run_single_query, sync_playwright  # noqa: E402

EXPERIMENT_DIR = Path(__file__).parent


def load_config() -> dict:
    with open(EXPERIMENT_DIR / "config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def run_condition(
    cfg: dict,
    condition: dict,
    questions: list[str],
    results: list[dict],
    raw_json_path: Path,
) -> None:
    server_cfg = cfg["server"]
    bench_cfg = cfg["benchmark"]
    condition_name = condition["name"]

    print(f"\n=== Condition: {condition_name} (a2ui_enabled={condition['a2ui_enabled']}) ===")
    print("Restarting jiuwenswarm backend...")
    try:
        process_manager.restart_with_a2ui(
            server_cfg["repo_root"],
            a2ui_enabled=condition["a2ui_enabled"],
            base_url=server_cfg["base_url"],
            startup_timeout_seconds=server_cfg["startup_timeout_seconds"],
        )
    except Exception as exc:  # noqa: BLE001
        # A persistently-failing restart (retries already exhausted inside
        # restart_with_a2ui) must not abort the whole multi-hour run - record
        # every question in this condition as errored and let the caller move
        # on to the next condition/iteration.
        print(f"Backend failed to restart for this condition, skipping it: {exc}")
        for idx, question in enumerate(questions, start=1):
            results.append({
                "test_index": idx,
                "condition": condition_name,
                "question": question,
                "status": "error",
                "duration_seconds": 0.0,
                "total_tokens": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "usage_by_purpose": {},
                "error": f"backend restart failed: {exc}",
            })
        raw_json_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        return
    print("Backend healthy.")

    log_path = server_cfg["agent_log_path"]

    with sync_playwright() as playwright:
        for idx, question in enumerate(questions, start=1):
            print(f"[{condition_name}] {idx}/{len(questions)}: {question[:70]}...")
            start_line = log_parser.tail_line_count(log_path)

            browser = None
            try:
                browser, page = new_page(playwright, server_cfg["base_url"])
                query_result = run_single_query(
                    page,
                    question,
                    log_path=log_path,
                    log_start_line=start_line,
                    per_test_timeout_seconds=bench_cfg["per_test_timeout_seconds"],
                    post_complete_grace_seconds=bench_cfg["post_complete_grace_seconds"],
                )
            except Exception as exc:  # noqa: BLE001
                query_result = None
                error = str(exc)
            else:
                error = query_result.error
            finally:
                if browser is not None:
                    try:
                        browser.close()
                    except Exception:  # noqa: BLE001
                        pass

            if query_result is None:
                usage = log_parser.WindowUsage()
                status, duration = "error", 0.0
            else:
                window_start = datetime.fromtimestamp(query_result.start_time)
                window_end = datetime.fromtimestamp(query_result.end_time)
                usage = log_parser.parse_usage_window(
                    log_path,
                    start_line=start_line,
                    window_start=window_start,
                    window_end=window_end,
                )
                status, duration = query_result.status, query_result.duration_seconds

            record = {
                "test_index": idx,
                "condition": condition_name,
                "question": question,
                "status": status,
                "duration_seconds": duration,
                "total_tokens": usage.total_tokens,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "usage_by_purpose": usage.as_dict(),
                "error": error,
            }
            results.append(record)
            print(
                f"    -> status={status} duration={duration:.1f}s "
                f"total_tokens={usage.total_tokens} purposes={list(usage.by_purpose.keys())}"
            )

            # Checkpoint after every test so a crash mid-run doesn't lose progress.
            raw_json_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")


def run_once(cfg: dict, questions: list[str], conditions: list[dict], results_dir: Path, out_cfg: dict) -> list[dict]:
    results_dir.mkdir(parents=True, exist_ok=True)
    raw_json_path = results_dir / out_cfg["raw_json"]

    results: list[dict] = []
    started_at = time.time()
    for condition in conditions:
        run_condition(cfg, condition, questions, results, raw_json_path)

    elapsed = time.time() - started_at
    print(f"\nRun complete in {elapsed / 60:.1f} minutes.")

    xlsx_io.write_summary_xlsx(results, results_dir / out_cfg["summary_xlsx"])
    print(f"Wrote {results_dir / out_cfg['summary_xlsx']}")

    import csv

    csv_path = results_dir / out_cfg["summary_csv"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["test_index", "condition", "question", "status", "duration_seconds", "total_tokens"])
        for row in results:
            writer.writerow([
                row["test_index"], row["condition"], row["question"],
                row["status"], round(row["duration_seconds"], 1), row["total_tokens"],
            ])
    print(f"Wrote {csv_path}")

    with_totals = sum(r["total_tokens"] for r in results if r["condition"] == "with_a2ui")
    without_totals = sum(r["total_tokens"] for r in results if r["condition"] == "without_a2ui")
    print(f"Total tokens - with_a2ui: {with_totals}, without_a2ui: {without_totals}")
    if without_totals:
        print(f"A2UI overhead: {with_totals - without_totals} tokens "
              f"({(with_totals - without_totals) / without_totals * 100:.1f}%)")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="A2UI token-usage benchmark runner")
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Only run the first N test cases (per condition). Useful for smoke-testing the pipeline.",
    )
    parser.add_argument(
        "--condition", type=str, default=None, choices=["with_a2ui", "without_a2ui"],
        help="Only run this single condition instead of all configured conditions.",
    )
    parser.add_argument(
        "--iterations", type=int, default=1,
        help="Repeat the full benchmark this many times, for variance/reliability analysis across trials. "
             "Each iteration writes to results/run<N>/ instead of results/ directly.",
    )
    parser.add_argument(
        "--start-iteration", type=int, default=1,
        help="First iteration number to run (for resuming a multi-iteration run that was interrupted).",
    )
    args = parser.parse_args()

    cfg = load_config()
    out_cfg = cfg["output"]
    bench_cfg = cfg["benchmark"]
    base_results_dir = EXPERIMENT_DIR / out_cfg["results_dir"]

    questions = xlsx_io.load_test_cases(
        EXPERIMENT_DIR / bench_cfg["test_cases_xlsx"],
        sheet_name=bench_cfg["sheet_name"],
        question_column=bench_cfg["question_column"],
    )
    if args.limit is not None:
        questions = questions[: args.limit]
    print(f"Loaded {len(questions)} test cases from {bench_cfg['test_cases_xlsx']}")

    conditions = bench_cfg["conditions"]
    if args.condition is not None:
        conditions = [c for c in conditions if c["name"] == args.condition]

    if args.iterations == 1 and args.start_iteration == 1:
        run_once(cfg, questions, conditions, base_results_dir, out_cfg)
        return

    overall_start = time.time()
    last_iteration = args.start_iteration + args.iterations - 1
    for i in range(args.start_iteration, last_iteration + 1):
        print(f"\n{'#' * 70}\n# ITERATION {i} / {last_iteration}\n{'#' * 70}")
        run_once(cfg, questions, conditions, base_results_dir / f"run{i}", out_cfg)
    print(f"\nAll {args.iterations} iterations complete in {(time.time() - overall_start) / 60:.1f} minutes.")
    print(f"Per-iteration results in {base_results_dir}/run<N>/. "
          f"Run compile_multi_run.py to aggregate into the variance CSV.")


if __name__ == "__main__":
    main()
