"""Anatomy of two semi-final plans: ours vs the second machine's.

Why this exists
---------------
`diagnostics/knives_ideal.py` prints a *theoretical floor* of 177,736 knives for
the semi-final drop.  The second machine's plan scores 175,952 knives under the
very same `platform_score.row_metrics` -- i.e. it is *below* the floor.  A plan
cannot beat a lower bound, so either the plan is not really valid, the knife
model is not what we think, or the "floor" is not a floor.  This script settles
which, by decomposing both plans down to the round and comparing each round's
own cost-per-piece against the per-diameter minimum the floor is built from.

Knife model (Rules.semi): knives = sum over rows of (sum_i int(L_i // size_i) + 1),
one head/tail pair per *row* (= per round, per blank type), plus the parting cuts
at each of the sum(k) joins.  A row's cost per piece is (sum_k + 1) / (p * sum_k).
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import platform_score as P  # noqa: E402

DATA = ROOT / 'data' / 'semi'      # load_scoring_data expects the directory holding the csvs


def find_plan(folder: Path) -> Path:
    cands = [p for p in folder.glob('*.json')
             if 'validation' not in p.name and 'score' not in p.name]
    if not cands:
        raise SystemExit(f'no plan json in {folder}')
    return max(cands, key=lambda p: p.stat().st_size)


def anatomy(name: str, path: Path, context, rules):
    plan = json.loads(path.read_text(encoding='utf-8'))
    rows = segments = 0
    per_dia = defaultdict(lambda: dict(rounds=0, segments=0, knives=0.0, pieces=0.0,
                                       p_sum=0, k_sum=0, k_max=0, p_max=0))
    ratios = []
    for batch in plan:
        dia = next((context.orders[o].diameter_mm for o in batch['orders']
                    if o in context.orders), None)
        slot = per_dia[dia]
        for scheme, parallel, stock in zip(batch['length_scheme'], batch['counts'],
                                          batch['blank_counts']):
            k = sum(int(length // context.orders[oid].size_m) for oid, length in scheme.items())
            rows += 1
            segments += k
            slot['rounds'] += 1
            slot['segments'] += k
            slot['knives'] += k + 1
            slot['p_sum'] += parallel
            slot['k_sum'] += k
            slot['k_max'] = max(slot['k_max'], k)
            slot['p_max'] = max(slot['p_max'], parallel)
            slot['pieces'] += parallel * k
            ratios.append(((k + 1) / (parallel * k) if k else 9e9, k, parallel, dia))
    ev = P.evaluate(plan, data=DATA, scoring_data=context, rules=rules)
    print(f'=== {name}  ({path.name}, {path.stat().st_size:,} B) ===')
    print(f'  schemes(batches)      {len(plan):>10,}')
    print(f'  rows(=rounds)         {rows:>10,}')
    print(f'  segments(sum k)       {segments:>10,}')
    print(f'  knives = seg + rows   {segments + rows:>10,}   (evaluate agrees: '
          f'{ev["knives"] == segments + rows})')
    print(f'  entries(row,order)    {ev["entries"]:>10,}   orders/row = {ev["entries"] / rows:.2f}')
    print(f'  yield  (scoring)      {ev["yield_percent"]:>10.4f}%')
    print(f'  coverage              {ev["coverage_percent_rounded"]:>10.2f}%')
    print(f'  violations            {ev["violation_count"]:>10,}')
    print(f'  score @B=160000       {ev["score_capped_display"]:>10.4f}')
    ratios.sort()
    print(f'  best row cost/piece   {ratios[0][0]:.5f}  (k={ratios[0][1]}, p={ratios[0][2]}, dia={ratios[0][3]})')
    print(f'  worst row cost/piece  {ratios[-1][0]:.5f}  (k={ratios[-1][1]}, p={ratios[-1][2]}, dia={ratios[-1][3]})')
    print(f'  {"dia":>6} {"rounds":>7} {"k":>10} {"knives":>9} {"pieces":>12} '
          f'{"k/rd":>6} {"p/rd":>6} {"p^mx":>5} {"cost/pc":>8}')
    for dia in sorted(per_dia, key=lambda d: -(d or 0)):
        s = per_dia[dia]
        if not s['rounds']:
            continue
        cp = s['knives'] / s['pieces'] if s['pieces'] else 0.0
        print(f'  {dia:>6} {s["rounds"]:>7,} {s["k_sum"]:>10,} {s["knives"]:>9,.0f} '
              f'{s["pieces"]:>12,.0f} {s["k_sum"] / s["rounds"]:>6.1f} '
              f'{s["p_sum"] / s["rounds"]:>6.1f} {s["p_max"]:>5} {cp:>8.5f}')
    print()
    return dict(knives=ev['knives'], rows=rows, segments=segments, per_dia=per_dia,
                yield_pct=ev['yield_percent'], score=ev['score_capped_display'],
                coverage=ev['coverage_percent_rounded'], violations=ev['violation_count'])


def floor_by_dia(context, cfg):
    """Per-order full-round best cost/piece, grouped by diameter (the floor's basis)."""
    best = {}
    for o in context.orders.values():
        p_width = int(cfg.bed_width_mm // o.diameter_mm)
        b, shape = 9e9, None
        for sum_k in range(1, int((cfg.bed_length - cfg.round_trim) // o.size_m) + 1):
            length = sum_k * o.size_m + cfg.round_trim
            if length < cfg.min_bed_length - 1e-8:
                continue
            p = min(p_width, int(cfg.bed_weight // (length * o.physical_linear_weight)))
            if p < 1:
                continue
            ratio = (sum_k + 1) / (p * sum_k)
            if ratio < b:
                b, shape = ratio, (sum_k, p, round(length, 1))
        if shape is None:
            continue
        if o.diameter_mm not in best or b < best[o.diameter_mm][0]:
            best[o.diameter_mm] = (b, shape, o.size_m)
    return best


def main():
    context = P.load_scoring_data(DATA, 'semi')
    rules = P.Rules.semi(160000.0)
    settings = json.loads((DATA / 'competition.config.json').read_text(encoding='utf-8'))

    # bed geometry constants are in the solver Config; read them without building it
    class Geom:
        bed_length = settings['bed_length']
        min_bed_length = settings['min_bed_length']
        bed_width_mm = settings['bed_width_mm']
        bed_weight = settings['bed_weight']
        round_trim = settings['trim']
    cfg = Geom()

    ours = anatomy('OURS (fork, submission_semi_nolimit)', find_plan(ROOT / 'submission_semi_nolimit'),
                   context, rules)
    theirs = anatomy('THEIRS (machine 2, main)', find_plan(ROOT / 'runs' / 'theirs_main'),
                     context, rules)

    print('=== HEAD TO HEAD ===')
    for k in ('knives', 'segments', 'rows'):
        a, b = ours[k], theirs[k]
        print(f'  {k:<10} ours {a:>10,}   theirs {b:>10,}   delta {b - a:>+10,} ({(b - a) / a:+.2%})')
    print(f'  {"yield":<10} ours {ours["yield_pct"]:>10.4f}%  theirs {theirs["yield_pct"]:>10.4f}%')
    print(f'  {"coverage":<10} ours {ours["coverage"]:>10.2f}%  theirs {theirs["coverage"]:>10.2f}%')
    print(f'  {"viol":<10} ours {ours["violations"]:>10,}   theirs {theirs["violations"]:>10,}')
    print(f'  {"score":<10} ours {ours["score"]:>10.4f}  theirs {theirs["score"]:>10.4f}')

    best = floor_by_dia(context, cfg)
    print()
    print('=== Does the "floor" survive contact with a real plan? ===')
    print(f'  {"dia":>6} {"floor cost/pc":>13} {"floor shape":>18} {"ours":>8} {"theirs":>8}  verdict')
    for dia in sorted(set(ours['per_dia']) | set(theirs['per_dia']) | set(best),
                      key=lambda d: -(d or 0)):
        f = best.get(dia)
        o_, t_ = ours['per_dia'].get(dia), theirs['per_dia'].get(dia)
        oc = o_['knives'] / o_['pieces'] if o_ and o_['pieces'] else float('nan')
        tc = t_['knives'] / t_['pieces'] if t_ and t_['pieces'] else float('nan')
        verdict = ''
        if f:
            if tc == tc and tc < f[0] * 0.9995:
                verdict = 'THEIRS BELOW FLOOR'
            elif oc == oc and oc < f[0] * 0.9995:
                verdict = 'OURS BELOW FLOOR'
        print(f'  {dia:>6} {f[0]:>13.5f} {str(f[1]):>18} {oc:>8.5f} {tc:>8.5f}  {verdict}')


if __name__ == '__main__':
    main()
