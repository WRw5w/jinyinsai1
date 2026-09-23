"""How many knives are sitting on the table in an already-solved plan?

A round's delivery to each order is `delta_j = ks_j * parallel`, and the closing
sweep only ever spends `delta`.  For a FIXED delta, `sum(ks) = sum(delta)/parallel`
is strictly decreasing in `parallel`, so the cheapest shape delivering that delta
is the one with the WIDEST legal bed.  `_enumerate_round_shapes.offer` keeps the
opposite one (see `_round_moves`: it walks `parallel` ascending and rejects any
wider re-offer of the same delta), so a solved plan can contain rounds that hand
over the same pieces for more knives than necessary.

For a fixed delta the widest legal parallel is found without enumerating shapes at
all: `ks_j = delta_j / parallel` must be integral, then the round just has to pass
the four geometry checks.  That makes the audit O(plimit) per round.

Reported per diameter so the reclaim can be compared with `plan_anatomy.py`.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import solver as S  # noqa: E402

DATA = ROOT / 'data' / 'semi'


def build():
    settings = json.loads((DATA / 'competition.config.json').read_text(encoding='utf-8'))
    settings.update(continuity=True, coverage_shared=True, enforce_order_mass_floor=True,
                    objective='platform_score', baseline_knives=160000)
    cfg = S.Config(**settings)
    orders = S.load_orders(str(DATA / 'orders.normalized.csv'), cfg, skip_invalid=True)
    blanks = S.load_blanks(str(DATA / 'blanks.normalized.csv'))
    return cfg, orders, S.Model(orders, cfg, blanks)


def widest_legal(model, cfg, ids, delta, start_parallel, blank, use_ceil=False):
    """Widest parallel delivering `delta` (exact) or >= `delta` (ceil), or None.

    `knives = sum(ks) + 1` and `ks_j = delta_j / parallel`, so for a fixed delta the
    widest legal bed is the cheapest round.  With `use_ceil` the round is allowed to
    hand over MORE pieces than `delta` -- over-production is free in the semi-final
    -- which is what makes this a strict improvement rather than a reshuffle.
    """
    sizes = [model.orders[i].size for i in ids]
    linear = model.orders[ids[0]].linear_weight
    usable = model.blank_length(ids, blank)
    plimit = model.parallel_limit(ids)
    for p in range(plimit, start_parallel - 1, -1):
        if use_ceil:
            ks = [(d + p - 1) // p for d in delta]
        else:
            if any(d % p for d in delta):
                continue
            ks = [d // p for d in delta]
        lengths = [k * s for k, s in zip(ks, sizes) if k]
        if not lengths:
            continue
        total = sum(lengths) + cfg.round_trim
        if not cfg.min_bed_length - 1e-8 <= total <= cfg.bed_length + 1e-8:
            continue
        if total * p * linear > cfg.bed_weight + 1e-8:
            continue
        if max(lengths) + cfg.round_trim > usable + 1e-8:
            continue
        return p, ks
    return None


def audit(cfg, model, plan, label, use_ceil=False):
    index = {o.oid: i for i, o in enumerate(model.orders)}
    per_dia = defaultdict(lambda: dict(rounds=0, reclaim=0, hit=0, examples=[]))
    lost = 0
    for batch in plan:
        ids = tuple(sorted(index[o] for o in batch['orders'] if o in index))
        blank = model.catalogue(ids)[0]
        for scheme, p in zip(batch['length_scheme'], batch['counts']):
            ks = [0] * len(ids)
            for oid, length in scheme.items():
                if oid in index:
                    ks[ids.index(index[oid])] = int(length // model.orders[index[oid]].size)
            delta = tuple(k * p for k in ks)
            got = widest_legal(model, cfg, ids, delta, p, blank, use_ceil)
            dia = model.orders[ids[0]].diameter
            slot = per_dia[dia]
            slot['rounds'] += 1
            if got is None:
                continue
            p2, ks2 = got
            save = sum(ks) - sum(ks2)
            if save <= 0:
                continue
            slot['hit'] += 1
            slot['reclaim'] += save
            lost += save
            if len(slot['examples']) < 2:
                slot['examples'].append((p, sum(ks), p2, sum(ks2), save))
    mode = 'deliver >= 原轮' if use_ceil else '交付量完全相同'
    print(f'=== {label}  [口径: {mode}] ===')
    print(f'  {"dia":>6} {"rounds":>7} {"hit":>6} {"knives reclaimable":>19}  example (p,k)->(p,k)')
    for dia in sorted(per_dia, key=lambda d: -per_dia[d]['reclaim']):
        s = per_dia[dia]
        ex = s['examples'][0] if s['examples'] else None
        exs = f'({ex[0]},{ex[1]})->({ex[2]},{ex[3]}) = -{ex[4]}' if ex else ''
        print(f'  {dia:>6} {s["rounds"]:>7,} {s["hit"]:>6,} {s["reclaim"]:>19,}  {exs}')
    print(f'  TOTAL reclaimable: {lost:,} knives over '
          f'{sum(s["hit"] for s in per_dia.values()):,} rounds')
    return lost


def main():
    cfg, orders, model = build()
    plans = {'OURS (submission_semi_nolimit)':
             ROOT / 'submission_semi_nolimit' / '复赛结果_棒材优化.json',
             'THEIRS (machine 2, main)':
             ROOT / 'runs' / 'theirs_main' / '复赛结果_棒材优化.json'}
    for label, path in plans.items():
        if not path.exists():
            print(f'missing {path}')
            continue
        for use_ceil in (False, True):
            audit(cfg, model, json.loads(path.read_text(encoding='utf-8')), label, use_ceil)
        print()


if __name__ == '__main__':
    main()
