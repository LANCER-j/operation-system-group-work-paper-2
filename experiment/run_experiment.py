#!/usr/bin/env python3
"""Isolated Linux 4.9 experiment. Compatible with Ubuntu 18.04 Python 3.6."""
import argparse
import gzip
import hashlib
import json
import os
import platform
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from sample_cpu import CpuSampler, reap_if_done


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def command_output(command):
    return subprocess.check_output(command, universal_newlines=True,
                                   stderr=subprocess.STDOUT).strip()


def cpu_mount():
    for line in Path("/proc/mounts").read_text().splitlines():
        fields = line.split()
        if fields[2] == "cgroup" and "cpu" in fields[3].split(","):
            return Path(fields[1].replace("\\040", " "))
    raise RuntimeError("No mounted cgroup v1 CPU controller. See README.")


def config_text(release):
    if Path("/proc/config.gz").exists():
        with gzip.open("/proc/config.gz", "rt") as src:
            return src.read()
    path = Path("/boot") / ("config-" + release)
    return path.read_text() if path.exists() else "unavailable\n"


def preflight(args):
    require(platform.system() == "Linux", "Linux guest required; Windows is unsupported.")
    require(os.geteuid() == 0, "Run as root inside the isolated experiment guest.")
    release = platform.release()
    require(release.startswith("4.9.") or release.startswith("4.9-"),
            "Expected Linux 4.9; found " + release)
    require("-paper02-" + args.scheduler in release,
            "Kernel release lacks expected -paper02-{} marker".format(args.scheduler))
    marker = Path("/proc/sys/kernel/sched_ktz_enabled")
    if args.scheduler == "ule":
        require(marker.exists() and marker.read_text().strip() == "1",
                "ULE requires sched_ktz_enabled=1; this script never changes it.")
    else:
        require(not marker.exists(), "CFS must use the upstream kernel, not disabled ULE.")
    available_cpus = sorted(os.sched_getaffinity(0))
    require(args.cpu in available_cpus, "Requested CPU is not available.")
    controller_cpus = [cpu for cpu in available_cpus if cpu != args.cpu]
    require(controller_cpus, "At least two available CPUs are required to isolate the controller.")
    controller_cpu = controller_cpus[0]
    # Pin control/sampling work separately. Every workload child explicitly
    # resets its own affinity to args.cpu in child_setup before exec.
    os.sched_setaffinity(0, {controller_cpu})
    required = ("gcc", "mysqld", "mysql", "mysqladmin", "sysbench")
    paths = {name: shutil.which(name) for name in required}
    require(all(paths.values()), "Missing programs: " +
            ", ".join(name for name in required if not paths[name]))
    versions = {name: command_output([paths[name], "--version"]) for name in required}
    require("5.7." in versions["mysqld"], "This protocol requires MySQL 5.7.")
    require("sysbench 1.0." in versions["sysbench"], "This protocol requires Sysbench 1.0.")
    require(args.lua.is_file(), "oltp_read_write.lua not found: " + str(args.lua))
    return {"kernel_release": release, "versions": versions, "paths": paths,
            "uname": list(platform.uname()), "cpu_mount": str(cpu_mount()),
            "controller_affinity_before": available_cpus,
            "controller_cpu": controller_cpu,
            "controller_affinity": sorted(os.sched_getaffinity(0)),
            "boot_command_line": Path("/proc/cmdline").read_text().strip(),
            "cpuinfo": Path("/proc/cpuinfo").read_text(),
            "os_release": Path("/etc/os-release").read_text(),
            "kernel_config": config_text(release),
            "ktz_enabled": marker.read_text().strip() if marker.exists() else "absent",
            "ktz_forced_timeslice": Path("/proc/sys/kernel/sched_ktz_forced_timeslice").read_text().strip()
                if Path("/proc/sys/kernel/sched_ktz_forced_timeslice").exists() else "absent",
            "clock_ticks_per_second": os.sysconf("SC_CLK_TCK")}


def configure_group(parent):
    parent.mkdir()
    for name in ("fibo", "database"):
        group = parent / name
        group.mkdir()
        (group / "cpu.shares").write_text("1024\n")
        # No hard quota: these groups compete by weight when both are runnable.
        quota = group / "cpu.cfs_quota_us"
        if quota.exists():
            require(quota.read_text().strip() == "-1", "Unexpected cgroup quota")


def launch(command, log_path, cpu=None, group=None):
    def child_setup():
        os.setsid()
        if cpu is not None:
            os.sched_setaffinity(0, {cpu})
        if group is not None:
            (group / "tasks").write_text(str(os.getpid()) + "\n")
    with open(str(log_path), "w") as log:
        return subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                preexec_fn=child_setup, universal_newlines=True)


