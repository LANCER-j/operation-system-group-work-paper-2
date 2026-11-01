"""Offline accounting checks; these are not experimental measurements."""
import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sample_cpu


class ProcTests(unittest.TestCase):
    def test_stat_comm_with_spaces_and_parentheses(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "71"
            task.mkdir()
            fields = ["0"] * 30
            fields[0], fields[11], fields[12], fields[19] = "S", "17", "3", "999"
            (task / "stat").write_text("71 (a name ) with (parens)) " + " ".join(fields))
            self.assertEqual(sample_cpu.read_stat(71, tmp),
                             {"state": "S", "ticks": 20, "start_ticks": 999})
            self.assertIsNone(sample_cpu.read_stat(72, tmp))

    def test_setup_baseline_thread_sum_and_exit_retention(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(sample_cpu.os, "sysconf", return_value=100, create=True), \
                patch.object(sample_cpu, "read_stat") as read:
            read.return_value = {"state": "S", "ticks": 500, "start_ticks": 1}
            sample = sample_cpu.CpuSampler(Path(tmp) / "samples.csv")
            try:
                sample.attach("mysql", 71, subtract_existing=True)
                read.return_value = {"state": "S", "ticks": 525, "start_ticks": 1}
                self.assertEqual(sample.sample()["database_cpu_s"], 0.25)
                read.return_value = {"state": "Z", "ticks": 530, "start_ticks": 1}
                self.assertEqual(sample.sample()["database_cpu_s"], 0.30)
                read.return_value = None
                self.assertEqual(sample.sample()["database_cpu_s"], 0.30)
                read.return_value = {"state": "S", "ticks": 999, "start_ticks": 2}
                with self.assertRaises(RuntimeError):
                    sample.sample()
            finally:
                sample.close()


if __name__ == "__main__":
    unittest.main()
