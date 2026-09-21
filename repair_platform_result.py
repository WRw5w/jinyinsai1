"""Migrate the old 30-minute solution to the observed platform length contract."""
import json
import random
import time
from pathlib import Path

from competition_solver import Factory, atomic_json
from platform_check import check
from solver import (Batch, Config, Model, _export, _key, load_blanks, load_orders,
                    search_10s, validate_plan)


def repair():
    out = Path('runs/platform_fix')
    out.mkdir(parents=True, exist_ok=True)
    cfg = Config(**json.loads(Path('data/competition.config.json').read_text()))
    if cfg.length_mode != 'net_shared_trim':
        raise ValueError('Run prepare_data.py with corrected platform settings first')
    orders = load_orders('data/orders.normalized.csv', cfg)
    blanks = load_blanks('data/blanks.normalized.csv')
    model = Model(orders, cfg, blanks)
    lookup = {o.oid: i for i, o in enumerate(orders)}
    old = json.loads(Path('runs/half_hour_20260915_2114/result.json').read_text())
    factory, rng = Factory(), random.Random(926)
    migrated, rebuilt, bad_rounds = [], [], 0
    deadline = time.perf_counter() + 60
    for a, batch in enumerate(old):
        ids = tuple(lookup[n] for n in batch['orders'])
        blank = next(b for b in blanks if b.bid == batch['blank_type'])
        rows = []
        for lengths, p in zip(batch['length_scheme'], batch['counts']):
            ks = []
            for i in ids:
                name = orders[i].oid
                ratio = (lengths[name] - 2) / orders[i].size if name in lengths else 0
                k = round(ratio)
                if abs(k - ratio) > 1e-7:
                    raise ValueError('Source does not match legacy length convention')
                ks.append(k)
            r = model.make_round(ids, tuple(ks), p, blank)
            if r is None:
                bad_rounds += 1
            rows.append(r)
        if all(r is not None for r in rows):
            migrated.append(Batch(ids, blank.bid, tuple(rows)))
            continue
        candidates = []
        for attempt in range(5):
            candidate = factory(model, ids, rng, deadline, attempt > 0)
            if candidate is not None:
                candidates.append(candidate)
        if not candidates:
            raise ValueError(f'Could not rebuild source scheme {a}')
        migrated.append(min(candidates, key=lambda b: _key(b.metrics, cfg, len(ids))))
        rebuilt.append(a)
    plan = _export(migrated, orders, cfg)
    before_search = validate_plan(plan, orders, cfg, blanks)
    audit = check(plan)
    if not audit['passed']:
        atomic_json(out / 'repair_failure.json', audit)
        raise ValueError('Independent checker rejected repaired initial solution')
    atomic_json(out / 'repaired_initial.json', plan)
    stats = {}
    plan = search_10s(orders, cfg, seconds=10, seed=926, blanks=blanks,
                      initial_plan=plan, constructor=factory, stats=stats)
    after_search = validate_plan(plan, orders, cfg, blanks)
    audit = check(plan)
    if not audit['passed']:
        raise ValueError('Independent checker rejected final solution')
    atomic_json(out / 'result.json', plan)
    atomic_json(out / 'result.metrics.json', after_search)
    atomic_json(out / 'independent_check.json', audit)
    atomic_json(out / 'repair_report.json', dict(source='runs/half_hour_20260915_2114/result.json',
                length_mode='net_shared_trim', rebuilt_schemes=rebuilt, invalid_rounds_after_conversion=bad_rounds,
                before_search=before_search, after_search=after_search, stats=stats,
                official_feedback={'noninteger_multiple': 11315, 'bed_weight': 395, 'bed_length': 44},
                root_cause='Legacy export included a 2m trim per segment; platform adds one 2m trim to the round.',
                official_acceptance_verified=False))
    print(json.dumps(dict(rebuilt_schemes=len(rebuilt), before=before_search, after=after_search, independent_passed=True), ensure_ascii=False))


if __name__ == '__main__':
    repair()
