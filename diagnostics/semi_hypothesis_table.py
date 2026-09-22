"""Re-measure every saved whole-plan candidate under the unresolved score readings.

Section 6.2 / 6.3 of `deliverables_semi/复赛技术报告.md` must not contain hand
arithmetic: the semi-final baseline knife count `B` and the treatment of the time
subscore are both unconfirmed, so each cell is produced by running the real
`platform_score.evaluate` under one hypothesis.

Hypotheses priced here:
  * `B = 90000`   -- the preliminary-round value, i.e. the knife axis likely bites
  * `B = 180000`  -- the baseline scaled with the order count (10x the dataset)
  * `B = 180000`, knife subscore uncapped -- if the platform does not clamp at 100
  * weights (0.4, 0.4, 0.2, 0) -- the Q&A claim that time is folded into yield

Usage:
    python -X utf8 diagnostics/semi_hypothesis_table.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from platform_score import evaluate, Rules  # noqa: E402

DATA = ROOT / 'data' / 'semi'
PLANS = [
    ('0.02', ROOT / 'runs/semi_v3/result.json'),
    ('0.05', ROOT / 'runs/capx_0.05/result.json'),
    ('0.10', ROOT / 'runs/capx_0.10/result.json'),
    ('0.25', ROOT / 'runs/capx_0.25/result.json'),
    ('0.50', ROOT / 'runs/capx_0.50/result.json'),
    ('1.00', ROOT / 'runs/capx_1.00/result.json'),
]

HEADER = (
    '| r | schemes | rounds | knives | yield | coverage | viol | overshoot kg | '
    'overshoot % | K_sub(B=90k) | K_sub(B=180k) | S(B=90k,capped) | S(B=90k,uncapped) | '
    'S(B=180k,capped) | S(B=180k,uncapped) | S(w=0.4/0.4/0.2/0) |'
)
SEP = ('|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|')

# The second unresolved reading that dominates every other term: `platform_score`
# charges knives with `int(L // size)` ('floor'), while `platform_check` credits
# delivered pieces with `abs(L/size - k) <= 1e-8` ('nearest').  `solver._export`
# writes `round(k * size, 9)`, which lands below the exact multiple on a large
# share of (k, size) pairs, so the two readings disagree by ~8% of the knife
# total -- a swing far larger than any lambda or r headroom.
CONV_HEADER = (
    '| r | knives(floor) | knives(nearest) | K_sub90(floor) | K_sub90(nearest) | '
    'dS90 | dS180 |'
)
CONV_SEP = '|---|---:|---:|---:|---:|---:|---:|'


def _rules(conv, baseline=90000.0, weights=None):
    base = Rules.semi(baseline)
    d = {**base.__dict__, 'segment_convention': conv}
    if weights is not None:
        d['weights'] = tuple(weights)
    return Rules(**d)


def main():
    rows, missing, conv_rows = [], [], []
    for label, path in PLANS:
        if not path.exists():
            missing.append(f'{label}: {path}')
            continue
        plan = json.loads(path.read_text(encoding='utf-8'))
        base = evaluate(plan, DATA, round_name='semi', baseline_knives=90000.0)
        b180 = evaluate(plan, DATA, round_name='semi', baseline_knives=180000.0)
        alt = evaluate(plan, DATA, round_name='semi', baseline_knives=90000.0,
                       weights=(0.4, 0.4, 0.2, 0.0))
        rows.append(dict(
            r=label, schemes=len(plan), rounds=base['rounds'], knives=base['knives'],
            yield_rate=base['yield_rate'], coverage=base['coverage'],
            viol=base['violation_count'], overshoot_kg=base['overshoot_kg'],
            overshoot_share=base['overshoot_kg'] / base['finished_weight_uncapped'],
            raw_weight=base['raw_weight'],
            knife_sub_90=base['subscores']['knives'],
            knife_sub_180=b180['subscores']['knives'],
            s90_cap=base['score_capped'], s90_uncap=base['score_uncapped'],
            s180_cap=b180['score_capped'], s180_uncap=b180['score_uncapped'],
            alt_cap=alt['score_capped'],
        ))

        n90 = evaluate(plan, DATA, rules=_rules('nearest', 90000.0), round_name='semi')
        n180 = evaluate(plan, DATA, rules=_rules('nearest', 180000.0), round_name='semi')
        conv_rows.append(dict(
            r=label,
            k_floor=base['knives'], k_nearest=n90['knives'],
            ks_floor=base['subscores']['knives'], ks_nearest=n90['subscores']['knives'],
            ds90=base['score_capped'] - n90['score_capped'],
            ds180=b180['score_capped'] - n180['score_capped'],
        ))

    out = [HEADER, SEP]
    for x in rows:
        out.append(
            f"| {x['r']} | {x['schemes']:,} | {x['rounds']:,} | {x['knives']:,} | "
            f"{x['yield_rate']:.4f} | {x['coverage']:.4f} | {x['viol']} | "
            f"{x['overshoot_kg']:,.0f} | {x['overshoot_share']:.2%} | "
            f"{x['knife_sub_90']:.2f} | {x['knife_sub_180']:.2f} | "
            f"{x['s90_cap']:.4f} | {x['s90_uncap']:.4f} | "
            f"{x['s180_cap']:.4f} | {x['s180_uncap']:.4f} | {x['alt_cap']:.4f} |")
    text = '\n'.join(out) + '\n'

    conv = [CONV_HEADER, CONV_SEP]
    for x in conv_rows:
        conv.append(
            f"| {x['r']} | {x['k_floor']:,} | {x['k_nearest']:,} | "
            f"{x['ks_floor']:.2f} | {x['ks_nearest']:.2f} | "
            f"{x['ds90']:+.4f} | {x['ds180']:+.4f} |")
    text += '\n### knife convention sensitivity (' + \
        "`int(L//size)` vs the `nearest` reading of the same plan)\n\n"
    text += '\n'.join(conv) + '\n'

    if missing:
        text += '\nmissing plans:\n' + '\n'.join(f'  {m}' for m in missing) + '\n'

    (ROOT / 'diagnostics' / 'semi_hypothesis_table.md').write_text(text, encoding='utf-8')
    (ROOT / 'diagnostics' / 'semi_hypothesis_table.json').write_text(
        json.dumps(dict(plans=rows, convention=conv_rows), ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
