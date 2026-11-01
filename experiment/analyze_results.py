#!/usr/bin/env python3
"""Analyze actual formal runs with interpolated cumulative residency curves.

Only Python's standard library and the neighboring summarize.py are required.
Run --self-test for mathematical checks without creating experiment results.
"""
import argparse
import bisect
import csv
import hashlib
import json
import math
import statistics
from pathlib import Path

from summarize import load_run


EPSILON = 1e-10


def read_json(path):
    return json.loads(path.read_text())


def finite(value, label):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite " + label)
    return number


def read_curve(path):
    times, values = [], []
    with path.open() as source:
        for row in csv.DictReader(source):
            stamp = finite(row["elapsed_s"], "elapsed_s")
            value = finite(row["fibo_cpu_s"], "fibo_cpu_s")
            if stamp < 0 or value < 0:
                raise ValueError("Negative time or residency")
            if times and stamp <= times[-1]:
                raise ValueError("CSV timestamps must be strictly increasing")
            if values and value < values[-1] - EPSILON:
                raise ValueError("Cumulative Fibo residency decreases")
            if values and value - values[-1] > stamp - times[-1] + 1e-5:
                raise ValueError("Fibo residency exceeds one CPU's available time")
            times.append(stamp)
            values.append(value)
    if len(times) < 2:
        raise ValueError("At least two CSV samples are required")
    return times, values


def interpolate(times, values, point):
    """Linear interpolation only; never extrapolate outside captured samples."""
    if point < times[0] - EPSILON or point > times[-1] + EPSILON:
        raise ValueError("Requested window is outside the recorded curve")
    if point <= times[0]:
        return values[0]
    if point >= times[-1]:
        return values[-1]
    right = bisect.bisect_right(times, point)
    left = right - 1
    fraction = (point - times[left]) / (times[right] - times[left])
    return values[left] + fraction * (values[right] - values[left])


def longest_flat(times, values, start, end):
    """Clip and merge adjacent sample intervals with no cumulative increase."""
    if end <= start:
        return {"seconds": 0.0, "start_elapsed_s": None, "end_elapsed_s": None}
    interpolate(times, values, start)
    interpolate(times, values, end)
    current_start = None
    best = {"seconds": 0.0, "start_elapsed_s": None, "end_elapsed_s": None}
    for index in range(len(times) - 1):
        left, right = max(start, times[index]), min(end, times[index + 1])
        if right <= left:
            continue
        if abs(values[index + 1] - values[index]) <= EPSILON:
            if current_start is None:
                current_start = left
            length = right - current_start
            if length > best["seconds"]:
                best = {"seconds": length, "start_elapsed_s": current_start,
                        "end_elapsed_s": right}
        else:
            current_start = None
    return best


def condition_parameters(parameters):
    # scheduler/output differ by design. status is bookkeeping, not an input.
    return {key: value for key, value in parameters.items()
            if key not in ("scheduler", "output", "status")}


