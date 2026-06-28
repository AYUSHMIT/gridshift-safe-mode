"""
Policy comparison experiment under a fixed scheduled adversary.

Compares safety policies with identical detector and workload:
  - none
  - freeze
  - directional

Metrics (aggregated across seeds):
  - overload_exceedance
  - safe_mode_ticks
  - bad_node_ticks
  - migrations
  - completion_rate
  - sla_violation_rate

Run:
    python -m experiments.fig_policy_compare
"""
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from core.config import SimConfig
from core.orchestrator import GridShiftOrchestrator
from core.state import TrustLevel


# ---- experiment configuration ----
POLICIES = ["none", "freeze", "directional"]
SEEDS = list(range(10))
TICKS = 50
INITIAL_BURST = 30
STEADY_BURST = 5
OUTDIR = os.path.join(os.path.dirname(__file__), "figures")

# Scheduled adversary settings (shared across all policies).
ATTACK_CFG = dict(
    compromised_fraction=0.34,
    attack_start_tick=15,
    lie_delta_mw=16.0,
    spike_mw=30.0,
    firmware_tamper=True,
    replay_nonce=False,
    key_compromise=False,
    detector_mode="fusion",
)


def run_one(policy: str, seed: int) -> dict:
    cfg = SimConfig(
        seed=seed,
        policy=policy,
        **ATTACK_CFG,
    )
    orch = GridShiftOrchestrator(seed=seed, config=cfg)
    orch.trigger_heatwave(TICKS)
    orch.submit_job_burst(INITIAL_BURST)

    overload_exceedance = 0.0
    safe_mode_ticks = 0
    bad_node_ticks = 0

    for t in range(1, TICKS + 1):
        if t % 5 == 0:
            orch.submit_job_burst(STEADY_BURST)

        r = orch.tick()
        overload_exceedance += max(0.0, r.grid.total_load_mw - r.grid.threshold_mw)
        if r.safe_mode:
            safe_mode_ticks += 1
        bad_node_ticks += sum(
            1 for a in r.assessments if a.level != TrustLevel.TRUSTED
        )

    submitted = orch.fleet._job_counter
    completed = len(orch.fleet.completed)
    sla = orch.fleet.sla_stats()

    return {
        "overload_exceedance": overload_exceedance,
        "safe_mode_ticks": float(safe_mode_ticks),
        "bad_node_ticks": float(bad_node_ticks),
        "migrations": float(orch.fleet.migration_count),
        "completion_rate": completed / max(1, submitted),
        "sla_violation_rate": float(sla["sla_violation_rate"]),
    }


def aggregate_by_policy() -> dict:
    keys = [
        "overload_exceedance",
        "safe_mode_ticks",
        "bad_node_ticks",
        "migrations",
        "completion_rate",
        "sla_violation_rate",
    ]
    out = {
        policy: {k: {"mean": 0.0, "ci": 0.0} for k in keys}
        for policy in POLICIES
    }

    for policy in POLICIES:
        runs = [run_one(policy, seed) for seed in SEEDS]
        for k in keys:
            vals = np.array([r[k] for r in runs], dtype=float)
            out[policy][k]["mean"] = float(vals.mean())
            if len(vals) > 1:
                out[policy][k]["ci"] = float(
                    1.96 * vals.std(ddof=1) / np.sqrt(len(vals))
                )
            else:
                out[policy][k]["ci"] = 0.0

    return out


def _plot_metric(ax, agg: dict, metric: str, title: str, y_label: str, scale: float = 1.0):
    x = np.arange(len(POLICIES))
    means = [agg[p][metric]["mean"] * scale for p in POLICIES]
    cis = [agg[p][metric]["ci"] * scale for p in POLICIES]
    colors = ["#1f77b4", "#d62728", "#2ca02c"]

    ax.bar(x, means, yerr=cis, capsize=3, color=colors, alpha=0.9)
    ax.set_title(title)
    ax.set_ylabel(y_label)
    ax.set_xticks(x)
    ax.set_xticklabels(POLICIES, rotation=15, ha="right")
    ax.grid(True, axis="y", alpha=0.3)


def make_figure(agg: dict) -> str:
    os.makedirs(OUTDIR, exist_ok=True)

    fig, axes = plt.subplots(2, 3, figsize=(13.5, 7.5))
    axes = axes.flatten()

    _plot_metric(
        axes[0], agg,
        "overload_exceedance",
        "Overload exceedance",
        "MW·ticks",
    )
    _plot_metric(
        axes[1], agg,
        "safe_mode_ticks",
        "Safe-mode time",
        "ticks",
    )
    _plot_metric(
        axes[2], agg,
        "bad_node_ticks",
        "Bad-node occupancy",
        "node·ticks",
    )
    _plot_metric(
        axes[3], agg,
        "migrations",
        "Migrations begun",
        "count",
    )
    _plot_metric(
        axes[4], agg,
        "completion_rate",
        "Completion rate",
        "%",
        scale=100.0,
    )
    _plot_metric(
        axes[5], agg,
        "sla_violation_rate",
        "SLA violation rate",
        "%",
        scale=100.0,
    )

    fig.suptitle(
        "GridShift policy comparison under scheduled adversary",
        fontsize=14,
        y=0.98,
    )
    fig.tight_layout(rect=[0, 0, 1, 0.96])

    png = os.path.join(OUTDIR, "fig_policy_compare.png")
    fig.savefig(png, dpi=150, bbox_inches="tight")
    print("saved:", png)
    return png


def main():
    print(
        f"policy sweep: policies={POLICIES} x seeds={len(SEEDS)} "
        f"x {TICKS} ticks"
    )
    print("scheduled adversary:", ATTACK_CFG)

    agg = aggregate_by_policy()

    print("\n policy      | overload | safe_ticks | bad_node_ticks | migr | completion% | SLA%")
    for policy in POLICIES:
        row = agg[policy]
        print(
            f" {policy:<11} | "
            f"{row['overload_exceedance']['mean']:>8.1f} | "
            f"{row['safe_mode_ticks']['mean']:>10.1f} | "
            f"{row['bad_node_ticks']['mean']:>14.1f} | "
            f"{row['migrations']['mean']:>4.1f} | "
            f"{row['completion_rate']['mean']*100:>10.1f} | "
            f"{row['sla_violation_rate']['mean']*100:>4.1f}"
        )

    png = make_figure(agg)
    plt.close("all")
    print("figure:", png)


if __name__ == "__main__":
    main()
