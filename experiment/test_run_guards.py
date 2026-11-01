"""Offline rejection tests. Fabricated fixtures never become experiment data."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import run_experiment
import summarize


class AccountingGuardTests(unittest.TestCase):
    def check_probe(self, proc_cpu, clock_cpu, expected, accounting="proc", trace_cpu=0.2):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            (output / "probe_fibo.json").write_text(json.dumps({
                "cpu_seconds": clock_cpu, "wall_seconds": 0.3}))
            sampler = MagicMock()
            sampler.hz = 100
            sampler.sample.return_value = {"fibo_cpu_s": proc_cpu}
            process = MagicMock()
            process.pid = 999
            args = SimpleNamespace(cpu=0, interval=0.1, setup_timeout=10, accounting=accounting)
            recorder = MagicMock()
            recorder.finish.return_value = {"fibo_cpu_s": trace_cpu}
            with patch.object(run_experiment, "launch", return_value=process), \
                    patch.object(run_experiment, "CpuSampler", return_value=sampler), \
                    patch.object(run_experiment, "reap_if_done", return_value=0), \
                    patch.object(run_experiment, "stop_owned"), \
                    patch.object(run_experiment, "trace_recorder", return_value=recorder):
                if expected == "failed":
                    with self.assertRaisesRegex(RuntimeError, "CPU accounting unavailable"):
                        run_experiment.probe_cpu_accounting(args, output)
                else:
                    run_experiment.probe_cpu_accounting(args, output)
            record = json.loads((output / "cpu_accounting_probe.json").read_text())
            self.assertEqual(record["status"], expected)
            if accounting == "trace" and expected == "passed":
                self.assertEqual(record["authoritative_cpu_source"], "sched_switch_trace")
                self.assertIn("trace_final_sample", record)
                if proc_cpu == 0 or clock_cpu == 0:
                    self.assertIn("warning", record)

    def test_zero_proc_counter_rejected(self):
        self.check_probe(0.0, 0.2, "failed")

    def test_zero_process_clock_rejected(self):
        self.check_probe(0.2, 0.0, "failed")

    def test_both_counters_advance(self):
        self.check_probe(0.2, 0.2, "passed")

    def test_trace_accepts_broken_kernel_accounting(self):
        self.check_probe(0.0, 0.0, "passed", accounting="trace", trace_cpu=0.2)

    def test_zero_trace_accounting_rejected(self):
        self.check_probe(0.2, 0.2, "failed", accounting="trace", trace_cpu=0.0)

    def test_summary_uses_trace_not_broken_process_clock(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            metadata = {"status": "completed", "accounting": "trace", "scheduler": "ule",
                "authoritative_cpu_source": "sched_switch_trace", "smoke": True,
                "sysbench_launch_elapsed_s": 0.1, "sysbench_end_elapsed_s": 0.5,
                "fibo_end_elapsed_s": 0.9, "fibo_finished_before_sysbench": False,
                "final_counters": {"fibo_cpu_s": 0.3}}
            (output / "run.json").write_text(json.dumps(metadata))
            (output / "fibo.json").write_text(json.dumps({"cpu_seconds": 0.0, "wall_seconds": 0.9}))
            (output / "sysbench_run.log").write_text(
                "transactions: 20 (40.00 per sec.)\ntotal time: 0.5s\nLatency (ms):\n avg: 1.5\n")
            (output / "cpu_samples.csv").write_text("elapsed_s,fibo_cpu_s\n0.1,0.1\n0.5,0.1\n")
            (output / "proc_raw_cpu_samples.csv").write_text("elapsed_s,fibo_cpu_s\n0.1,0\n0.5,0\n")
            row = summarize.load_run(output)
            self.assertEqual(row["fibo_cpu_s"], 0.3)
            self.assertEqual(row["fibo_process_clock_raw_s"], 0.0)
            self.assertEqual(row["fibo_wall_s"], 0.9)


if __name__ == "__main__":
    unittest.main()
