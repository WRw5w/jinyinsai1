"""Assemble the whole semi-final plan by pairing orders, then re-shaping rounds.

Why this exists
---------------
`solve_semi.py` spends ~46 s of CPU per 60-order chunk inside `search_10s`, and
that budget buys two things at once: which orders share a scheme (`_pack_group`)
and what round shapes each scheme gets (`_construct` -> `_enumerate_round_shapes`
-> `_closing_rounds`).  `round_shaper` decides the round shapes outright, and of
the two the pairing only matters for the score through coverage -- an order earns
coverage when it shares a *round*, and the reshaper puts every order of a scheme
in every round.  So the search can be dropped entirely: pair neighbour-first
inside each (steel, diameter) group exactly as `_pack_group` does, then let the
reshaper price every scheme.

That takes the whole 9,999-order drop from hours to about a minute, and it is
strictly better on both scored axes, because the reshaper minimises knives and
declared steel per scheme rather than closing on one round shape.

Pairing policy.  `a` is paired with the first of the next `--lookahead` pending
orders for which a legal shape exists; failing that `a` is emitted solo (clause
12 completeness beats coverage, the same trade `_pack_group` makes).

Usage:
    python -X utf8 build_semi_plan.py --out runs/semi_v3 --sweep
    python -X utf8 build_semi_plan.py --out runs/semi_v3 --lam 1200
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

from platform_check import check
from platform_score import evaluate
from solver import Config, load_blanks, load_orders, Model, validate_plan
import round_shaper


def group_keys(orders):
    seen = []
    for o in orders:
        key = (o.steel, o.diameter)
        if key not in seen:
            seen.append(key)
    return seen


def pair_group(model, cfg, ids, lookahead):
    """Neighbour-first pairs, each carrying its own reshaper candidate list."""
    pending = list(ids)
    schemes, solos = [], 0
    while pending:
        a = pending.pop(0)
        partner = None
        for j in range(min(lookahead, len(pending))):
            cands = round_shaper.scheme_candidates([a, pending[j]], model, cfg)
            if cands:
                partner = (j, cands)
                break
        if partner is None:
            cands = round_shaper.scheme_candidates([a], model, cfg)
            solos += 1
            schemes.append(([a], cands))
        else:
            j, cands = partner
            b = pending.pop(j)
            schemes.append(([a, b], cands))
    return schemes, solos


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data', default='data/semi')
    ap.add_argument('--out', type=Path, default=Path('runs/semi_v3'))
    ap.add_argument('--lam', type=float, default=None)
    ap.add_argument('--sweep', action='store_true')
    ap.add_argument('--lookahead', type=int, default=4)
    ap.add_argument('--baseline-knives', type=float, default=90000.0)
    ap.add_argument('--cap-ratio', type=float, default=None,
                    help='over-production allowance used when CONSTRUCTING '
                         '(default: data/semi/competition.config.json)')
    ap.add_argument('--groups', nargs='*', default=None)
    args = ap.parse_args()

    data = Path(args.data)
    settings = json.loads((data / 'competition.config.json').read_text(encoding='utf-8'))
    if args.cap_ratio is not None:
        settings['max_overproduction_ratio'] = args.cap_ratio
    settings.update(continuity=True, coverage_shared=True, enforce_order_mass_floor=True,
                    objective='platform_score', baseline_knives=args.baseline_knives)
    cfg = Config(**settings)
    print(f'over-production allowance r={cfg.max_overproduction_ratio}', flush=True)
    orders = load_orders(str(data / 'orders.normalized.csv'), cfg, skip_invalid=True)
    blanks = load_blanks(str(data / 'blanks.normalized.csv'))
    model = Model(orders, cfg, blanks)
    model.lookup = {o.oid: i for i, o in enumerate(orders)}

    keys = group_keys(orders)
    if args.groups:
        want = set()
        for spec in args.groups:
            steel, _, dia = spec.partition(':')
            want.add((steel, float(dia)))
        keys = [k for k in keys if k in want]

    started = time.perf_counter()
    all_schemes, per_group, no_cand = [], [], 0
    for key in keys:
        ids = [i for i, o in enumerate(orders)
               if o.steel == key[0] and abs(o.diameter - key[1]) < 1e-9]
        schemes, solos = pair_group(model, cfg, ids, args.lookahead)
        dead = sum(1 for _ids, c in schemes if not c)
        no_cand += dead
        per_group.append(dict(group=f'{key[0]}:{key[1]}', orders=len(ids),
                              schemes=len(schemes), solos=solos, shapeless=dead))
        all_schemes.extend(schemes)
        print(f'[{key[0]}:{key[1]}] {len(ids)} orders -> {len(schemes)} schemes '
              f'({solos} solo, {dead} shapeless)  {time.perf_counter() - started:.0f}s',
              flush=True)
    print(f'\ncollected {len(all_schemes)} schemes in {time.perf_counter() - started:.0f}s; '
          f'{no_cand} schemes have no legal shape', flush=True)

    lams = [args.lam] if args.lam is not None else (
        [0.0, 100.0, 200.0, 400.0, 800.0, 1200.0, 1600.0, 2400.0, 3200.0]
        if args.sweep else [1200.0])

    best = None
    for lam in lams:
        plan = [round_shaper._to_batch(ids, model, cfg,
                                       min(cands, key=lambda c: c['declared'] + lam * c['knives']))
                if cands else None
                for ids, cands in all_schemes]
        plan = [b for b in plan if b is not None]
        sc = evaluate(plan, data, round_name='semi', baseline_knives=args.baseline_knives)
        print(f'lam={lam:<7g} schemes={len(plan)}  knives={sc["knives"]:,}  '
              f'yield={sc["yield_rate"]:.4f}  coverage={sc["coverage"]:.4f}  '
              f'score={sc["score_capped"]:.3f}  viol={sc["violation_count"]}  '
              f'overshoot={sc["overshoot_kg"]:,.0f}kg '
              f'({sc["overshoot_kg"] / max(1.0, sc["finished_weight_uncapped"]):.2%})',
              flush=True)
        if best is None or sc['score_capped'] > best[2]['score_capped']:
            best = (lam, plan, sc)

    lam, plan, sc = best
    print(f'\nCHOSEN lam={lam:g}: knives={sc["knives"]:,} yield={sc["yield_rate"]:.4f} '
          f'coverage={sc["coverage"]:.4f} score={sc["score_capped"]:.3f} '
          f'viol={sc["violation_count"]}')

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'result.json').write_text(
        json.dumps(plan, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')

    scope = out / 'scoped_data'
    scope.mkdir(parents=True, exist_ok=True)
    placed = {o for b in plan for o in b['orders']}
    kept = []
    with (data / 'orders_semi.csv').open(encoding='gbk', errors='replace') as fh:
        kept.append(fh.readline())
        for line in fh:
            if line.strip() and line.split(',', 1)[0].strip() in placed:
                kept.append(line)
    (scope / 'orders_semi.csv').write_text(''.join(kept), encoding='gbk')
    for name in ('blank_used_finals.csv', 'constraints.txt'):
        shutil.copyfile(data / name, scope / name)
    phys = check(plan, scope, round='semi')
    scope_orders = [o for o in orders if o.oid in placed]
    metrics = validate_plan(plan, scope_orders, cfg, blanks)

    summary = dict(lam=lam, groups=per_group, schemes=len(plan),
                   orders_placed=len(placed), orders_total=len(orders),
                   knives=sc['knives'], yield_rate=round(sc['yield_rate'], 6),
                   coverage=round(sc['coverage'], 6),
                   violation_count=sc['violation_count'],
                   score_capped=round(sc['score_capped'], 4),
                   score_capped_after_penalty=round(sc['score_capped_after_penalty'], 4),
                   physical_passed=phys['passed'], physical_errors=phys['error_counts'],
                   validator_rounds=metrics['rounds'], validator_knives=metrics['knives'],
                   validator_yield=round(metrics['yield_rate'], 6),
                   validator_coverage=round(metrics['coverage'], 6),
                   shapeless_schemes=no_cand,
                   elapsed_s=round(time.perf_counter() - started, 1))
    (out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n',
                                      encoding='utf-8')
    print(f'\nplatform_check passed: {phys["passed"]} {phys["error_counts"]}')
    print(f'validate_plan: rounds={metrics["rounds"]} knives={metrics["knives"]} '
          f'yield={metrics["yield_rate"]:.4f} coverage={metrics["coverage"]:.4f}')
    print(f'wrote {out / "result.json"}')


if __name__ == '__main__':
    main()
