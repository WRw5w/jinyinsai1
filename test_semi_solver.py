"""Solver behaviour under the semi-final rule set.

Three things must hold that the preliminary model never checked:

1. `Config.continuity` makes `validate_plan` reject non-adjacent rounds for one
   order (constraints.txt clause 6).
2. `Config.enforce_order_mass_floor` makes it reject an order whose allocated
   mass falls short of its weight (clause 8).
3. The solve path must *produce* plans that satisfy both, not merely reject bad
   ones -- so `brute_force` and `search_10s` are run with the switches on and
   their output validated.

The independent oracle in test_solver.py enumerates rounds as an unordered
multiset, which cannot express continuity.  Rather than weaken it, the solver
tests here check the properties directly.

Fixture arithmetic: 20 mm bar at 7850 kg/m3 is 2.4662 kg/m.  An order of `pieces`
pieces at a 2 m定尺 weighs (pieces - 0.25) * 2 * 2.4662 kg, so a plan that
allocates exactly `pieces` pieces at 1 parallel bar hits the demand precisely and
never trips the overproduction ceiling.
"""
import math
import unittest

from solver import (Blank, Config, ModelError, Order, brute_force, search_10s,
                    validate_plan)

LINEAR = math.pi * (20 / 1000) ** 2 / 4 * 7850


def order(name, pieces, size=2.0, steel="S", dia=20, density=7850):
    linear = math.pi * (dia / 1000) ** 2 / 4 * density
    return Order(name, steel, dia, size, (pieces - 0.25) * size * linear, density, pieces)


def semi_cfg(**overrides):
    base = dict(bed_length=10, min_bed_length=0, bed_width=2, trim=1,
                max_rounds=3, length_mode="net_shared_trim",
                continuity=True, coverage_shared=True, enforce_order_mass_floor=True)
    base.update(overrides)
    return Config(**base)


def prelim_cfg(**overrides):
    """The same bed with the preliminary switches, for A/B contrasts."""
    return semi_cfg(continuity=False, coverage_shared=False,
                    enforce_order_mass_floor=False, **overrides)


def scheme(rounds, names, parallel=None, blanks=None):
    parallel = parallel or [1] * len(rounds)
    blanks = blanks or [1] * len(rounds)
    return {"orders": list(names), "length_scheme": rounds, "counts": parallel,
            "blank_type": 1, "blank_counts": blanks}


