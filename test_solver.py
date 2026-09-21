"""Independent tiny-instance oracle and regression tests (no third-party packages)."""
import copy
import itertools
import math
import random
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path

from solver import (Blank, Config, ExactLimitError, InfeasibleError, ModelError,
                    Order, SearchTimeout, brute_force, load_blanks, load_orders,
                    search_10s, validate_plan)


def order(name, size, pieces, steel="S", dia=20):
    linear = math.pi * (dia / 1000) ** 2 / 4 * 7850
    return Order(name, steel, dia, size, (pieces - 0.25) * size * linear, 7850, pieces)


def objective(m, cfg, n):
    knives, finished, raw, covered = m
    y, c = finished / raw, covered / n
    if cfg.objective == "score":
        return (-40 * cfg.baseline_knives / knives - 40 * y - 20 * c, knives, -y, -c)
    return knives, -y, -c


def oracle(orders, cfg, blanks):
    """Literal Cartesian enumeration, with no solver model/DP/dominance helpers."""
    def partitions(ids):
        if not ids:
            yield []
            return
        for rest in partitions(ids[1:]):
            yield [(ids[0],)] + rest
            for j in range(len(rest)):
                yield rest[:j] + [(ids[0],) + rest[j]] + rest[j + 1:]

    cache = {}

    def candidates(ids):
        ids = tuple(sorted(ids))
        if ids in cache:
            return cache[ids]
        output = []
        cache[ids] = output
        selected = [orders[i] for i in ids]
        if len({(o.steel, o.diameter) for o in selected}) != 1:
            return output
        caps = [o.pieces + math.floor(o.pieces * cfg.max_overproduction_ratio + 1e-9) for o in selected]
        linear = selected[0].linear_weight
        for blank in blanks:
            usable = blank.weight * cfg.rolling_yield / linear
            if blank.usable_length is not None:
                usable = min(usable, blank.usable_length)
            patterns = []
            for p in range(1, cfg.bed_width + 1):
                if cfg.bed_width_mm is not None and p * selected[0].diameter + (p - 1) * cfg.bar_gap_mm > cfg.bed_width_mm + 1e-8:
                    continue
                for ks in itertools.product(*(range(cap // p + 1) for cap in caps)):
                    if not any(ks):
                        continue
                    shared = cfg.length_mode == 'net_shared_trim'
                    lengths = [k * o.size + (0 if shared else 2 * cfg.trim) for o, k in zip(selected, ks) if k]
                    total = sum(lengths) + (2 * cfg.trim if shared else 0)
                    if max(lengths) + (2 * cfg.trim if shared else 0) > usable + 1e-8:
                        continue
                    if not cfg.min_bed_length - 1e-8 <= total <= cfg.bed_length + 1e-8:
                        continue
                    if not cfg.min_bed_weight - 1e-8 <= total * p * linear <= cfg.bed_weight + 1e-8:
                        continue
                    raw = math.ceil(total * p / usable - 1e-9) * blank.weight
                    knives = sum(ks) + 1 if shared else sum(k + 1 for k in ks if k)
                    patterns.append((tuple(k * p for k in ks), knives, raw))
            for nr in range(1, cfg.max_rounds + 1):
                for indices in itertools.combinations_with_replacement(range(len(patterns)), nr):
                    rows = [patterns[j] for j in indices]
                    produced = [sum(r[0][i] for r in rows) for i in range(len(ids))]
                    if not all(o.pieces <= q <= cap for o, q, cap in zip(selected, produced, caps)):
                        continue
                    output.append((sum(r[1] for r in rows), sum(q * o.size * linear for q, o in zip(produced, selected)),
                                   sum(r[2] for r in rows), len(ids) if len(ids) > 1 else 0))
        return output

    best = None
    for partition in partitions(tuple(range(len(orders)))):
        for batches in itertools.product(*(candidates(ids) for ids in partition)):
            metrics = tuple(sum(b[i] for b in batches) for i in range(4))
            key = objective(metrics, cfg, len(orders))
            if best is None or key < best:
                best = key
    return best


class SolverTests(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(bed_length=10, bed_width=2, trim=0.5, max_rounds=2)
        self.orders = [order("A", 2, 2), order("B", 3, 1)]
        self.blanks = [Blank(1, self.orders[0].linear_weight * 10)]

    def test_matches_unpruned_oracle(self):
        rng = random.Random(771)
        for case in range(36):
            cfg = replace(self.cfg, bed_length=rng.choice([5, 8, 10]),
                          max_overproduction_ratio=rng.choice([0, 0.5]),
                          min_bed_length=rng.choice([0, 2]),
                          objective="score" if case % 2 else "lex", baseline_knives=6)
            orders = [order(str(i), rng.choice([1.5, 2, 3]), rng.randint(1, 3),
                            "T" if case % 4 == 0 and i == 1 else "S") for i in range(2)]
            blanks = self.blanks + [Blank(2, self.blanks[0].weight * 0.65)]
            expected = oracle(orders, cfg, blanks)
            with self.subTest(case=case):
                if expected is None:
                    with self.assertRaises(InfeasibleError):
                        brute_force(orders, cfg, blanks)
                else:
                    plan = brute_force(orders, cfg, blanks)
                    m = validate_plan(plan, orders, cfg, blanks)
                    actual = objective((m['knives'], m['finished_weight'], m['blank_weight'], round(m['coverage'] * len(orders))), cfg, len(orders))
                    for a, b in zip(actual, expected):
                        self.assertAlmostEqual(a, b, places=8)

    def test_three_order_partition_and_global_ratio(self):
        orders = [order("A", 1.5, 2), order("B", 2, 1), order("C", 3, 2, steel="T")]
        for mode in ("lex", "score"):
            cfg = replace(self.cfg, objective=mode, baseline_knives=8, max_overproduction_ratio=0.5)
            expected = oracle(orders, cfg, self.blanks)
            m = validate_plan(brute_force(orders, cfg, self.blanks), orders, cfg, self.blanks)
            actual = objective((m['knives'], m['finished_weight'], m['blank_weight'], round(m['coverage'] * 3)), cfg, 3)
            for a, b in zip(actual, expected):
                self.assertAlmostEqual(a, b, places=8)

    def test_pdf_50m_16_pieces_is_17_knives(self):
        cfg = Config(bed_length=50, bed_width=1, trim=1, max_rounds=1)
        orders = [order("A", 3, 16)]
        plan = brute_force(orders, cfg)
        metrics = validate_plan(plan, orders, cfg)
        self.assertEqual(plan[0]['length_scheme'], [{'A': 50}])
        self.assertEqual(metrics['knives'], 17)

    def test_parallel_material_accounting(self):
        cfg = Config(bed_length=10, bed_width=5, trim=1, max_rounds=1, blank_length=10)
        orders = [order("A", 3, 5)]
        plan = brute_force(orders, cfg)
        self.assertEqual(plan[0]['counts'], [5])
        self.assertEqual(plan[0]['blank_counts'], [3])
        bad = copy.deepcopy(plan)
        bad[0]['blank_counts'] = [1]
        with self.assertRaisesRegex(ModelError, 'Insufficient blanks'):
            validate_plan(bad, orders, cfg)

    def test_rejects_corrupt_results(self):
        plan = brute_force(self.orders, self.cfg, self.blanks)
        for kind in ("duplicate", "missing", "short", "fraction", "width", "rounds", "unknown", "nan"):
            bad = copy.deepcopy(plan)
            first = bad[0]
            oid = next(iter(first['length_scheme'][0]))
            if kind == "duplicate": bad.append(copy.deepcopy(first))
            elif kind == "missing": bad.clear()
            elif kind == "short": first['length_scheme'][0][oid] = 1.0
            elif kind == "fraction": first['counts'][0] = 1.5
            elif kind == "width": first['counts'][0] = 999
            elif kind == "rounds": first['blank_counts'].clear()
            elif kind == "unknown": first['blank_type'] = 999
            elif kind == "nan": first['length_scheme'][0][oid] = float('nan')
            with self.subTest(kind=kind), self.assertRaises(ModelError):
                validate_plan(bad, self.orders, self.cfg, self.blanks)

    def test_empty_infeasible_and_limits(self):
        self.assertEqual(brute_force([], self.cfg), [])
        self.assertEqual(search_10s([], self.cfg), [])
        with self.assertRaises(InfeasibleError):
            brute_force([order("A", 20, 1)], self.cfg)
        with self.assertRaises(ExactLimitError):
            brute_force(self.orders, replace(self.cfg, exact_max_work=1))
        with self.assertRaises(ModelError):
            search_10s(self.orders, self.cfg, seconds=0)
        # A microscopic budget is NOT an error any more.  Seeding is not
        # interruptible (clause 12 requires a scheme for every order), so a budget
        # too small for the annealing neighbourhood now just means "no annealing":
        # the seeded plan is returned and it is valid.  The old test asserted
        # `SearchTimeout` here, which encoded the bug -- one shared deadline let the
        # clock expire during seeding, and `_seed_group` was truncated to a partial
        # plan before the timeout surfaced.  `seconds=0` above is the real guard
        # against a nonsensical budget.
        plan = search_10s(self.orders, self.cfg, seconds=1e-12, blanks=self.blanks)
        validate_plan(plan, self.orders, self.cfg, self.blanks)
        self.assertTrue(plan)


    def test_search_feasibility_quality_and_budget(self):
        exact = validate_plan(brute_force(self.orders, self.cfg, self.blanks), self.orders, self.cfg, self.blanks)
        start = time.perf_counter()
        for seed in range(5):
            result = search_10s(self.orders, self.cfg, seconds=0.08, seed=seed, blanks=self.blanks)
            actual = validate_plan(result, self.orders, self.cfg, self.blanks)
            self.assertGreaterEqual(actual['knives'], exact['knives'])
            self.assertEqual(actual['knives'], exact['knives'])
            self.assertAlmostEqual(actual['yield_rate'], exact['yield_rate'])
        self.assertLess(time.perf_counter() - start, 2.0)

    def test_lower_bounds_require_combination(self):
        cfg = replace(self.cfg, min_bed_length=6, max_rounds=1)
        orders = [order("A", 2, 1), order("B", 2, 1)]
        plan = search_10s(orders, cfg, seconds=0.05, blanks=self.blanks)
        self.assertEqual(len(plan), 1)
        self.assertEqual(validate_plan(plan, orders, cfg, self.blanks)['coverage'], 1)

    def test_width_weight_and_decimal_lengths(self):
        cfg = replace(self.cfg, trim=0.1, bed_width_mm=39, bed_weight=20)
        orders = [order("A", 0.3, 3)]
        plan = brute_force(orders, cfg, self.blanks)
        self.assertTrue(all(p == 1 for b in plan for p in b['counts']))
        validate_plan(plan, orders, cfg, self.blanks)

    def test_csv_validation_and_blank_dimensions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'orders.csv'
            path.write_text('订单号,钢种,直径,定尺长度,重量,密度\nA,S,20,3,0.01,7850\n', encoding='utf-8-sig')
            orders = load_orders(str(path), replace(self.cfg, weight_scale=1000))
            self.assertEqual(orders[0].pieces, 2)
            path.write_text('order_id,steel,diameter,length,weight,density\nA,S,20,3,0,7850\n', encoding='utf-8')
            with self.assertRaises(ModelError): load_orders(str(path), self.cfg)
            path.write_text('blank_type,width,thickness,length,density\n2,100,100,10,7850\n', encoding='utf-8')
            self.assertAlmostEqual(load_blanks(str(path))[0].weight, 785)


if __name__ == '__main__':
    unittest.main(verbosity=2)
