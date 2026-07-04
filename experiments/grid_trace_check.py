"""Sanity check for trace-backed grid baseline loading."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from core.grid_model import BostonGridModel, GridConfig, RegionalGridTraceSource
from experiments.build_iso_ne_grid_summary import build_iso_ne_grid_summary
from experiments.grid_trace_loader import load_grid_trace


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data" / "grid" / "iso_ne_grid_sample_5min.csv"
JSON_FIXTURE = ROOT / "data" / "grid" / "iso_ne_fiveminute_zonal_load_sample.json"


def main() -> None:
    points = load_grid_trace(FIXTURE)
    source = RegionalGridTraceSource(points)
    grid = BostonGridModel(
        config=GridConfig(threshold_mw=4500.0),
        trace_source=source,
    )

    grid.start_heatwave(2)
    grid.set_dc_load(25.0)

    states = [grid.tick() for _ in range(2)]
    expected_base = [4091.7, 4106.1]
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

    with TemporaryDirectory() as tmpdir:
        output = Path(tmpdir) / "iso_ne_json_derived.csv"
        build_iso_ne_grid_summary(
            input_path=None,
            json_input_path=str(JSON_FIXTURE),
            fetch_day=None,
            mapping_path=None,
            output_path=str(output),
            timestamp_column=None,
            zone_column=None,
            load_column=None,
            source_timezone=None,
        )
        json_points = load_grid_trace(output)
        json_regions = {point.gridshift_region for point in json_points}
        expected_regions = {"SEMASS", "WCMASS", "NEMASSBOST"}
        if json_regions != expected_regions:
            raise AssertionError(
                f"unexpected JSON-derived regions: {json_regions}"
            )
        if {point.iso_ne_zone for point in json_points} != {
            ".Z.SEMASS",
            ".Z.WCMASS",
            ".Z.NEMASSBOST",
        }:
            raise AssertionError("JSON-derived trace did not preserve ISO-NE zones")

    print("json_fixture_rows:", len(json_points))
    print("OK: trace-backed grid baseline passed sanity checks.")


if __name__ == "__main__":
    main()
