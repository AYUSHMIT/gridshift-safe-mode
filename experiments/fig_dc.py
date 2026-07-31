# experiments/fig_dc.py
"""
Arash / DC-module figure: the safety-vs-availability operating curve.

Sweeps offered workload intensity and, under the (current) directional
safe-mode policy with the k-tick migration-cost model, records:
  - grid-overload exceedance  (MW.ticks above threshold)  -> grid stress
  - job-completion rate                                    -> availability
  - SLA-violation rate                                     -> migration cost

Produces a 2-panel figure:
  (A) Pareto operating curve: overload exceedance vs job completion
  (B) SLA-violation rate vs offered load  (showcases migration cost)

This experiment ISOLATES the effect of workload intensity: it runs under
the directional policy with NO adversary, so the load signal is not masked
by the attack (the policy/detector/robustness figures carry the adversarial
story). 500 seeds.

Run:  python -m experiments.fig_dc
"""
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from core.config import SimConfig
from core.orchestrator import GridShiftOrchestrator

TICKS = 50
SEEDS = list(range(500))
LOAD_LEVELS = [4, 8, 12, 16, 20, 24]   # jobs injected per 5-tick interval
OUTDIR = os.path.join(os.path.dirname(__file__), "figures")

def run_one(load: int, seed: int) -> dict:
    """One simulation: returns scalar metrics for this (load, seed).

    No adversary (default SimConfig) so the figure isolates load intensity."""
    cfg = SimConfig(seed=seed, policy="directional")
    orch = GridShiftOrchestrator(seed=seed, config=cfg)
    orch.trigger_heatwave(TICKS)
    orch.submit_job_burst(load * 3)          # initial backlog scales with load

    overload_exceedance = 0.0
    peak_overload = 0.0
    for t in range(1, TICKS + 1):
        if t % 5 == 0:
            orch.submit_job_burst(load)
        r = orch.tick()
        over = max(0.0, r.grid.total_load_mw - r.grid.threshold_mw)
        overload_exceedance += over
        peak_overload = max(peak_overload, over)

    submitted = orch.fleet._job_counter
    completed = len(orch.fleet.completed)
    sla = orch.fleet.sla_stats()
    return {
        "overload_exceedance": overload_exceedance,
        "peak_overload": peak_overload,
        "completion_rate": completed / max(1, submitted),
        "sla_violation_rate": sla["sla_violation_rate"],
        "migrations": orch.fleet.migration_count,
        "migration_overhead": orch.fleet.migration_overhead_accum,
    }


def aggregate():
    """Run the sweep, return per-load mean/CI arrays."""
    keys = ["overload_exceedance", "completion_rate",
            "sla_violation_rate", "migrations", "migration_overhead"]
    out = {k: {"mean": [], "ci": []} for k in keys}
    for load in LOAD_LEVELS:
        runs = [run_one(load, s) for s in SEEDS]
        for k in keys:
            vals = np.array([r[k] for r in runs], dtype=float)
            out[k]["mean"].append(vals.mean())
            # 95% CI half-width
            out[k]["ci"].append(1.96 * vals.std(ddof=1) / np.sqrt(len(vals)))
    return out


def make_figure(agg):
    os.makedirs(OUTDIR, exist_ok=True)
    fig, (axA, axB) = plt.subplots(1, 2, figsize=(11, 4.2))

    # ---- Panel A: Pareto operating curve ----
    comp = np.array(agg["completion_rate"]["mean"]) * 100
    over = np.array(agg["overload_exceedance"]["mean"])
    over_ci = np.array(agg["overload_exceedance"]["ci"])
    axA.errorbar(comp, over, yerr=over_ci, fmt="-o", color="#1f77b4",
                 capsize=3, lw=2, ms=7)
    for i, (x, y, lvl) in enumerate(zip(comp, over, LOAD_LEVELS)):
        dy = 9 if i % 2 == 0 else -15      # stagger to avoid label overlap
        axA.annotate(f"load={lvl}", (x, y), textcoords="offset points",
                     xytext=(7, dy), fontsize=8, color="#444")
    axA.set_xlabel("Job-completion rate (%)  → availability")
    axA.set_ylabel("Grid-overload exceedance (MW·ticks)")
    axA.set_title("(A) Safety–availability operating curve\n(directional policy, 9 DCs / 3 regions)")
    axA.grid(True, alpha=0.3)
    axA.invert_xaxis()   # better=up-left: high availability, low overload

    # ---- Panel B: SLA cost of migration vs load ----
    sla = np.array(agg["sla_violation_rate"]["mean"]) * 100
    sla_ci = np.array(agg["sla_violation_rate"]["ci"]) * 100
    axB.errorbar(LOAD_LEVELS, sla, yerr=sla_ci, fmt="-s", color="#d62728",
                 capsize=3, lw=2, ms=7, label="SLA-violation rate")
    axB.set_xlabel("Offered load (jobs / 5-tick interval)")
    axB.set_ylabel("SLA-violation rate (%)", color="#d62728")
    axB.tick_params(axis="y", labelcolor="#d62728")
    axB.set_title("(B) Migration-cost effect:\nSLA violations vs offered load")
    axB.grid(True, alpha=0.3)

    ax2 = axB.twinx()
    migr = np.array(agg["migrations"]["mean"])
    ax2.plot(LOAD_LEVELS, migr, "--^", color="#2ca02c", lw=1.8, ms=6,
             label="migrations")
    ax2.set_ylabel("Migrations begun", color="#2ca02c")
    ax2.tick_params(axis="y", labelcolor="#2ca02c")

    fig.suptitle("GridShift DC module — safety/availability trade-off and migration cost",
                 fontsize=12, y=1.02)
    fig.tight_layout()
    png = os.path.join(OUTDIR, "fig_dc_tradeoff.png")
    fig.savefig(png, dpi=150, bbox_inches="tight")
    print("saved:", png)
    return png


def main():
    print(f"sweep: loads={LOAD_LEVELS} x seeds={len(SEEDS)} "
          f"x {TICKS} ticks = {len(LOAD_LEVELS)*len(SEEDS)} runs")
    agg = aggregate()
    print("\n load |  overload(MW.t) | completion% | SLA% | migr")
    for i, lvl in enumerate(LOAD_LEVELS):
        print(f" {lvl:>4} | {agg['overload_exceedance']['mean'][i]:>14.1f} "
              f"| {agg['completion_rate']['mean'][i]*100:>10.1f} "
              f"| {agg['sla_violation_rate']['mean'][i]*100:>4.1f} "
              f"| {agg['migrations']['mean'][i]:>4.1f}")
    make_figure(agg)


if __name__ == "__main__":
    main()
