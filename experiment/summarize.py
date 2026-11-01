#!/usr/bin/env python3
"""Summarize actual completed runs only; never substitute paper values."""
import argparse
import csv
import json
import re
import statistics
from pathlib import Path


def load_run(path):
    meta = json.loads((path / "run.json").read_text())
    if meta.get("status") != "completed":
        return None
    accounting = meta.get("accounting", "proc")
    if accounting == "trace" and meta.get("authoritative_cpu_source") != "sched_switch_trace":
        raise ValueError("Missing authoritative trace accounting in " + str(path))
    fibo = json.loads((path / "fibo.json").read_text())
    raw = (path / "sysbench_run.log").read_text()
    transactions = re.search(r"transactions:\s+(\d+)\s+\(([\d.]+) per sec\.\)", raw)
    latency = re.search(r"Latency \(ms\):.*?avg:\s+([\d.]+)", raw, re.S)
    total_time = re.search(r"total time:\s+([\d.]+)s", raw)
    if not all((transactions, latency, total_time)):
        raise ValueError("Missing Sysbench summary in " + str(path))
    start = meta["sysbench_launch_elapsed_s"]
    end = min(meta["sysbench_end_elapsed_s"], meta["fibo_end_elapsed_s"])
    with (path / "cpu_samples.csv").open() as src:
        samples = [row for row in csv.DictReader(src) if start <= float(row["elapsed_s"]) <= end]
    share = None
    longest_flat = None
    if len(samples) > 1:
        first, last = samples[0], samples[-1]
        span = float(last["elapsed_s"]) - float(first["elapsed_s"])
        if span > 0:
            share = (float(last["fibo_cpu_s"]) - float(first["fibo_cpu_s"])) / span
        longest_flat = 0.0
        flat_start = float(first["elapsed_s"])
        last_cpu = float(first["fibo_cpu_s"])
        for sample in samples[1:]:
            now, cpu = float(sample["elapsed_s"]), float(sample["fibo_cpu_s"])
            if cpu > last_cpu:
                flat_start = now
            else:
                longest_flat = max(longest_flat, now - flat_start)
            last_cpu = cpu
    return {"path": str(path), "scheduler": meta["scheduler"], "smoke": meta["smoke"],
            "accounting": accounting,
            "cpu_time_measurement": "scheduled_residency_seconds" if accounting == "trace" else "process_cpu_seconds",
            "fibo_cpu_s": meta["final_counters"]["fibo_cpu_s"] if accounting == "trace" else fibo["cpu_seconds"],
            "fibo_process_clock_raw_s": fibo["cpu_seconds"], "fibo_wall_s": fibo["wall_seconds"],
            "sysbench_total_s": float(total_time.group(1)),
            "transactions": int(transactions.group(1)),
            "transactions_per_s": float(transactions.group(2)),
            "latency_avg_ms": float(latency.group(1)), "fibo_overlap_cpu_share": share,
            "longest_sampled_flat_s": longest_flat,
            "fibo_finished_before_sysbench": meta["fibo_finished_before_sysbench"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directories", type=Path, nargs="+")
    args = parser.parse_args()
    seen, rows, excluded, settings = set(), [], [], {}
    for directory in args.directories:
        for run_file in directory.resolve().rglob("run.json"):
            if run_file in seen:
                continue
            seen.add(run_file)
            row = load_run(run_file.parent)
            if row is None:
                excluded.append(str(run_file.parent))
                continue
            params = json.loads((run_file.parent.parent / "parameters.json").read_text())
            # Never pool different thread counts, budgets, or Fibo workloads.
            keys = ("cpu", "threads", "table_size", "events", "fibo_n", "fibo_repeats",
                    "lead_seconds", "seed", "interval", "lua", "accounting")
            signature = json.dumps({key: params.get(key, "proc" if key == "accounting" else None)
                                    for key in keys}, sort_keys=True)
            settings.setdefault(signature, []).append(row)
            rows.append(row)
    groups = []
    metrics = ("fibo_cpu_s", "fibo_wall_s", "sysbench_total_s", "transactions_per_s",
               "latency_avg_ms", "fibo_overlap_cpu_share", "longest_sampled_flat_s")
    for signature, candidates in settings.items():
        for scheduler in ("cfs", "ule"):
            selected = [row for row in candidates if row["scheduler"] == scheduler and not row["smoke"]]
            if not selected:
                continue
            group = {"scheduler": scheduler, "parameters": json.loads(signature), "n": len(selected),
                     "at_least_three_completed_runs": len(selected) >= 3,
                     "all_fibo_runs_outlast_sysbench": all(not row["fibo_finished_before_sysbench"] for row in selected),
                     "metrics": {}}
            for metric in metrics:
                values = [row[metric] for row in selected if row[metric] is not None]
                group["metrics"][metric] = {"n": len(values),
                    "mean": statistics.mean(values) if values else None,
                    "sample_stddev": statistics.stdev(values) if len(values) >= 2 else None}
            groups.append(group)
    print(json.dumps({"completed_runs": rows, "failed_or_incomplete_runs": excluded,
                      "formal_groups": groups}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
