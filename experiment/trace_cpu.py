#!/usr/bin/env python3
"""Reconstruct scheduled residency from Linux 4.9 sched_switch events.

This deliberately does not read scheduler runtime accounting.  Durations are
scheduled residency, including interrupt time and possible guest steal time;
they must not be described as exact user+system process CPU accounting.
Python 3.6 compatible.  Only this module owns its private ftrace instance.
"""
import csv
import json
import math
import os
import platform
import re
import shutil
import time
import uuid
from pathlib import Path


NAMES = ("fibo", "mysql", "sysbench")
COMM = {"fibo": "fibo", "mysql": "mysqld", "sysbench": "sysbench"}
EVENT_LINE = re.compile(
    r"\[(?P<cpu>\d+)\]\s+.*?\s(?P<time>\d+\.\d+):\s+"
    r"(?P<event>(?:[A-Za-z0-9_]+:)?[A-Za-z0-9_]+):\s*(?P<body>.*)$")
SWITCH = re.compile(
    r"^prev_comm=(.*?) prev_pid=(\d+) prev_prio=\S+ prev_state=\S+\s+"
    r"==> next_comm=(.*?) next_pid=(\d+) next_prio=\S+")
FORK = re.compile(r"(?:^|\s)(?:parent_pid|pid)=(\d+)\s+.*?\bchild_pid=(\d+)\b")
TASK_PID = re.compile(r"\bpid=(\d+)\b")


