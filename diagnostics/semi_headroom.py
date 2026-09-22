"""Where is the remaining headroom? Decompose the delivered plan's two axes.

Answers "is it optimised to the limit?" with measurements instead of opinion.

The masses are taken from `platform_score.evaluate` (the same call the report's
tables use) and re-derived geometrically, so the two must agree; if they do not,
this script is wrong and not the scorer.

  * KNIVES -- total = (scored segments) + (rounds).  A round costs exactly 1
    knife on top of its segments, so the question is twofold: are the segments
    already at their floor, and are the rounds full?  A round is capped by
    `net + 2 <= 150 m` and by `(net + 2) * p * mu <= 60 t`; a round sitting well
    below both has money left on the table.

    The segment term carries a second, larger question: `platform_score` charges
    knives with `int(L // size)`, which floors *below* the declared piece count
    whenever `solver._export`'s `round(k * size, 9)` lands under the exact
    multiple.  `platform_check` credits pieces with the `nearest` reading of the
    same number, so the two readings of one plan differ by ~8% of the knife
    total.  Both are printed; whichever the platform uses changes the score more
    than every lambda, r and pairing knob combined.

  * YIELD -- raw steel splits exactly four ways,
        raw = credited + over_production + shared_trim + uncut
    where `credited` is the scorer's demand-capped numerator, `over_production`
    is dialled by lambda, `shared_trim` is the geometrically forced 2 m/round,
    and `uncut` is whole-blank round-up (steel delivered on the bed that no
    scheme declares).  Only the first loss is a choice; the last two are facts.

Usage:
    python -X utf8 diagnostics/semi_headroom.py [plan.json]
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from platform_check import read_plan  # noqa: E402
from platform_score import Rules, evaluate, load_scoring_data  # noqa: E402
from solver import Config, load_blanks, load_orders, Model  # noqa: E402

DATA = ROOT / 'data' / 'semi'
DEFAULT_PLAN = ROOT / 'deliverables_semi' / '复赛结果_棒材优化.json'


def pct(x, base):
    return f'{100.0 * x / base:.2f}%' if base else 'n/a'


def quantiles(values, qs=(0.0, 0.1, 0.5, 0.9, 1.0)):
    if not values:
        return []
    s = sorted(values)
    return [s[min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))] for q in qs]


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PLAN
    plan = read_plan(path)
    try:
        shown = path.relative_to(ROOT)
    except ValueError:
        shown = path

    cfg = Config(**json.loads((DATA / 'cap010.config.json').read_text(encoding='utf-8')))
    orders = load_orders(str(DATA / 'orders.normalized.csv'), cfg, skip_invalid=True)
    blanks = load_blanks(str(DATA / 'blanks.normalized.csv'))
    model = Model(orders, cfg, blanks)
    model.lookup = {o.oid: i for i, o in enumerate(orders)}
    blank_by_id = {b.bid: b for b in blanks}
    sc = load_scoring_data(DATA, 'semi')

    print(f'plan: {shown}')
    print(f'schemes={len(plan):,}  rounds={sum(len(b["length_scheme"]) for b in plan):,}\n')

    res_floor = evaluate(plan, DATA, round_name='semi', baseline_knives=90000.0)
    res_near = evaluate(plan, DATA, rules=Rules(
        **{**Rules.semi(90000.0).__dict__, 'segment_convention': 'nearest'}),
        round_name='semi')

    rounds = 0
    seg_scored = 0        # charged by platform_score: int(L // size)
    seg_true = 0          # physically real cuts: k per (round, order), no floor
    util_len, util_mass, fill, counts = [], [], [], []
    cap_kind = Counter()
    useful_mass = trim_mass = per_bar_net = 0.0     # scoring (integer-diameter) basis
    mass_phys = 0.0                                 # physical basis, for the bed cap

    for batch in plan:
        ids = [model.lookup[o] for o in batch['orders']]
        blank = blank_by_id[batch['blank_type']]
        usable = model.blank_length(ids, blank)
        for scheme, p, count in zip(batch['length_scheme'], batch['counts'],
                                    batch['blank_counts']):
            rounds += 1
            net = sum(scheme.values())
            mu_phys = model.orders[ids[0]].linear_weight     # round cap uses physical mass
            mu_int = sc.orders[batch['orders'][0]].linear_weight  # yield uses int diameter
            for oid, L in scheme.items():
                size = model.orders[model.lookup[oid]].size
                seg_scored += int(round(L, 9) // size)
                seg_true += round(L / size)
            cap_len = min(cfg.bed_length, cfg.bed_weight / (p * mu_phys))
            mass = (cfg.round_trim + net) * p * mu_phys
            util_len.append(net / cap_len)
            util_mass.append(mass / cfg.bed_weight)
            fill.append((cfg.round_trim + net) * p / usable)
            counts.append(count)
            if abs(cap_len - cfg.bed_length) < 1e-9:
                cap_kind['length'] += 1
            elif abs(cap_len - cfg.bed_weight / (p * mu_phys)) < 1e-9:
                cap_kind['weight'] += 1
            else:
                cap_kind['both'] += 1
            useful_mass += net * p * mu_int
            trim_mass += cfg.round_trim * p * mu_int
            mass_phys += (cfg.round_trim + net) * p * mu_phys
            per_bar_net += net

    seg_lb = sum(o.pieces / model.parallel_limit([i]) for i, o in enumerate(orders))
    raw = res_floor['raw_weight']
    produced = res_floor['finished_weight_uncapped']      # declared net x p x mu_int
    credited = res_floor['finished_weight']
    over = res_floor['overshoot_kg']
    uncut = raw - produced - trim_mass
    rounds_lb_mass = math.ceil(mass_phys / cfg.bed_weight)
    rounds_lb_len = math.ceil(per_bar_net / cfg.bed_length)
    near_cap = sum(1 for u in util_len if u > 0.98) + sum(1 for u in util_mass if u > 0.98)

    print('--- knives ---')
    print(f'scored segments            {seg_scored:,}   (int(L // size): what row_metrics charges)')
    print(f'  true cuts (k, no floor)  {seg_true:,}   (physical parting cuts)')
    print(f'  floor discount           {seg_true - seg_scored:,}   '
          f'({100.0 * (seg_true - seg_scored) / seg_true:.2f}% of true cuts, free knives)')
    print(f'segments lower bound       {math.ceil(seg_lb):,}   (sum_i P_i / parallel_limit_i)')
    print(f'  scored vs bound          {100.0 * (seg_scored / seg_lb - 1):+.1f}%'
          f'   |  true vs bound {100.0 * (seg_true / seg_lb - 1):+.1f}%')
    print(f'rounds                     {rounds:,}   (each costs +1 knife)')
    print(f'rounds lower bound  mass   {rounds_lb_mass:,}   (physical bar mass / bed weight)'
          f'  -> headroom {100.0 * (rounds / rounds_lb_mass - 1):.1f}%')
    print(f'rounds lower bound  length {rounds_lb_len:,}   (sum per-bar net / bed length)')
    print(f'knives  floor reading      {res_floor["knives"]:,}   '
          f'(= {seg_scored:,} + {rounds:,})')
    print(f'knives  nearest reading    {res_near["knives"]:,}   '
          f'(= {seg_true:,} + {rounds:,})   -> swing '
          f'{res_near["knives"] - res_floor["knives"]:,} knives '
          f'({100.0 * (res_near["knives"] / res_floor["knives"] - 1):.1f}%)')
    print(f'  score B=90k   floor {res_floor["score_capped"]:.4f}  '
          f'nearest {res_near["score_capped"]:.4f}   '
          f'-> {res_floor["score_capped"] - res_near["score_capped"]:+.4f}')
    print(f'net/cap_len  p0/p10/p50/p90/p100 = '
          + ' / '.join(f'{v:.2f}' for v in quantiles(util_len)))
    print(f'mass/bed_wt  p0/p10/p50/p90/p100 = '
          + ' / '.join(f'{v:.2f}' for v in quantiles(util_mass)))
    print(f'rounds near a cap (>98%)   {near_cap:,} / {rounds:,} '
          f'({100.0 * near_cap / rounds:.1f}%)')
    print(f'binding cap: {dict(cap_kind)}')

    print('\n--- yield: raw = credited + over_production + shared_trim + uncut ---')
    for label, value in (('credited (capped by demand)', credited),
                         ('over-production (lambda dial)', over),
                         ('shared trim (2 m/round, rule)', trim_mass),
                         ('uncut (whole-blank round-up)', uncut)):
        print(f'  {label:<30} {value:>18,.0f} kg   {pct(value, raw):>7} of raw')
    total = credited + over + trim_mass + uncut
    print(f'  {"SUM":<30} {total:>18,.0f} kg'
          f'   (raw = {raw:,.0f}, residual {raw - total:,.2f})')
    print(f'  declared net (scorer numerator) {useful_mass:,.0f} kg'
          f'   vs scorer uncapped {produced:,.0f}'
          f'   (equal -> the plan declares net length only)')
    print(f'  yield floor if uncut were free: {credited / (useful_mass + trim_mass):.4f}'
          f'   (= credited / (net + trim))')
    print(f'  ... and if trim were free too:  {credited / useful_mass:.4f}'
          f'   (= credited / net)')

    print('\n--- blanks per round ---')
    print(f'mean count   {sum(counts) / len(counts):.2f}')
    print(f'count histogram (top 8): '
          + ', '.join(f'{k}:{v}' for k, v in Counter(counts).most_common(8)))
    print(f'mean fill fraction (net+2)p/usable {sum(fill) / len(fill):.3f}')
    print(f'mean fractional part of fill       '
          f'{sum(f - math.floor(f) for f in fill) / len(fill):.3f}')


if __name__ == '__main__':
    main()
