"""
Detector comparison experiment under fixed scheduled adversary cases.

Compares detector modes under two attack cases with identical workload:
  - attestation-only
  - behavior-only
  - fusion

Attack cases:
    - behavioral-lie-only
    - firmware-tamper-only

Metrics (aggregated across seeds):
  - overload_exceedance
  - safe_mode_ticks
  - bad_node_ticks
  - migrations
  - completion_rate
  - sla_violation_rate

Run:
    python -m experiments.fig_detector_compare
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
DETECTOR_MODES = ["attestation-only", "behavior-only", "fusion"]
SEEDS = list(range(500))
TICKS = 50
INITIAL_BURST = 30
STEADY_BURST = 5
OUTDIR = os.path.join(os.path.dirname(__file__), "figures")

# Scheduled adversary base settings (shared by all cases).
BASE_ATTACK_CFG = dict(
    compromised_fraction=0.34,
    attack_start_tick=15,
    spike_mw=30.0,
    replay_nonce=False,
    key_compromise=False,
)

ATTACK_CASES = {
    "behavioral-lie-only": dict(
        firmware_tamper=False,
        lie_delta_mw=16.0,
    ),
    "firmware-tamper-only": dict(
        firmware_tamper=True,
        lie_delta_mw=0.0,
    ),
}


def run_one(detector_mode: str, seed: int, attack_cfg: dict) -> dict:
    cfg = SimConfig(
        seed=seed,
        detector_mode=detector_mode,
        **attack_cfg,
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


def aggregate_by_case_mode() -> dict:
    keys = [
        "overload_exceedance",
        "safe_mode_ticks",
        "bad_node_ticks",
        "migrations",
        "completion_rate",
        "sla_violation_rate",
    ]
    out = {
        case: {
            mode: {k: {"mean": 0.0, "ci": 0.0} for k in keys}
            for mode in DETECTOR_MODES
        }
        for case in ATTACK_CASES
    }

    for case, case_overrides in ATTACK_CASES.items():
        attack_cfg = {**BASE_ATTACK_CFG, **case_overrides}
        for mode in DETECTOR_MODES:
            runs = [run_one(mode, seed, attack_cfg) for seed in SEEDS]
            for k in keys:
                vals = np.array([r[k] for r in runs], dtype=float)
                out[case][mode][k]["mean"] = float(vals.mean())
                if len(vals) > 1:
                    out[case][mode][k]["ci"] = float(
                        1.96 * vals.std(ddof=1) / np.sqrt(len(vals))
                    )
                else:
                    out[case][mode][k]["ci"] = 0.0

    return out


def _plot_metric(ax, agg: dict, metric: str, title: str, y_label: str, scale: float = 1.0):
    x = np.arange(len(DETECTOR_MODES))
    width = 0.36
    colors = {
        "behavioral-lie-only": "#1f77b4",
        "firmware-tamper-only": "#d62728",
    }

    for i, case in enumerate(ATTACK_CASES.keys()):
        means = [agg[case][m][metric]["mean"] * scale for m in DETECTOR_MODES]
        cis = [agg[case][m][metric]["ci"] * scale for m in DETECTOR_MODES]
        offset = (i - 0.5) * width
        ax.bar(
            x + offset,
            means,
            width=width,
            yerr=cis,
            capsize=3,
            color=colors[case],
            alpha=0.9,
            label=case,
        )

    ax.set_title(title)
    ax.set_ylabel(y_label)
    ax.set_xticks(x)
    ax.set_xticklabels(DETECTOR_MODES, rotation=15, ha="right")
    ax.grid(True, axis="y", alpha=0.3)


def make_figure(agg: dict) -> str:
    os.makedirs(OUTDIR, exist_ok=True)

    # Safe-mode time and bad-node occupancy are constant across the compared
    # configurations (they track attack duration, not the detector), so they are
    # omitted; the four panels below are the ones that distinguish the methods.
    fig, axes = plt.subplots(2, 2, figsize=(9.5, 7.5))
    axes = axes.flatten()

    _plot_metric(
        axes[0], agg,
        "overload_exceedance",
        "Overload exceedance",
        "MW·ticks",
    )
    _plot_metric(
        axes[1], agg,
        "migrations",
        "Migrations begun",
        "count",
    )
    _plot_metric(
        axes[2], agg,
        "completion_rate",
        "Completion rate",
        "%",
        scale=100.0,
    )
    _plot_metric(
        axes[3], agg,
        "sla_violation_rate",
        "SLA violation rate",
        "%",
        scale=100.0,
    )
    axes[0].legend(fontsize=8)   # label the two attack cases

    fig.suptitle(
        "Detector comparison under scheduled adversary",
        fontsize=14,
        y=0.98,
    )
    fig.tight_layout(rect=[0, 0, 1, 0.96])

    png = os.path.join(OUTDIR, "fig_detector_compare.png")
    fig.savefig(png, dpi=150, bbox_inches="tight")
    print("saved:", png)
    return png


def main():
    print(
        f"detector sweep: modes={DETECTOR_MODES} x seeds={len(SEEDS)} "
        f"x {TICKS} ticks"
    )
    print("scheduled adversary base:", BASE_ATTACK_CFG)
    print("attack cases:", ATTACK_CASES)

    agg = aggregate_by_case_mode()

    print("\n case               | mode             | overload | safe_ticks | bad_node_ticks | migr | completion% | SLA%")
    for case in ATTACK_CASES:
        for mode in DETECTOR_MODES:
            row = agg[case][mode]
            print(
                f" {case:<18} | {mode:<16} | "
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