def stop_owned(process):
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)
    except ProcessLookupError:
        process.wait()


def remove_groups(parent):
    # rmdir only removes empty cgroups; never recurse or touch external tasks.
    for name in ("fibo", "database"):
        group = parent / name
        if group.exists():
            group.rmdir()
    if parent.exists():
        parent.rmdir()


def trace_recorder(args, output):
    from trace_cpu import TraceCpuRecorder
    return TraceCpuRecorder(output, args.cpu, interval=args.interval)


def probe_cpu_accounting(args, output):
    # The upstream ULE port may omit sum_exec_runtime accounting. A flat
    # /proc curve is not evidence of starvation until counters are validated.
    command = [str(output / "fibo"), "36", "10"]
    record = {"status": "started", "command": command, "accounting": args.accounting,
              "purpose": "validate CPU counters before interpreting starvation",
              "cpu": args.cpu}
    process = sampler = trace = None
    try:
        if args.accounting == "trace":
            probe_trace_dir = output / "probe_trace"
            probe_trace_dir.mkdir()
            trace = trace_recorder(args, probe_trace_dir)
            trace.start()
        origin = time.monotonic()
        process = launch(command, output / "probe_fibo.json", args.cpu)
        if trace is not None:
            trace.attach("fibo", process.pid)
        sampler = CpuSampler(output / "probe_cpu_samples.csv", origin)
        sampler.attach("fibo", process.pid)
        deadline = time.monotonic() + args.setup_timeout
        while True:
            sampler.sample()
            code = reap_if_done(process, sampler)
            if code is not None:
                record["returncode"] = code
                require(code == 0, "CPU accounting probe executable failed")
                break
            require(time.monotonic() < deadline, "CPU accounting probe timed out")
            time.sleep(args.interval)
        record["final_sample"] = sampler.sample()
        record["fibo_self_report"] = json.loads((output / "probe_fibo.json").read_text())
        proc_cpu = record["final_sample"]["fibo_cpu_s"]
        clock_cpu = record["fibo_self_report"]["cpu_seconds"]
        minimum = 1.0 / sampler.hz
        record["minimum_cpu_seconds"] = minimum
        record["proc_and_process_clock_advance"] = proc_cpu >= minimum and clock_cpu >= minimum
        if trace is not None:
            record["trace_final_sample"] = trace.finish(origin, time.monotonic())
            require(record["trace_final_sample"]["fibo_cpu_s"] >= minimum,
                    "Trace CPU accounting unavailable: fixed Fibo work completed "
                    "without enough traced CPU time. No experiment ran.")
            if not record["proc_and_process_clock_advance"]:
                record["warning"] = ("Kernel /proc or process CPU clock is unusable; "
                                     "only validated sched_switch trace accounting will be analyzed.")
            record["authoritative_cpu_source"] = "sched_switch_trace"
        else:
            require(record["proc_and_process_clock_advance"],
                    "Kernel CPU accounting unavailable: fixed Fibo work completed, but "
                    "/proc CPU time={:.9f}s and process CPU clock={:.9f}s. "
                    "Do not interpret zero CPU counts as starvation; no experiment ran."
                    .format(proc_cpu, clock_cpu))
            record["authoritative_cpu_source"] = "proc_stat"
        record["status"] = "passed"
        print("CPU accounting probe passed", flush=True)
    except BaseException as exc:
        record["status"] = "failed"
        record["error"] = str(exc)
        raise
    finally:
        if sampler is not None:
            sampler.close()
        if trace is not None:
            trace.close()
        stop_owned(process)
        save_json(output / "cpu_accounting_probe.json", record)


