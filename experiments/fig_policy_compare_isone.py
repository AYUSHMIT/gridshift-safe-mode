"""Plot the canonical 500-seed ISO-NE policy comparison.

Run from the repository root with:
    python -m experiments.fig_policy_compare_isone
"""

from __future__ import annotations

import csv
import shutil
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


POLICIES = ("none", "freeze", "directional")
POLICY_LABELS = ("None", "Freeze", "Directional")
COLORS = ("#4c78a8", "#d65f5f", "#54a24b")

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SUMMARY_CSV = REPOSITORY_ROOT / "experiments/results/trace_compare_summary.csv"
SIMULATOR_PNG = (
    REPOSITORY_ROOT / "experiments/figures/fig_policy_compare_isone.png"
)
PAPER_PNG = REPOSITORY_ROOT.parent / "gridshift-paper/fig_policy_compare.png"


def _as_float(row: dict[str, str], field: str) -> float:
    return float(row[field])


def load_canonical_rows() -> dict[str, dict[str, str]]:
    """Load and validate the three canonical synthetic-policy rows."""
    with SUMMARY_CSV.open(newline="", encoding="utf-8") as stream:
        candidates = [
            row
            for row in csv.DictReader(stream)
            if row["workload_source"] == "synthetic"
            and row["detector_mode"] == "fusion"
            and row["n"] == "500"
            and row["policy"] in POLICIES
        ]

    rows_by_policy: dict[str, dict[str, str]] = {}
    for policy in POLICIES:
        matches = [row for row in candidates if row["policy"] == policy]
        if len(matches) != 1:
            raise ValueError(
                f"Expected exactly one canonical row for {policy!r}; "
                f"found {len(matches)}"
            )
        rows_by_policy[policy] = matches[0]

    for policy, row in rows_by_policy.items():
        expected_strings = {
            "experiment": "trace_compare",
            "workload_source": "synthetic",
            "detector_mode": "fusion",
            "grid_baseline_source": "data/grid/iso_ne_grid_derived_5min.csv",
            "grid_threshold_window": "eval",
            "threshold_strategy": "max_baseline_plus_headroom_eval",
            "n": "500",
        }
        for field, expected in expected_strings.items():
            if row[field] != expected:
                raise ValueError(
                    f"{policy}: expected {field}={expected!r}, got {row[field]!r}"
                )

        expected_numbers = {
            "load_scale": 1.0,
            "threshold_headroom_mw_mean": 0.0,
            "grid_eval_first_tick_mean": 178.0,
            "grid_eval_last_tick_mean": 227.0,
        }
        for field, expected in expected_numbers.items():
            actual = _as_float(row, field)
            if actual != expected:
                raise ValueError(
                    f"{policy}: expected {field}={expected}, got {actual}"
                )

    return rows_by_policy


def _values(
    rows: dict[str, dict[str, str]], metric: str, scale: float = 1.0
) -> tuple[list[float], list[float]]:
    means = [
        _as_float(rows[policy], f"{metric}_mean") * scale
        for policy in POLICIES
    ]
    cis = [
        _as_float(rows[policy], f"{metric}_ci") * scale
        for policy in POLICIES
    ]
    return means, cis


def _plot_panel(
    ax,
    rows: dict[str, dict[str, str]],
    metric: str,
    title: str,
    ylabel: str,
    *,
    scale: float = 1.0,
    percent: bool = False,
) -> None:
    means, cis = _values(rows, metric, scale)
    x = np.arange(len(POLICIES))
    bars = ax.bar(x, means, yerr=cis, capsize=3, color=COLORS, alpha=0.9)
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.set_xticks(x)
    ax.set_xticklabels(POLICY_LABELS, rotation=15, ha="right")
    ax.grid(True, axis="y", alpha=0.3)

    for bar, mean in zip(bars, means):
        label = f"{mean:.2f}%" if percent else f"{mean:.2f}"
        ax.annotate(
            label,
            (bar.get_x() + bar.get_width() / 2, bar.get_height()),
            xytext=(0, 4),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=8,
        )


def print_plotted_values(rows: dict[str, dict[str, str]]) -> None:
    """Print the twelve values used as bar heights."""
    for policy, label in zip(POLICIES, POLICY_LABELS):
        row = rows[policy]
        print(f"{label}:")
        print(f"  overload {_as_float(row, 'overload_mean'):.2f}")
        print(f"  migrations {_as_float(row, 'migrations_mean'):.2f}")
        print(
            "  completion "
            f"{100.0 * _as_float(row, 'completion_rate_mean'):.2f}%"
        )
        print(
            "  SLA violation "
            f"{100.0 * _as_float(row, 'sla_violation_rate_mean'):.2f}%"
        )


def make_figure(rows: dict[str, dict[str, str]]) -> None:
    """Render the canonical simulator figure and copy it into the paper."""
    fig, axes = plt.subplots(2, 2, figsize=(9.5, 7.5))
    _plot_panel(
        axes[0, 0], rows, "overload", "Overload exceedance", "MW·ticks"
    )
    _plot_panel(
        axes[0, 1], rows, "migrations", "Migrations begun", "count"
    )
    _plot_panel(
        axes[1, 0],
        rows,
        "completion_rate",
        "Completion rate",
        "%",
        scale=100.0,
        percent=True,
    )
    _plot_panel(
        axes[1, 1],
        rows,
        "sla_violation_rate",
        "SLA violation rate",
        "%",
        scale=100.0,
        percent=True,
    )
    fig.suptitle(
        "GridShift policy comparison, ISO-NE peak-window synthetic workload",
        fontsize=14,
        y=0.98,
    )
    fig.tight_layout(rect=[0, 0, 1, 0.96])

    SIMULATOR_PNG.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(SIMULATOR_PNG, dpi=150, bbox_inches="tight")
    plt.close(fig)
    shutil.copyfile(SIMULATOR_PNG, PAPER_PNG)

    print(f"wrote: {SIMULATOR_PNG}")
    print(f"copied: {PAPER_PNG}")


def main() -> None:
    rows = load_canonical_rows()
    print_plotted_values(rows)
    make_figure(rows)


if __name__ == "__main__":
    main()
