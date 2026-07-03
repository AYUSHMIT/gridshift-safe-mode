"""Validate and summarize a derived GridShift trace workload.

Run:
    python -m experiments.validate_trace_workload
"""
from __future__ import annotations

import os

from experiments.trace_loader import load_derived_trace
from experiments.workloads import DerivedTraceWorkloadSource

ROOT = os.path.dirname(os.path.dirname(__file__))
DEFAULT_TRACE = os.path.join(
    ROOT,
    "data",
    "traces",
    "google_cluster_derived_5min.csv",
)


def main() -> None:
    points = load_derived_trace(DEFAULT_TRACE)
    source = DerivedTraceWorkloadSource(DEFAULT_TRACE)
    source.reset(seed=0)

    total_specs = 0
    total_work = 0.0
    for tick in range(points[0].tick, points[-1].tick + 1):
        specs = source.jobs_for_tick(tick)
        for spec in specs:
            if spec.duration_ticks <= 0:
                raise AssertionError("Derived workload emitted a zero-duration job")
            if spec.power_mw < 0:
                raise AssertionError("Derived workload emitted a negative-power job")
            total_specs += 1
            total_work += spec.power_mw * spec.duration_ticks

    print("trace:", DEFAULT_TRACE)
    print("rows:", len(points))
    print("tick_range:", f"{points[0].tick}..{points[-1].tick}")
    print("submitted_jobs_at_scale_1:", total_specs)
    print("sampled_work_mw_ticks:", round(total_work, 2))
    print("expected_work_mw_ticks:", round(source.expected_work_units(points[-1].tick), 2))
    print("OK: derived trace workload passed sanity checks.")


if __name__ == "__main__":
    main()
