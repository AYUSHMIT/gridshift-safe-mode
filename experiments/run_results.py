"""Reproducible results harness for the paper experiments."""
from __future__ import annotations

import os

from core.config import SimConfig
from core.orchestrator import GridShiftOrchestrator
from core.state import TrustLevel

from experiments.metrics import ensure_dir, summarize_runs, write_rows_csv


SEEDS = list(range(10))
TICKS = 50
INITIAL_BURST = 30
STEADY_BURST = 5
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")

METRIC_KEYS = [
    "overload_exceedance",
    "safe_mode_ticks",
    "bad_node_ticks",
    "migrations",
    "completion_rate",
    "sla_violation_rate",
]

POLICY_COMPARE_ATTACK_CFG = dict(
    compromised_fraction=0.34,
    attack_start_tick=15,
    lie_delta_mw=16.0,
    spike_mw=30.0,
    firmware_tamper=True,
    replay_nonce=False,
    key_compromise=False,
    detector_mode="fusion",
)

DETECTOR_COMPARE_BASE_ATTACK_CFG = dict(
    compromised_fraction=0.34,
    attack_start_tick=15,
    spike_mw=30.0,
    replay_nonce=False,
    key_compromise=False,
)

DETECTOR_COMPARE_CASES = {
    "behavioral-lie-only": dict(
        firmware_tamper=False,
        lie_delta_mw=16.0,
    ),
    "firmware-tamper-only": dict(
        firmware_tamper=True,
        lie_delta_mw=0.0,
    ),
}


def _run_trial(
    *,
    experiment: str,
    case: str,
    policy: str,
    detector_mode: str,
    seed: int,
    attack_cfg: dict,
) -> dict:
    config_kwargs = {
        "seed": seed,
        "policy": policy,
        "detector_mode": detector_mode,
    }
    config_kwargs.update(attack_cfg)
    cfg = SimConfig(**config_kwargs)
    orch = GridShiftOrchestrator(seed=seed, config=cfg)
    orch.trigger_heatwave(TICKS)
    orch.submit_job_burst(INITIAL_BURST)

    overload_exceedance = 0.0
    safe_mode_ticks = 0
    bad_node_ticks = 0

    for t in range(1, TICKS + 1):
        if t % 5 == 0:
            orch.submit_job_burst(STEADY_BURST)

        result = orch.tick()
        overload_exceedance += max(
            0.0, result.grid.total_load_mw - result.grid.threshold_mw
        )
        if result.safe_mode:
            safe_mode_ticks += 1
        bad_node_ticks += sum(
            1 for assessment in result.assessments
            if assessment.level != TrustLevel.TRUSTED
        )

    submitted = orch.fleet._job_counter
    completed = len(orch.fleet.completed)
    sla = orch.fleet.sla_stats()

    return {
        "experiment": experiment,
        "case": case,
        "policy": policy,
        "detector_mode": detector_mode,
        "seed": seed,
        "overload_exceedance": float(overload_exceedance),
        "safe_mode_ticks": float(safe_mode_ticks),
        "bad_node_ticks": float(bad_node_ticks),
        "migrations": float(orch.fleet.migration_count),
        "completion_rate": completed / max(1, submitted),
        "sla_violation_rate": float(sla["sla_violation_rate"]),
    }


def _group_summary(rows: list[dict], group_fields: list[str]) -> list[dict]:
    grouped: dict[tuple, list[dict]] = {}
    for row in rows:
        key = tuple(row[field] for field in group_fields)
        grouped.setdefault(key, []).append(row)

    summary_rows: list[dict] = []
    for key in sorted(grouped):
        group_rows = grouped[key]
        metric_runs = [
            {metric: row[metric] for metric in METRIC_KEYS}
            for row in group_rows
        ]
        summary = summarize_runs(metric_runs)
        base = {field: value for field, value in zip(group_fields, key)}
        base["n"] = len(group_rows)
        for metric in METRIC_KEYS:
            base[f"{metric}_mean"] = summary.get(metric, {}).get("mean", 0.0)
            base[f"{metric}_ci"] = summary.get(metric, {}).get("ci", 0.0)
        summary_rows.append(base)

    return summary_rows


def run_policy_compare() -> list[dict]:
    raw_rows: list[dict] = []
    for policy in ["none", "freeze", "directional"]:
        for seed in SEEDS:
            raw_rows.append(
                _run_trial(
                    experiment="policy_compare",
                    case="scheduled",
                    policy=policy,
                    detector_mode="fusion",
                    seed=seed,
                    attack_cfg=POLICY_COMPARE_ATTACK_CFG,
                )
            )
    return raw_rows


def run_detector_compare() -> list[dict]:
    raw_rows: list[dict] = []
    for case, overrides in DETECTOR_COMPARE_CASES.items():
        attack_cfg = {**DETECTOR_COMPARE_BASE_ATTACK_CFG, **overrides}
        for detector_mode in ["attestation-only", "behavior-only", "fusion"]:
            for seed in SEEDS:
                raw_rows.append(
                    _run_trial(
                        experiment="detector_compare",
                        case=case,
                        policy="directional",
                        detector_mode=detector_mode,
                        seed=seed,
                        attack_cfg=attack_cfg,
                    )
                )
    return raw_rows


def main() -> None:
    ensure_dir(RESULTS_DIR)

    policy_rows = run_policy_compare()
    detector_rows = run_detector_compare()
    summary_rows = _group_summary(
        policy_rows + detector_rows,
        ["experiment", "case", "policy", "detector_mode"],
    )

    policy_csv = os.path.join(RESULTS_DIR, "policy_compare.csv")
    detector_csv = os.path.join(RESULTS_DIR, "detector_compare.csv")
    summary_csv = os.path.join(RESULTS_DIR, "summary.csv")

    write_rows_csv(policy_csv, policy_rows)
    write_rows_csv(detector_csv, detector_rows)
    write_rows_csv(summary_csv, summary_rows)

    print("wrote:", policy_csv)
    print("wrote:", detector_csv)
    print("wrote:", summary_csv)
    print("rows:", {
        "policy_compare": len(policy_rows),
        "detector_compare": len(detector_rows),
        "summary": len(summary_rows),
    })


if __name__ == "__main__":
    main()
