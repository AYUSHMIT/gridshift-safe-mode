"""Smoke tests for the TCAE mechanism audit helper.

The fixtures here are tiny synthetic rows. They are not paper data.
"""

from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from experiments import audit_tcae_mechanism


REGIME_COLUMNS = [
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

PAIRED_COLUMNS = [
    "phase",
    "region_capacity_mw",
    "seed",
    "delta_completion_rate_directional_minus_freeze",
    "delta_overload_directional_minus_freeze",
    "directional_post_attack_fraction_ticks_with_any_feasible_trusted_destination",
    "directional_successful_corrective_migrations",
]


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


class AuditTcaeMechanismTest(unittest.TestCase):
    def test_cli_writes_outputs_and_classifies_mechanisms(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            regime = root / "regime.csv"
            paired = root / "paired.csv"
            output_csv = root / "audit.csv"
            output_md = root / "audit.md"

            _write_csv(
                regime,
                REGIME_COLUMNS,
                [
                    {
                        "phase": "consequential",
                        "region_capacity_mw": 160,
                        "n": 1,
                        "delta_completion_rate_directional_minus_freeze_mean": 0.1,
                        "delta_overload_directional_minus_freeze_mean": 0.0,
                        "directional_post_attack_fraction_ticks_with_any_feasible_trusted_destination_mean": 0.5,
                        "directional_post_attack_mean_migration_feasibility_rate_mean": 0.4,
                        "directional_post_attack_mean_trusted_residual_headroom_mw_mean": 12.0,
                        "directional_successful_corrective_migrations_mean": 2.0,
                    },
                    {
                        "phase": "latent",
                        "region_capacity_mw": 192,
                        "n": 1,
                        "delta_completion_rate_directional_minus_freeze_mean": 0.0,
                        "delta_overload_directional_minus_freeze_mean": 0.0,
                        "directional_post_attack_fraction_ticks_with_any_feasible_trusted_destination_mean": 0.2,
                        "directional_post_attack_mean_migration_feasibility_rate_mean": 0.1,
                        "directional_post_attack_mean_trusted_residual_headroom_mw_mean": 20.0,
                        "directional_successful_corrective_migrations_mean": 1.0,
                    },
                    {
                        "phase": "empty",
                        "region_capacity_mw": 224,
                        "n": 1,
                        "delta_completion_rate_directional_minus_freeze_mean": 0.0,
                        "delta_overload_directional_minus_freeze_mean": 0.0,
                        "directional_post_attack_fraction_ticks_with_any_feasible_trusted_destination_mean": 0.0,
                        "directional_post_attack_mean_migration_feasibility_rate_mean": 0.0,
                        "directional_post_attack_mean_trusted_residual_headroom_mw_mean": 5.0,
                        "directional_successful_corrective_migrations_mean": 0.0,
                    },
                ],
            )
            _write_csv(
                paired,
                PAIRED_COLUMNS,
                [
                    {
                        "phase": "consequential",
                        "region_capacity_mw": 160,
                        "seed": 0,
                        "delta_completion_rate_directional_minus_freeze": 0.1,
                        "delta_overload_directional_minus_freeze": 0.0,
                        "directional_post_attack_fraction_ticks_with_any_feasible_trusted_destination": 0.5,
                        "directional_successful_corrective_migrations": 2.0,
                    }
                ],
            )

            audit_tcae_mechanism.main(
                [
                    "--regime-summary",
                    str(regime),
                    "--paired",
                    str(paired),
                    "--output-csv",
                    str(output_csv),
                    "--output-md",
                    str(output_md),
                ]
            )

            self.assertTrue(output_csv.exists())
            self.assertTrue(output_md.exists())

            with output_csv.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            classes = {row["mechanism_class"] for row in rows}
            self.assertEqual(
                classes,
                {
                    "consequential_envelope",
                    "latent_envelope",
                    "no_envelope",
                },
            )
            self.assertIn("mechanism consistency", output_md.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
