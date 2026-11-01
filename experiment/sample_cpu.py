#!/usr/bin/env python3
"""Read process-wide CPU accounting; /proc/PID/stat already sums all threads."""
import csv
import os
import time
from pathlib import Path


def read_stat(pid, proc_root="/proc"):
    try:
        raw = (Path(proc_root) / str(pid) / "stat").read_text()
    except (FileNotFoundError, ProcessLookupError):
        return None
    # comm may contain spaces and parentheses. Field 3 follows its last ')'.
    fields = raw[raw.rfind(")") + 2:].split()
    if len(fields) < 20:
        raise ValueError("truncated process stat")
    return {"state": fields[0], "ticks": int(fields[11]) + int(fields[12]),
            "start_ticks": int(fields[19])}


class CpuSampler:
    names = ("fibo", "mysql", "sysbench")

    def __init__(self, output, origin=None):
        self.origin = time.monotonic() if origin is None else origin
        self.hz = os.sysconf("SC_CLK_TCK")
        self.processes = {}
        self.states = {name: "not_started" for name in self.names}
        self.previous = {name: 0 for name in self.names}
        self.file = open(str(output), "w", newline="")
        self.writer = csv.DictWriter(self.file, fieldnames=["elapsed_s"] +
            [name + "_cpu_s" for name in self.names] + ["database_cpu_s"] +
            [name + "_state" for name in self.names])
        self.writer.writeheader()

    def attach(self, name, pid, subtract_existing=False):
        stat = read_stat(pid)
        if stat is None:
            raise RuntimeError("process vanished before sampling: {}".format(name))
        self.processes[name] = (pid, stat["start_ticks"],
                                stat["ticks"] if subtract_existing else 0)

    def sample(self):
        row = {"elapsed_s": time.monotonic() - self.origin}
        for name in self.names:
            if name in self.processes:
                pid, identity, offset = self.processes[name]
                stat = read_stat(pid)
                if stat is None:
                    self.states[name] = "gone"
                elif stat["start_ticks"] != identity:
                    raise RuntimeError("PID reuse detected for {}".format(name))
                else:
                    self.previous[name] = max(self.previous[name], stat["ticks"] - offset)
                    self.states[name] = stat["state"]
            row[name + "_cpu_s"] = self.previous[name] / self.hz
            row[name + "_state"] = self.states[name]
        row["database_cpu_s"] = row["mysql_cpu_s"] + row["sysbench_cpu_s"]
        self.writer.writerow(row)
        self.file.flush()
        return row

    def close(self):
        self.file.close()


def reap_if_done(process, sampler):
    # Keep the zombie until its final /proc CPU counters have been sampled.
    if process.returncode is not None:
        return process.returncode
    info = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    if info is None:
        return None
    sampler.sample()
    return process.wait()
