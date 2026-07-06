# Trusted Actuation Envelope Reproducibility

This note documents the trusted-actuation-envelope analysis used to interpret
the GridShift phase/headroom experiment. It is a lightweight reproducibility
artifact for reviewers; it does not add new experiments or change simulator
behavior.

## What The Envelope Means

The trusted actuation envelope is the remaining post-attack capacity for safe
outbound movement into trusted destinations. In the instrumentation, it is
observed through post-attack trusted destination headroom, trusted feasible
migration opportunities, safety-filtered migration decisions, and successful
corrective migrations.

This is deliberately narrower than a claim that all available capacity is
usable. A destination must remain trusted and have enough compute headroom for
the workload movement to be operationally meaningful.

## Why The Phase/Headroom Sweep Exists

The 4 phase x 4 regional compute-headroom sweep tests when that envelope becomes
operationally useful. The workload phase controls whether the attack arrives
before, during, or after a burst; the capacity level controls whether trusted
destinations have enough residual headroom to absorb corrective movement.

The analysis is produced by:

- `experiments/run_trace_phase_headroom.py`
- `experiments/analyze_tcae_phase_headroom.py`

The analyzer reads `experiments/results/trace_phase_headroom.csv` and writes:

- `experiments/results/tcae_phase_headroom_paired.csv`
- `experiments/results/tcae_phase_headroom_regime_summary.csv`

These result files are generated artifacts and are intentionally not committed.

## Key Regimes

| Regime | Feasibility fraction | Corrective migrations | ΔCompletion | ΔOverload |
|---|---:|---:|---:|---:|
| burst_peak / 160 MW | 0.128 | 24.6 | +2.1 pp | -421.4 MW·ticks |
| burst_peak / 192 MW | 0.108 | 4.4 | 0.0 pp | +0.2 MW·ticks |
| burst_peak / 224 MW | 0.111 | 2.6 | 0.0 pp | 0.0 MW·ticks |
| post_burst / 160 MW | 0.072 | 19.6 | +0.3 pp | -110.7 MW·ticks |

## Why This Is Not A Dominance Claim

The original Google event-calibrated bursty setting has paired 95% confidence
intervals crossing zero. Directional safe mode should therefore not be read as
universally dominating freeze-based safety. The more defensible interpretation
is conditional: directional control helps in regimes where the post-attack
trusted actuation envelope is available and actually used.

## Necessary But Not Sufficient

Nonzero feasibility or nonzero trusted residual headroom alone does not
guarantee directional improvement. The opportunity must be converted into
consequential corrective migrations, and those migrations must improve the
outcome metrics enough to overcome migration overhead, workload timing, and
capacity constraints.

## Reproduction Commands

The reproduction commands require local generated trace inputs that are not
committed to the repository:

- `data/grid/iso_ne_grid_derived_5min.csv`
- `data/traces/google_cluster_sample.csv` or the derived trace fixture used by
  the phase/headroom workflow
- generated files under `experiments/results/`

Run the full phase/headroom matrix and TCAE analyzer with:

```bash
.venv/bin/python -m compileall core experiments

.venv/bin/python -m experiments.run_trace_phase_headroom \
  --phases all \
  --capacity-levels 128,160,192,224 \
  --seeds 0,1,2,3,4,5,6,7,8,9 \
  --experiment-ticks 50

.venv/bin/python -m experiments.analyze_tcae_phase_headroom
```

The analyzer will regenerate:

```text
experiments/results/tcae_phase_headroom_paired.csv
experiments/results/tcae_phase_headroom_regime_summary.csv
experiments/results/tcae_feasibility_vs_completion_delta.png
experiments/results/tcae_feasibility_vs_overload_delta.png
```

Do not commit those generated outputs unless the artifact policy changes.

## Mechanism Audit

After regenerating the paired and regime-summary CSVs, run the compact
mechanism audit:

```bash
.venv/bin/python -m experiments.audit_tcae_mechanism
```

This reads:

```text
experiments/results/tcae_phase_headroom_paired.csv
experiments/results/tcae_phase_headroom_regime_summary.csv
```

and writes generated reviewer-inspection artifacts:

```text
experiments/results/tcae_mechanism_audit.csv
experiments/results/tcae_mechanism_audit.md
```

The audit classifies each phase/headroom regime as `consequential_envelope`,
`latent_envelope`, or `no_envelope`. It is a mechanism-consistency check, not a
new experiment and not a policy-dominance test.

## Reviewer Interpretation

Directional control helps when trusted feasible migration opportunities are
actually converted into consequential corrective migrations. The trusted
actuation envelope is therefore necessary but not sufficient.
