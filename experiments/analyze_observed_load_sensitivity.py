"""Summarize observed-load sensitivity ablation results."""

from __future__ import annotations

import argparse
import csv
import os
from collections import defaultdict
from pathlib import Path
from statistics import mean

from experiments.run_trace_compare import RESULTS_DIR


GROUP_FIELDS = [
    "detector_mode",
    "observed_load_noise_std_mw",
    "observed_load_bias_mw",
]

METRICS = [
    "overload",
    "completion_rate",
    "sla_violation_rate",
    "migrations",
    "safe_mode_ticks",
    "bad_node_ticks",
    "mean_mismatch_mw",
]

REQUIRED_COLUMNS = ["seed", *GROUP_FIELDS, *METRICS]


def _read_rows(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _require_columns(rows: list[dict[str, str]], path: str) -> None:
    if not rows:
        raise ValueError(f"{path} has no data rows")
    present = set(rows[0])
    missing = [column for column in REQUIRED_COLUMNS if column not in present]
    if missing:
        raise ValueError(f"{path} is missing required column(s): {', '.join(missing)}")


def _group_means(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, float, float], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        key = (
            row["detector_mode"],
            float(row["observed_load_noise_std_mw"]),
            float(row["observed_load_bias_mw"]),
        )
        grouped[key].append(row)

    out = []
    for key in sorted(grouped, key=lambda item: (item[0], item[1], item[2])):
        detector_mode, noise_std, bias = key
        group_rows = grouped[key]
        result: dict[str, object] = {
            "detector_mode": detector_mode,
            "observed_load_noise_std_mw": noise_std,
            "observed_load_bias_mw": bias,
            "n": len(group_rows),
        }
        for metric in METRICS:
            result[f"{metric}_mean"] = mean(float(row[metric]) for row in group_rows)
        out.append(result)
    return out


def _with_baseline_deltas(grouped_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    baselines = {
        row["detector_mode"]: row
        for row in grouped_rows
        if row["observed_load_noise_std_mw"] == 0.0
        and row["observed_load_bias_mw"] == 0.0
    }
    missing = sorted(
        {
            str(row["detector_mode"])
            for row in grouped_rows
            if row["detector_mode"] not in baselines
        }
    )
    if missing:
        raise ValueError(
            "missing zero-noise/zero-bias baseline for detector mode(s): "
            + ", ".join(missing)
        )

    out = []
    for row in grouped_rows:
        enriched = dict(row)
        baseline = baselines[row["detector_mode"]]
        for metric in METRICS:
            key = f"{metric}_mean"
            enriched[f"delta_{metric}_vs_baseline"] = float(row[key]) - float(
                baseline[key]
            )
        out.append(enriched)
    return out


def _fmt(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


def _markdown_table(rows: list[dict[str, object]]) -> str:
    headers = [
        "Detector",
        "Noise std MW",
        "Bias MW",
        "n",
        "Overload",
        "Delta overload",
        "Completion",
        "Delta completion",
        "SLA viol.",
        "Migrations",
        "Safe-mode ticks",
        "Bad-node ticks",
        "Mismatch MW",
    ]
    lines = [
        "| " + " | ".join(headers) + " |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["detector_mode"]),
                    _fmt(row["observed_load_noise_std_mw"]),
                    _fmt(row["observed_load_bias_mw"]),
                    _fmt(row["n"]),
                    _fmt(row["overload_mean"]),
                    _fmt(row["delta_overload_vs_baseline"]),
                    _fmt(row["completion_rate_mean"]),
                    _fmt(row["delta_completion_rate_vs_baseline"]),
                    _fmt(row["sla_violation_rate_mean"]),
                    _fmt(row["migrations_mean"]),
                    _fmt(row["safe_mode_ticks_mean"]),
                    _fmt(row["bad_node_ticks_mean"]),
                    _fmt(row["mean_mismatch_mw_mean"]),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def _write_markdown(path: str | Path, rows: list[dict[str, object]], source: str) -> None:
    content = f"""# Observed-Load Sensitivity Summary

Input: `{source}`

This is a sensitivity ablation, not a defense for a compromised grid-side
observed-load channel. It compares each noise/bias setting against the
zero-noise, zero-bias baseline for the same detector mode.

Behavior-only and fusion can coincide in this ablation because firmware tamper
is disabled; under that case, both modes depend on the reported-vs-observed load
mismatch. Higher observed-load noise or bias can inflate mismatch and alter
safe-mode behavior, migrations, and downstream workload/grid outcomes.

## Group Means And Baseline Deltas

{_markdown_table(rows)}
"""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(content, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Summarize observed-load sensitivity ablation CSV."
    )
    parser.add_argument(
        "--input",
        default=os.path.join(RESULTS_DIR, "observed_load_sensitivity.csv"),
        help="Observed-load sensitivity CSV.",
    )
    parser.add_argument(
        "--output-md",
        default=os.path.join(RESULTS_DIR, "observed_load_sensitivity_summary.md"),
        help="Markdown summary output path.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    rows = _read_rows(args.input)
    _require_columns(rows, args.input)
    summary_rows = _with_baseline_deltas(_group_means(rows))
    _write_markdown(args.output_md, summary_rows, args.input)

    print("wrote:", args.output_md)
    print("input_rows:", len(rows))
    print("groups:", len(summary_rows))
    print(
        "detector_modes:",
        ",".join(sorted({str(row["detector_mode"]) for row in summary_rows})),
    )


if __name__ == "__main__":
    main()
