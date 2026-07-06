# Observed-Load Sensitivity Ablation

GridShift's behavioral detector compares controller-reported load against a
grid-side observed-load channel. The main experiments treat that observed-load
channel as trusted. This ablation tests how the detector and directional
safe-mode outcomes degrade when the observed-load channel is noisy or biased.

This is a sensitivity ablation, not a defense for a compromised grid-side
sensor. It does not make the observed-load channel adversary-proof and does not
change the default simulator behavior. With zero noise and zero bias, the
simulator uses the existing observed-load path exactly.

Run the compact sweep with:

```bash
.venv/bin/python -m experiments.run_observed_load_sensitivity
```

The output CSV is generated at:

```text
experiments/results/observed_load_sensitivity.csv
```

Generated CSVs under `experiments/results/` are ignored and should not be
committed.
