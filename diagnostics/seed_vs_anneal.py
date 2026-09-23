"""Decisive A/B: seeding-only plan vs the same driver WITH annealing.

Both arms come from the identical chunking and identical per-chunk seeds; the
only difference is whether `search_10s`'s annealing phase ran.  Report every
subscore plus a structural breakdown (round count, delivered pieces, effective
p, parallel histogram) so the gap is attributed to a named quantity instead of
guessed at.

    python diagnostics/seed_vs_anneal.py --a runs/seed_fixed_all.json \
        --b runs/semi_nolimit_v1/result.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import platform_score as PS  # noqa: E402

DATA = ROOT / 'data' / 'semi'
SIZE: dict[str, float] = {}


def anatomy(plan):
    """Rounds / floor-knives / delivered pieces / effective p / parallel histogram."""
    knives = rounds = blanks = delivered = 0
    par = Counter()
    multi = 0
    for b in plan:
        if len(b['orders']) > 1:
            multi += 1
        for scheme, p, bc in zip(b['length_scheme'], b['counts'], b['blank_counts']):
            rounds += 1
            blanks += bc
            par[p] += 1
            k = sum(int((scheme[o] / SIZE[o]) + 1e-9) for o in scheme)
            knives += k + 1
            delivered += k * p
    return dict(knives=knives, rounds=rounds, blanks=blanks, delivered=delivered,
                eff_p=delivered / (knives - rounds) if knives > rounds else 0.0,
                par=par, schemes=len(plan), multi_scheme=multi)


def report(tag, path, rules, sd):
    plan = json.loads(Path(path).read_text(encoding='utf-8'))
    res = PS.evaluate(plan, DATA, scoring_data=sd, rules=rules)
    a = anatomy(plan)
    print(f"\n===== {tag}")
    print(f"  file            {path}")
    print(f"  score_capped    {res['score_capped']:.6f}   display {res['score_capped_display']}")
    print(f"  knives(platform){res['knives']}")
    print(f"  rounds          {res['rounds']}   plans {res['plans']}   entries {res['entries']}")
    print(f"  yield_rate      {res['finished_weight'] / res['raw_weight']:.6%}"
          f"   (finished {res['finished_weight']:.1f} / raw {res['raw_weight']:.1f})")
    print(f"  coverage        {res['coverage']:.6f}   ({res['combination_order_count']}"
          f"/{res['valid_order_count']})  violations {res['violation_count']}")
    print(f"  --- anatomy (floor k=int(L/size))")
    print(f"  schemes         {a['schemes']}  (multi-order {a['multi_scheme']})"
          f"   rounds {a['rounds']}   blanks {a['blanks']}")
    print(f"  floor-knives    {a['knives']}   delivered {a['delivered']}"
          f"   eff_p {a['eff_p']:.4f}")
    tot = sum(a['par'].values())
    hist = sorted(a['par'].items())
    print(f"  parallel p>=20  {sum(v for k, v in a['par'].items() if k >= 20)}/{tot}"
          f"   p>=10 {sum(v for k, v in a['par'].items() if k >= 10)}/{tot}")
    print(f"  parallel hist   {hist[:6]} ... {hist[-6:]}")
    return res, a


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--a', required=True)
    ap.add_argument('--b', required=True)
    ap.add_argument('--tag-a', default='A  seeding only')
    ap.add_argument('--tag-b', default='B  seeding + annealing')
    args = ap.parse_args()

    sd = PS.load_scoring_data(DATA, round='semi')
    SIZE.update({oid: o.size_m for oid, o in sd.orders.items()})
    rules = PS.Rules.semi(baseline_knives=160000.0)

    ra, aa = report(args.tag_a, args.a, rules, sd)
    rb, ab = report(args.tag_b, args.b, rules, sd)

    print("\n===== DELTA  (B - A)")
    print(f"  {'score_capped':16s} {rb['score_capped'] - ra['score_capped']:+.6f}")
    print(f"  {'knives':16s} {rb['knives'] - ra['knives']:+d}")
    print(f"  {'rounds':16s} {rb['rounds'] - ra['rounds']:+d}")
    print(f"  {'plans':16s} {rb['plans'] - ra['plans']:+d}")
    ya = ra['finished_weight'] / ra['raw_weight']
    yb = rb['finished_weight'] / rb['raw_weight']
    print(f"  {'yield_rate':16s} {yb - ya:+.6%}")
    print(f"  {'coverage':16s} {rb['coverage'] - ra['coverage']:+.6f}")
    print(f"  {'violations':16s} {rb['violation_count'] - ra['violation_count']:+d}")
    print(f"  {'floor-knives':16s} {ab['knives'] - aa['knives']:+d}")
    print(f"  {'delivered':16s} {ab['delivered'] - aa['delivered']:+d}")
    print(f"  {'eff_p':16s} {ab['eff_p'] - aa['eff_p']:+.4f}")
    print(f"  {'schemes':16s} {ab['schemes'] - aa['schemes']:+d}")


if __name__ == '__main__':
    main()
