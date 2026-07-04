"""Smoke check for delayed-job resume and SLA behavior.

Run: python -m experiments.delayed_lifecycle_check
"""
from core.config import SimConfig
from core.dc_simulator import DataCenterFleet
from core.state import Job, JobPriority


def main() -> None:
    cfg = SimConfig(seed=1, delay_ticks=2, sla_slack=0.0)
    fleet = DataCenterFleet(seed=1, config=cfg)
    dc = next(iter(fleet.dcs.values()))

    job = Job(
        job_id="J-delay",
        priority=JobPriority.FLEXIBLE,
        power_mw=1.0,
        duration_ticks=2,
        home_dc=dc.dc_id,
        region=dc.region,
        submit_tick=fleet.now,
        base_duration_ticks=2,
    )
    dc.add_running_job(job)
    fleet.assert_load_caches_valid()

    assert fleet.delay(job.job_id)
    fleet.assert_load_caches_valid()
    assert len(dc.running_jobs) == 0
    assert len(dc.delayed_jobs) == 1
    assert job.duration_ticks == 2

    fleet.tick()
    fleet.place_pending()
    fleet.assert_load_caches_valid()
    assert len(dc.delayed_jobs) == 1
    assert job.duration_ticks == 2

    fleet.tick()
    fleet.place_pending()
    fleet.assert_load_caches_valid()
    assert len(dc.delayed_jobs) == 0
    assert len(dc.running_jobs) == 1

    fleet.tick()
    fleet.tick()
    fleet.assert_load_caches_valid()
    assert len(fleet.completed) == 1
    assert fleet.completed[0].completed_tick == 4
    assert fleet.sla_stats()["sla_violation_rate"] == 1.0

    print("OK: delayed job resumed, completed late, and violated SLA.")


if __name__ == "__main__":
    main()
