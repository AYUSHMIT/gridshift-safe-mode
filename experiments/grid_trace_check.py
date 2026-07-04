"""Sanity check for trace-backed grid baseline loading."""
from __future__ import annotations

from pathlib import Path

from core.grid_model import BostonGridModel, GridConfig, RegionalGridTraceSource
from experiments.grid_trace_loader import load_grid_trace


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data" / "grid" / "iso_ne_grid_sample_5min.csv"


def main() -> None:
    points = load_grid_trace(FIXTURE)
    source = RegionalGridTraceSource(points)
    grid = BostonGridModel(
        config=GridConfig(threshold_mw=850.0),
        trace_source=source,
    )

    grid.start_heatwave(3)
    grid.set_dc_load(25.0)

    states = [grid.tick() for _ in range(3)]
    expected_base = [800.0, 804.5, 809.0]
    observed_base = [round(state.base_load_mw, 1) for state in states]
    if observed_base != expected_base:
        raise AssertionError(
            f"unexpected trace baseline: {observed_base} != {expected_base}"
        )

    for state, base in zip(states, expected_base):
        if state.heatwave_multiplier != 1.0:
            raise AssertionError("trace mode should not apply heatwave by default")
        if round(state.total_load_mw, 1) != round(base + 25.0, 1):
            raise AssertionError("GridState total_load_mw semantics changed")

    print("trace:", FIXTURE)
    print("rows:", len(points))
    print("base_load_mw:", observed_base)
    print("dc_load_mw:", states[0].dc_load_mw)
    print("threshold_mw:", states[0].threshold_mw)
    print("OK: trace-backed grid baseline passed sanity checks.")


if __name__ == "__main__":
    main()
