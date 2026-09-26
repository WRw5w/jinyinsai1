"""The round-fill arithmetic behind "not every short round is a waste".

`round_fill.py` claims rounds stop short of their cap because the bill's ceiling
is a staircase: a round pays `ceil((net + 2) * count * lin / weight)` whole
billets of declared mass, so the metres left before the next tread -- not the
metres left to the cap -- are what a fuller round could actually take.  The two
numbers coincide when the bed sets the cap (the cap *is* the full-bed tread), so
the interesting cases are the length-capped rounds, where a metre of net is worth
`count * lin` kg of bill.  These cases pin both numbers and the predicate that
separates them, at a blank weight where the arithmetic can be done by hand.

Nothing here is a claim about the plan; `round_fill.py` measures the real one.
"""
import importlib.util
import unittest
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    'round_fill', ROOT / 'tools/analysis/round_fill.py')
rf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rf)

WEIGHT = 1000          # 1 t billets: a tread every 1,000 kg of declared mass


def plan_with(net, count, lin, piece=5.0):
    """One batch, one round: `count` bars carrying `net` metres of `piece` pieces."""
    return [dict(orders=['A'], length_scheme=[{'A': float(net)}], counts=[count],
                 blank_type=1, blank_counts=[1])]


def orders_with(lin, piece=5.0):
    size = D(str(piece))
    return {'A': dict(size=size, dia=D('20'), linear=D(str(lin)), linear_int=D(str(lin)),
                      steel='GC-3', pieces=10, weight=D('1000'))}


class RoundFillTests(unittest.TestCase):
    def rows(self, net, count, lin):
        plan = plan_with(net, count, lin)
        return list(rf.rows_of(plan, orders_with(lin), {1: WEIGHT}))[0]

    def test_the_room_left_is_metres_of_bar_not_of_bill(self):
        # lin 7 kg/m, count 10 -> 70 kg per metre of net.  139 m of net declares
        # (139 + 2) * 70 = 9,870 kg, so a tenth billet is 130 kg -- 1.86 m -- away.
        row = self.rows(139, 10, 7)
        self.assertAlmostEqual(row['room'], 130.0 / 70.0, places=9)
        # At 98 m of net the same round declares exactly (98 + 2) * 70 = 7,000 kg,
        # seven billets: the tread is where it stands, so the room is nil.
        self.assertAlmostEqual(self.rows(98, 10, 7)['room'], 0.0, places=9)

    def test_the_cap_is_148_until_the_bed_takes_over(self):
        # lin 7, count 10 -> 60,000 / 70 - 2 = 855 m, so the length limit rules.
        self.assertEqual(self.rows(139, 10, 7)['cap'], 148.0)
        # lin 10, count 50 -> 60,000 / 500 - 2 = 118 m, so the bed rules.
        self.assertEqual(self.rows(115, 50, 10)['cap'], 118.0)

    def test_a_round_is_tread_bound_when_the_tread_is_closer_than_the_cap(self):
        # 139 m: 9 m short of the cap but 1.86 m from the next tread, so a fuller
        # round could use 1.86 m of those 9 -- the cap is not what stops it.
        row = self.rows(139, 10, 7)
        self.assertAlmostEqual(row['cap'] - row['net'], 9.0, places=9)
        self.assertLess(row['room'], row['cap'] - row['net'])
        # 147.5 m: half a metre from the cap, 7.6 m from the next tread -- here the
        # cap is what stops it, and those 0.5 m are all that is left to win.
        row = self.rows(147.5, 10, 7)
        self.assertGreater(row['room'], row['cap'] - row['net'])
        self.assertAlmostEqual(row['cap'] - row['net'], 0.5, places=9)

    def test_the_count_is_measured_against_the_roller(self):
        # dia 20 -> floor(2000 / 20) = 100 bars is what the roller admits.
        self.assertEqual(self.rows(139, 10, 7)['roller'], 100)


if __name__ == '__main__':
    unittest.main(verbosity=2)
