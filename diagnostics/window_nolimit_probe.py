"""Re-run `_pack_group`'s scheme-width measurement WITHOUT a deadline.

`solver._pack_group` pins its merge window to a pair with this justification:

    Measured on `NP01:43` for the first 20 orders: pairs construct 10/10 in 3.9 s,
    while three- and four-order chunks construct 0/10 and 0/6 -- every attempt runs
    to its deadline and only delays the orders that would otherwise be placed.  So
    a wider window is strictly worse, not merely slower.

Read that carefully: the three- and four-order chunks scored 0 out of 10 and 0 out
of 6 because "every attempt runs to its deadline".  `construct` returns `None` both
when a chunk is genuinely infeasible AND when it runs out of clock, and the
measurement could not tell the two apart -- under the preliminary round's 10 s
budget it did not matter.  The semi-final has no time limit at all (`RULES.md`
§0.1), so a wider window costs nothing but wall clock, and the pair ceiling is a
constant that was calibrated against a constraint that no longer exists.

This probe repeats the measurement with `deadline=None` and reports, per chunk
width, how many chunks construct and what they cost in knives.  If the wider
windows now succeed, `window = 2` is a deadline artefact and the plan's 2.73
orders per round -- against the other machine's 4.64, and worth an effective
`parallel` of 56.0 against 58.5, i.e. ~7,900 of the 8,698-segment deficit -- is
the direct consequence.
"""
from __future__ import annotations

import json
import random
import sys
import time
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
    return cfg, orders, blanks


def run_width(model, pool, cfg, blanks, orders, width, deadline_factory):
    """Partition the WHOLE pool into chunks of `width`; leftovers become singles.

    Slices that do not fill a chunk are emitted through `_solo_scheme`, so every
    width covers the identical order set and the knife totals are comparable --
    without that, the wider rows look better only because they silently drop the
    tail orders.
    """
    knives = rounds = chunks = failed = 0
    for k in range(0, len(pool), width):
        ids = tuple(pool[k:k + width])
        batch = None
        if len(ids) > 1 and S._can_share(model, ids):
            for attempt in range(8):
                batch = S._construct(model, ids, random.Random(1 + k),
                                     deadline_factory(), attempt > 0)
                if batch is not None:
                    break
        if batch is not None:
            chunks += 1
            knives += sum(r.knives for r in batch.rounds)
            rounds += len(batch.rounds)
            continue
        failed += 1 if len(ids) > 1 else 0
        for i in ids:
            solo = None
            for blank in model.catalogue((i,)):
                solo = S._solo_scheme(model, (i,), blank, random.Random(1 + i), None,
                                      randomized=True)
                if solo is not None:
                    break
            if solo is None:
                continue
            knives += sum(r.knives for r in solo.rounds)
            rounds += len(solo.rounds)
    return knives, rounds, chunks, failed


def main(model):
    cfg, orders, blanks = build()
    pool = [i for i, o in enumerate(orders)
            if o.steel == 'NP01' and abs(o.diameter - 43.0) < 1e-9]
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    pool = pool[:n]
    print(f'NP01:43 first {len(pool)} orders, identical order set in every row')
    print(f'{"width":>6} {"chunks":>7} {"failed":>7} {"knives":>9} {"rounds":>7} '
          f'{"orders/round":>13} {"seconds":>9}')

    for width in (2, 3, 4, 6):
        started = time.perf_counter()
        knives, rounds, chunks, failed = run_width(
            model, pool, cfg, blanks, orders, width, lambda: None)
        dt = time.perf_counter() - started
        print(f'{width:>6} {chunks:>7} {failed:>7} {knives:>9,} {rounds:>7,} '
              f'{len(pool) / rounds:>13.2f} {dt:>9.1f}', flush=True)

    # Does the historical claim ("three- and four-order chunks construct 0/10 --
    # every attempt runs to its DEADLINE") still reproduce when a deadline exists?
    print('\nsame widths WITH a 10 s deadline per construct (the condition the '
          'pair-only rule was measured under):')
    for width in (2, 3, 4):
        started = time.perf_counter()
        knives, rounds, chunks, failed = run_width(
            model, pool, cfg, blanks, orders, width,
            lambda: time.perf_counter() + 10.0)
        dt = time.perf_counter() - started
        print(f'{width:>6} {chunks:>7} {failed:>7} {knives:>9,} {rounds:>7,} '
              f'{len(pool) / rounds:>13.2f} {dt:>9.1f}', flush=True)


if __name__ == '__main__':
    _cfg, _orders, _blanks = build()
    main(S.Model(_orders, _cfg, _blanks))
