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
from core.config import SimConfig


@dataclass
class DataCenter:
    dc_id: str
    region: str
    capacity_mw: float
    migration_overhead_mw: float = 0.0   # extra draw per in-flight job (set by fleet)
    running_jobs: List[Job] = field(default_factory=list)
    delayed_jobs: List[Job] = field(default_factory=list)
    running_load_mw: float = 0.0
    migrating_jobs_count: int = 0
    # Attack simulation:
    lying: bool = False
    lie_delta_mw: float = 0.0     # positive = under-reports by this much
    spike_mw: float = 0.0         # extra real load (e.g., attacker-controlled)

    def true_load_mw(self) -> float:
        return (
            self.running_load_mw
            + self.spike_mw
            + self.migration_overhead_mw * self.migrating_jobs_count
        )

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

    def add_running_job(self, job: Job) -> None:
        self.running_jobs.append(job)
        self.running_load_mw += job.power_mw
        if job.migrating:
            self.migrating_jobs_count += 1

    def remove_running_job(self, job: Job) -> None:
        self.running_jobs.remove(job)
        self.running_load_mw -= job.power_mw
        if job.migrating:
            self.migrating_jobs_count -= 1

    def finish_migration(self, job: Job) -> None:
        if job.migrating:
            job.migrating = False
            self.migrating_jobs_count -= 1
        job.migration_target = None

    def assert_load_cache_valid(self, tolerance: float = 1e-9) -> None:
        actual_load = sum(j.power_mw for j in self.running_jobs)
        actual_migrating = sum(1 for j in self.running_jobs if j.migrating)
        if abs(actual_load - self.running_load_mw) > tolerance:
            raise AssertionError(
                f"{self.dc_id} running_load_mw cache mismatch: "
                f"cached={self.running_load_mw}, actual={actual_load}"
            )
        if actual_migrating != self.migrating_jobs_count:
            raise AssertionError(
                f"{self.dc_id} migrating_jobs_count cache mismatch: "
                f"cached={self.migrating_jobs_count}, actual={actual_migrating}"
            )


