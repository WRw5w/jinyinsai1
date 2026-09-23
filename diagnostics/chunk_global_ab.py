"""Does scoring a chunk as the whole drop stop the annealing from losing points?

The semi drop is solved 183 chunks at a time, and `_key` scored each chunk on its OWN
numbers.  That mis-prices all three terms at once:

  * knives -- `40 * min(1, 160000/800)` is 1 for every chunk, so the term is a flat 40
    with no gradient;
  * raw mass -- the price is `30 * D_chunk / R_chunk^2` = `1.078e-5` per kg, against
    the drop's real `30 * D_tot / R_tot^2` = `4.935e-8`, i.e. 218x too high;
  * coverage -- one covered order is worth `20/60 = 0.33` points instead of
    `20/9999 = 0.002`, i.e. 167x too much.

This probe holds every OTHER chunk at a fixed reference (the seeding-only full plan)
and scores `background + candidate` as one whole drop with `platform_score`, so the
only difference between arms is how the candidate chunk was produced.  Arms:

  seed    `seconds` tiny -- `search_10s` returns its seeded incumbent untouched
  none    the pre-fix objective (all offsets 0, uncapped numerator), full budget
  exact   all four offsets taken from the background, numerator capped -- the fixed
          objective with PERFECT offsets, i.e. what `--offset-mode anchor` produces
  share   the fixed objective with `--offset-mode share` offsets: cold start, so the
          knife total is extrapolated from a prior rather than measured

Each chunk is independent, so the mean delta over chunks estimates the per-chunk bias
and `183 x mean` estimates what two hours of annealing costs or earns on the drop.

    python diagnostics/chunk_global_ab.py --offsets 0 60 600 1800 3000 --seconds 20
    python diagnostics/chunk_global_ab.py --arms share --offsets 0 600 --seconds 20
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import platform_score as PS  # noqa: E402
import solver as S  # noqa: E402
from solve_semi import (DropAccounting, internal_knives, order_info,  # noqa: E402
                        plan_capped_numerator, plan_raw_mass, plan_shared_coverage)

DATA = ROOT / 'data' / 'semi'
REFERENCE = ROOT / 'runs' / 'seed_fixed_all.json'
ARMS = ('seed', 'none', 'exact', 'share')


def load():
    settings = json.loads((DATA / 'competition.config.json').read_text(encoding='utf-8'))
    settings.update(continuity=True, coverage_shared=True, enforce_order_mass_floor=True,
                    cap_yield_numerator=True, knife_convention='platform_floor',
                    objective='platform_score', baseline_knives=160000)
    cfg = S.Config(**settings)
    orders = S.load_orders(str(DATA / 'orders.normalized.csv'), cfg, skip_invalid=True)
    blanks = S.load_blanks(str(DATA / 'blanks.normalized.csv'))
    return cfg, orders, blanks


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--groups', nargs='+', default=['NP01:43', 'C60:26.5', '30C:45.0',
                                                   'GB40:28.0', 'GC-4:32.0', 'GC-7:52.0'])
    ap.add_argument('--chunk', type=int, default=60)
    ap.add_argument('--offsets', type=int, nargs='+', default=[0, 60, 600, 1800, 3000])
    ap.add_argument('--seconds', type=float, default=20.0)
    ap.add_argument('--seed-base', type=int, default=1)
    ap.add_argument('--arms', nargs='+', choices=ARMS, default=list(ARMS),
                    help='restrict to these arms (default: all)')
    args = ap.parse_args()
    arms = tuple(a for a in ARMS if a in args.arms)

    cfg, orders, blanks = load()
    sd = PS.load_scoring_data(DATA, round='semi')
    rules = PS.Rules.semi(baseline_knives=160000.0)
    info = order_info(orders)
    blank_weights = {b.bid: b.weight for b in blanks}
    n_total = len(orders)
    reference = json.loads(REFERENCE.read_text(encoding='utf-8'))

    print(f"reference (seeding only, whole drop): "
          f"score {PS.evaluate(reference, DATA, scoring_data=sd, rules=rules)['score_capped']:.4f}")
    print(f"{len(args.groups)} groups x {len(args.offsets)} chunk positions x "
          f"{len(arms)} arms, {args.seconds:g}s annealing\n")

    # Cold `share` estimator, built once and never fed a solved chunk: this is the worst
    # case and exactly what the first chunk of a `--offset-mode share` run sees.  The
    # prior is in the `platform_floor` convention (the knife offset feeds `_key`, which
    # measures in it) -- quoting it in the `intended` convention instead is what put the
    # estimate 7.9% high.
    share_est = DropAccounting(
        n_total, numerator_whole=sum(o.size * o.linear_weight_int * o.pieces for o in orders),
        knife_prior=175731.0, knife_floor=1.05 * 160000.0, covered_prior=float(n_total))

    deltas = {a: [] for a in ARMS}
    # The trailing columns are pairwise reads of the full arm set; drop the ones whose
    # arms were not requested rather than raising a KeyError on a restricted run.
    pairs = [p for p in (('none', 'seed'), ('exact', 'seed'), ('exact', 'none'))
             if p[0] in arms and p[1] in arms]
    header = f"{'group':>9} {'@':>5} " + " ".join(f"{a:>18}" for a in arms)
    print(header + "".join(f" {f'{a}-{b}':>11}" for a, b in pairs))
    for spec in args.groups:
        steel, _, dia = spec.partition(':')
        members = [o for o in orders if o.steel == steel and abs(o.diameter - float(dia)) < 1e-9]
        for start in args.offsets:
            chunk = members[start:start + args.chunk]
            if len(chunk) < args.chunk:
                continue
            oids = {o.oid for o in chunk}
            background = [b for b in reference if not (set(b['orders']) & oids)]
            bg = dict(knives=float(internal_knives(background, info)),
                      numerator=plan_capped_numerator(background, info),
                      raw=plan_raw_mass(background, blank_weights),
                      covered=float(plan_shared_coverage(background)))
            row = {}
            for arm in arms:
                c = S.Config(**{**cfg.__dict__})
                secs = args.seconds
                if arm == 'seed':
                    secs = 1e-9
                elif arm == 'none':
                    # The pre-fix objective: chunk-scored, `intended` knives, uncapped.
                    c.knife_convention = 'intended'
                    c.cap_yield_numerator = False
                elif arm in ('exact', 'share'):
                    got = bg if arm == 'exact' else share_est.offsets(chunk)
                    c.knife_offset = got['knives']
                    c.numerator_offset = got['numerator']
                    c.raw_offset = got['raw']
                    c.covered_offset = got['covered']
                    c.coverage_total = float(n_total)
                S._ROUND_SHAPE_CACHE.clear()
                plan = S.search_10s(chunk, c, seconds=secs, seed=args.seed_base + start,
                                    blanks=blanks)
                res = PS.evaluate(background + plan, DATA, scoring_data=sd, rules=rules)
                row[arm] = (res['score_capped'], res['knives'],
                            res['finished_weight'] / res['raw_weight'])
            for arm in arms:
                deltas[arm].append(row[arm][0] - row['seed'][0])
            print(f"{spec:>9} {start:>5} "
                  + " ".join(f"{row[a][0]:>10.4f}({row[a][1] - row['seed'][1]:+5d},"
                             f"{100 * (row[a][2] - row['seed'][2]):+.4f}pp)"
                             for a in arms)
                  + "".join(f" {row[a][0] - row[b][0]:>+11.4f}" for a, b in pairs))

    n = len(deltas['seed'])
    print(f"\nmean over {n} chunks, and the projection 183 x mean")
    print(f"{'arm':>8} {'mean/ chunk':>13} {'183x':>10} {'worst':>10} {'best':>10}")
    for arm in arms:
        m = sum(deltas[arm]) / n
        print(f"{arm:>8} {m:>+13.4f} {183 * m:>+10.3f} "
              f"{min(deltas[arm]):>+10.4f} {max(deltas[arm]):>+10.4f}")
    print("\n'none' is the pre-fix objective, 'exact' the fixed one with perfect offsets")
    print("(= --offset-mode anchor), 'share' the fixed one with a cold-start estimate.")
    print("If 183*mean for 'none' is near the measured -1.85 and 'exact' near 0, the")
    print("per-chunk mis-pricing fully explains the loss and the offsets remove it.")


if __name__ == '__main__':
    main()
