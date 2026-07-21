# One-off backfill: iteration 1's without_a2ui condition failed entirely
# (backend restart failed 3x in a row). Re-runs just that condition to a
# temp results dir, then merges the fresh records into results/run1/
# raw_results.json (replacing the old all-error without_a2ui rows) and
# regenerates run1's summary xlsx/csv.
#
# Run with: uv run --with playwright --with psutil python experiment/backfill_run1_without_a2ui.py

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))
from lib import xlsx_io  # noqa: E402
from run_benchmark import run_once  # noqa: E402

EXPERIMENT_DIR = Path(__file__).parent


def main() -> None:
    with open(EXPERIMENT_DIR / "config.yaml", "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    out_cfg = cfg["output"]
    bench_cfg = cfg["benchmark"]

    questions = xlsx_io.load_test_cases(
        EXPERIMENT_DIR / bench_cfg["test_cases_xlsx"],
        sheet_name=bench_cfg["sheet_name"],
        question_column=bench_cfg["question_column"],
    )
    without_a2ui_condition = [c for c in bench_cfg["conditions"] if c["name"] == "without_a2ui"]
    assert without_a2ui_condition, "without_a2ui condition not found in config.yaml"

    backfill_dir = EXPERIMENT_DIR / out_cfg["results_dir"] / "run1_without_a2ui_backfill"
    print("Running without_a2ui condition for iteration 1 backfill...")
    backfill_results = run_once(cfg, questions, without_a2ui_condition, backfill_dir, out_cfg)

    run1_dir = EXPERIMENT_DIR / out_cfg["results_dir"] / "run1"
    run1_json_path = run1_dir / out_cfg["raw_json"]
    existing = json.loads(run1_json_path.read_text(encoding="utf-8"))

    kept = [r for r in existing if r["condition"] != "without_a2ui"]
    merged = kept + backfill_results
    merged.sort(key=lambda r: (r["condition"], r["test_index"]))

    run1_json_path.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    xlsx_io.write_summary_xlsx(merged, run1_dir / out_cfg["summary_xlsx"])
    print(f"Merged backfill into {run1_json_path} and regenerated {run1_dir / out_cfg['summary_xlsx']}")

    with_total = sum(r["total_tokens"] for r in merged if r["condition"] == "with_a2ui")
    without_total = sum(r["total_tokens"] for r in merged if r["condition"] == "without_a2ui")
    print(f"run1 totals after backfill - with_a2ui: {with_total}, without_a2ui: {without_total}")


if __name__ == "__main__":
    main()
