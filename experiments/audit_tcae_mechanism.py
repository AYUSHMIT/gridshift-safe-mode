"""Audit trusted-actuation-envelope mechanism consistency.

This is a post-processing helper for reviewer inspection. It reads generated
TCAE analyzer CSVs and classifies each phase/headroom regime without changing
simulation behavior or producing new experiment results.
"""

from __future__ import annotations

import argparse
import csv
import os
from collections import Counter
from pathlib import Path
from typing import Iterable

from experiments.run_trace_compare import RESULTS_DIR


EPSILON = 1e-9

REQUIRED_REGIME_COLUMNS = [
    "phase",
    "region_capacity_mw",
    "n",
    "delta_completion_rate_directional_minus_freeze_mean",
    "delta_overload_directional_minus_freeze_mean",
    "directional_post_attack_fraction_ticks_with_any_feasible_trusted_destination_mean",
    "directional_post_attack_mean_migration_feasibility_rate_mean",
    "directional_post_attack_mean_trusted_residual_headroom_mw_mean",
    "directional_successful_corrective_migrations_mean",
]

REQUIRED_PAIRED_COLUMNS = [
    "phase",
    "region_capacity_mw",
    "seed",
    "delta_completion_rate_directional_minus_freeze",
    "delta_overload_directional_minus_freeze",
    "directional_post_attack_fraction_ticks_with_any_feasible_trusted_destination",
    "directional_successful_corrective_migrations",
]

OUTPUT_COLUMNS = [
    "phase",
    "region_capacity_mw",
    "feasibility_fraction",
    "mean_migration_feasibility_rate",
    "mean_trusted_residual_headroom_mw",
    "successful_corrective_migrations",
    "delta_completion_directional_minus_freeze",
    "delta_overload_directional_minus_freeze",
    "directional_completion_better_than_freeze",
    "directional_overload_better_than_freeze",
    "envelope_nonempty",
    "corrective_migrations_nonzero",
    "mechanism_class",
]


