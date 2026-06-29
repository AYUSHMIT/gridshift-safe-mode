# experiments/fig_robustness.py
"""
Robustness sweep: how the three policies degrade as the adversary controls
a larger fraction of the fleet. Addresses the "results look hand-tuned"
concern by sweeping compromised_fraction instead of fixing it at 0.34, and
uses 20 seeds to tighten the confidence intervals.

For each (policy, compromised_fraction): scheduled fusion-detector attack,
20 seeds. Metrics: grid-overload exceedance, completion rate, SLA-violation
rate, and compromised-node load exposure (job MW.ticks on untrusted nodes).

Run:  python -m experiments.fig_robustness
"""
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from core.config import SimConfig
from core.orchestrator import GridShiftOrchestrator

POLICIES = ["none", "freeze", "directional"]
FRACTIONS = [0.0, 0.11, 0.22, 0.33, 0.44, 0.56]   # 0..5 of 9 DCs
SEEDS = list(range(20))
TICKS, INITIAL_BURST, STEADY_BURST = 50, 30, 5
OUTDIR = os.path.join(os.path.dirname(__file__), "figures")
COLORS = {"none": "#1f77b4", "freeze": "#d62728", "directional": "#2ca02c"}


def run_one(policy, frac, seed):
    cfg = SimConfig(
        seed=seed, policy=policy,
        compromised_fraction=frac, attack_start_tick=15,
        lie_delta_mw=16.0, spike_mw=30.0, firmware_tamper=True,
        detector_mode="fusion",
    )
    orch = GridShiftOrchestrator(seed=seed, config=cfg)
    orch.trigger_heatwave(TICKS)
    orch.submit_job_burst(INITIAL_BURST)

    overload, exposure = 0.0, 0.0
    for t in range(1, TICKS + 1):
        if t % 5 == 0:
            orch.submit_job_burst(STEADY_BURST)
        r = orch.tick()
        overload += max(0.0, r.grid.total_load_mw - r.grid.threshold_mw)
        bad = {a.node_id for a in r.assessments if a.level.value != "trusted"}
        exposure += sum(
            sum(j.power_mw for j in orch.fleet.dcs[n].running_jobs)
            for n in bad)

    submitted = orch.fleet._job_counter
    completed = len(orch.fleet.completed)
    sla = orch.fleet.sla_stats()["sla_violation_rate"]
    return overload, completed / max(1, submitted), sla, exposure


def aggregate():
    # metrics index: 0 overload, 1 completion, 2 sla, 3 exposure
    data = {p: {m: {"mean": [], "ci": []} for m in range(4)} for p in POLICIES}
    for p in POLICIES:
        for f in FRACTIONS:
            runs = np.array([run_one(p, f, s) for s in SEEDS])
            for m in range(4):
                col = runs[:, m]
                data[p][m]["mean"].append(col.mean())
                data[p][m]["ci"].append(
                    1.96 * col.std(ddof=1) / np.sqrt(len(col)))
    return data


def make_figure(data):
    os.makedirs(OUTDIR, exist_ok=True)
    x = [f * 100 for f in FRACTIONS]
    titles = ["Grid-overload exceedance (MW·ticks)",
              "Workload completion rate (%)",
              "SLA-violation rate (%)",
              "Compromised-node exposure (MW·ticks)"]
    scale = [1.0, 100.0, 100.0, 1.0]   # completion/SLA -> percent
    fig, axes = plt.subplots(2, 2, figsize=(11, 7.5))
    for m, ax in enumerate(axes.flat):
        for p in POLICIES:
            y = np.array(data[p][m]["mean"]) * scale[m]
            ci = np.array(data[p][m]["ci"]) * scale[m]
            ax.errorbar(x, y, yerr=ci, fmt="-o", color=COLORS[p],
                        capsize=3, lw=2, ms=5, label=p)
        ax.set_title(titles[m])
        ax.set_xlabel("Compromised fraction (%)")
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=8)
    fig.suptitle("GridShift robustness vs. adversary-controlled fraction "
                 "(fusion detector, 20 seeds, 95% CI)", fontsize=12, y=1.01)
    fig.tight_layout()
    png = os.path.join(OUTDIR, "fig_robustness.png")
    fig.savefig(png, dpi=150, bbox_inches="tight")
    print("saved:", png)
    return png


def main():
    print(f"sweep: {len(POLICIES)} policies x {len(FRACTIONS)} fractions "
          f"x {len(SEEDS)} seeds = "
          f"{len(POLICIES)*len(FRACTIONS)*len(SEEDS)} runs")
    data = aggregate()
    print("\npolicy        frac  overload  compl%  sla%  exposure")
    for p in POLICIES:
        for i, f in enumerate(FRACTIONS):
            print(f"{p:>12} {f:>5.2f} "
                  f"{data[p][0]['mean'][i]:>9.0f} "
                  f"{data[p][1]['mean'][i]*100:>6.1f} "
                  f"{data[p][2]['mean'][i]*100:>5.1f} "
                  f"{data[p][3]['mean'][i]:>8.0f}")
    make_figure(data)


if __name__ == "__main__":
    main()