def analyze_run(directory):
    meta = read_json(directory / "run.json")
    if meta.get("status") != "completed":
        raise ValueError("Run failed or is incomplete")
    if meta.get("smoke") is not False:
        raise ValueError("Smoke tests are excluded from formal analysis")
    if meta.get("accounting") != "trace" or meta.get("authoritative_cpu_source") != "sched_switch_trace":
        raise ValueError("Formal comparison requires the same trace accounting on both kernels")
    parameters = read_json(directory.parent / "parameters.json")
    if parameters.get("smoke") is not False or parameters.get("accounting") != "trace":
        raise ValueError("Parameter file does not describe a formal trace experiment")
    scheduler = meta.get("scheduler")
    if scheduler not in ("cfs", "ule") or parameters.get("scheduler") != scheduler:
        raise ValueError("Scheduler identity mismatch")
    environment = read_json(directory.parent / "environment.json")
    release = environment.get("kernel_release", "")
    if not (release.startswith("4.9.") or release.startswith("4.9-")):
        raise ValueError("Formal analysis requires Linux 4.9, found " + release)
    if "-paper02-" + scheduler not in release:
        raise ValueError("Missing kernel identity marker")
    trace_metadata = read_json(directory / "trace_metadata.json")
    if trace_metadata.get("status") != "completed":
        raise ValueError("Trace integrity was not confirmed")
    for key in ("overrun", "commit overrun", "dropped events"):
        if int(trace_metadata.get("cpu_stats", {}).get(key, -1)) != 0:
            raise ValueError("Trace integrity counter is missing or nonzero: " + key)
    metrics = load_run(directory)
    times, values = read_curve(directory / "cpu_samples.csv")
    final_value = finite(meta["final_counters"]["fibo_cpu_s"], "final Fibo residency")
    if abs(values[-1] - final_value) > 1e-7:
        raise ValueError("CSV and run metadata disagree on final Fibo residency")
    start = finite(meta["sysbench_launch_elapsed_s"], "Sysbench start")
    sysbench_end = finite(meta["sysbench_end_elapsed_s"], "Sysbench end")
    fibo_end = finite(meta["fibo_end_elapsed_s"], "Fibo end")
    end = min(sysbench_end, fibo_end)
    if start < 0 or end <= start:
        raise ValueError("No valid joint-alive measurement window")
    start_value = interpolate(times, values, start)
    end_value = interpolate(times, values, end)
    residency = max(0.0, end_value - start_value)
    share = residency / (end - start)
    if share > 1 + 1e-5:
        raise ValueError("Fibo's residency fraction exceeds one CPU")
    steps = [right - left for left, right in zip(times, times[1:])]
    result = {"run": str(directory), "scheduler": scheduler, "kernel_release": release,
        "measurement": "scheduled_residency_seconds", "status": "completed",
        "window": {"start_elapsed_s": start, "end_elapsed_s": end, "duration_s": end - start,
                   "sysbench_end_elapsed_s": sysbench_end, "fibo_end_elapsed_s": fibo_end,
                   "scope": "joint_alive_period",
                   "bounds_source": "run.json: Sysbench launch to min(Fibo end, Sysbench end)",
                   "cumulative_start_s": start_value, "cumulative_end_s": end_value,
                   "boundary_method": "piecewise_linear_interpolation"},
        "fibo_residency_s_in_overlap": residency,
        "fibo_residency_share_in_overlap": share,
        "longest_flat_sampled_interval": longest_flat(times, values, start, end),
        "fibo_alive_for_entire_sysbench_window": fibo_end >= sysbench_end,
        "sampling": {"configured_interval_s": parameters["interval"],
                     "median_csv_step_s": statistics.median(steps), "max_csv_step_s": max(steps)},
        "fibo_total_residency_s": final_value, "fibo_wall_s": metrics["fibo_wall_s"],
        "transactions": metrics["transactions"], "transactions_per_s": metrics["transactions_per_s"],
        "latency_avg_ms": metrics["latency_avg_ms"], "sysbench_total_s": metrics["sysbench_total_s"]}
    # Capture executed database limits, not just high-level CLI parameters.
    params = dict(parameters)
    for command in meta.get("commands", []):
        if any(arg.startswith("--max-connections=") for arg in command):
            for argument in command:
                for option in ("max-connections", "innodb-buffer-pool-size", "innodb-log-file-size", "performance-schema"):
                    prefix = "--" + option + "="
                    if argument.startswith(prefix):
                        params["mysql_" + option.replace("-", "_")] = argument[len(prefix):]
    if "mysql_max_connections" not in params:
        raise ValueError("Executed MySQL connection limit was not recorded")
    return result, condition_parameters(params)


def describe(values):
    return {"n": len(values), "mean": statistics.mean(values) if values else None,
            "sample_stddev": statistics.stdev(values) if len(values) >= 2 else None}


def aggregate(runs):
    names = ("fibo_residency_share_in_overlap", "fibo_residency_s_in_overlap",
             "transactions_per_s", "latency_avg_ms", "sysbench_total_s",
             "fibo_total_residency_s", "fibo_wall_s")
    metrics = {name: describe([run[name] for run in runs]) for name in names}
    metrics["longest_flat_sampled_interval_s"] = describe(
        [run["longest_flat_sampled_interval"]["seconds"] for run in runs])
    return {"n": len(runs), "at_least_three_runs": len(runs) >= 3,
            "fibo_outlasts_sysbench_in_all_runs": bool(runs) and all(
                run["fibo_alive_for_entire_sysbench_window"] for run in runs), "metrics": metrics}


