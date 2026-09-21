import json
import random
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from competition_solver import Factory
from solver import Config, Model, _export, load_blanks, load_orders, search_10s, validate_plan


class CompetitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = Config(**json.loads(Path('data/competition.config.json').read_text()))
        cls.orders = load_orders('data/orders.normalized.csv', cls.cfg)
        cls.blanks = load_blanks('data/blanks.normalized.csv')

    def test_normalization_and_anomaly(self):
        self.assertEqual(len(self.orders), 4999)
        self.assertEqual(self.orders[0].size, 6.4)
        self.assertEqual(self.orders[0].density, 9860)
        self.assertAlmostEqual(self.blanks[0].weight, 9613.5)
        self.assertAlmostEqual(self.blanks[1].weight, 6162.5)
        audit = json.loads(Path('data/data_audit.json').read_text(encoding='utf-8'))
        self.assertEqual(audit['rejected'][0]['order_id'], 'A20260949')

    def test_construct_real_orders_and_warm_restart(self):
        orders = self.orders[:12]
        model = Model(orders, self.cfg, self.blanks)
        factory = Factory()
        deadline = time.perf_counter() + 10
        initial = _export([factory(model, (i,), random.Random(i), deadline) for i in range(len(orders))], orders, self.cfg)
        before = validate_plan(initial, orders, self.cfg, self.blanks)
        saved = []
        stats = {}
        result = search_10s(orders, self.cfg, seconds=0.15, blanks=self.blanks, initial_plan=initial,
                            constructor=factory, checkpoint=lambda p, e, i: saved.append(p), checkpoint_interval=0.03, stats=stats)
        after = validate_plan(result, orders, self.cfg, self.blanks)
        self.assertLessEqual(after['knives'], before['knives'])
        self.assertGreater(stats['iterations'], 0)
        self.assertGreaterEqual(len(saved), 1)
        for p in saved:
            validate_plan(p, orders, self.cfg, self.blanks)

    def test_watcher_records_success_and_failure(self):
        for code in [0, 7]:
            with tempfile.TemporaryDirectory() as directory:
                child = subprocess.run([sys.executable, 'task_watcher.py', '--run-dir', directory,
                                        '--timeout', '10', '--', sys.executable, '-c', f'raise SystemExit({code})'],
                                       capture_output=True, timeout=15)
                receipt = json.loads((Path(directory) / 'task_status.json').read_text())
                self.assertEqual(child.returncode, code)
                self.assertEqual(receipt['exit_code'], code)
                self.assertEqual(receipt['state'], 'completed' if code == 0 else 'failed')


if __name__ == '__main__':
    unittest.main(verbosity=2)
