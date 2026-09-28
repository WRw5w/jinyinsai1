"""window_ils: the sweep driver, and the baseline bug it would otherwise inherit.

`window_ils` re-prices its exchange rate against the plan it is actually walking.  The
probe got that wrong once (K0/M0 were hard-coded to plan_deep1 while `--plan` took any
path), so the bug is pinned here as a regression test, not just fixed.
"""
import importlib.util
import unittest
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    'window_ils', ROOT / 'tools' / 'analysis' / 'window_ils.py')
wi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wi)
BS = wi.BS

BLANKS = {1: D('5000')}
MERGE2 = ROOT / 'artifacts/runs/requant/plan_merge2.json'


def order(size, lin, pieces, dia=20.0):
    return dict(size=D(str(size)), dia=D(str(dia)), linear=D(str(lin)),
                linear_int=D(str(lin)), steel='GC-3', pieces=int(pieces),
                weight=D(str(pieces)) * D(str(size)) * D(str(lin)))


def mk(schemes, counts, weight='5000', blank_type=1, lin='5.0'):
    out = {'orders': [], 'length_scheme': list(schemes), 'counts': list(counts),
           'blank_type': blank_type, 'blank_counts': []}
    for s in schemes:
        for o in s:
            if o not in out['orders']:
                out['orders'].append(o)
    for s, c in zip(schemes, counts):
        mass = (sum((D(str(v)) for v in s.values()), D(0)) + 2) * D(str(c)) * D(lin)
        out['blank_counts'].append(int((mass / D(weight)).to_integral_value(
            rounding='ROUND_CEILING')))
    return out


class BestWindowTests(unittest.TestCase):
    """The per-batch step the sweep is built from."""

    def setUp(self):
        # The joint-move fixture from test_blank_step: S must deliver exactly 140 pieces
        # across two rounds, so neither round alone can shed the billet -- only the pair.
        self.b = mk([{'A': 60.0, 'S': 40.0}, {'B': 60.0, 'S': 16.0}], [10, 10])
        self.orders = {'A': order(10.0, 5.0, 60), 'B': order(10.0, 5.0, 60),
                       'S': order(4.0, 5.0, 140)}

    def test_it_returns_the_improving_window(self):
        best, truncated = wi.best_window(self.b, self.orders, BLANKS, 2, 4, 3, 40000, 60)
        self.assertIsNotNone(best)
        self.assertFalse(truncated)
        res, win, _meta, _st = best
        self.assertGreater(res['dS'], 0.0)
        self.assertEqual(win, (0, 1))

    def test_it_is_silent_when_no_window_improves(self):
        # 1,000 pieces at count 10 need 100 segments; the bed caps this near 14, so the
        # domain is exhausted with nothing to gain -- a bounded NO, not an abstention.
        b = mk([{'A': 60.0}], [10])
        best, truncated = wi.best_window(b, {'A': order(10.0, 5.0, 1000)},
                                         BLANKS, 2, 4, 3, 40000, 60)
        self.assertIsNone(best)
        self.assertFalse(truncated)


class RebindingTests(unittest.TestCase):
    """dS must be priced at the plan under test, never at a baked-in baseline."""

    def test_rebind_takes_k0_and_m0_from_the_plan_it_is_given(self):
        if not MERGE2.is_file():
            self.skipTest('plan_merge2.json missing (gitignored tree)')
        from platform_check import read_plan
        scratch = ROOT / 'tmp' / '_window_ils_test_base.json'
        scratch.parent.mkdir(parents=True, exist_ok=True)
        base = wi.rebind(read_plan(MERGE2), scratch)
        self.assertEqual(BS.K0, base['knives'])
        self.assertEqual(BS.K0, 184494)
        self.assertEqual(BS.M0, 535792670)
        self.assertNotEqual(BS.K0, 184531)          # plan_deep1: the stale hard-coded value


if __name__ == '__main__':
    unittest.main()