def run_one(args, env, number, output):
    label = "{}_r{:02d}{}".format(args.scheduler.upper(), number,
                                 "_SMOKE" if args.smoke else "")
    run = output / label
    run.mkdir()
    groups = Path(env["cpu_mount"]) / ("paper02_{}_{}_{}".format(os.getpid(), number, args.scheduler))
    mysql_process = fibo_process = sysbench_process = prepare_process = init_process = None
    sampler = trace = None
    metadata = {"label": label, "status": "started", "commands": [],
                "scheduler": args.scheduler, "smoke": args.smoke,
                "accounting": args.accounting,
                "proc_counters_valid_for_analysis": args.accounting == "proc",
                "authoritative_cpu_source": "sched_switch_trace" if args.accounting == "trace" else "proc_stat"}
    # Only this automatically created private directory will ever be deleted.
    temp = tempfile.TemporaryDirectory(prefix="paper02-mysql-")
    private = Path(temp.name)
    socket = private / "mysql.sock"
    datadir = private / "data"
    datadir.mkdir(mode=0o700)
    try:
        configure_group(groups)
        db_group = groups / "database"
        init_cmd = [env["paths"]["mysqld"], "--no-defaults", "--initialize-insecure",
                    "--user=root", "--datadir=" + str(datadir)]
        metadata["commands"].append(init_cmd)
        init_process = launch(init_cmd, run / "mysql_initialize.log", args.cpu, db_group)
        require(init_process.wait(timeout=args.setup_timeout) == 0,
                "MySQL initialization failed; inspect mysql_initialize.log")
        mysql_cmd = [env["paths"]["mysqld"], "--no-defaults", "--user=root",
                     "--datadir=" + str(datadir), "--socket=" + str(socket),
                     "--pid-file=" + str(private / "mysql.pid"), "--skip-networking",
                     "--log-error=" + str(run / "mysql_error.log"),
                     "--innodb-buffer-pool-size=128M", "--innodb-log-file-size=16M",
                     "--max-connections=112", "--performance-schema=OFF"]
        metadata["commands"].append(mysql_cmd)
        if args.accounting == "trace":
            trace = trace_recorder(args, run)
            trace.start()
        mysql_process = launch(mysql_cmd, run / "mysql_console.log", args.cpu, db_group)
        if trace is not None:
            trace.attach("mysql", mysql_process.pid)
        client_args = ["--no-defaults", "--protocol=socket", "--socket=" + str(socket), "-uroot"]
        deadline = time.monotonic() + args.setup_timeout
        while True:
            require(mysql_process.poll() is None, "Private MySQL exited during startup")
            if socket.exists():
                result = subprocess.run([env["paths"]["mysqladmin"]] + client_args + ["ping"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
                if result.returncode == 0:
                    break
            require(time.monotonic() < deadline, "Private MySQL startup timed out")
            time.sleep(0.2)
        subprocess.check_call([env["paths"]["mysql"]] + client_args +
                              ["-e", "CREATE DATABASE sbtest;"])
        common = [env["paths"]["sysbench"], str(args.lua), "--db-driver=mysql",
                  "--mysql-socket=" + str(socket), "--mysql-user=root", "--mysql-password=",
                  "--mysql-db=sbtest", "--tables=1", "--table-size=" + str(args.table_size),
                  "--threads=" + str(args.threads), "--rand-seed=" + str(args.seed)]
        prepare_cmd = common + ["prepare"]
        metadata["commands"].append(prepare_cmd)
        prepare_process = launch(prepare_cmd, run / "sysbench_prepare.log", args.cpu, db_group)
        require(prepare_process.wait(timeout=args.setup_timeout) == 0,
                "Sysbench prepare failed; inspect its log")
        # Setup work is excluded by resetting MySQL's sampling baseline here.
        origin = time.monotonic()
        sampler = CpuSampler(run / ("proc_raw_cpu_samples.csv" if trace is not None else "cpu_samples.csv"), origin)
        sampler.attach("mysql", mysql_process.pid, subtract_existing=True)
        fibo_cmd = [str(output / "fibo"), str(args.fibo_n), str(args.fibo_repeats)]
        metadata["commands"].append(fibo_cmd)
        fibo_process = launch(fibo_cmd, run / "fibo.json", args.cpu, groups / "fibo")
        if trace is not None:
            trace.attach("fibo", fibo_process.pid)
        sampler.attach("fibo", fibo_process.pid)
        metadata["fibo_launch_elapsed_s"] = time.monotonic() - origin
        metadata["pids"] = {"fibo": fibo_process.pid, "mysql": mysql_process.pid}
        launch_after = time.monotonic() + args.lead_seconds
        while time.monotonic() < launch_after:
            sampler.sample()
            require(reap_if_done(fibo_process, sampler) is None,
                    "Fibo finished during the lead period; increase fixed workload")
            time.sleep(min(args.interval, max(0, launch_after - time.monotonic())))
        run_cmd = common + ["--events=" + str(args.events), "--time=0", "--report-interval=1", "run"]
        metadata["commands"].append(run_cmd)
        sysbench_process = launch(run_cmd, run / "sysbench_run.log", args.cpu, db_group)
        if trace is not None:
            trace.attach("sysbench", sysbench_process.pid)
        sampler.attach("sysbench", sysbench_process.pid)
        metadata["pids"]["sysbench"] = sysbench_process.pid
        metadata["sysbench_launch_elapsed_s"] = time.monotonic() - origin
        metadata["observed_affinity"] = {name: sorted(os.sched_getaffinity(pid))
            for name, pid in metadata["pids"].items()}
        require(all(cpus == [args.cpu] for cpus in metadata["observed_affinity"].values()),
                "A workload process has unexpected CPU affinity")
        metadata["observed_cgroups"] = {name: Path("/proc/{}/cgroup".format(pid)).read_text()
            for name, pid in metadata["pids"].items()}
        metadata["cpu_shares"] = {name: (groups / name / "cpu.shares").read_text().strip()
            for name in ("fibo", "database")}
        save_json(run / "run.json", metadata)
        deadline = origin + args.timeout
        final_codes = {}
        while len(final_codes) != 2:
            sampler.sample()
            require(mysql_process.poll() is None, "Private MySQL exited during measurement")
            for name, process in (("fibo", fibo_process), ("sysbench", sysbench_process)):
                if name not in final_codes:
                    code = reap_if_done(process, sampler)
                    if code is not None:
                        final_codes[name] = code
                        metadata[name + "_end_elapsed_s"] = time.monotonic() - origin
                        require(code == 0, name + " returned error " + str(code))
            require(time.monotonic() < deadline, "Measurement timed out; incomplete result")
            if len(final_codes) != 2:
                time.sleep(args.interval)
        metadata["proc_final_counters"] = sampler.sample()
        metadata["final_counters"] = trace.finish(origin, time.monotonic()) if trace is not None else metadata["proc_final_counters"]
        metadata["returncodes"] = final_codes
        metadata["status"] = "completed"
        metadata["fibo_finished_before_sysbench"] = metadata["fibo_end_elapsed_s"] < metadata["sysbench_end_elapsed_s"]
        print(label + " completed", flush=True)
    except BaseException as exc:
        metadata["status"] = "failed"
        metadata["error"] = str(exc)
        raise
    finally:
        if sampler is not None:
            sampler.close()
        if trace is not None:
            trace.close()
        for process in (sysbench_process, fibo_process, prepare_process, init_process, mysql_process):
            stop_owned(process)
        metadata["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        save_json(run / "run.json", metadata)
        remove_groups(groups)
        temp.cleanup()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scheduler", choices=("cfs", "ule"), required=True)
    parser.add_argument("--accounting", choices=("trace", "proc"), default="trace",
                        help="CPU accounting method; use trace on both kernels for the comparison")
    parser.add_argument("--output", type=Path, required=True, help="new output directory; never overwritten")
    parser.add_argument("--cpu", type=int, default=0)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--threads", type=int, default=80)
    parser.add_argument("--table-size", type=int, default=10000)
    parser.add_argument("--events", type=int, default=10000)
    parser.add_argument("--fibo-n", type=int, default=40)
    parser.add_argument("--fibo-repeats", type=int, default=100)
    parser.add_argument("--lead-seconds", type=float, default=7.0)
    parser.add_argument("--interval", type=float, default=0.1)
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--setup-timeout", type=float, default=600)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--lua", type=Path, default=Path("/usr/share/sysbench/oltp_read_write.lua"))
    parser.add_argument("--smoke", action="store_true", help="small pipeline check, not a paper result")
    args = parser.parse_args()
    if args.smoke:
        args.runs, args.threads, args.table_size, args.events = 1, 4, 100, 20
        args.fibo_n, args.fibo_repeats, args.lead_seconds = 34, 10, 0.05
    require(args.runs >= (1 if args.smoke else 3), "Formal experiments require at least 3 runs")
    require(all(x > 0 for x in (args.threads, args.table_size, args.events,
                                args.fibo_repeats, args.interval, args.timeout, args.setup_timeout)),
            "Counts and timeouts must be positive")
    require(2 <= args.fibo_n <= 48 and args.lead_seconds >= 0, "Invalid Fibo/lead setting")
    args.lua = args.lua.resolve()
    env = preflight(args)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    params = {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()}
    params["status"] = "smoke_only" if args.smoke else "configured"
    save_json(output / "parameters.json", params)
    save_json(output / "environment.json", env)
    source = Path(__file__).resolve().parent / "fibo.c"
    compile_cmd = [env["paths"]["gcc"], "-O2", "-std=c11", "-Wall", "-Wextra", "-Werror",
                   str(source), "-o", str(output / "fibo")]
    with open(str(output / "compile.log"), "w") as log:
        subprocess.check_call(compile_cmd, stdout=log, stderr=subprocess.STDOUT)
    save_json(output / "build.json", {"command": compile_cmd,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "binary_sha256": hashlib.sha256((output / "fibo").read_bytes()).hexdigest()})
    probe_cpu_accounting(args, output)
    for number in range(1, args.runs + 1):
        run_one(args, env, number, output)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        raise SystemExit("ERROR: " + str(error))
