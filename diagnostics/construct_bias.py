"""Why does the greedy constructor pick a narrow bed when a wider one is cheaper?

`diagnostics/ab_report.py`'s census says a wide bed would recover 8,363 knives on
the shipped plan, and the A/B (`diagnostics/seeding_ab.py`) says fixing the
closing search's `offer()` recovers only 29 of them on `NP01:43`.  So the bulk of
the loss is not in the closing search at all -- it is in the greedy's *choice* of
`parallel`, in `_construct`.

For a fixed delivery the knife bill `sum(ks) + 1 = sum(delta) / parallel + 1` falls
with `parallel`, so a narrow round for the same delivery is strictly dominated.
That leaves exactly two explanations for a narrow round surviving, and they call
for completely different fixes:

  * **not offered** -- the wider shape is physically illegal (drops below the 50 m
    floor, or the 60 t ceiling: `length_limit = min(150, 60000 / (p * linear))`
    SHRINKS as `p` grows).  Nothing to fix in the selection; the geometry forbids
    it.
  * **offered and rejected** -- `_construct` builds a round for every `p` in
    `1..limit`, so a legal wider shape is in the candidate list, and the ranking
    `(useful / knives, useful / raw, -tail)` chose the narrow one anyway.  That is
    a selection defect, and it is where the 8,000 knives live.

The probe separates the two by shadowing `Model.make_round`: `_construct` calls it
for every candidate, so the log holds everything the greedy saw, and the returned
`Batch.rounds` identifies which of those it actually took (kept alive by identity,
so the rejected ones can be compared against the accepted one field by field).
"""
from __future__ import annotations

import json
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
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
    return cfg, orders, blanks


def capture(ids, cfg, blanks, orders, seed):
    """Run `_construct` once with `make_round` shadowed; return (batch, log)."""
    model = S.Model(orders, cfg, blanks)
    log = []
    original = S.Model.make_round

    def patched(self, cids, ks, parallel, blank):
        r = original(self, cids, ks, parallel, blank)
        log.append(dict(parallel=parallel, ks=tuple(ks), round=r, blank=blank.bid))
        return r

    S.Model.make_round = patched
    try:
        batch = S._construct(model, tuple(sorted(ids)), random.Random(seed), None)
    finally:
        S.Model.make_round = original
    return model, batch, log


def audit_group(ids, cfg, blanks, orders, sizes, seed=1, verbose=False):
    """Classify every accepted round of one group into the two explanations."""
    model, batch, log = capture(ids, cfg, blanks, orders, seed)
    tally = Counter()
    if batch is None:
        tally['construct returned None'] += 1
        return tally
    accepted = {id(r) for r in batch.rounds}
    tally['rounds'] += len(batch.rounds)
    tally['candidates considered'] += len(log)
    dims = [sizes[i] for i in ids]
    for r in batch.rounds:
        delta = tuple(k * r.parallel for k in r.ks)
        k_now = sum(r.ks) + 1
        cd = [c for c in log if c['blank'] == batch.blank_type and c['round'] is not None
              and c['parallel'] > r.parallel
              and tuple(k * c['parallel'] for k in c['ks']) == delta
              and sum(c['ks']) + 1 < k_now]
        if not cd:
            tally['no cheaper wider shape for this delivery'] += 1
            continue
        if any(id(c['round']) in accepted for c in cd):
            tally['cheaper wider shape existed, greedy used it elsewhere'] += 1
        else:
            tally['OFFERED AND REJECTED BY THE RANKING'] += 1
            tally['knives recoverable'] += k_now - min(sum(c['ks']) + 1 for c in cd)
        if verbose and any(id(c['round']) not in accepted for c in cd):
            best = min((c for c in cd if id(c['round']) not in accepted),
                       key=lambda c: sum(c['ks']) + 1)
            print(f'    p={r.parallel} ks={r.ks} kn={k_now} '
                  f'-> offered p={best["parallel"]} ks={best["ks"]} kn={sum(best["ks"]) + 1}')
    return tally


def main():
    cfg, orders, blanks = build()
    sizes = [o.size for o in orders]
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    steps = int(sys.argv[2]) if len(sys.argv) > 2 else 20

    # 2-order groups of NP01:43 -- 3,131 of the plan's 4,048 schemes are 2-order,
    # and they are the ones the closing search (`_round_moves`) does NOT serve, so
    # whatever bed-width bias the greedy has shows up here undiluted.
    pool = [i for i, o in enumerate(orders)
            if o.steel == 'NP01' and abs(o.diameter - 43.0) < 1e-9]
    print(f'NP01:43 orders {len(pool)}; auditing {min(limit, len(pool) - 1)} pairs '
          f'at stride {steps}')
    total = Counter()
    for k in range(0, min(limit, len(pool) - 1), steps):
        ids = (pool[k], pool[k + 1])
        sub = audit_group(ids, cfg, blanks, orders, sizes, verbose=True)
        print(f'  pair {ids} pieces {[orders[i].pieces for i in ids]} '
              f'sizes {[orders[i].size for i in ids]} -> '
              f'recovered {sub.get("knives recoverable", 0)} knives of '
              f'{sub.get("rounds", 0)} rounds', flush=True)
        total.update(sub)
    print('\n--- aggregate ---')
    for key, value in total.most_common():
        print(f'  {key:<52} {value:,}')


if __name__ == '__main__':
    main()

