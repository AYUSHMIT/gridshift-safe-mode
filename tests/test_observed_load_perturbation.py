"""Tests for experiment-only observed-load perturbation."""

from __future__ import annotations

import unittest

from core.config import SimConfig
from core.orchestrator import GridShiftOrchestrator


class ObservedLoadPerturbationTest(unittest.TestCase):
    def test_zero_perturbation_preserves_observed_load(self) -> None:
        orch = GridShiftOrchestrator(seed=7, config=SimConfig(seed=7))
        result = orch.tick()
        self.assertTrue(result.assessments)
        self.assertTrue(
            all(assessment.mismatch_mw == 0.0 for assessment in result.assessments)
        )

    def test_bias_changes_behavioral_mismatch_only_when_configured(self) -> None:
        cfg = SimConfig(seed=7, observed_load_bias_mw=7.5)
        orch = GridShiftOrchestrator(seed=7, config=cfg)
        result = orch.tick()
        self.assertTrue(result.assessments)
        self.assertTrue(
            all(assessment.mismatch_mw == 7.5 for assessment in result.assessments)
        )

    def test_noise_is_deterministic_for_seed(self) -> None:
        cfg_a = SimConfig(seed=3, observed_load_noise_std_mw=5.0)
        cfg_b = SimConfig(seed=3, observed_load_noise_std_mw=5.0)
        result_a = GridShiftOrchestrator(seed=3, config=cfg_a).tick()
        result_b = GridShiftOrchestrator(seed=3, config=cfg_b).tick()
        self.assertEqual(
            [assessment.observed_load_mw for assessment in result_a.assessments],
            [assessment.observed_load_mw for assessment in result_b.assessments],
        )


if __name__ == "__main__":
    unittest.main()
