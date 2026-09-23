"""Score two plan files side by side on identical terms, plus a per-round
"is this round on the cheapest bed for its delivery?" census.

Written to price the `_enumerate_round_shapes` fix, so besides the headline
numbers it reports the metric the fix is supposed to move: how many rounds could
still be rewritten onto a wider bed WITHOUT reducing any order's delivery, and how
many knives that would hand back.  A fixed solver should drive both to ~0 in the
plan it produces; a legacy one should not.

    python diagnostics/ab_report.py runs/seed_legacy.json runs/seed_fixed.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import platform_check as pc  # noqa: E402
import platform_score as ps  # noqa: E402
import solver as S  # noqa: E402

DATA = ROOT / 'data' / 'semi'
EPS_REL = 1e-9


def intended_k(length, size):
    return int((length + abs(length) * EPS_REL) // size)


def build():
    settings = json.loads((DATA / 'competition.config.json').read_text(encoding='utf-8'))
    settings.update(continuity=True, coverage_shared=True, enforce_order_mass_floor=True,
                    objective='platform_score', baseline_knives=160000)
    cfg = S.Config(**settings)
    orders = S.load_orders(str(DATA / 'orders.normalized.csv'), cfg, skip_invalid=True)
    blanks = S.load_blanks(str(DATA / 'blanks.normalized.csv'))
    return cfg, orders, S.Model(orders, cfg, blanks)


def widest_twin_census(cfg, model, plan):
    """Rounds that could be moved to a strictly wider bed at >= their delivery."""
    index = {o.oid: i for i, o in enumerate(model.orders)}
    rounds = wider = knives = 0
    for batch in plan:
        ids = tuple(sorted(index[o] for o in batch['orders'] if o in index))
        if not ids:
            continue
        blank = None
        for b in model.catalogue(ids):
            if b.bid == batch['blank_type']:
                blank = b
                break
        blank = blank or model.catalogue(ids)[0]
        usable = model.blank_length(ids, blank)
        sizes = [model.orders[i].size for i in ids]
        linear = model.orders[ids[0]].linear_weight
        plimit = model.parallel_limit(ids)
        for scheme, parallel in zip(batch['length_scheme'], batch['counts']):
            rounds += 1
            ks = [0] * len(ids)
            for oid, length in scheme.items():
                if oid in index:
                    ks[ids.index(index[oid])] = intended_k(length, model.orders[index[oid]].size)
            k_now = sum(ks) + 1
            delta = tuple(k * parallel for k in ks)
            for p2 in range(plimit, parallel, -1):
                ks2 = [(d + p2 - 1) // p2 for d in delta]
                lengths = [k * s for k, s in zip(ks2, sizes) if k]
                if not lengths:
                    continue
                total = sum(lengths) + cfg.round_trim
                if not cfg.min_bed_length - 1e-8 <= total <= cfg.bed_length + 1e-8:
                    continue
                if total * p2 * linear > cfg.bed_weight + 1e-8:
                    continue
                if max(lengths) + cfg.round_trim > usable + 1e-8:
                    continue
                k_new = sum(ks2) + 1
                if k_new < k_now:
                    wider += 1
                    knives += k_now - k_new
                    break
    return rounds, wider, knives


def row(path, cfg, model):
    plan = json.loads(Path(path).read_text(encoding='utf-8'))
    check = pc.check(plan, data=DATA, weight_mode='strict', round='semi')
    scored = ps.evaluate(plan, data=DATA, scoring_data=ps.load_scoring_data(DATA, 'semi'),
                         rules=ps.Rules.semi(160000.0))
    rounds, wider, knives = widest_twin_census(cfg, model, plan)
    return dict(
        path=str(path), schemes=len(plan),
        knives=round(scored['knives']),
        yield_pct=scored['yield_percent'],
        coverage=scored['coverage_percent_rounded'],
        violations=scored['violation_count'],
        score=scored['score_capped_display'],
        physical_ok=check.get('passed', check.get('ok')),
        rounds=rounds, wider_rounds=wider, recoverable_knives=knives)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    cfg, orders, model = build()
    rows = [row(p, cfg, model) for p in sys.argv[1:]]
    keys = ['schemes', 'knives', 'yield_pct', 'coverage', 'violations', 'score',
            'physical_ok', 'rounds', 'wider_rounds', 'recoverable_knives']
    head = f'{"metric":<22}' + ''.join(f'{Path(r["path"]).name:>22}' for r in rows)
    print(head)
    print('-' * len(head))
    for k in keys:
        print(f'{k:<22}' + ''.join(f'{r[k]!s:>22}' for r in rows))
    if len(rows) == 2:
        print(f'\ndelta knives {rows[1]["knives"] - rows[0]["knives"]:+,}   '
              f'delta score {rows[1]["score"] - rows[0]["score"]:+.4f}   '
              f'(columns are in argv order)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
