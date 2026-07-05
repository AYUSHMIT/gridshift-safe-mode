"""TCAE paired analysis for trace phase x compute headroom results."""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path

from experiments.run_trace_compare import RESULTS_DIR


RESPONSE_METRICS = [
    "overload",
    "completion_rate",
    "sla_violation_rate",
    "migrations",
]

EXPLANATORY_METRICS = [
    "post_attack_mean_migration_feasibility_rate",
    "post_attack_fraction_ticks_with_any_feasible_trusted_destination",
    "post_attack_mean_trusted_residual_headroom_mw",
    "post_attack_min_trusted_residual_headroom_mw",
    "time_to_first_feasible_corrective_action",
    "successful_corrective_migrations",
    "post_attack_scheduler_migration_decisions_raw",
    "post_attack_scheduler_migration_decisions_to_trusted_capacity_feasible",
    "post_attack_safety_allowed_migrations",
    "post_attack_safety_explicit_block_migrations",
    "post_attack_safety_raw_migrations_removed_or_converted",
    "post_attack_safety_blocked_migrations",
    "post_attack_executed_migrations",
    "post_attack_executed_corrective_migrations",
]

POLICIES = {"none", "freeze", "directional"}


def _read_rows(path: str | Path) -> list[dict]:
    with Path(path).open(newline="") as handle:
        return list(csv.DictReader(handle))


def _group_rows(rows: list[dict]) -> dict[tuple[str, str, str], dict[str, dict]]:
    grouped: dict[tuple[str, str, str], dict[str, dict]] = {}
    for index, row in enumerate(rows, start=2):
        key = (row["phase"], row["region_capacity_mw"], row["seed"])
        policy = row["policy"]
        if policy not in POLICIES:
            raise ValueError(f"row {index}: unexpected policy {policy!r}")
        policies = grouped.setdefault(key, {})
        if policy in policies:
            raise ValueError(f"duplicate policy row for key={key}, policy={policy}")
        policies[policy] = row

    missing = []
    for key, policies in grouped.items():
        missing_policies = sorted(POLICIES - set(policies))
        if missing_policies:
            missing.append((key, missing_policies))
    if missing:
        raise ValueError(f"missing policy rows for paired TCAE analysis: {missing}")
    return grouped


def _paired_rows(rows: list[dict]) -> list[dict]:
    grouped = _group_rows(rows)
    out = []
    for key in sorted(grouped, key=lambda item: (item[0], float(item[1]), int(item[2]))):
        phase, capacity, seed = key
        directional = grouped[key]["directional"]
        freeze = grouped[key]["freeze"]
        row = {
            "phase": phase,
            "region_capacity_mw": capacity,
            "seed": seed,
        }
        for metric in RESPONSE_METRICS:
            row[f"delta_{metric}_directional_minus_freeze"] = (
                float(directional[metric]) - float(freeze[metric])
            )
        for metric in EXPLANATORY_METRICS:
            row[f"directional_{metric}"] = float(directional[metric])
            row[f"freeze_{metric}"] = float(freeze[metric])
        out.append(row)
    return out


def _regime_summary(paired_rows: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str], list[dict]] = {}
    for row in paired_rows:
        key = (row["phase"], row["region_capacity_mw"])
        grouped.setdefault(key, []).append(row)

    metric_keys = [
        f"delta_{metric}_directional_minus_freeze"
        for metric in RESPONSE_METRICS
    ]
    for metric in EXPLANATORY_METRICS:
        metric_keys.append(f"directional_{metric}")
        metric_keys.append(f"freeze_{metric}")

    out = []
    for key in sorted(grouped, key=lambda item: (item[0], float(item[1]))):
        rows = grouped[key]
        summary = {
            "phase": key[0],
            "region_capacity_mw": key[1],
            "n": len(rows),
        }
        for metric in metric_keys:
            summary[f"{metric}_mean"] = _mean(float(row[metric]) for row in rows)
        out.append(summary)
    return out


def _write_rows(path: str | Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError(f"no rows to write: {path}")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_optional_plots(
    paired_rows: list[dict],
    *,
    completion_plot: str | Path,
    overload_plot: str | Path,
) -> list[str]:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return []

    x_key = (
        "directional_post_attack_fraction_ticks_with_any_feasible_trusted_destination"
    )
    plots = [
        (
            completion_plot,
            "delta_completion_rate_directional_minus_freeze",
            "Directional - freeze completion rate",
        ),
        (
            overload_plot,
            "delta_overload_directional_minus_freeze",
            "Directional - freeze overload",
        ),
    ]
    written = []
    for path, y_key, ylabel in plots:
        fig, ax = plt.subplots(figsize=(7, 5))
        ax.scatter(
            [float(row[x_key]) for row in paired_rows],
            [float(row[y_key]) for row in paired_rows],
            alpha=0.75,
        )
        ax.set_xlabel(
            "Directional fraction post-attack ticks with trusted feasible destination"
        )
        ax.set_ylabel(ylabel)
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        fig.savefig(path, dpi=160)
        plt.close(fig)
        written.append(str(path))
    return written


def _mean(values) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build paired TCAE rows and regime summaries."
    )
    parser.add_argument(
        "--input",
        default=os.path.join(RESULTS_DIR, "trace_phase_headroom.csv"),
        help="Raw trace phase/headroom CSV.",
    )
    parser.add_argument(
        "--paired-output",
        default=os.path.join(RESULTS_DIR, "tcae_phase_headroom_paired.csv"),
        help="Output directional-minus-freeze paired CSV.",
    )
    parser.add_argument(
        "--summary-output",
        default=os.path.join(RESULTS_DIR, "tcae_phase_headroom_regime_summary.csv"),
        help="Output regime summary CSV.",
    )
    parser.add_argument(
        "--skip-plots",
        action="store_true",
        help="Skip optional matplotlib scatter plots.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    raw_rows = _read_rows(args.input)
    paired = _paired_rows(raw_rows)
    summary = _regime_summary(paired)
    _write_rows(args.paired_output, paired)
    _write_rows(args.summary_output, summary)

    print("wrote:", args.paired_output)
    print("paired_rows:", len(paired))
    print("wrote:", args.summary_output)
    print("summary_rows:", len(summary))

    if not args.skip_plots:
        written = _write_optional_plots(
            paired,
            completion_plot=os.path.join(
                RESULTS_DIR,
                "tcae_feasibility_vs_completion_delta.png",
            ),
            overload_plot=os.path.join(
                RESULTS_DIR,
                "tcae_feasibility_vs_overload_delta.png",
            ),
        )
        if written:
            for path in written:
                print("wrote:", path)
        else:
            print("plots skipped: matplotlib is not available")


if __name__ == "__main__":
    main()