class DataCenterFleet:
    def __init__(self, seed: Optional[int] = None,
                 config: Optional[SimConfig] = None):
        self.cfg = config or SimConfig()
        if seed is not None:
            random.seed(seed)
        elif self.cfg.seed is not None:
            random.seed(self.cfg.seed)
        self.dcs = self._build_fleet()
        self.pending_jobs: List[Job] = []
        self.completed: List[Job] = []
        self._job_counter = 0
        self.now = 0   # fleet clock; advanced once per tick()
        # cumulative DC-side metrics (Arash's columns for the logger)
        self.migration_count = 0
        self.migration_overhead_accum = 0.0   # MW.ticks spent on migration
        self.blocked_count = 0                 # migrations refused by safety

    def _build_fleet(self) -> dict:
        """Build dcs_per_region DCs in each configured region.

        IDs use a 3-letter region code (Boston->BOS, ...). The default
        config reproduces BOS-1/BOS-2/WOR-1 as a superset, so existing
        demo/attack helpers keep working.
        """
        dcs = {}
        per = self.cfg.dcs_per_region
        for region in self.cfg.regions:
            code = region[:3].upper()
            base_cap = self.cfg.region_capacity_mw / per
            for i in range(1, per + 1):
                cap = round(base_cap * random.uniform(0.85, 1.15), 1)
                dc_id = f"{code}-{i}"
                dcs[dc_id] = DataCenter(
                    dc_id, region, capacity_mw=cap,
                    migration_overhead_mw=self.cfg.migration_overhead_mw,
                )
        return dcs

    def region_of(self, dc_id: str) -> str:
        return self.dcs[dc_id].region

    def submit_burst(self, n: int = 10):
        priorities = [JobPriority.CRITICAL, JobPriority.FLEXIBLE,
                      JobPriority.MIGRATABLE]
        weights = [0.2, 0.4, 0.4]
        for _ in range(n):
            pri = random.choices(priorities, weights=weights)[0]
            dur = random.randint(self.cfg.job_dur_min, self.cfg.job_dur_max)
            self.submit_jobs(
                [{
                    "priority": pri,
                    "power_mw": round(random.uniform(
                        self.cfg.job_power_min_mw,
                        self.cfg.job_power_max_mw,
                    ), 2),
                    "duration_ticks": dur,
                }]
            )

    def submit_jobs(self, job_specs):
        """Submit explicit experiment-generated job specs."""
        for spec in job_specs:
            priority = _spec_value(spec, "priority")
            power_mw = float(_spec_value(spec, "power_mw"))
            duration_ticks = int(_spec_value(spec, "duration_ticks"))
            if duration_ticks <= 0:
                raise ValueError("Submitted jobs must have positive duration_ticks")
            if power_mw < 0:
                raise ValueError("Submitted jobs must have non-negative power_mw")

            if not isinstance(priority, JobPriority):
                priority = JobPriority(str(priority))

            home = _spec_value(spec, "home_dc", None)
            if home is None:
                home = random.choice(list(self.dcs.keys()))
            if home not in self.dcs:
                raise ValueError(f"Unknown home_dc for submitted job: {home}")

            self._job_counter += 1
            job = Job(
                job_id=f"J{self._job_counter:04d}",
                priority=priority,
                power_mw=round(power_mw, 2),
                duration_ticks=duration_ticks,
                home_dc=home,
                region=self.dcs[home].region,
                submit_tick=self.now,
                base_duration_ticks=duration_ticks,
            )
            self.pending_jobs.append(job)

    def submit_poisson(self):
        """Stochastic arrivals: ~Poisson(arrival_per_tick) new jobs this tick.

        Used by the Monte-Carlo runner so workload intensity is a single
        sweepable knob (feeds the Fig 2 Pareto)."""
        lam = self.cfg.arrival_per_tick
        # simple Knuth Poisson sampler
        import math
        L, k, p = math.exp(-lam), 0, 1.0
        while True:
            k += 1
            p *= random.random()
            if p <= L:
                break
        self.submit_burst(k - 1)

    def place_pending(self):
        """Greedy-place resumable delayed jobs, then pending jobs."""
        for dc in self.dcs.values():
            still_delayed = []
            for job in dc.delayed_jobs:
                if job.delayed_until_tick is not None and self.now < job.delayed_until_tick:
                    still_delayed.append(job)
                    continue
                if self._place_job(job):
                    job.delayed_until_tick = None
                else:
                    still_delayed.append(job)
            dc.delayed_jobs = still_delayed

        still_pending = []
        for job in self.pending_jobs:
            if not self._place_job(job):
                still_pending.append(job)
        self.pending_jobs = still_pending

    def _place_job(self, job: Job) -> bool:
        dc = self.dcs[job.home_dc]
        if dc.can_accept(job):
            dc.add_running_job(job)
            return True

        # Try any DC that can accept it
        for alt_dc in self.dcs.values():
            if alt_dc.can_accept(job):
                job.home_dc = alt_dc.dc_id
                job.region = alt_dc.region
                alt_dc.add_running_job(job)
                return True
        return False

    def migrate(self, job_id: str, target_dc_id: str) -> bool:
        """Begin a k-tick migration of job to target_dc.

        The job is moved to the target immediately (so the source region
        gets load relief at once -- important for the unwind policy), but
        is marked in-flight: it does not make progress for `migration_ticks`
        and incurs migration overhead at the target during that window.
        """
        for dc in self.dcs.values():
            for j in dc.running_jobs:
                if j.job_id == job_id:
                    if j.migrating:
                        return False             # already in-flight
                    target = self.dcs[target_dc_id]
                    if not target.can_accept(j):
                        return False
                    dc.remove_running_job(j)
                    j.migration_source = dc.dc_id
                    j.migration_target = target_dc_id
                    j.home_dc = target_dc_id
                    j.region = target.region
                    if self.cfg.migration_ticks > 0:
                        j.migrating = True
                        j.migration_remaining = self.cfg.migration_ticks
                    target.add_running_job(j)
                    self.migration_count += 1
                    return True
        return False

    def delay(self, job_id: str) -> bool:
        for dc in self.dcs.values():
            for j in dc.running_jobs:
                if j.job_id == job_id and j.priority != JobPriority.CRITICAL:
                    dc.remove_running_job(j)
                    j.delayed_until_tick = self.now + max(0, self.cfg.delay_ticks)
                    dc.delayed_jobs.append(j)
                    return True
        return False

    def tick(self):
        """Advance all running jobs by one tick. Completed jobs are retired.

        In-flight (migrating) jobs do not make progress: they burn down their
        migration timer and accrue overhead, which lengthens completion time
        and thus drives the SLA penalty.
        """
        self.now += 1
        for dc in self.dcs.values():
            still_running = []
            for j in dc.running_jobs:
                if j.migrating:
                    self.migration_overhead_accum += dc.migration_overhead_mw
                    j.migration_remaining -= 1
                    if j.migration_remaining <= 0:
                        dc.finish_migration(j)
                    still_running.append(j)   # occupies the DC, no progress
                    continue
                j.duration_ticks -= 1
                if j.duration_ticks <= 0:
                    dc.running_load_mw -= j.power_mw
                    j.completed_tick = self.now
                    self.completed.append(j)
                else:
                    still_running.append(j)
            dc.running_jobs = still_running

    def total_true_load_mw(self) -> float:
        return sum(dc.true_load_mw() for dc in self.dcs.values())

    def region_load_mw(self) -> dict:
        """True load aggregated per region (for Mehran's per-region limits)."""
        out = {r: 0.0 for r in self.cfg.regions}
        for dc in self.dcs.values():
            out[dc.region] = out.get(dc.region, 0.0) + dc.true_load_mw()
        return out

    def load_on_nodes(self, node_ids) -> float:
        """True load (MW) currently residing on the given set of nodes.

        With node_ids = the untrusted/compromised set, this is the
        load-weighted EXPOSURE ('trapped load') metric: it captures how
        much real load sits on bad nodes -- which the directional policy
        drains and freeze-all traps. Unlike a node-count, it responds to
        the safety policy."""
        ids = set(node_ids)
        return sum(dc.true_load_mw()
                   for k, dc in self.dcs.items() if k in ids)

    def inflight_count(self) -> int:
        return sum(dc.migrating_jobs_count for dc in self.dcs.values())

    def assert_load_caches_valid(self) -> None:
        for dc in self.dcs.values():
            dc.assert_load_cache_valid()

    def sla_stats(self) -> dict:
        """SLA outcome over completed jobs. A job breaches SLA if it finishes
        later than submit + base_duration*(1+slack); delays and migrations
        push completion out, so this is where their cost shows up."""
        comp = self.completed
        if not comp:
            return {"completed": 0, "sla_violations": 0,
                    "sla_violation_rate": 0.0, "mean_lateness": 0.0}
        viol, late_sum = 0, 0
        for j in comp:
            deadline = j.sla_deadline(self.cfg.sla_slack)
            late = (j.completed_tick or self.now) - deadline
            if late > 0:
                viol += 1
                late_sum += late
        return {
            "completed": len(comp),
            "sla_violations": viol,
            "sla_violation_rate": viol / len(comp),
            "mean_lateness": late_sum / len(comp),
        }

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


def _spec_value(spec, key: str, default=None):
    if isinstance(spec, dict):
        return spec.get(key, default)
    return getattr(spec, key, default)


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
