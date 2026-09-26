import json
import math
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from platform_check import check
from solver import Config, ModelError, brute_force, validate_plan
from test_solver import objective, oracle, order


class PlatformTests(unittest.TestCase):
    def test_exact_net_model_against_independent_oracle(self):
        orders = [order('A', 2, 3), order('B', 3, 2)]
        for mode in ['lex', 'score']:
            cfg = Config(bed_length=10, bed_width=2, trim=1, max_rounds=2,
                         length_mode='net_shared_trim', objective=mode, baseline_knives=8)
            from solver import Blank
            blanks = [Blank(1, orders[0].linear_weight * 12)]
            expected = oracle(orders, cfg, blanks)
            plan = brute_force(orders, cfg, blanks)
            m = validate_plan(plan, orders, cfg, blanks)
            actual = objective((m['knives'], m['finished_weight'], m['blank_weight'], round(m['coverage'] * 2)), cfg, 2)
            for a, b in zip(actual, expected):
                self.assertAlmostEqual(a, b, places=8)

    def test_net_export_pdf_example(self):
        cfg = Config(bed_length=50, bed_width=1, trim=1, max_rounds=1, length_mode='net_shared_trim')
        orders = [order('A', 3, 16)]
        plan = brute_force(orders, cfg)
        self.assertEqual(plan[0]['length_scheme'], [{'A': 48}])
        self.assertEqual(validate_plan(plan, orders, cfg)['knives'], 17)
        plan[0]['length_scheme'] = [{'A': 50}]
        with self.assertRaises(ModelError):
            validate_plan(plan, orders, cfg)

    def test_exact_boundary_with_two_segments(self):
        cfg = Config(bed_length=50, min_bed_length=50, bed_width=1, trim=1,
                     max_rounds=1, length_mode='net_shared_trim')
        orders = [order('A', 8, 3), order('B', 6, 4)]
        plan = brute_force(orders, cfg)
        self.assertEqual(len(plan), 1)
        self.assertEqual(sum(plan[0]['length_scheme'][0].values()), 48)
        self.assertEqual(validate_plan(plan, orders, cfg)['knives'], 8)

    def test_independent_checker_adds_trim_to_both_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'orders_quarter.csv').write_text('订单号,订单重量(t),坯料钢种,订单直径(mm),订单定尺(mm)\nA,0.1,S,56,4000\n', encoding='utf-8')
            (root / 'blank_used.csv').write_text('坯料,宽度mm,厚度mm,长度mm\n1,260,300,12500\n', encoding='utf-8')
            plan = [{'orders': ['A'], 'length_scheme': [{'A': 144}], 'counts': [17], 'blank_type': 1, 'blank_counts': [20]}]
            self.assertEqual(check(plan, root)['error_counts'].get('bed_weight'), 1)
            plan[0]['length_scheme'] = [{'A': 152}]
            plan[0]['counts'] = [1]
            self.assertEqual(check(plan, root)['error_counts'].get('bed_length'), 1)
            plan[0]['length_scheme'] = [{'A': 48}]
            self.assertTrue(check(plan, root)['passed'])

    def test_reproduces_reported_integer_and_length_failures(self):
        plan = json.loads(Path('tests/fixtures/prelim/legacy/platform_before_fix/初赛结果_棒材优化.json').read_text(encoding='utf-8'))
        result = check(plan, check_delivery=False)
        self.assertEqual(result['error_counts']['noninteger_multiple'], 11315)
        self.assertEqual(result['error_counts']['bed_length'], 44)
        self.assertEqual([(v['scheme'], v['round']) for v in result['errors']['bed_length'][:4]],
                         [(13, 45), (13, 46), (13, 73), (13, 75)])
        # Weight count is deliberately conservative (437 vs reported 395);
        # no private-platform weight implementation has been supplied.
        self.assertGreaterEqual(result['error_counts']['bed_weight'], 395)

    @unittest.skipUnless(Path('runs/platform_fix/result.json').is_file(),
                         'Optional preliminary generated run is not shipped in Git')
    def test_fixed_whole_dataset(self):
        plan = json.loads(Path('runs/platform_fix/result.json').read_text(encoding='utf-8'))
        result = check(plan)
        self.assertTrue(result['passed'], result['error_counts'])
        self.assertEqual(result['valid_orders'], 4999)
        self.assertLessEqual(result['max_round_weight_kg'], 60000 + 1e-7)


if __name__ == '__main__':
    unittest.main(verbosity=2)
