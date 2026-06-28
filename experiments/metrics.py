"""Small helpers for experiment result exports."""
from __future__ import annotations

import csv
import math
import os
from typing import Any

import numpy as np


def summarize_runs(runs: list[dict]) -> dict:
    """Return mean and 95% CI for numeric metrics in a list of run dicts."""
    if not runs:
        return {}

    numeric_keys = set()
    for row in runs:
        for key, value in row.items():
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float, np.integer, np.floating)):
                numeric_keys.add(key)

    summary: dict[str, dict[str, Any]] = {}
    for key in sorted(numeric_keys):
        values = np.array([float(row[key]) for row in runs if key in row], dtype=float)
        if values.size == 0:
            continue
        mean = float(values.mean())
        if values.size > 1:
            ci = float(1.96 * values.std(ddof=1) / math.sqrt(values.size))
        else:
            ci = 0.0
        summary[key] = {"mean": mean, "ci": ci, "n": int(values.size)}

    return summary


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def write_rows_csv(path: str, rows: list[dict]) -> None:
    """Write a list of dictionaries to CSV, preserving the union of keys."""
    ensure_dir(os.path.dirname(path))
    fieldnames: list[str] = []
    seen = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)

    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
