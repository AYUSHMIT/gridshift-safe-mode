# core/dc_simulator.py
"""
[Owned by: Optical Network & Data Center teammate]

Models the data center fleet, job queue, and the two telemetry channels:
  - reported_load_mw(): what the controller claims (can lie)
  - observed_load_mw(): grid-side sensor ground truth (cannot lie)
"""
import random
from dataclasses import dataclass, field
from typing import List, Optional
from core.state import Job, JobPriority


@dataclass
class DataCenter:
    dc_id: str
    region: str
    capacity_mw: float
    migration_cost_mw: float = 2.0
    running_jobs: List[Job] = field(default_factory=list)
    delayed_jobs: List[Job] = field(default_factory=list)
    # Attack simulation:
    lying: bool = False
    lie_delta_mw: float = 0.0     # positive = under-reports by this much
    spike_mw: float = 0.0         # extra real load (e.g., attacker-controlled)

    def true_load_mw(self) -> float:
        return sum(j.power_mw for j in self.running_jobs) + self.spike_mw

    def reported_load_mw(self) -> float:
        """What the controller claims. Can lie if compromised."""
        true = self.true_load_mw()
        if self.lying:
            return max(0.0, true - self.lie_delta_mw)
        return true

    def observed_load_mw(self) -> float:
        """Grid-side sensor ground truth. Cannot lie."""
        return self.true_load_mw()

    def can_accept(self, job: Job) -> bool:
        return self.true_load_mw() + job.power_mw <= self.capacity_mw


class DataCenterFleet:
    def __init__(self, seed: Optional[int] = None):
        if seed is not None:
            random.seed(seed)
        self.dcs = {
            "BOS-1": DataCenter("BOS-1", "Boston",    capacity_mw=60),
            "BOS-2": DataCenter("BOS-2", "Boston",    capacity_mw=50),
            "WOR-1": DataCenter("WOR-1", "Worcester", capacity_mw=45),
        }
        self.pending_jobs: List[Job] = []
        self.completed: List[Job] = []
        self._job_counter = 0

    def submit_burst(self, n: int = 10):
        priorities = [JobPriority.CRITICAL, JobPriority.FLEXIBLE,
                      JobPriority.MIGRATABLE]
        weights = [0.2, 0.4, 0.4]
        for _ in range(n):
            self._job_counter += 1
            pri = random.choices(priorities, weights=weights)[0]
            home = random.choice(list(self.dcs.keys()))
            job = Job(
                job_id=f"J{self._job_counter:04d}",
                priority=pri,
                power_mw=round(random.uniform(1.5, 6.0), 2),
                duration_ticks=random.randint(3, 12),
                home_dc=home,
            )
            self.pending_jobs.append(job)

    def place_pending(self):
        """Greedy-place pending jobs in their home DC if possible."""
        still_pending = []
        for job in self.pending_jobs:
            dc = self.dcs[job.home_dc]
            if dc.can_accept(job):
                dc.running_jobs.append(job)
            else:
                # Try any DC that can accept it
                placed = False
                for alt_dc in self.dcs.values():
                    if alt_dc.can_accept(job):
                        job.home_dc = alt_dc.dc_id
                        alt_dc.running_jobs.append(job)
                        placed = True
                        break
                if not placed:
                    still_pending.append(job)
        self.pending_jobs = still_pending

    def migrate(self, job_id: str, target_dc_id: str) -> bool:
        for dc in self.dcs.values():
            for j in dc.running_jobs:
                if j.job_id == job_id:
                    target = self.dcs[target_dc_id]
                    if target.can_accept(j):
                        dc.running_jobs.remove(j)
                        j.home_dc = target_dc_id
                        target.running_jobs.append(j)
                        return True
        return False

    def delay(self, job_id: str) -> bool:
        for dc in self.dcs.values():
            for j in dc.running_jobs:
                if j.job_id == job_id and j.priority != JobPriority.CRITICAL:
                    dc.running_jobs.remove(j)
                    dc.delayed_jobs.append(j)
                    return True
        return False

    def tick(self):
        """Advance all running jobs by one tick. Completed jobs are retired."""
        for dc in self.dcs.values():
            still_running = []
            for j in dc.running_jobs:
                j.duration_ticks -= 1
                if j.duration_ticks <= 0:
                    self.completed.append(j)
                else:
                    still_running.append(j)
            dc.running_jobs = still_running

    def total_true_load_mw(self) -> float:
        return sum(dc.true_load_mw() for dc in self.dcs.values())

    # --- Attack controls (for the demo) ---
    def enable_lie(self, dc_id: str, lie_delta_mw: float):
        self.dcs[dc_id].lying = True
        self.dcs[dc_id].lie_delta_mw = lie_delta_mw

    def disable_lie(self, dc_id: str):
        self.dcs[dc_id].lying = False
        self.dcs[dc_id].lie_delta_mw = 0.0

    def spike(self, dc_id: str, extra_mw: float):
        """
        Supervisor-scenario attack helper: inject extra real power draw
        on a DC (representing an attacker cranking up a workload they
        control). The observed-load sensor will see this; reported_load
        will only reflect it if the DC is not lying.
        """
        self.dcs[dc_id].spike_mw = extra_mw

    def clear_spike(self, dc_id: str):
        self.dcs[dc_id].spike_mw = 0.0


if __name__ == "__main__":
    fleet = DataCenterFleet(seed=42)
    fleet.submit_burst(12)
    fleet.place_pending()
    for dc_id, dc in fleet.dcs.items():
        print(f"{dc_id}: true={dc.true_load_mw():.1f} "
              f"reported={dc.reported_load_mw():.1f} "
              f"jobs={len(dc.running_jobs)}")
    fleet.enable_lie("BOS-1", lie_delta_mw=16.0)
    print("\nAfter attack on BOS-1:")
    print(f"  reported={fleet.dcs['BOS-1'].reported_load_mw():.1f}")
    print(f"  observed={fleet.dcs['BOS-1'].observed_load_mw():.1f}")
