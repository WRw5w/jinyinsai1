"""Which term of `_key` does the annealer think it is improving?

Every arm of `chunk_global_ab.py` came out slightly WORSE than the seeded incumbent on
the assembled drop, yet `search_10s` only ever moves to a state with a smaller `_key`.
So `_key` and the platform disagree somewhere specific, and which term it is decides
the fix.  This probe runs the identical code path twice per arm -- `seconds=1e-9`
returns the seeded incumbent untouched, the real budget runs the annealing -- and
prints the solver's OWN metric vector term by term, next to the platform's verdict on
the same two plans against a fixed background.

    python diagnostics/key_decompose.py --group NP01:43 --offset 600 --seconds 20
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

DATA = ROOT / 'data' / 'semi'
REFERENCE = ROOT / 'runs' / 'seed_fixed_all.json'
CAPTURED: dict = {}


def instrument(model):
    """Capture the in-memory metrics of `best` at export time, in `_key`'s units."""
    real = S._export

    def export(batches, orders, cfg):
        batches = list(batches)
        CAPTURED['metrics'] = S._totals(batches, model)
        return real(batches, orders, cfg)
    S._export = export


def background_knives(background, sizes):
    return sum(sum(int(round(scheme[oid] / sizes[oid])) for oid in scheme) + 1
               for b in background for scheme in b['length_scheme'])


def decomposed(m, cfg, off, n):
    K, D, R, cov = m
    knife_term = 40 * min(1.0, cfg.baseline_knives / (K + off)) if K + off else 0.0
    yield_term = 30 * (D / R if R else 0.0)
    cover_term = 20 * (cov / n if n else 0.0)
    return dict(knives=float(K), knife_term=knife_term, numerator=float(D), raw=float(R),
                yield_term=yield_term, covered=float(cov), cover_term=cover_term,
                key_score=-(knife_term + yield_term + cover_term + 10))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--group', default='NP01:43')
    ap.add_argument('--offset', type=int, default=600)
    ap.add_argument('--seconds', type=float, default=20.0)
    ap.add_argument('--seed-base', type=int, default=1)
    ap.add_argument('--arms', nargs='+', default=['none', 'cap', 'both'])
    args = ap.parse_args()

    settings = json.loads((DATA / 'competition.config.json').read_text(encoding='utf-8'))
    settings.update(continuity=True, coverage_shared=True, enforce_order_mass_floor=True,
                    objective='platform_score', baseline_knives=160000)
    cfg = S.Config(**settings)
    orders = S.load_orders(str(DATA / 'orders.normalized.csv'), cfg, skip_invalid=True)
    blanks = S.load_blanks(str(DATA / 'blanks.normalized.csv'))
    sd = PS.load_scoring_data(DATA, round='semi')
    rules = PS.Rules.semi(baseline_knives=160000.0)
    sizes = {o.oid: o.size for o in orders}
    reference = json.loads(REFERENCE.read_text(encoding='utf-8'))

    steel, _, dia = args.group.partition(':')
    members = [o for o in orders if o.steel == steel and abs(o.diameter - float(dia)) < 1e-9]
    chunk = members[args.offset:args.offset + 60]
    oids = {o.oid for o in chunk}
    background = [b for b in reference if not (set(b['orders']) & oids)]
    off = float(background_knives(background, sizes))

    ceiling = sum(PS._demand_numerator_mass(sd.orders[o.oid]) for o in orders)
    res_ref = PS.evaluate(reference, DATA, scoring_data=sd, rules=rules)
    pinned = abs(ceiling - res_ref['finished_weight']) < 1.0
    print(f"chunk {args.group} @{args.offset}   {len(chunk)} orders   "
          f"background knife offset {off:.0f}")
    print(f"sum of every order's demand ceiling : {ceiling:.1f}")
    print(f"reference plan's capped numerator   : {res_ref['finished_weight']:.1f}"
          f"   -> {'PINNED (no yield gradient left)' if pinned else 'NOT pinned'}")
    print(f"reference plan's raw                : {res_ref['raw_weight']:.1f}\n")

    for arm in args.arms:
        c = S.Config(**{**cfg.__dict__})
        c.cap_yield_numerator = arm in ('cap', 'both')
        c.knife_offset = off if arm == 'both' else 0.0
        rows = []
        for secs in (1e-9, args.seconds):
            model = S.Model(chunk, c, blanks)
            instrument(model)
            S._ROUND_SHAPE_CACHE.clear()
            plan = S.search_10s(chunk, c, seconds=secs, seed=args.seed_base + args.offset,
                                blanks=blanks)
            d = decomposed(CAPTURED['metrics'], c, c.knife_offset, len(chunk))
            glob = PS.evaluate(background + plan, DATA, scoring_data=sd, rules=rules)
            d['global'] = glob['score_capped']
            d['global_K'] = float(glob['knives'])
            rows.append(d)
        a, b = rows
        print(f"--- arm {arm}   offset {c.knife_offset:.0f}   cap {c.cap_yield_numerator}")
        for k in ('knives', 'knife_term', 'numerator', 'raw', 'yield_term', 'covered',
                  'cover_term', 'key_score', 'global_K', 'global'):
            unit = {'numerator': ' kg', 'raw': ' kg'}.get(k, '')
            print(f"      {k:>11} {a[k]:>18,.4f}{unit:4s} -> {b[k]:>18,.4f}{unit:4s}"
                  f"   {b[k] - a[k]:>+16,.4f}")
        print(f"      {'key gain':>11} {a['key_score'] - b['key_score']:>+16,.4f}"
              f"   {'GLOBAL gain':>16} {b['global'] - a['global']:>+16,.4f}")
        print()


if __name__ == '__main__':
    main()
