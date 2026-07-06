# SWF Multi-Window Reproducibility

This workflow extends the single SDSC BLUE SWF validation from one aligned
50-tick window to multiple aligned 50-tick source windows. It is a workload
trace validation, not power-trace replay and not a universal HPC policy claim.

## Window Selection

`experiments/run_swf_multiwindow.py` first converts the local SWF file into the
same derived workload format used by the existing SWF workflow. It then selects
active source ticks from the derived trace, using evenly spaced anchors across
the active trace range and enforcing non-overlap by at least the configured
window length. Each selected native SWF tick is aligned to GridShift simulation
tick 1, preserving the current 50-tick experiment semantics.

The local SDSC BLUE trace and generated CSV outputs are intentionally not
committed.

## Commands

Tiny validation run:

```bash
.venv/bin/python -m experiments.run_swf_multiwindow \
  --swf-path ~/Downloads/gridshift-local-traces/SDSC-BLUE-2000-4.2-cln.swf \
  --num-windows 2 \
  --seeds 0
```

Five-window validation:

```bash
.venv/bin/python -m experiments.run_swf_multiwindow \
  --swf-path ~/Downloads/gridshift-local-traces/SDSC-BLUE-2000-4.2-cln.swf \
  --num-windows 5 \
  --seeds 0,1,2
```

Analyze paired policy deltas:

```bash
.venv/bin/python -m experiments.analyze_swf_multiwindow
```

Generated outputs:

```text
experiments/results/swf_multiwindow_raw.csv
experiments/results/swf_multiwindow_paired.csv
experiments/results/swf_multiwindow_summary.csv
experiments/results/swf_multiwindow_summary.md
```

Generated outputs under `experiments/results/` should remain ignored and should
not be committed.
