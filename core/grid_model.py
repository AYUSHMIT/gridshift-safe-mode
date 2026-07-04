# core/grid_model.py
"""
[Owned by: Smart Grids teammate]

Simulates Boston's electrical load with a realistic diurnal curve,
optional heatwave stress, and a pluggable DC-load input.
"""
import math
from dataclasses import dataclass
from typing import Iterable
from core.state import GridState


@dataclass
class GridConfig:
    base_min_mw: float = 700.0
    base_max_mw: float = 870.0
    threshold_mw: float = 900.0
    heatwave_peak_multiplier: float = 1.12
    ticks_per_day: int = 288      # 5-minute ticks


class BostonGridModel:
    """
    Tick-driven Boston grid model. Call tick() once per simulation step.
    """

    def __init__(
        self,
        config: GridConfig = None,
        trace_source=None,
        apply_heatwave_to_trace: bool = False,
    ):
        self.cfg = config or GridConfig()
        self.tick_count = 0
        self.heatwave_active = False
        self.heatwave_ticks_remaining = 0
        self._dc_load_mw = 0.0
        self.trace_source = trace_source
        self.apply_heatwave_to_trace = apply_heatwave_to_trace

    def start_heatwave(self, duration_ticks: int = 36):
        self.heatwave_active = True
        self.heatwave_ticks_remaining = duration_ticks

    def set_dc_load(self, dc_load_mw: float):
        self._dc_load_mw = dc_load_mw

    def _diurnal_base(self) -> float:
        # Sinusoidal: peak around tick 204 (~5 PM), trough around tick 60 (~5 AM)
        phase = (self.tick_count % self.cfg.ticks_per_day) / self.cfg.ticks_per_day
        amp = (self.cfg.base_max_mw - self.cfg.base_min_mw) / 2
        mid = (self.cfg.base_max_mw + self.cfg.base_min_mw) / 2
        return mid + amp * math.sin(2 * math.pi * (phase - 0.25))

    def _heatwave_multiplier(self) -> float:
        if not self.heatwave_active:
            return 1.0
        return self.cfg.heatwave_peak_multiplier

    def _trace_base(self, tick: int) -> float | None:
        if self.trace_source is None:
            return None

        if hasattr(self.trace_source, "base_load_mw_for_tick"):
            return self.trace_source.base_load_mw_for_tick(tick)
        if callable(self.trace_source):
            return self.trace_source(tick)
        raise TypeError(
            "trace_source must be callable or expose base_load_mw_for_tick(tick)"
        )

    def tick(self) -> GridState:
        simulation_tick = self.tick_count + 1
        if self.trace_source is None:
            base = self._diurnal_base()
            mult = self._heatwave_multiplier()
        else:
            trace_base = self._trace_base(simulation_tick)
            if trace_base is None:
                raise ValueError(
                    f"Grid trace has no baseline load for tick {simulation_tick}"
                )
            base = trace_base
            mult = (
                self._heatwave_multiplier()
                if self.apply_heatwave_to_trace
                else 1.0
            )
        state = GridState(
            base_load_mw=base,
            dc_load_mw=self._dc_load_mw,
            threshold_mw=self.cfg.threshold_mw,
            heatwave_multiplier=mult,
        )
        self.tick_count += 1
        if self.heatwave_active:
            self.heatwave_ticks_remaining -= 1
            if self.heatwave_ticks_remaining <= 0:
                self.heatwave_active = False
        return state

    def risk_band(self, state: GridState) -> str:
        if state.total_load_mw < state.threshold_mw * 0.95:
            return "normal"
        if state.total_load_mw < state.threshold_mw:
            return "elevated"
        if state.total_load_mw < state.threshold_mw * 1.05:
            return "alert"
        return "critical"


class RegionalGridTraceSource:
    """Aggregate regional trace rows into the current scalar grid baseline.

    Rows are expected to expose tick, gridshift_region, and load_mw attributes.
    The trace is measured baseline load; experimental perturbations such as
    data-center spikes remain separate in the data-center model.
    """

    def __init__(self, points: Iterable, selected_regions: set[str] | None = None):
        self.selected_regions = set(selected_regions) if selected_regions else None
        self._load_by_tick: dict[int, float] = {}

        for point in points:
            region = point.gridshift_region
            if (
                self.selected_regions is not None
                and region not in self.selected_regions
            ):
                continue
            tick = int(point.tick)
            self._load_by_tick[tick] = self._load_by_tick.get(tick, 0.0) + float(
                point.load_mw
            )

        if not self._load_by_tick:
            raise ValueError("RegionalGridTraceSource has no usable trace points")

    def base_load_mw_for_tick(self, tick: int) -> float | None:
        return self._load_by_tick.get(tick)

    def baseline_range_mw(self) -> tuple[float, float]:
        values = list(self._load_by_tick.values())
        return min(values), max(values)


if __name__ == "__main__":
    # Smoke test
    g = BostonGridModel()
    g.start_heatwave(duration_ticks=60)
    g.set_dc_load(85)
    for i in range(10):
        s = g.tick()
        print(f"tick {i}: base={s.base_load_mw:.1f} "
              f"total={s.total_load_mw:.1f} risk={g.risk_band(s)}")