class SemiSolverTests(unittest.TestCase):
    def setUp(self):
        self.blanks = [Blank(1, LINEAR * 12)]

    def test_validate_plan_rejects_a_round_gap(self):
        cfg = semi_cfg()
        # Demand 2 pieces each.  A uses two rounds of k=1; B uses one round of k=2.
        orders = [order("A", 2), order("B", 2)]
        plan = [scheme([{"A": 2.0}, {"B": 4.0}, {"A": 2.0}], ["A", "B"])]
        with self.assertRaisesRegex(ModelError, "Continuity violation"):
            validate_plan(plan, orders, cfg, self.blanks)
        # The same plan under the preliminary switches is structurally fine.
        validate_plan(plan, orders, prelim_cfg(), self.blanks)

    def test_validate_plan_accepts_an_adjacent_block(self):
        cfg = semi_cfg()
        # A: k=2 then k=1 => 3 pieces.  B: k=2 => 2 pieces.
        orders = [order("A", 3), order("B", 2)]
        plan = [scheme([{"A": 4.0}, {"A": 2.0, "B": 4.0}], ["A", "B"])]
        metrics = validate_plan(plan, orders, cfg, self.blanks)
        # Both orders share round 0, so both are combination orders.
        self.assertEqual(metrics["coverage"], 1.0)

    def test_coverage_counts_only_orders_sharing_a_round(self):
        # Clause 8 is switched off here on purpose: this case is about the coverage
        # denominator, and adjacency plus the mass floor would otherwise be the
        # binding constraints on these tiny fixtures.
        cfg = semi_cfg(continuity=False, enforce_order_mass_floor=False)
        orders = [order("A", 2), order("B", 2), order("C", 2)]
        # A and B share a round; C is cut alone in its own scheme.
        plan = [scheme([{"A": 4.0, "B": 4.0}], ["A", "B"]),
                scheme([{"C": 4.0}], ["C"])]
        self.assertAlmostEqual(validate_plan(plan, orders, cfg, self.blanks)["coverage"], 2 / 3)
        # A scheme where the two orders never share a round contributes nothing.
        split = [scheme([{"A": 4.0}, {"B": 4.0}], ["A", "B"])]
        self.assertEqual(validate_plan(split, orders[:2], cfg, self.blanks)["coverage"], 0.0)

    def test_preliminary_coverage_rule_counts_a_multi_order_scheme(self):
        """The same split scheme IS a combination under the preliminary rule.

        This is the 09-14 11:32 wording ("同一批内不同轮次切过即可"), which the
        semi-final round tightened.  Keeping both reachable is what makes the
        difference visible in one test file.
        """
        cfg = prelim_cfg()
        orders = [order("A", 2), order("B", 2)]
        split = [scheme([{"A": 4.0}, {"B": 4.0}], ["A", "B"])]
        self.assertEqual(validate_plan(split, orders, cfg, self.blanks)["coverage"], 1.0)
        # Under the semi-final rule the same plan scores zero coverage.
        semi = semi_cfg(continuity=False, enforce_order_mass_floor=False)
        self.assertEqual(validate_plan(split, orders, semi, self.blanks)["coverage"], 0.0)

    def test_order_mass_floor_is_satisfied_whenever_demand_is_met(self):
        """Clause 8 is a consequence of the piece demand, not an extra axis.

        pieces = ceil(W / (size * linear)), so reaching `pieces` with k pieces on
        `parallel` bars delivers k * size * linear * parallel >= W.  The floor is
        therefore normally implied -- the code still checks it, because nothing
        forces an externally supplied plan to have consistent length/count fields.
        """
        cfg = semi_cfg(max_rounds=10)
        orders = [order("H", 2)]
        # Two pieces delivered on a single bar: pieces and mass both satisfied.
        validate_plan([scheme([{"H": 4.0}], ["H"], parallel=[1])], orders, cfg, self.blanks)
        # Two pieces delivered as k=1 on each of two bars: same demand, and because
        # each bar carries the order's length the mass also clears the floor.
        validate_plan([scheme([{"H": 2.0}], ["H"], parallel=[2])], orders, cfg, self.blanks)

    def test_order_mass_floor_rejects_an_inconsistent_plan(self):
        """The clause does have teeth once length and count disagree.

        `counts` is the number of parallel bars, so a round allocates
        k * size * linear * counts.  For a 4-piece order one round of k=1 at 2 bars
        allocates only half the 18.5 kg the demand weighs, and because the mass check
        runs before the piece check the reported defect is the mass floor.  A
        hand-edited export is the realistic way to produce such a plan.
        """
        cfg = semi_cfg(max_rounds=10)
        orders = [order("H", 4)]
        with self.assertRaisesRegex(ModelError, "Order mass floor violated"):
            validate_plan([scheme([{"H": 2.0}], ["H"], parallel=[2])], orders, cfg, self.blanks)
        # With the switch off the same plan fails later, on the piece demand.
        relaxed = semi_cfg(max_rounds=10, enforce_order_mass_floor=False)
        with self.assertRaisesRegex(ModelError, "Demand/overproduction"):
            validate_plan([scheme([{"H": 2.0}], ["H"], parallel=[2])], orders, relaxed, self.blanks)

    def test_brute_force_produces_a_continuity_safe_plan(self):
        cfg = semi_cfg(max_rounds=3)
        orders = [order("A", 2), order("B", 2)]
        plan = brute_force(orders, cfg, self.blanks)
        metrics = validate_plan(plan, orders, cfg, self.blanks)
        self.assertEqual(metrics["orders"], 2)
        self.assertGreaterEqual(metrics["coverage"], 0.0)
        # A single scheme has one round list, so adjacency is structural.
        self.assertEqual(metrics["rounds"], len(plan[0]["length_scheme"]))

    def test_search_produces_a_continuity_safe_plan(self):
        cfg = semi_cfg(max_rounds=3)
        orders = [order("A", 2), order("B", 2)]
        for seed in range(4):
            plan = search_10s(orders, cfg, seconds=0.06, seed=seed, blanks=self.blanks)
            validate_plan(plan, orders, cfg, self.blanks)

    def test_six_round_cap_is_visible_to_the_model(self):
        cfg = semi_cfg(max_rounds=6)
        orders = [order("A", 2)]
        plan = brute_force(orders, cfg, self.blanks)
        self.assertLessEqual(len(plan[0]["length_scheme"]), 6)
        # A hand-built seven-round scheme must be refused.
        too_many = [scheme([{"A": 2.0}] * 7, ["A"])]
        with self.assertRaisesRegex(ModelError, "Invalid round count"):
            validate_plan(too_many, orders, cfg, self.blanks)


if __name__ == "__main__":
    unittest.main(verbosity=2)
