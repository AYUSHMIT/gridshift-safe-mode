"""Pluggable workload sources for experiment runners.

The experiment layer keeps workload generation separate from the simulator:
synthetic workloads reproduce the existing fixed burst schedule, while trace
workloads consume a target DC power profile and translate it into arrivals
without changing the regional grid model.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
import math
import random

from core.state import JobPriority
from experiments.trace_loader import (
    DerivedTracePoint,
    PowerTracePoint,
    derived_at_tick,
    load_derived_trace,
    load_power_trace,
    power_at_tick,
)


@dataclass(frozen=True)
class WorkloadJobSpec:
    """Experiment-side job description consumed by DataCenterFleet."""

    priority: JobPriority
    power_mw: float
    duration_ticks: int


def power_to_job_count(
    power_mw: float,
    *,
    mw_per_job: float = 4.0,
    max_jobs_per_tick: int = 20,
) -> int:
    """Convert calibrated DC power into an approximate arrival count.

    This conversion is intentionally kept in the experiment layer so future
    datasets can improve the calibration curve without touching orchestration
    or simulator code.
    """
    if power_mw <= 0:
        return 0

    jobs = round(power_mw / mw_per_job)
    return max(0, min(max_jobs_per_tick, jobs))


class WorkloadSource(ABC):
    """Interface for experiment workload sources."""

    name: str

    def reset(self, seed: int) -> None:
        """Reset any per-run state.

        Most sources are deterministic for a fixed seed and do not need to
        store anything, but the hook keeps the abstraction future-proof.
        """

    @abstractmethod
    def jobs_for_tick(self, tick: int) -> int | list[WorkloadJobSpec]:
        """Return the number of arrivals to submit at the given tick."""

    def expected_work_units(self, ticks: int) -> float:
        """Return expected submitted MW-ticks over a horizon."""
        return 0.0


@dataclass
class SyntheticWorkloadSource(WorkloadSource):
    """Reproduce the current synthetic workload schedule exactly."""

    initial_burst: int = 30
    steady_burst: int = 5
    steady_period: int = 5
    load_scale: float = 1.0
    avg_power_mw: float = 3.75
    avg_duration_ticks: float = 7.5
    name: str = "synthetic"

    def reset(self, seed: int) -> None:
        self._rng = random.Random(seed)

    def jobs_for_tick(self, tick: int) -> int:
        base = 0
        if tick == 1:
            base = self.initial_burst
        elif tick % self.steady_period == 0:
            base = self.steady_burst
        return _scaled_count(base, self.load_scale, getattr(self, "_rng", None))

    def expected_work_units(self, ticks: int) -> float:
        arrivals = 0.0
        for tick in range(1, ticks + 1):
            if tick == 1:
                arrivals += self.initial_burst
            elif tick % self.steady_period == 0:
                arrivals += self.steady_burst
        return arrivals * self.load_scale * self.avg_power_mw * self.avg_duration_ticks


@dataclass(frozen=True)
class TraceWorkloadSource(WorkloadSource):
    """Use a normalized trace to calibrate DC demand.

    The trace is a power profile, not a job replay. The source therefore loads
    target power values and converts them into arrivals internally.
    """

    trace_path: str
    name: str = "trace"

    def load(self) -> list[PowerTracePoint]:
        return load_power_trace(self.trace_path)

    def power_for_tick(self, points: list[PowerTracePoint], tick: int) -> float:
        return power_at_tick(points, tick)

    def jobs_for_tick(self, tick: int) -> int:
        points = self.load()
        power_mw = self.power_for_tick(points, tick)
        return power_to_job_count(power_mw)


def jobs_from_trace_power(power_mw: float) -> int:
    """Internal conversion from trace power to arrivals."""
    return power_to_job_count(power_mw)


@dataclass
class DerivedTraceWorkloadSource(WorkloadSource):
    """Convert derived ClusterData rows into GridShift job specs."""

    trace_path: str
    load_scale: float = 1.0
    min_power_mw: float = 1.5
    max_power_mw: float = 6.0
    name: str = "trace-calibrated"

    def __post_init__(self) -> None:
        self._points: list[DerivedTracePoint] | None = None
        self._rng = random.Random(0)

    def reset(self, seed: int) -> None:
        self._rng = random.Random(seed)

    def load(self) -> list[DerivedTracePoint]:
        if self._points is None:
            self._points = load_derived_trace(self.trace_path)
        return self._points

    def jobs_for_tick(self, tick: int) -> list[WorkloadJobSpec]:
        point = derived_at_tick(self.load(), tick)
        if point is None:
            return []

        arrivals = _scaled_count(point.arrivals, self.load_scale, self._rng)
        return [self._job_from_point(point) for _ in range(arrivals)]

    def expected_work_units(self, ticks: int) -> float:
        total = 0.0
        for point in self.load():
            if 1 <= point.tick <= ticks:
                total += (
                    point.arrivals
                    * self.load_scale
                    * self._power_for_cpu(point.cpu_demand_norm)
                    * self._expected_duration(point)
                )
        return total

    def _job_from_point(self, point: DerivedTracePoint) -> WorkloadJobSpec:
        priority = self._priority_for_point(point)
        duration = self._duration_for_point(point)
        power = self._power_for_cpu(point.cpu_demand_norm)
        jittered_power = power * self._rng.uniform(0.85, 1.15)
        power_mw = min(self.max_power_mw, max(self.min_power_mw, jittered_power))
        return WorkloadJobSpec(
            priority=priority,
            power_mw=round(power_mw, 2),
            duration_ticks=duration,
        )

    def _priority_for_point(self, point: DerivedTracePoint) -> JobPriority:
        if self._rng.random() < point.priority_high_frac:
            return JobPriority.CRITICAL
        if self._rng.random() < point.latency_sensitive_frac:
            return JobPriority.FLEXIBLE
        return JobPriority.MIGRATABLE

    def _duration_for_point(self, point: DerivedTracePoint) -> int:
        p50 = max(1.0, point.duration_p50)
        p90 = max(p50, point.duration_p90)
        draw = self._rng.random()
        if draw < 0.5:
            low, high = max(1, int(round(p50 * 0.5))), max(1, int(round(p50)))
        elif draw < 0.9:
            low, high = max(1, int(round(p50))), max(1, int(round(p90)))
        else:
            tail = max(1, int(round(p90 - p50)))
            low, high = max(1, int(round(p90))), max(1, int(round(p90)) + tail)
        return max(1, self._rng.randint(low, max(low, high)))

    def _expected_duration(self, point: DerivedTracePoint) -> float:
        p50 = max(1.0, point.duration_p50)
        p90 = max(p50, point.duration_p90)
        low_a, high_a = max(1.0, p50 * 0.5), p50
        low_b, high_b = p50, p90
        tail = max(1.0, p90 - p50)
        low_c, high_c = p90, p90 + tail
        return (
            0.5 * ((low_a + high_a) / 2.0)
            + 0.4 * ((low_b + high_b) / 2.0)
            + 0.1 * ((low_c + high_c) / 2.0)
        )

    def _power_for_cpu(self, cpu_demand_norm: float) -> float:
        cpu = min(1.0, max(0.0, cpu_demand_norm))
        span = self.max_power_mw - self.min_power_mw
        return self.min_power_mw + span * math.sqrt(cpu)


def _scaled_count(
    base: float,
    scale: float,
    rng: random.Random | None,
) -> int:
    if base <= 0 or scale <= 0:
        return 0
    scaled = base * scale
    whole = int(math.floor(scaled))
    frac = scaled - whole
    if frac > 0 and (rng or random).random() < frac:
        whole += 1
    return whole