def _write_json(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def _cpu_mask(cpu):
    """Kernel cpumask text is comma-separated 32-bit hexadecimal groups."""
    groups = ["00000000"] * (cpu // 32 + 1)
    groups[-1] = "{:x}".format(1 << (cpu % 32))
    return ",".join(reversed(groups))


def reconstruct_rows(lines, origin, end, interval, cpu, roots, initial_tids=None):
    """Pure parser for real traces or explicitly synthetic offline fixtures.

    `roots` maps fibo/mysql/sysbench to the known process leader PID.  Initial
    TIDs are snapshots from /proc/PID/task; subsequent descendants are learned
    from sched_process_fork.  Names alone never admit an unrelated process.
    Traces must cover the measurement window; sched_switch consistency and
    timestamp checks reject discontinuities rather than inventing zero time.
    """
    if not (math.isfinite(origin) and math.isfinite(end) and end > origin and
            math.isfinite(interval) and interval > 0):
        raise ValueError("Invalid trace time window or interval")
    if not roots or any(name not in NAMES for name in roots):
        raise ValueError("Known workload root PIDs are required")
    duration = end - origin
    count = max(1, int(math.ceil(duration / interval)))
    increments = {name: [0.0] * count for name in NAMES}
    owners = {}
    root_pids = {}
    expected_comms = {}
    for name, pid in roots.items():
        pid = int(pid)
        if pid <= 0 or pid in root_pids:
            raise ValueError("Invalid or duplicate workload root PID")
        root_pids[pid] = name
        owners[pid] = name
        expected_comms[pid] = COMM[name]
        for tid in (initial_tids or {}).get(name, []):
            owners[int(tid)] = name

    metadata = {"switch_events": 0, "fork_events": 0, "matched_intervals": 0,
                "first_event_time": None, "last_event_time": None,
                "measurement": "scheduled_residency_seconds",
                "includes_interrupt_and_possible_guest_steal_time": True,
                "comm_mismatches": {}, "learned_tids": {},
                "first_switch_time": None, "last_switch_time": None}
    current_pid = None
    current_comm = None
    current_owner = None
    previous_switch = None
    previous_event = None
    root_birth_seen = set()

    def owner(pid, comm):
        name = owners.get(pid)
        if name is None:
            return None
        # Root process pre-exec work still has e.g. "python3" as comm.  Do not
        # mislabel it as Fibo or Sysbench work.  Child thread identities are
        # authoritative even if the application renames its worker threads.
        if pid in root_pids and comm != expected_comms[pid]:
            key = "{}:{}".format(pid, comm)
            metadata["comm_mismatches"][key] = metadata["comm_mismatches"].get(key, 0) + 1
            return None
        return name

    def add_segment(name, start, finish):
        if name is None:
            return
        start, finish = max(start, origin), min(finish, end)
        if finish <= start:
            return
        metadata["matched_intervals"] += 1
        index = max(0, min(count - 1, int((start - origin) / interval)))
        while index < count and start < finish:
            boundary = min(end, origin + (index + 1) * interval)
            right = min(boundary, finish)
            if right > start:
                increments[name][index] += right - start
            start = right
            index += 1

    for line in lines:
        if "LOST " in line or "LOST EVENTS" in line:
            raise RuntimeError("Trace reports lost events")
        match = EVENT_LINE.search(line)
        if match is None:
            if "sched_switch:" in line or "sched_process_fork:" in line:
                raise RuntimeError("Unrecognized scheduler trace line: " + line[:240])
            continue
        if int(match.group("cpu")) != cpu:
            continue
        stamp = float(match.group("time"))
        if previous_event is not None and stamp < previous_event:
            raise RuntimeError("Non-monotonic trace timestamps")
        previous_event = stamp
        if metadata["first_event_time"] is None:
            metadata["first_event_time"] = stamp
        metadata["last_event_time"] = stamp
        event = match.group("event").split(":")[-1]
        body = match.group("body")
        if event == "sched_process_fork":
            fork = FORK.search(body)
            if fork is None:
                raise RuntimeError("Unrecognized sched_process_fork payload")
            parent, child = (int(value) for value in fork.groups())
            metadata["fork_events"] += 1
            if child in root_pids:
                if child in root_birth_seen:
                    raise RuntimeError("Workload root PID reused during trace")
                root_birth_seen.add(child)
            elif parent in owners:
                owners[child] = owners[parent]
            else:
                # A new unrelated task may reuse a formerly owned TID.
                owners.pop(child, None)
        elif event == "sched_process_exec":
            task = TASK_PID.search(body)
            if task:
                tid = int(task.group(1))
                if tid == current_pid and tid in owners:
                    # exec can change python3 to fibo without a context switch.
                    # Split the open segment here instead of dropping its first
                    # running quantum or charging the launcher's pre-exec work.
                    add_segment(current_owner, previous_switch, stamp)
                    current_owner = owners[tid]
                    previous_switch = stamp
        elif event == "sched_process_free":
            task = TASK_PID.search(body)
            if task:
                tid = int(task.group(1))
                owners.pop(tid, None)
        elif event == "sched_switch":
            switch = SWITCH.match(body)
            if switch is None:
                raise RuntimeError("Unrecognized sched_switch payload")
            prev_comm, prev_pid, next_comm, next_pid = switch.groups()
            prev_pid, next_pid = int(prev_pid), int(next_pid)
            if current_pid is None:
                # The first event's prev task owned the CPU since the start
                # of the measured window if no earlier switch is present.
                add_segment(owner(prev_pid, prev_comm), origin, stamp)
                metadata["first_switch_time"] = stamp
            else:
                if current_pid != prev_pid:
                    raise RuntimeError("Discontinuous sched_switch chain: expected {}, got {}".format(
                        current_pid, prev_pid))
                add_segment(current_owner, previous_switch, stamp)
            current_pid, current_comm = next_pid, next_comm
            current_owner = owner(next_pid, next_comm)
            previous_switch = stamp
            metadata["last_switch_time"] = stamp
            metadata["switch_events"] += 1

    if not metadata["switch_events"]:
        raise RuntimeError("No sched_switch events for target CPU")
    if previous_switch < end:
        add_segment(current_owner, previous_switch, end)
    metadata["learned_tids"] = {str(tid): name for tid, name in owners.items()}
    totals = {name: 0.0 for name in NAMES}
    rows = []

    def row(elapsed):
        data = {"elapsed_s": elapsed}
        for name in NAMES:
            data[name + "_cpu_s"] = totals[name]
            data[name + "_state"] = "trace" if name in roots else "not_started"
        data["database_cpu_s"] = totals["mysql"] + totals["sysbench"]
        return data

    rows.append(row(0.0))
    for index in range(count):
        for name in NAMES:
            totals[name] += increments[name][index]
        rows.append(row(min(duration, (index + 1) * interval)))
    if sum(totals.values()) > duration + 0.00001:
        raise RuntimeError("Trace assigns more workload residency than available CPU time")
    metadata["total_residency_s"] = totals
    return rows, metadata


class TraceCpuRecorder:
    def __init__(self, output_dir, cpu, interval=0.1, buffer_kb=65536):
        self.output = Path(output_dir)
        self.cpu = int(cpu)
        self.interval = float(interval)
        self.buffer_kb = int(buffer_kb)
        if self.cpu < 0 or self.interval <= 0 or self.buffer_kb <= 0:
            raise ValueError("Invalid trace recorder settings")
        self.instance = None
        self.started = None
        self.stopped = None
        self.roots = {}
        self.initial_tids = {}
        self.finished = False
        self.metadata = {"status": "not_started", "cpu": self.cpu,
                         "interval": self.interval, "buffer_kb_per_cpu": self.buffer_kb,
                         "measurement": "scheduled_residency_seconds",
                         "includes_interrupt_and_possible_guest_steal_time": True}

    def start(self):
        if platform.system() != "Linux" or os.geteuid() != 0:
            raise RuntimeError("ftrace recording requires root in the Linux experiment guest")
        if self.instance is not None:
            raise RuntimeError("Trace recorder already started")
        bases = (Path("/sys/kernel/debug/tracing"), Path("/sys/kernel/tracing"))
        base = next((path for path in bases if (path / "instances").is_dir()), None)
        if base is None:
            raise RuntimeError("ftrace instances unavailable; mount debugfs and verify tracing config")
        self.output.mkdir(parents=True, exist_ok=True)
        for name in ("trace_raw.txt", "trace_metadata.json", "cpu_samples.csv"):
            if (self.output / name).exists():
                raise RuntimeError("Refusing to overwrite trace output: " + str(self.output / name))
        name = "paper02_{}_{}".format(os.getpid(), uuid.uuid4().hex[:8])
        self.instance = base / "instances" / name
        self.instance.mkdir()
        try:
            self._write("tracing_on", "0")
            self._write("events/enable", "0")
            if (self.instance / "current_tracer").exists():
                self._write("current_tracer", "nop")
            clocks = (self.instance / "trace_clock").read_text().replace("[", "").replace("]", "").split()
            if "mono" not in clocks:
                raise RuntimeError("Linux trace_clock=mono unavailable; clocks=" + repr(clocks))
            self._write("trace_clock", "mono")
            self._write("tracing_cpumask", _cpu_mask(self.cpu))
            self._write("buffer_size_kb", str(self.buffer_kb))
            self._write("trace", "")
            for event in ("sched_switch", "sched_process_fork", "sched_process_exec",
                          "sched_process_exit", "sched_process_free"):
                self._write("events/sched/{}/enable".format(event), "1")
            self.metadata["trace_clock"] = (self.instance / "trace_clock").read_text().strip()
            self.metadata["tracing_cpumask"] = (self.instance / "tracing_cpumask").read_text().strip()
            self.metadata["instance"] = str(self.instance)
            self._write("tracing_on", "1")
            self.started = time.monotonic()
            self.metadata["recording_started_monotonic"] = self.started
            self.metadata["status"] = "recording"
            return self.started
        except BaseException:
            self.close()
            raise

    def _write(self, relative, value):
        (self.instance / relative).write_text(value + ("\n" if value else ""))

    def attach(self, name, pid):
        if self.started is None or self.finished:
            raise RuntimeError("Trace recorder is not recording")
        if name not in NAMES or name in self.roots:
            raise ValueError("Unknown or duplicate workload name: " + name)
        pid = int(pid)
        task_directory = Path("/proc") / str(pid) / "task"
        tids = [int(path.name) for path in task_directory.iterdir() if path.name.isdigit()]
        if pid not in tids:
            raise RuntimeError("Workload process vanished before trace attachment")
        self.roots[name] = pid
        self.initial_tids[name] = sorted(tids)
        return tids

    def finish(self, origin, end):
        if self.instance is None or self.started is None or self.finished:
            raise RuntimeError("Trace recorder is not recording or already finished")
        self.finished = True
        try:
            if origin < self.started or end <= origin:
                raise ValueError("Trace measurement must be wholly inside the recording interval")
            self._write("tracing_on", "0")
            self.stopped = time.monotonic()
            if end > self.stopped:
                raise ValueError("Trace end exceeds recording stop time")
            stats_text = (self.instance / "per_cpu" / ("cpu" + str(self.cpu)) / "stats").read_text()
            (self.output / "trace_cpu_stats.txt").write_text(stats_text)
            stats = {}
            for line in stats_text.splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    stats[key.strip()] = value.strip()
            self.metadata.update({"cpu_stats": stats, "roots": self.roots,
                                  "initial_tids": self.initial_tids,
                                  "origin_monotonic": origin, "end_monotonic": end,
                                  "recording_stopped_monotonic": self.stopped})
            with (self.instance / "trace").open() as src, (self.output / "trace_raw.txt").open("w") as dest:
                shutil.copyfileobj(src, dest)
            for key in ("overrun", "commit overrun", "dropped events"):
                if key not in stats:
                    raise RuntimeError("Missing ftrace integrity counter: " + key)
                if int(stats[key]) != 0:
                    raise RuntimeError("Incomplete ftrace buffer: {}={}".format(key, stats[key]))
            with (self.output / "trace_raw.txt").open() as lines:
                rows, parsed = reconstruct_rows(lines, origin, end, self.interval, self.cpu,
                                                self.roots, self.initial_tids)
            self.metadata["parser"] = parsed
            fieldnames = (["elapsed_s"] + [name + "_cpu_s" for name in NAMES] +
                          ["database_cpu_s"] + [name + "_state" for name in NAMES])
            with (self.output / "cpu_samples.csv").open("w", newline="") as out:
                writer = csv.DictWriter(out, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            self.metadata["status"] = "completed"
            return rows[-1]
        except BaseException as exc:
            self.metadata["status"] = "failed"
            self.metadata["error"] = str(exc)
            raise
        finally:
            _write_json(self.output / "trace_metadata.json", self.metadata)
            self.close()

    def close(self):
        if self.instance is not None:
            instance = self.instance
            try:
                self._write("tracing_on", "0")
                self._write("events/enable", "0")
                instance.rmdir()
            except OSError:
                # Never try recursive deletion against a kernel pseudo-filesystem.
                pass
            self.instance = None
