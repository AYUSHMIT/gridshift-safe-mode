"""Paired analysis for the SWF policy comparison."""

from __future__ import annotations

import argparse
import csv
import math
import os
from pathlib import Path
from statistics import mean, stdev

from experiments.run_trace_compare import RESULTS_DIR


POLICIES = {"none", "freeze", "directional"}

METRICS = [
    "overload",
    "completion_rate",
    "sla_violation_rate",
    "migrations",
    "completed_jobs",
    "submitted_jobs",
    "total_submitted_work",
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

CORE_METRICS = [
    "overload",
    "completion_rate",
    "sla_violation_rate",
    "migrations",
]

# n=10 paired seeds, df=9, two-sided 95% Student-t critical value.
T_CRIT_95_DF9 = 2.262157


def _read_rows(path: str | Path) -> list[dict]:
    with Path(path).open(newline="") as handle:
        return list(csv.DictReader(handle))


def _validate_and_group(rows: list[dict]) -> dict[str, dict[str, dict]]:
    if not rows:
        raise ValueError("no SWF comparison rows found")

    grouped: dict[str, dict[str, dict]] = {}
    for index, row in enumerate(rows, start=2):
        policy = row.get("policy")
        seed = row.get("seed")
        if policy not in POLICIES:
            raise ValueError(f"row {index}: unexpected policy {policy!r}")
        if row.get("workload_source") != "hpc-swf":
            raise ValueError(
                f"row {index}: workload_source must be 'hpc-swf', "
                f"found {row.get('workload_source')!r}"
            )
        if int(float(row["submitted_jobs"])) <= 0:
            raise ValueError(f"row {index}: submitted_jobs must be > 0")
        if float(row["total_submitted_work"]) <= 0:
            raise ValueError(f"row {index}: total_submitted_work must be > 0")

        seed_rows = grouped.setdefault(seed, {})
        if policy in seed_rows:
            raise ValueError(
                f"duplicate row for policy={policy!r}, seed={seed!r}"
            )
        seed_rows[policy] = row

    for seed, seed_rows in sorted(grouped.items(), key=lambda item: int(item[0])):
        present = set(seed_rows)
        if present != POLICIES:
            missing = sorted(POLICIES - present)
            extra = sorted(present - POLICIES)
            raise ValueError(
                f"seed {seed}: expected exactly policies "
                f"{sorted(POLICIES)}, missing={missing}, extra={extra}"
            )

        work_values = {
            policy: float(seed_rows[policy]["total_submitted_work"])
            for policy in POLICIES
        }
        first_policy, first_work = next(iter(work_values.items()))
        for policy, work in work_values.items():
            if not math.isclose(work, first_work, rel_tol=0.0, abs_tol=1e-9):
                raise ValueError(
                    f"seed {seed}: paired workload identity failed; "
                    f"{first_policy} total_submitted_work={first_work}, "
                    f"{policy} total_submitted_work={work}"
                )

    return grouped


def _paired_rows(rows: list[dict]) -> list[dict]:
    grouped = _validate_and_group(rows)
    seeds = sorted(grouped, key=int)

    out = []
    for policy_a, policy_b in COMPARISONS:
        for metric in METRICS:
            diffs = [
                float(grouped[seed][policy_a][metric])
                - float(grouped[seed][policy_b][metric])
                for seed in seeds
            ]
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
    with Path(path).open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _print_core_summary(rows: list[dict]) -> None:
    print("CORE PAIRED COMPARISONS")
    wanted = {
        (f"{policy_a}-{policy_b}", metric)
        for policy_a, policy_b in COMPARISONS
        for metric in CORE_METRICS
    }
    for row in rows:
        key = (row["comparison"], row["metric"])
        if key not in wanted:
            continue
        print(
            row["comparison"],
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
        description="Compute paired policy differences for SWF comparison results."
    )
    parser.add_argument(
        "--input",
        default=os.path.join(RESULTS_DIR, "swf_compare.csv"),
        help="Raw SWF comparison CSV.",
    )
    parser.add_argument(
        "--output",
        default=os.path.join(RESULTS_DIR, "swf_compare_paired.csv"),
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
    _print_core_summary(paired_rows)


if __name__ == "__main__":
    main()
