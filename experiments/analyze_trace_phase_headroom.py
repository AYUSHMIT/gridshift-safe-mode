"""Paired analysis for the trace phase x compute headroom study."""

from __future__ import annotations

import argparse
import csv
import math
import os
from pathlib import Path
from statistics import mean, stdev

from experiments.run_trace_compare import RESULTS_DIR


METRICS = [
    "overload",
    "completion_rate",
    "sla_violation_rate",
    "migrations",
    "migration_candidates_considered",
    "candidates_with_trusted_feasible_destination",
    "candidates_blocked_insufficient_destination_capacity",
    "migration_feasibility_rate",
]

COMPARISONS = [
    ("directional", "freeze"),
    ("directional", "none"),
    ("freeze", "none"),
]

CORE_METRICS = {
    "overload",
    "completion_rate",
    "sla_violation_rate",
    "migrations",
    "migration_feasibility_rate",
}

# n=10 paired seeds, df=9, two-sided 95% Student-t critical value.
T_CRIT_95_DF9 = 2.262157


def _read_rows(path: str | Path) -> list[dict]:
    with Path(path).open(newline="") as f:
        return list(csv.DictReader(f))


def _paired_rows(rows: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str, str], dict[str, dict]] = {}
    for row in rows:
        key = (row["phase"], row["region_capacity_mw"], row["seed"])
        grouped.setdefault(key, {})[row["policy"]] = row

    missing_groups = []
    for key, policies in grouped.items():
        missing = {"none", "freeze", "directional"} - set(policies)
        if missing:
            missing_groups.append((key, sorted(missing)))

    if missing_groups:
        raise ValueError(f"missing policy rows for paired comparison: {missing_groups}")

    out = []
    phases = sorted({row["phase"] for row in rows})
    capacities = sorted({row["region_capacity_mw"] for row in rows}, key=float)

    for phase in phases:
        for capacity in capacities:
            phase_capacity_keys = [
                key for key in grouped
                if key[0] == phase and key[1] == capacity
            ]

            for policy_a, policy_b in COMPARISONS:
                for metric in METRICS:
                    diffs = []
                    for key in sorted(phase_capacity_keys, key=lambda item: int(item[2])):
                        value_a = float(grouped[key][policy_a][metric])
                        value_b = float(grouped[key][policy_b][metric])
                        diffs.append(value_a - value_b)

                    n = len(diffs)
                    mean_diff = mean(diffs)
                    if n > 1 and stdev(diffs) > 0:
                        sd = stdev(diffs)
                        ci = T_CRIT_95_DF9 * sd / math.sqrt(n)
                        cohen_dz = mean_diff / sd
                    else:
                        ci = 0.0
                        cohen_dz = 0.0

                    out.append(
                        {
                            "phase": phase,
                            "region_capacity_mw": capacity,
                            "comparison": f"{policy_a}-{policy_b}",
                            "metric": metric,
                            "n": n,
                            "mean_diff": mean_diff,
                            "ci95_low": mean_diff - ci,
                            "ci95_high": mean_diff + ci,
                            "crosses_zero": (mean_diff - ci) <= 0 <= (mean_diff + ci),
                            "cohen_dz": cohen_dz,
                        }
                    )

    return out


def _write_rows(path: str | Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError("no rows to write")

    fieldnames = list(rows[0])
    with Path(path).open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _print_directional_freeze_core(rows: list[dict]) -> None:
    print("NONZERO PAIRED CIs: Directional - Freeze, core metrics")
    for row in rows:
        if row["comparison"] != "directional-freeze":
            continue
        if row["metric"] not in CORE_METRICS:
            continue
        if row["crosses_zero"]:
            continue

        print(
            row["phase"],
            row["region_capacity_mw"],
            row["metric"],
            "mean_diff=" + str(round(float(row["mean_diff"]), 6)),
            "CI=["
            + str(round(float(row["ci95_low"]), 6))
            + ","
            + str(round(float(row["ci95_high"]), 6))
            + "]",
            "dz=" + str(round(float(row["cohen_dz"]), 4)),
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compute paired policy differences for trace phase x headroom results."
    )
    parser.add_argument(
        "--input",
        default=os.path.join(RESULTS_DIR, "trace_phase_headroom.csv"),
        help="Raw trace phase headroom CSV.",
    )
    parser.add_argument(
        "--output",
        default=os.path.join(RESULTS_DIR, "trace_phase_headroom_paired.csv"),
        help="Output paired-comparison CSV.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    raw_rows = _read_rows(args.input)
    paired_rows = _paired_rows(raw_rows)
    _write_rows(args.output, paired_rows)

    print("wrote:", args.output)
    print("rows:", len(paired_rows))
    _print_directional_freeze_core(paired_rows)


if __name__ == "__main__":
    main()
