# experiments/smoke_run.py
"""
Quick end-to-end driver to sanity-check the DC module changes inside the
full orchestrator (9 DCs, k-tick migration cost, SLA, attack scenario).
Not the Monte-Carlo harness (that's Ayush's) -- just an eyeball test.

Run:  python -m experiments.smoke_run
"""
from core.orchestrator import GridShiftOrchestrator

TICKS = 40
ATTACK_AT = 15
TARGET = "BOS-1"


def main():
    orch = GridShiftOrchestrator(seed=7)
    orch.trigger_heatwave(TICKS)
    orch.submit_job_burst(40)            # heavier initial load

    print(f"{'tick':>4} {'grid_MW':>8} {'risk':>9} {'safe':>5} "
          f"{'dec':>3} {'migr':>4} {'inflt':>5} {'#bad':>4}")
    print("-" * 52)

    for t in range(1, TICKS + 1):
        if t % 5 == 0:                   # steady trickle of new jobs
            orch.submit_job_burst(5)
        if t == ATTACK_AT:               # supervisor-scenario attack
            orch.start_attack_tamper(TARGET)
            orch.spike_load(TARGET, 45.0)   # push BOS-1 past its 75% line

        r = orch.tick()
        bad = sum(1 for a in r.assessments if a.level.value != "trusted")
        risk = orch.grid.risk_band(r.grid)
        print(f"{r.tick:>4} {r.grid.total_load_mw:>8.1f} {risk:>9} "
              f"{str(r.safe_mode):>5} {len(r.decisions):>3} "
              f"{orch.fleet.migration_count:>4} "
              f"{orch.fleet.inflight_count():>5} {bad:>4}")

    print("\n=== Final DC metrics ===")
    print("migrations begun     :", orch.fleet.migration_count)
    print("migration overhead   : %.1f MW.ticks" % orch.fleet.migration_overhead_accum)
    print("jobs completed       :", len(orch.fleet.completed))
    print("SLA stats            :", orch.fleet.sla_stats())
    print("per-region load (MW) :",
          {k: round(v, 1) for k, v in orch.fleet.region_load_mw().items()})
    print("\nOK: simulation ran end-to-end.")


if __name__ == "__main__":
    main()