def _read_rows(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _require_columns(rows: list[dict[str, str]], columns: Iterable[str], path: str) -> None:
    if not rows:
        raise ValueError(f"{path} has no data rows")
    present = set(rows[0])
    missing = [column for column in columns if column not in present]
    if missing:
        raise ValueError(f"{path} is missing required column(s): {', '.join(missing)}")


def _mechanism_class(
    *,
    envelope_nonempty: bool,
    corrective_migrations_nonzero: bool,
    completion_better: bool,
    overload_better: bool,
) -> str:
    if envelope_nonempty and corrective_migrations_nonzero and (
        completion_better or overload_better
    ):
        return "consequential_envelope"
    if envelope_nonempty:
        return "latent_envelope"
    if not corrective_migrations_nonzero:
        return "no_envelope"
    # Corrective migrations without measured feasibility are anomalous but still
    # not evidence of a consequential trusted envelope.
    return "latent_envelope"


def _audit_rows(regime_rows: list[dict[str, str]]) -> list[dict[str, object]]:
    audit = []
    for row in sorted(
        regime_rows,
        key=lambda item: (item["phase"], float(item["region_capacity_mw"])),
    ):
        feasibility_fraction = float(
            row[
                "directional_post_attack_fraction_ticks_with_any_feasible_trusted_destination_mean"
            ]
        )
        mean_feasibility_rate = float(
            row["directional_post_attack_mean_migration_feasibility_rate_mean"]
        )
        residual_headroom = float(
            row["directional_post_attack_mean_trusted_residual_headroom_mw_mean"]
        )
        corrective_migrations = float(
            row["directional_successful_corrective_migrations_mean"]
        )
        delta_completion = float(
            row["delta_completion_rate_directional_minus_freeze_mean"]
        )
        delta_overload = float(row["delta_overload_directional_minus_freeze_mean"])

        # Threshold convention: values whose absolute magnitude is <= EPSILON
        # are treated as zero. Completion is better when directional-freeze is
        # positive; overload is better when directional-freeze is negative.
        envelope_nonempty = feasibility_fraction > EPSILON
        corrective_nonzero = corrective_migrations > EPSILON
        completion_better = delta_completion > EPSILON
        overload_better = delta_overload < -EPSILON

        audit.append(
            {
                "phase": row["phase"],
                "region_capacity_mw": row["region_capacity_mw"],
                "feasibility_fraction": feasibility_fraction,
                "mean_migration_feasibility_rate": mean_feasibility_rate,
                "mean_trusted_residual_headroom_mw": residual_headroom,
                "successful_corrective_migrations": corrective_migrations,
                "delta_completion_directional_minus_freeze": delta_completion,
                "delta_overload_directional_minus_freeze": delta_overload,
                "directional_completion_better_than_freeze": completion_better,
                "directional_overload_better_than_freeze": overload_better,
                "envelope_nonempty": envelope_nonempty,
                "corrective_migrations_nonzero": corrective_nonzero,
                "mechanism_class": _mechanism_class(
                    envelope_nonempty=envelope_nonempty,
                    corrective_migrations_nonzero=corrective_nonzero,
                    completion_better=completion_better,
                    overload_better=overload_better,
                ),
            }
        )
    return audit


def _write_csv(path: str | Path, rows: list[dict[str, object]]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _format_number(value: object) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def _markdown_table(rows: list[dict[str, object]]) -> str:
    lines = [
        "| Phase | Capacity MW | Feas. frac. | Corrective migrations | "
        "Delta completion | Delta overload | Class |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            "| {phase} | {capacity} | {feasibility} | {corrective} | "
            "{completion} | {overload} | {klass} |".format(
                phase=row["phase"],
                capacity=_format_number(float(row["region_capacity_mw"])),
                feasibility=_format_number(row["feasibility_fraction"]),
                corrective=_format_number(row["successful_corrective_migrations"]),
                completion=_format_number(
                    row["delta_completion_directional_minus_freeze"]
                ),
                overload=_format_number(row["delta_overload_directional_minus_freeze"]),
                klass=row["mechanism_class"],
            )
        )
    return "\n".join(lines)


def _write_markdown(
    path: str | Path,
    *,
    rows: list[dict[str, object]],
    paired_rows: list[dict[str, str]],
    regime_path: str,
    paired_path: str,
) -> None:
    counts = Counter(str(row["mechanism_class"]) for row in rows)
    count_lines = "\n".join(
        f"- `{klass}`: {counts.get(klass, 0)}"
        for klass in [
            "consequential_envelope",
            "latent_envelope",
            "no_envelope",
        ]
    )
    content = f"""# TCAE Mechanism Audit

The trusted actuation envelope is the post-attack opportunity to move workload
out of untrusted infrastructure into trusted destinations with usable residual
capacity.

This audit checks mechanism consistency, not policy dominance. A regime is
classified as consequential only when the envelope is nonempty, corrective
migrations occur, and directional improves completion or overload relative to
freeze. Larger residual headroom alone is not sufficient if corrective
migrations are not executed.

Threshold convention: values with absolute magnitude <= `{EPSILON}` are treated
as zero.

Inputs:

- `{regime_path}`
- `{paired_path}` ({len(paired_rows)} paired rows)

## Summary

{count_lines}

## Regime Table

{_markdown_table(rows)}
"""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(content, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit TCAE mechanism consistency from generated analyzer CSVs."
    )
    parser.add_argument(
        "--regime-summary",
        default=os.path.join(RESULTS_DIR, "tcae_phase_headroom_regime_summary.csv"),
        help="Generated TCAE regime summary CSV.",
    )
    parser.add_argument(
        "--paired",
        default=os.path.join(RESULTS_DIR, "tcae_phase_headroom_paired.csv"),
        help="Generated TCAE paired CSV.",
    )
    parser.add_argument(
        "--output-md",
        default=os.path.join(RESULTS_DIR, "tcae_mechanism_audit.md"),
        help="Markdown report output path.",
    )
    parser.add_argument(
        "--output-csv",
        default=os.path.join(RESULTS_DIR, "tcae_mechanism_audit.csv"),
        help="Compact audit CSV output path.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    regime_rows = _read_rows(args.regime_summary)
    paired_rows = _read_rows(args.paired)
    _require_columns(regime_rows, REQUIRED_REGIME_COLUMNS, args.regime_summary)
    _require_columns(paired_rows, REQUIRED_PAIRED_COLUMNS, args.paired)

    audit_rows = _audit_rows(regime_rows)
    _write_csv(args.output_csv, audit_rows)
    _write_markdown(
        args.output_md,
        rows=audit_rows,
        paired_rows=paired_rows,
        regime_path=args.regime_summary,
        paired_path=args.paired,
    )
    counts = Counter(str(row["mechanism_class"]) for row in audit_rows)
    print("wrote:", args.output_csv)
    print("wrote:", args.output_md)
    print("regimes:", len(audit_rows))
    for klass in [
        "consequential_envelope",
        "latent_envelope",
        "no_envelope",
    ]:
        print(f"{klass}:", counts.get(klass, 0))


if __name__ == "__main__":
    main()
