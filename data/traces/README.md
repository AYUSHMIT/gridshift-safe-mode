# Trace-Calibrated Workloads

This directory contains the normalized trace interface used by GridShift.

## Placeholder sample

[`google_power_sample.csv`](google_power_sample.csv) is a small development-only
placeholder. It is not real Google data and should only be used to exercise the
loader and plotting code during development.

## Real Google PowerData 2019 workflow

Google publishes PowerData 2019 through BigQuery and documents the trace in
[`PowerData2019.md`](https://github.com/google/cluster-data/blob/master/PowerData2019.md).
The notebook referenced there, [`power_trace_analysis_colab.ipynb`](https://github.com/google/cluster-data/blob/master/power_trace_analysis_colab.ipynb),
shows that the raw tables are queried from the `powerdata_2019` dataset and use
columns such as `time`, `measured_power_util`, `production_power_util`,
`bad_measurement_data`, and `bad_production_power_data`.

The raw Google export is a power-utilization trace, not a pre-normalized
GridShift CSV. To build a local normalized trace:

1. Export the raw Google PowerData rows locally from BigQuery.
2. If your export already contains a direct power column in MW, pass it to the
	 converter with `--power-column`.
3. If your export only contains utilization columns, pass the utilization
	 series directly with `--utilization-column`. The converter will preserve the
	 temporal pattern and normalize the shape against a reference trace.
4. Run the converter with `--reference-trace data/traces/google_power_sample.csv`
   or another normalized GridShift trace to preserve the temporal pattern while
   normalizing to the existing workload range.
5. The converter writes `data/traces/google_power_2019_normalized.csv`.

Example:

```bash
python -m experiments.convert_google_power_trace \
	--input /path/to/local/google_power_export.csv \
	--utilization-column production_power_util \
	--reference-trace data/traces/google_power_sample.csv \
	--output data/traces/google_power_2019_normalized.csv
```

The normalized output always uses:

```csv
tick,power_mw
```

Each row is a sequential simulation tick. The trace calibrates only the data-
center workload demand. It must not replace ISO-NE regional grid demand inside
the simulator.

## Repository policy

Large public datasets and generated normalized outputs are **not** committed
to this repository. Keep Google raw exports local and configurable, and only
check in tiny placeholder traces used for testing.