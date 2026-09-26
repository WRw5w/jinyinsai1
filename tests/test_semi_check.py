"""Semi-final checks in the independent physical validator.

The point of these cases is that `platform_check.check(..., round='semi')` must
reject exactly the three things the preliminary checker never looked for -- the
six-round cap, non-adjacent rounds for one order, and an order whose allocated
mass falls short of its weight -- while still accepting a clean plan.

Fixture arithmetic (56 mm bar, density 9860 => 24.2853 kg/m):
  one round of 18 pieces of 4 m at 17 parallel is 72 m net / 74 m physical,
  30550.9 kg, i.e. 3.18 blanks of the 9613.5 kg type 1 blank.
"""
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from platform_check import check, load_orders_and_blanks


ORDERS = '订单号,订单重量,钢种,规格,定尺长度\nA,10.0,S,56,4000\nB,10.0,S,56,4000\n'
BLANKS = '编号,钢坯长度,钢坯宽度,钢坯定尺,钢铁密度：9.86g/cm3\n1,260,300,12500,\n'
# One round: 72 m of net segments at 17 parallel.  72 + 2 m trim = 74 m physical,
# which is inside the 50..150 m band; the mass is 74 * 17 * 24.2853 = 30550.9 kg,
# inside the 60 t bed limit and covered by 4 blanks of 9613.5 kg.
TOTAL_NET = 72.0
PARALLEL = 17
BLANK_COUNT = 4


def clean_plan(rounds, orders=('A', 'B')):
    """rounds is a list of {oid: length_m} dicts, one per cold-bed round.

    Default to splitting the 72 m round equally between both orders, so the plan
    is complete: every order appears exactly once overall (required in both rounds)
    and each order reaches its 10 t weight.  Tests that deliberately starve an
    order pass a `rounds` list explicitly.
    """
    rounds = [{oid: TOTAL_NET / len(orders) for oid in orders} if r is None else r for r in rounds]
    return [{'orders': list(orders), 'length_scheme': rounds, 'counts': [PARALLEL] * len(rounds),
             'blank_type': 1, 'blank_counts': [BLANK_COUNT] * len(rounds)}]


class SemiCheckTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        (root / 'orders_semi.csv').write_text(ORDERS, encoding='gbk')
        (root / 'blank_used_finals.csv').write_text(BLANKS, encoding='gbk')
        # The preliminary filenames too, so both modes can be exercised in one setUp.
        (root / 'orders_quarter.csv').write_text(
            '订单号,订单重量(t),坯料钢种,订单直径(mm),订单定尺(mm)\nA,10.0,S,56,4000\nB,10.0,S,56,4000\n',
            encoding='utf-8-sig')
        (root / 'blank_used.csv').write_text(
            '坯料,宽度mm,厚度mm,长度mm\n1,260,300,12500\n', encoding='utf-8-sig')
        self.root = root

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_valid_semi_round_is_accepted(self):
        result = check(clean_plan([None]), self.root, round='semi')
        self.assertEqual(result['error_counts'], {}, result['errors'])
        self.assertTrue(result['passed'])

    def test_seven_rounds_are_rejected_and_six_are_accepted(self):
        six = check(clean_plan([None] * 6), self.root, round='semi')
        self.assertNotIn('round_cap', six['error_counts'])
        seven = check(clean_plan([None] * 7), self.root, round='semi')
        self.assertEqual(seven['error_counts'].get('round_cap'), 1)
        # The preliminary clause list has no round cap at all.
        self.assertNotIn('round_cap', check(clean_plan([None] * 7), self.root,
                                            round='prelim')['error_counts'])

    def test_non_adjacent_rounds_for_one_order_are_rejected(self):
        # A in rounds 0 and 2 with B alone in round 1: the 09-15 11:11 example.
        plan = clean_plan([{'A': TOTAL_NET}, {'B': TOTAL_NET}, {'A': TOTAL_NET}])
        semi = check(plan, self.root, round='semi')
        self.assertEqual(semi['error_counts'].get('continuity'), 1)
        self.assertEqual(semi['errors']['continuity'][0]['rounds'], [0, 2])
        self.assertNotIn('continuity', check(plan, self.root, round='prelim')['error_counts'])

    def test_adjacent_block_is_accepted(self):
        plan = clean_plan([{'A': TOTAL_NET, 'B': TOTAL_NET}, {'A': TOTAL_NET, 'B': TOTAL_NET}, {'A': TOTAL_NET}])
        self.assertNotIn('continuity', check(plan, self.root, round='semi')['error_counts'])
        self.assertNotIn('continuity', check(plan, self.root, round='semi')['error_counts'])

    def test_order_mass_floor_is_enforced_only_in_semi(self):
        """constraints.txt clause 8: 每订单冷床分配总重量须不低于该订单重量.

        Each order asks for 10 t.  One round of 18 x 4 m pieces at 17 parallel is
        18 * 17 * 4 m * 24.2853 kg/m = 29.7 t, so a single round already clears the
        floor for both orders -- that is the accepted case.  Cutting the piece
        count to 1 per order delivers only 1.65 t and must trip clause 8, which the
        preliminary clause list never mentions.
        """
        ok = check(clean_plan([None]), self.root, round='semi')
        self.assertNotIn('order_mass_floor', ok['error_counts'])
        thin = check(clean_plan([{'A': 4.0, 'B': 4.0}]), self.root, round='semi')
        self.assertEqual(thin['error_counts'].get('order_mass_floor'), 2)
        self.assertNotIn('order_mass_floor',
                         check(clean_plan([{'A': 4.0, 'B': 4.0}]), self.root,
                               round='prelim')['error_counts'])

    def test_under_delivery_is_flagged(self):
        # 10 t per order at 4 m needs 103 pieces; one 18-piece round delivers 306
        # for A, so A is fine, but B is absent from that round and starves.
        plan = clean_plan([{'A': TOTAL_NET, 'B': 0.0}])
        self.assertEqual(check(plan, self.root, round='semi')['error_counts'].get('short_delivery'), 1)

    def test_semi_files_are_gbk_and_masses_match_the_preliminary_blanks(self):
        _, blanks, _, _ = load_orders_and_blanks(self.root, 'semi')
        self.assertEqual(blanks[1], Decimal(260) * Decimal(300) * Decimal(12500)
                         * Decimal(9860) / Decimal(10) ** 9)
        self.assertAlmostEqual(float(blanks[1]), 9613.5, places=6)
        # The first two semi-final blanks are the two preliminary blanks.
        ping, pblanks, _, _ = load_orders_and_blanks(self.root, 'prelim')
        self.assertEqual(blanks[1], pblanks[1])
        self.assertAlmostEqual(float(blanks[1]), 9613.5, places=6)
        self.assertEqual(sorted(ping), ['A', 'B'])

    def test_negative_weight_order_is_excluded(self):
        root = Path(self._tmp.name)
        (root / 'orders_semi.csv').write_text(ORDERS + 'C,-25.418,S,56,4000\n', encoding='gbk')
        result = check([], root, round='semi')
        self.assertEqual(result['excluded_invalid_orders'], ['C'])
        self.assertEqual(result['valid_orders'], 2)
        self.assertEqual(result['source_orders'], 3)

    def test_cross_round_seam_continuity(self):
        """Every order shared by adjacent rounds must sit at the seam.

        A BOUNDARY counts once however many shared orders fail, because that is
        how the official notices count: 7030 / 7342 / 7559 are seams, not orders.
        Pinned by the 2026-09-23 notice on `submission_semi_merged_v2` (0 points,
        35150 penalty) and the 2026-09-26 notice on the rotated `_v4` (0 points,
        36710 penalty).
        """
        # Two shared orders: both would have to be at the seam at once -> 1.
        rounds = [{'A': TOTAL_NET / 2, 'B': 0.0}, {'A': TOTAL_NET / 2, 'B': 0.0}]
        plan = clean_plan(rounds)
        errs = check(plan, Path(self._tmp.name), round='semi')['error_counts']
        self.assertEqual(errs.get('continuity_seam'), 1)

        # Exactly one shared order, sitting at the seam -> clean.
        rounds = [{'A': TOTAL_NET / 2, 'B': TOTAL_NET / 2}, {'B': TOTAL_NET / 2}]
        plan = clean_plan(rounds)
        errs = check(plan, Path(self._tmp.name), round='semi')['error_counts']
        self.assertIsNone(errs.get('continuity_seam'))

        # One shared order, but the right round opens on something else -> 1.
        rounds = [{'B': TOTAL_NET / 2, 'A': TOTAL_NET / 2}, {'B': TOTAL_NET / 2}]
        plan = clean_plan(rounds)
        errs = check(plan, Path(self._tmp.name), round='semi')['error_counts']
        self.assertEqual(errs.get('continuity_seam'), 1)

        # No shared order at all: nothing to align -> clean.
        rounds = [{'A': TOTAL_NET / 2}, {'B': TOTAL_NET / 2}]
        plan = clean_plan(rounds)
        errs = check(plan, Path(self._tmp.name), round='semi')['error_counts']
        self.assertIsNone(errs.get('continuity_seam'))

    def test_rotate_scheme_rounds_does_not_close_a_seam(self):
        """The key-rotation helper does NOT repair clause 6, and never did.

        It was believed to close every seam at zero cost.  It cannot: a boundary
        sharing two orders needs BOTH at the seam simultaneously, and rotating
        moves one of them.  `submission_semi_merged_v4` went through this helper,
        our checker then reported 0, and the platform returned 0 points for 7342
        `跨轮接续不连续`.  The helper survives only so the rejected packages stay
        byte-reproducible -- see the receipt at
        artifacts/rejected/submission_semi_merged_v4/official_feedback.json.
        """
        from build_submission import rotate_scheme_rounds

        rounds = [{'A': TOTAL_NET / 2, 'B': 0.0},
                  {'A': TOTAL_NET / 2, 'B': 0.0}]
        plan = clean_plan(rounds)
        before = [[set(r.items()) for r in b['length_scheme']] for b in plan]
        self.assertEqual(check(plan, Path(self._tmp.name), round='semi')
                         ['error_counts'].get('continuity_seam'), 1)

        rotated = rotate_scheme_rounds(plan)
        after = [[set(r.items()) for r in b['length_scheme']] for b in rotated]
        self.assertEqual(before, after, 'rotation must not change any round content')
        self.assertEqual(check(rotated, Path(self._tmp.name), round='semi')
                         ['error_counts'].get('continuity_seam'), 1,
                         'rotating cannot bring two shared orders to the seam at once')

    def test_seam_predicate_is_not_the_weaker_head_to_tail_rule(self):
        """Discriminate the candidate predicate from the one it replaced.

        The falsified rule compared only the left round's last key with the right
        round's first key, so it called `A,B -> B,A` clean -- the seam lines up.
        But A's two appearances are separated by B, which is what clause 6
        forbids, and the 2026-09-26 notice (7342, on a package that weaker rule
        scored at 0) is consistent with exactly this.  Restoring the weaker rule
        makes this fail.
        """
        rounds = [{'A': TOTAL_NET / 2, 'B': 0.0},
                  {'B': TOTAL_NET / 2, 'A': 0.0}]
        plan = clean_plan(rounds)
        errs = check(plan, Path(self._tmp.name), round='semi')['error_counts']
        self.assertEqual(errs.get('continuity_seam'), 1,
                         'A,B -> B,A lines up at the seam but still splits A around B')

        # The shape that IS clean: one shared order, last on the left and first on
        # the right.
        rounds = [{'A': TOTAL_NET / 2, 'B': TOTAL_NET / 2},
                  {'B': TOTAL_NET / 2}]
        plan = clean_plan(rounds)
        errs = check(plan, Path(self._tmp.name), round='semi')['error_counts']
        self.assertIsNone(errs.get('continuity_seam'),
                          'a single shared order at the seam is clean')


if __name__ == '__main__':
    unittest.main(verbosity=2)
