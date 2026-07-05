"""Compare synthetic and derived trace-calibrated workload inputs."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass

from core.grid_model import GridConfig, RegionalGridTraceSource
from experiments.grid_trace_loader import load_grid_trace
from experiments.metrics import ensure_dir, summarize_runs, write_rows_csv
from experiments.trial_runner import TrialSpec, run_trial
from experiments.workloads import DerivedTraceWorkloadSource, SyntheticWorkloadSource

SEEDS = list(range(10))
TICKS = 50
INITIAL_BURST = 30
STEADY_BURST = 5
LOAD_SCALES = [1.0]
ROOT = os.path.dirname(os.path.dirname(__file__))
TRACE_PATH = os.path.join(
    ROOT,
    "data",
    "traces",
    "google_cluster_derived_5min.csv",
)
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")

POLICIES = ["none", "freeze", "directional"]

ATTACK_CFG = dict(
    compromised_fraction=0.34,
    attack_start_tick=15,
    lie_delta_mw=16.0,
    spike_mw=30.0,
    firmware_tamper=True,
    replay_nonce=False,
    key_compromise=False,
    detector_mode="fusion",
)

METRIC_KEYS = [
    "overload",
    "safe_mode_ticks",
    "bad_node_ticks",
    "migrations",
    "experiment_ticks",
    "grid_threshold_mw",
    "grid_baseline_min_mw",
    "grid_baseline_max_mw",
    "grid_eval_first_tick",
    "grid_eval_last_tick",
    "grid_eval_baseline_min_mw",
    "grid_eval_baseline_max_mw",
    "threshold_headroom_mw",
    "attack_start_tick",
    "active_arrival_first_tick",
    "active_arrival_last_tick",
    "submitted_jobs",
    "completed_jobs",
    "total_submitted_work",
    "load_mismatch_pct",
    "completion_rate",
    "sla_violation_rate",
]


@dataclass
class AlignedTraceWorkloadSource:
    """Shift a derived trace in simulation time without editing the CSV.

    Use this when a short derived arrival window needs to overlap the
    safe-mode interval being evaluated. The default start tick preserves the
    trace's native timing.
    """

    source: DerivedTraceWorkloadSource
    start_tick: int | None = None
    name: str = "trace-calibrated"

    def __post_init__(self) -> None:
        points = self.source.load()
        self.native_first_tick = min(point.tick for point in points)
        self.native_last_tick = max(point.tick for point in points)
        if self.start_tick is None:
            self.start_tick = self.native_first_tick
        if self.start_tick < 1:
            raise ValueError("trace_start_tick must be >= 1")

    @property
    def load_scale(self) -> float:
        return self.source.load_scale

    def reset(self, seed: int) -> None:
        self.source.reset(seed)

    def jobs_for_tick(self, tick: int):
        return self.source.jobs_for_tick(self._native_tick(tick))

    def expected_work_units(self, ticks: int) -> float:
        total = 0.0
        for point in self.source.load():
            aligned_tick = self._aligned_tick(point.tick)
            if 1 <= aligned_tick <= ticks:
                total += (
                    point.arrivals
                    * self.load_scale
                    * self.source._power_for_cpu(point.cpu_demand_norm)
                    * self.source._expected_duration(point)
                )
        return total

    def _native_tick(self, aligned_tick: int) -> int:
        return aligned_tick - int(self.start_tick) + self.native_first_tick

    def _aligned_tick(self, native_tick: int) -> int:
        return native_tick - self.native_first_tick + int(self.start_tick)


@dataclass
class AlignedGridTraceSource:
    """Map simulation tick 1 to an arbitrary trace tick."""

    source: RegionalGridTraceSource
    start_tick: int = 1

    def __post_init__(self) -> None:
        if self.start_tick < 1:
            raise ValueError("grid_trace_start_tick must be >= 1")

    def base_load_mw_for_tick(self, tick: int) -> float | None:
        return self.source.base_load_mw_for_tick(self._trace_tick(tick))

    def baseline_range_mw(self) -> tuple[float, float]:
        return self.source.baseline_range_mw()

    def baseline_range_mw_for_sim_window(self, ticks: int) -> tuple[float, float]:
        values = []
        for sim_tick in range(1, ticks + 1):
            trace_tick = self._trace_tick(sim_tick)
            load_mw = self.source.base_load_mw_for_tick(trace_tick)
            if load_mw is None:
                raise ValueError(
                    "Grid trace is missing tick "
                    f"{trace_tick} for simulation tick {sim_tick}"
                )
            values.append(float(load_mw))
        return min(values), max(values)

    def _trace_tick(self, simulation_tick: int) -> int:
        return int(self.start_tick) + int(simulation_tick) - 1


def _trace_workload(
    load_scale: float,
    *,
    trace_start_tick: int | None = None,
) -> AlignedTraceWorkloadSource:
    source = DerivedTraceWorkloadSource(trace_path=TRACE_PATH, load_scale=load_scale)
    return AlignedTraceWorkloadSource(source=source, start_tick=trace_start_tick)


def _sampled_work_units(source, *, seed: int, ticks: int) -> float:
    source.reset(seed)
    total = 0.0
    for tick in range(1, ticks + 1):
        jobs = source.jobs_for_tick(tick)
        if isinstance(jobs, int):
            total += jobs * source.avg_power_mw * source.avg_duration_ticks
        else:
            total += sum(job.power_mw * job.duration_ticks for job in jobs)
    return total


def _synthetic_workload(
    load_scale: float,
    *,
    seed: int,
    trace_start_tick: int | None,
    ticks: int,
) -> SyntheticWorkloadSource:
    trace_units = _trace_workload(
        load_scale,
        trace_start_tick=trace_start_tick,
    ).expected_work_units(ticks)
    synthetic_base = SyntheticWorkloadSource(
        initial_burst=INITIAL_BURST,
        steady_burst=STEADY_BURST,
    )
    synthetic_units = synthetic_base.expected_work_units(ticks)
    matched_scale = trace_units / synthetic_units if synthetic_units else load_scale
    trace_sampled_units = _sampled_work_units(
        _trace_workload(load_scale, trace_start_tick=trace_start_tick),
        seed=seed,
        ticks=ticks,
    )
    return SyntheticWorkloadSource(
        initial_burst=INITIAL_BURST,
        steady_burst=STEADY_BURST,
        load_scale=matched_scale,
        target_work_units=trace_sampled_units,
        calibration_ticks=ticks,
    )


def run_workload_trial(
    *,
    workload_source: str,
    policy: str,
    seed: int,
    experiment: str = "trace_compare",
    load_scale: float = 1.0,
    attack_start_tick: int | None = None,
    trace_start_tick: int | None = None,
    ticks: int = TICKS,
    grid_options: dict | None = None,
    config_overrides: dict | None = None,
) -> dict:
    if workload_source == "synthetic":
        source = _synthetic_workload(
            load_scale,
            seed=seed,
            trace_start_tick=trace_start_tick,
            ticks=ticks,
        )
    elif workload_source == "trace-calibrated":
        source = _trace_workload(load_scale, trace_start_tick=trace_start_tick)
    else:
        raise ValueError(f"Unknown workload_source: {workload_source}")

    attack_cfg = dict(ATTACK_CFG)
    if attack_start_tick is not None:
        attack_cfg["attack_start_tick"] = attack_start_tick

    row = run_trial(
        spec=TrialSpec(
            experiment=experiment,
            case=workload_source,
            policy=policy,
            detector_mode="fusion",
            seed=seed,
            attack_cfg=attack_cfg,
            workload_source=source,
            ticks=ticks,
            grid_config=(grid_options or {}).get("grid_config"),
            grid_trace_source=(grid_options or {}).get("grid_trace_source"),
            apply_heatwave_to_trace=(grid_options or {}).get(
                "apply_heatwave_to_trace",
                False,
            ),
            grid_metadata=(grid_options or {}).get("grid_metadata"),
            config_overrides=config_overrides,
        )
    )

    row["workload_source"] = workload_source
    row["load_scale"] = load_scale
    row["effective_load_scale"] = float(getattr(source, "load_scale", load_scale))
    row["trace_start_tick"] = trace_start_tick or getattr(
        source,
        "native_first_tick",
        None,
    )
    return row


def _grid_options(args: argparse.Namespace) -> dict:
    if args.grid_threshold_mw is not None and args.grid_threshold_headroom_mw is not None:
        raise ValueError(
            "Use either --grid-threshold-mw or --grid-threshold-headroom-mw, not both"
        )

    experiment_ticks = int(args.experiment_ticks)
    if experiment_ticks < 1:
        raise ValueError("--experiment-ticks must be >= 1")

    if args.grid_trace_path is None:
        threshold = args.grid_threshold_mw or 900.0
        return {
            "grid_config": GridConfig(threshold_mw=threshold),
            "grid_trace_source": None,
            "apply_heatwave_to_trace": False,
            "grid_metadata": {
                "grid_threshold_mw": float(threshold),
                "grid_baseline_source": "synthetic",
                "grid_baseline_min_mw": None,
                "grid_baseline_max_mw": None,
                "grid_eval_first_tick": 1,
                "grid_eval_last_tick": experiment_ticks,
                "grid_eval_baseline_min_mw": None,
                "grid_eval_baseline_max_mw": None,
                "grid_threshold_window": None,
                "threshold_strategy": (
                    "explicit_override"
                    if args.grid_threshold_mw is not None
                    else "synthetic_default"
                ),
                "threshold_headroom_mw": None,
            },
        }

    points = load_grid_trace(args.grid_trace_path)
    native_trace_source = RegionalGridTraceSource(points)
    trace_source = AlignedGridTraceSource(
        source=native_trace_source,
        start_tick=args.grid_trace_start_tick,
    )
    baseline_min, baseline_max = trace_source.baseline_range_mw()
    eval_baseline_min, eval_baseline_max = trace_source.baseline_range_mw_for_sim_window(
        experiment_ticks
    )
    eval_first_tick = int(args.grid_trace_start_tick)
    eval_last_tick = eval_first_tick + experiment_ticks - 1

    if args.grid_threshold_headroom_mw is not None:
        if args.grid_threshold_window == "eval":
            threshold_reference_max = eval_baseline_max
            strategy = "max_baseline_plus_headroom_eval"
        else:
            threshold_reference_max = baseline_max
            strategy = "max_baseline_plus_headroom_full"
        threshold = threshold_reference_max + args.grid_threshold_headroom_mw
        headroom = args.grid_threshold_headroom_mw
    elif args.grid_threshold_mw is not None:
        threshold = args.grid_threshold_mw
        strategy = "explicit_override"
        headroom = None
    else:
        raise ValueError(
            "Trace-backed grid runs require --grid-threshold-mw or "
            "--grid-threshold-headroom-mw"
        )

    return {
        "grid_config": GridConfig(threshold_mw=threshold),
        "grid_trace_source": trace_source,
        "apply_heatwave_to_trace": False,
        "grid_metadata": {
            "grid_threshold_mw": float(threshold),
            "grid_baseline_source": args.grid_trace_path,
            "grid_baseline_min_mw": float(baseline_min),
            "grid_baseline_max_mw": float(baseline_max),
            "grid_eval_first_tick": int(eval_first_tick),
            "grid_eval_last_tick": int(eval_last_tick),
            "grid_eval_baseline_min_mw": float(eval_baseline_min),
            "grid_eval_baseline_max_mw": float(eval_baseline_max),
            "grid_threshold_window": (
                args.grid_threshold_window
                if args.grid_threshold_headroom_mw is not None
                else None
            ),
            "threshold_strategy": strategy,
            "threshold_headroom_mw": (
                None if headroom is None else float(headroom)
            ),
        },
    }


def _add_pair_load_mismatch(rows: list[dict]) -> None:
    grouped: dict[tuple, dict[str, dict]] = {}
    for row in rows:
        key = (row["policy"], row["seed"], row["load_scale"])
        grouped.setdefault(key, {})[row["workload_source"]] = row

    for pair in grouped.values():
        synthetic = pair.get("synthetic")
        trace = pair.get("trace-calibrated")
        if synthetic is None or trace is None:
            continue
        trace_work = trace["total_submitted_work"]
        mismatch = synthetic["total_submitted_work"] - trace_work
        denom = max(1.0, trace_work)
        mismatch_pct = 100.0 * mismatch / denom
        for row in (synthetic, trace):
            row["paired_trace_work"] = trace_work
            row["load_mismatch"] = mismatch
            row["load_mismatch_pct"] = mismatch_pct


def group_summary(rows: list[dict]) -> list[dict]:
    grouped: dict[tuple, list[dict]] = {}

    for row in rows:
        key = (
            row["workload_source"],
            row["policy"],
            row["detector_mode"],
            row["load_scale"],
        )
        grouped.setdefault(key, []).append(row)

    summary_rows: list[dict] = []

    for key in sorted(grouped):
        group_rows = grouped[key]
        metric_runs = [
            {metric: row[metric] for metric in METRIC_KEYS}
            for row in group_rows
        ]
        summary = summarize_runs(metric_runs)

        base = {
            "experiment": "trace_compare",
            "workload_source": key[0],
            "policy": key[1],
            "detector_mode": key[2],
            "load_scale": key[3],
            "grid_baseline_source": group_rows[0].get("grid_baseline_source"),
            "threshold_strategy": group_rows[0].get("threshold_strategy"),
            "grid_threshold_window": group_rows[0].get("grid_threshold_window"),
            "n": len(group_rows),
        }

        for metric in METRIC_KEYS:
            base[f"{metric}_mean"] = summary.get(metric, {}).get("mean", 0.0)
            base[f"{metric}_ci"] = summary.get(metric, {}).get("ci", 0.0)

        summary_rows.append(base)

    return summary_rows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare synthetic and trace-calibrated GridShift workloads."
    )
    parser.add_argument(
        "--attack-start-tick",
        type=int,
        default=ATTACK_CFG["attack_start_tick"],
        help="Trust attack start tick for this trace_compare run.",
    )
    parser.add_argument(
        "--experiment-ticks",
        type=int,
        default=TICKS,
        help="Number of 5-minute simulation ticks to execute.",
    )
    parser.add_argument(
        "--trace-start-tick",
        type=int,
        default=None,
        help=(
            "Simulation tick where the first derived trace arrival should occur. "
            "Defaults to the trace CSV's native first tick."
        ),
    )
    parser.add_argument(
        "--grid-trace-path",
        default=None,
        help=(
            "Optional derived ISO-NE grid trace. If supplied, the run uses this "
            "measured baseline instead of the synthetic diurnal baseline."
        ),
    )
    parser.add_argument(
        "--grid-trace-start-tick",
        type=int,
        default=1,
        help=(
            "Grid trace tick consumed by simulation tick 1. Defaults to 1, "
            "preserving prior direct tick alignment."
        ),
    )
    parser.add_argument(
        "--grid-threshold-mw",
        type=float,
        default=None,
        help="Explicit grid overload threshold in MW.",
    )
    parser.add_argument(
        "--grid-threshold-headroom-mw",
        type=float,
        default=None,
        help=(
            "For trace-backed runs, set threshold to max baseline plus this "
            "fixed MW headroom."
        ),
    )
    parser.add_argument(
        "--grid-threshold-window",
        choices=["full", "eval"],
        default="full",
        help=(
            "When using --grid-threshold-headroom-mw, choose whether the max "
            "baseline comes from the full loaded grid trace or the evaluated "
            "simulation window. Defaults to full for backward compatibility."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    ensure_dir(RESULTS_DIR)
    grid_options = _grid_options(args)

    rows: list[dict] = []

    for load_scale in LOAD_SCALES:
        for policy in POLICIES:
            for seed in SEEDS:
                for workload_source in ["synthetic", "trace-calibrated"]:
                    rows.append(
                        run_workload_trial(
                            workload_source=workload_source,
                            policy=policy,
                            seed=seed,
                            load_scale=load_scale,
                            attack_start_tick=args.attack_start_tick,
                            trace_start_tick=args.trace_start_tick,
                            ticks=args.experiment_ticks,
                            grid_options=grid_options,
                        )
                    )

    _add_pair_load_mismatch(rows)

    raw_csv = os.path.join(RESULTS_DIR, "trace_compare.csv")
    summary_csv = os.path.join(RESULTS_DIR, "trace_compare_summary.csv")

    write_rows_csv(raw_csv, rows)
    write_rows_csv(summary_csv, group_summary(rows))

    mismatches = [abs(row["load_mismatch_pct"]) for row in rows]
    max_mismatch = max(mismatches) if mismatches else 0.0
    print("wrote:", raw_csv)
    print("wrote:", summary_csv)
    print("rows:", len(rows))
    print("attack_start_tick:", args.attack_start_tick)
    print("experiment_ticks:", args.experiment_ticks)
    print("grid_threshold_mw:", grid_options["grid_metadata"]["grid_threshold_mw"])
    print("threshold_strategy:", grid_options["grid_metadata"]["threshold_strategy"])
    if args.grid_trace_path is not None:
        print("grid_trace_start_tick:", args.grid_trace_start_tick)
        print(
            "grid_eval_tick_range:",
            grid_options["grid_metadata"]["grid_eval_first_tick"],
            grid_options["grid_metadata"]["grid_eval_last_tick"],
        )
        print(
            "grid_eval_baseline_range_mw:",
            grid_options["grid_metadata"]["grid_eval_baseline_min_mw"],
            grid_options["grid_metadata"]["grid_eval_baseline_max_mw"],
        )
        print(
            "grid_threshold_window:",
            grid_options["grid_metadata"]["grid_threshold_window"],
        )
    if args.trace_start_tick is not None:
        print("trace_start_tick:", args.trace_start_tick)
    print(f"max synthetic-vs-trace submitted-work mismatch: {max_mismatch:.2f}%")


if __name__ == "__main__":
    main()
