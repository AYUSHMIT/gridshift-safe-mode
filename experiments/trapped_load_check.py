# experiments/trapped_load_check.py
"""
Verify the new trapped-load (exposure) metric separates the policies where
overload-exceedance cannot. Replicates fig_policy_compare's scheduled
adversary exactly, but also integrates per-tick trapped_load_mw.

trapped_load_mw.ticks = sum over ticks of (true load on untrusted nodes).
Expectation: freeze TRAPS exposure (high), directional DRAINS it (low),
none never flags so it is unmitigated.

Run:  python -m experiments.trapped_load_check
"""
import numpy as np
from core.config import SimConfig
from core.orchestrator import GridShiftOrchestrator

POLICIES = ["none", "freeze", "directional"]
SEEDS = list(range(10))
TICKS, INITIAL_BURST, STEADY_BURST = 50, 30, 5
ATTACK_CFG = dict(
    compromised_fraction=0.34, attack_start_tick=15,
    lie_delta_mw=16.0, spike_mw=30.0,
    firmware_tamper=True, detector_mode="fusion",
)


def run_one(policy, seed):
    cfg = SimConfig(seed=seed, policy=policy, **ATTACK_CFG)
    orch = GridShiftOrchestrator(seed=seed, config=cfg)
    orch.trigger_heatwave(TICKS)
    orch.submit_job_burst(INITIAL_BURST)
    trapped, trapped_jobs, overload = 0.0, 0.0, 0.0
    for t in range(1, TICKS + 1):
        if t % 5 == 0:
            orch.submit_job_burst(STEADY_BURST)
        r = orch.tick()
        trapped += r.trapped_load_mw
        # job-only exposure on bad nodes (excludes undrainable attacker spike)
        bad = {a.node_id for a in r.assessments if a.level.value != "trusted"}
        trapped_jobs += sum(
            sum(j.power_mw for j in orch.fleet.dcs[n].running_jobs)
            for n in bad)
        overload += max(0.0, r.grid.total_load_mw - r.grid.threshold_mw)
    return trapped, trapped_jobs, overload


def main():
    print(f"{'policy':>12} | {'trapped_total':>16} | {'trapped_JOBS':>16} | {'overload':>14}")
    print("-" * 70)
    for p in POLICIES:
        tr = np.array([run_one(p, s) for s in SEEDS])
        def mci(col):
            return tr[:, col].mean(), 1.96 * tr[:, col].std(ddof=1) / np.sqrt(len(SEEDS))
        a = mci(0); b = mci(1); c = mci(2)
        print(f"{p:>12} | {a[0]:>8.0f}+/-{a[1]:>5.0f} | "
              f"{b[0]:>8.0f}+/-{b[1]:>5.0f} | {c[0]:>7.0f}+/-{c[1]:>4.0f}")
    print("\ntrapped_JOBS excludes the undrainable attacker spike -- if "
          "directional drains, it should show here.")


if __name__ == "__main__":
    main()
