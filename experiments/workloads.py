"""Pluggable workload sources for experiment runners.

The experiment layer keeps workload generation separate from the simulator:
synthetic workloads reproduce the existing fixed burst schedule, while trace
workloads consume a target DC power profile and translate it into arrivals
without changing the regional grid model.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from experiments.trace_loader import PowerTracePoint, load_power_trace, power_at_tick


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
    def jobs_for_tick(self, tick: int) -> int:
        """Return the number of arrivals to submit at the given tick."""


@dataclass(frozen=True)
class SyntheticWorkloadSource(WorkloadSource):
    """Reproduce the current synthetic workload schedule exactly."""

    initial_burst: int = 30
    steady_burst: int = 5
    steady_period: int = 5
    name: str = "synthetic"

    def jobs_for_tick(self, tick: int) -> int:
        if tick == 1:
            return self.initial_burst
        if tick % self.steady_period == 0:
            return self.steady_burst
        return 0


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
