"""Observed-load channel sensitivity ablation.

This compact experiment perturbs the grid-side observed-load signal used by the
behavior detector and observed-load unwind override. It is a sensitivity check,
not a claim that the observed-load channel is adversary-proof.
"""

from __future__ import annotations

import argparse
import os

from core.config import SimConfig
from core.orchestrator import GridShiftOrchestrator
from core.state import TrustLevel
from experiments.metrics import ensure_dir, write_rows_csv
from experiments.run_trace_compare import RESULTS_DIR


DEFAULT_NOISE_STD_MW = [0.0, 5.0, 10.0, 25.0]
DEFAULT_BIAS_MW = [0.0, 10.0, -10.0]
DEFAULT_SEEDS = [0, 1, 2]
DEFAULT_DETECTOR_MODES = ["behavior-only", "fusion"]
TICKS = 50
INITIAL_BURST = 30
STEADY_BURST = 5

ATTACK_CFG = dict(
    compromised_fraction=0.34,
    attack_start_tick=15,
    lie_delta_mw=16.0,
    spike_mw=30.0,
    firmware_tamper=False,
    replay_nonce=False,
    key_compromise=False,
)


def _parse_float_list(value: str) -> list[float]:
    return [float(part.strip()) for part in value.split(",") if part.strip()]


def _parse_int_list(value: str) -> list[int]:
    return [int(part.strip()) for part in value.split(",") if part.strip()]


def _parse_str_list(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _run_one(
    *,
    seed: int,
    detector_mode: str,
    noise_std_mw: float,
    bias_mw: float,
) -> dict:
    cfg = SimConfig(
        seed=seed,
        policy="directional",
        detector_mode=detector_mode,
        observed_load_noise_std_mw=noise_std_mw,
        observed_load_bias_mw=bias_mw,
        **ATTACK_CFG,
    )
    orch = GridShiftOrchestrator(seed=seed, config=cfg)
    orch.trigger_heatwave(TICKS)
    orch.submit_job_burst(INITIAL_BURST)

    overload = 0.0
    safe_mode_ticks = 0
    bad_node_ticks = 0
    compromised_assessments = 0
    suspicious_assessments = 0
    trusted_assessments = 0
    total_mismatch_mw = 0.0
    assessment_count = 0
    post_attack_bad_node_ticks = 0

    for tick in range(1, TICKS + 1):
        if tick % 5 == 0:
            orch.submit_job_burst(STEADY_BURST)

        result = orch.tick()
        overload += max(0.0, result.grid.total_load_mw - result.grid.threshold_mw)
        if result.safe_mode:
            safe_mode_ticks += 1

        tick_bad_nodes = 0
        for assessment in result.assessments:
            assessment_count += 1
            total_mismatch_mw += assessment.mismatch_mw
            if assessment.level == TrustLevel.COMPROMISED:
                compromised_assessments += 1
                tick_bad_nodes += 1
            elif assessment.level == TrustLevel.SUSPICIOUS:
                suspicious_assessments += 1
                tick_bad_nodes += 1
            else:
                trusted_assessments += 1
        bad_node_ticks += tick_bad_nodes
        if tick >= cfg.attack_start_tick:
            post_attack_bad_node_ticks += tick_bad_nodes

    submitted = orch.fleet._job_counter
    completed = len(orch.fleet.completed)
    sla = orch.fleet.sla_stats()
    return {
        "experiment": "observed_load_sensitivity",
        "seed": seed,
        "detector_mode": detector_mode,
        "policy": "directional",
        "observed_load_noise_std_mw": float(noise_std_mw),
        "observed_load_bias_mw": float(bias_mw),
        "safe_mode_ticks": int(safe_mode_ticks),
        "bad_node_ticks": int(bad_node_ticks),
        "post_attack_bad_node_ticks": int(post_attack_bad_node_ticks),
        "compromised_assessments": int(compromised_assessments),
        "suspicious_assessments": int(suspicious_assessments),
        "trusted_assessments": int(trusted_assessments),
        "mean_mismatch_mw": (
            total_mismatch_mw / assessment_count if assessment_count else 0.0
        ),
        "overload": float(overload),
        "completion_rate": completed / max(1, submitted),
        "sla_violation_rate": float(sla["sla_violation_rate"]),
        "migrations": float(orch.fleet.migration_count),
        "submitted_jobs": int(submitted),
        "completed_jobs": int(completed),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run compact observed-load noise/bias sensitivity ablation."
    )
    parser.add_argument(
        "--noise-std-mw",
        type=_parse_float_list,
        default=DEFAULT_NOISE_STD_MW,
        help="Comma-separated observed-load noise std values in MW.",
    )
    parser.add_argument(
        "--bias-mw",
        type=_parse_float_list,
        default=DEFAULT_BIAS_MW,
        help="Comma-separated observed-load bias values in MW.",
    )
    parser.add_argument(
        "--seeds",
        type=_parse_int_list,
        default=DEFAULT_SEEDS,
        help="Comma-separated seed list. Defaults to 0,1,2.",
    )
    parser.add_argument(
        "--detector-modes",
        type=_parse_str_list,
        default=DEFAULT_DETECTOR_MODES,
        help="Comma-separated detector modes. Defaults to behavior-only,fusion.",
    )
    parser.add_argument(
        "--output",
        default=os.path.join(RESULTS_DIR, "observed_load_sensitivity.csv"),
        help="Output CSV path under experiments/results by default.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    rows = []
    for detector_mode in args.detector_modes:
        for noise_std_mw in args.noise_std_mw:
            for bias_mw in args.bias_mw:
                for seed in args.seeds:
                    rows.append(
                        _run_one(
                            seed=seed,
                            detector_mode=detector_mode,
                            noise_std_mw=noise_std_mw,
                            bias_mw=bias_mw,
                        )
                    )
    ensure_dir(os.path.dirname(args.output))
    write_rows_csv(args.output, rows)
    print("wrote:", args.output)
    print("rows:", len(rows))
    print("detector_modes:", ",".join(args.detector_modes))
    print("noise_std_mw:", ",".join(str(value) for value in args.noise_std_mw))
    print("bias_mw:", ",".join(str(value) for value in args.bias_mw))
    print("seeds:", ",".join(str(value) for value in args.seeds))


if __name__ == "__main__":
    main()