def build_analysis(directories):
    seen, runs, excluded, groups = set(), [], [], {}
    for root in directories:
        root = root.resolve()
        if not root.is_dir():
            excluded.append({"run": str(root), "reason": "Input directory does not exist"})
            continue
        for marker in sorted(root.rglob("run.json")):
            if marker in seen:
                continue
            seen.add(marker)
            try:
                run, parameters = analyze_run(marker.parent)
                signature = json.dumps(parameters, sort_keys=True, separators=(",", ":"))
                identifier = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:12]
                run["condition_id"] = identifier
                runs.append(run)
                groups.setdefault(signature, {"condition_id": identifier,
                    "parameters": parameters, "runs": []})["runs"].append(run)
            except (ValueError, KeyError, TypeError, OSError) as error:
                excluded.append({"run": str(marker.parent), "reason": str(error)})
    comparisons = []
    for group in groups.values():
        by_scheduler = {name: [run for run in group["runs"] if run["scheduler"] == name]
                        for name in ("cfs", "ule")}
        record = {"condition_id": group["condition_id"], "parameters": group["parameters"],
                  "schedulers": {name: aggregate(data) for name, data in by_scheduler.items()},
                  "matching_parameters": True,
                  "both_schedulers_present": all(by_scheduler.values()),
                  "at_least_three_runs_each": all(len(data) >= 3 for data in by_scheduler.values()),
                  "ule_relative_to_cfs": None}
        if record["both_schedulers_present"]:
            cfs = record["schedulers"]["cfs"]["metrics"]
            ule = record["schedulers"]["ule"]["metrics"]
            differences = {}
            for name in ("transactions_per_s", "latency_avg_ms", "sysbench_total_s"):
                base, observed = cfs[name]["mean"], ule[name]["mean"]
                differences[name + "_percent_change"] = 100 * (observed / base - 1) if base else None
            differences["fibo_residency_share_percentage_point_change"] = 100 * (
                ule["fibo_residency_share_in_overlap"]["mean"] -
                cfs["fibo_residency_share_in_overlap"]["mean"])
            record["ule_relative_to_cfs"] = differences
        comparisons.append(record)
    return {"status": "analyzed" if runs else "no_formal_results", "formal_run_count": len(runs),
        "runs": runs, "excluded_runs": excluded, "comparisons": comparisons,
        "interpretation_limits": [
            "Residency is reconstructed from sched_switch and can include interrupts or guest descheduling.",
            "Linear interpolation estimates boundary values within recorded sampling intervals.",
            "Flat intervals are bounded observations at the configured sampling resolution, normally 0.1 s; they do not prove unbounded starvation.",
            "Observed completion timestamps include polling delay. Residency/flat metrics end at the earlier workload exit; full Sysbench throughput can include a later phase without Fibo when coverage is false.",
            "Matching parameters do not independently certify identical binaries, hardware, or background load."],
        "paper_reference": {"label": "not_directly_comparable", "directly_comparable": False,
            "source": "https://www.usenix.org/system/files/conference/atc18/atc18-bouron.pdf",
            "location": "Section 5.1, Table 2 (printed p.90), Figure 1 (printed p.91)",
            "value_type": "published_reference_not_reproduced_measurement",
            "cfs": {"fibo_runtime_s_as_published": 160, "transactions_per_s": 290,
                    "latency_avg_ms": 441, "sysbench_completion_s_in_text": 235},
            "ule": {"fibo_runtime_s_as_published": 158, "transactions_per_s": 532,
                    "latency_avg_ms": 125, "sysbench_completion_s_in_text": 143},
            "reason": "Hardware, workload details, software configuration, virtualization and timing definitions differ or were not fully specified. No percent replication error is inferred."}}


def self_test():
    # Synthetic mathematical fixtures only, never written as measured results.
    times, values = [0.0, 0.1, 0.2, 0.3, 0.4], [0.0, 0.1, 0.1, 0.1, 0.2]
    assert abs(interpolate(times, values, 0.05) - 0.05) < EPSILON
    assert abs(interpolate(times, values, 0.35) - 0.15) < EPSILON
    assert abs((interpolate(times, values, 0.35) - interpolate(times, values, 0.05)) / 0.3 - 1 / 3) < EPSILON
    flat = longest_flat(times, values, 0.15, 0.35)
    assert abs(flat["seconds"] - 0.15) < EPSILON and flat["start_elapsed_s"] == 0.15
    assert longest_flat(times, values, 0.31, 0.39)["seconds"] == 0
    try:
        interpolate(times, values, 0.5)
    except ValueError:
        pass
    else:
        raise AssertionError("Extrapolation was not rejected")
    one = condition_parameters({"scheduler": "cfs", "output": "a", "threads": 80})
    two = condition_parameters({"scheduler": "ule", "output": "b", "threads": 80})
    assert one == two
    assert one != condition_parameters({"scheduler": "ule", "output": "b", "threads": 40})
    assert describe([1, 2, 3])["sample_stddev"] == 1
    print("Self-test passed: interpolation, clipped flat intervals, no extrapolation, parameter matching and sample deviation.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directories", type=Path, nargs="*")
    parser.add_argument("--output", type=Path, default=Path("analysis.json"))
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not args.directories:
        parser.error("provide one or more result directories")
    result = build_analysis(args.directories)
    encoded = json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Refuse overwriting any existing evidence or previous analysis silently.
    with args.output.open("x") as destination:
        destination.write(encoded)
    print("Wrote {}: {} formal runs, {} excluded".format(
        args.output, result["formal_run_count"], len(result["excluded_runs"])))


if __name__ == "__main__":
    main()
