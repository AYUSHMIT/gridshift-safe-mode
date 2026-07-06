"""Analyze multi-window SWF validation results."""

from __future__ import annotations

import argparse
import csv
import os
from collections import defaultdict
from pathlib import Path
from statistics import mean

from experiments.metrics import write_rows_csv
from experiments.run_trace_compare import RESULTS_DIR


POLICIES = {"none", "freeze", "directional"}
METRICS = ["overload", "completion_rate", "sla_violation_rate", "migrations"]


def _read_rows(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _group_by_window_seed(rows: list[dict[str, str]]) -> dict[tuple[str, str], dict[str, dict[str, str]]]:
    grouped: dict[tuple[str, str], dict[str, dict[str, str]]] = defaultdict(dict)
    for index, row in enumerate(rows, start=2):
        policy = row.get("policy")
        if policy not in POLICIES:
            raise ValueError(f"row {index}: unexpected policy {policy!r}")
        key = (row["window_id"], row["seed"])
        if policy in grouped[key]:
            raise ValueError(f"duplicate row for key={key}, policy={policy}")
        grouped[key][policy] = row

    missing = []
    for key, policies in grouped.items():
        missing_policies = sorted(POLICIES - set(policies))
        if missing_policies:
            missing.append((key, missing_policies))
    if missing:
        raise ValueError(f"missing policy rows for paired SWF analysis: {missing}")
    return grouped


def _paired_rows(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    grouped = _group_by_window_seed(rows)
    paired = []
    for key in sorted(grouped, key=lambda item: (int(item[0]), int(item[1]))):
        window_id, seed = key
        policies = grouped[key]
        directional = policies["directional"]
        freeze = policies["freeze"]
        none = policies["none"]
        out: dict[str, object] = {
            "window_id": int(window_id),
            "seed": int(seed),
            "swf_start_tick": int(directional["swf_start_tick"]),
            "swf_window_length": int(directional["swf_window_length"]),
        }
        for metric in METRICS:
            out[f"directional_minus_freeze_{metric}"] = (
                float(directional[metric]) - float(freeze[metric])
            )
            out[f"directional_minus_none_{metric}"] = (
                float(directional[metric]) - float(none[metric])
            )
        paired.append(out)
    return paired


def _window_summary(paired_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[int, list[dict[str, object]]] = defaultdict(list)
    for row in paired_rows:
        grouped[int(row["window_id"])].append(row)

    summaries = []
    for window_id in sorted(grouped):
        rows = grouped[window_id]
        out: dict[str, object] = {
            "window_id": window_id,
            "swf_start_tick": rows[0]["swf_start_tick"],
            "swf_window_length": rows[0]["swf_window_length"],
            "n": len(rows),
        }
        for metric in METRICS:
            out[f"directional_minus_freeze_{metric}_mean"] = mean(
                float(row[f"directional_minus_freeze_{metric}"]) for row in rows
            )
            out[f"directional_minus_none_{metric}_mean"] = mean(
                float(row[f"directional_minus_none_{metric}"]) for row in rows
            )
        summaries.append(out)
    return summaries


def _count_windows(summaries: list[dict[str, object]], key: str, predicate) -> int:
    return sum(1 for row in summaries if predicate(float(row[key])))


def _write_markdown(path: str | Path, summaries: list[dict[str, object]]) -> None:
    directional_completion_gt_freeze = _count_windows(
        summaries,
        "directional_minus_freeze_completion_rate_mean",
        lambda value: value > 0.0,
    )
    freeze_overload_lt_directional = _count_windows(
        summaries,
        "directional_minus_freeze_overload_mean",
        lambda value: value > 0.0,
    )
    directional_overload_lt_none = _count_windows(
        summaries,
        "directional_minus_none_overload_mean",
        lambda value: value < 0.0,
    )
    directional_completion_gt_none = _count_windows(
        summaries,
        "directional_minus_none_completion_rate_mean",
        lambda value: value > 0.0,
    )

    lines = [
        "# SWF Multi-Window Validation Summary",
        "",
        "This is a multi-window SWF validation over aligned SDSC BLUE workload ",
        "windows. It is not a universal HPC claim, not power-trace replay, and ",
        "not a policy-dominance claim unless every paired condition supports it.",
        "",
        "## Window Counts",
        "",
        f"- windows where directional completion > freeze: {directional_completion_gt_freeze}",
        f"- windows where freeze overload < directional: {freeze_overload_lt_directional}",
        f"- windows where directional overload < none: {directional_overload_lt_none}",
        f"- windows where directional completion > none: {directional_completion_gt_none}",
        "",
        "## Per-Window Mean Deltas",
        "",
        "| Window | SWF start tick | n | Delta completion dir-freeze | Delta overload dir-freeze | Delta completion dir-none | Delta overload dir-none |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summaries:
        lines.append(
            "| {window_id} | {start} | {n} | {dcf:.6g} | {dof:.6g} | {dcn:.6g} | {don:.6g} |".format(
                window_id=row["window_id"],
                start=row["swf_start_tick"],
                n=row["n"],
                dcf=float(row["directional_minus_freeze_completion_rate_mean"]),
                dof=float(row["directional_minus_freeze_overload_mean"]),
                dcn=float(row["directional_minus_none_completion_rate_mean"]),
                don=float(row["directional_minus_none_overload_mean"]),
            )
        )
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze SWF multi-window validation.")
    parser.add_argument(
        "--input",
        default=os.path.join(RESULTS_DIR, "swf_multiwindow_raw.csv"),
        help="Raw multi-window SWF CSV.",
    )
    parser.add_argument(
        "--paired-output",
        default=os.path.join(RESULTS_DIR, "swf_multiwindow_paired.csv"),
        help="Output paired delta CSV.",
    )
    parser.add_argument(
        "--summary-output",
        default=os.path.join(RESULTS_DIR, "swf_multiwindow_summary.csv"),
        help="Output per-window summary CSV.",
    )
    parser.add_argument(
        "--markdown-output",
        default=os.path.join(RESULTS_DIR, "swf_multiwindow_summary.md"),
        help="Output Markdown summary.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    raw_rows = _read_rows(args.input)
    paired = _paired_rows(raw_rows)
    summaries = _window_summary(paired)
    write_rows_csv(args.paired_output, paired)
    write_rows_csv(args.summary_output, summaries)
    _write_markdown(args.markdown_output, summaries)
    print("wrote:", args.paired_output)
    print("wrote:", args.summary_output)
    print("wrote:", args.markdown_output)
    print("paired_rows:", len(paired))
    print("windows:", len(summaries))


if __name__ == "__main__":
    main()
