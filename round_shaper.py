"""Re-shape each scheme's cold-bed rounds to the knife-minimal legal form.

Why this exists
---------------
`solve_semi.py` builds a plan with `solver.search_10s`, which merges orders into
pairs (`_pack_group`) and closes each pair with rounds drawn from
`_enumerate_round_shapes`.  That move set dedups a *delivered piece vector*
`delta` down to the SMALLEST legal `parallel`:

    old = best.get(delta)
    if old is not None and parallel >= old[0]:      # keeps the first, ascending, p
        return False

but the semi-final knife rule charges `segments + 1` per round and this round's
segments are `sum(delta_i / parallel)`.  For a fixed delivery the FEWEST knives
come from the LARGEST legal `parallel`, so keeping the smallest is backwards.
Measured on `runs/semi_v2/chunks/NP01_43.0/000000.json`: 2,546 knives over 60
orders = 45 knives/order, where ~19 is reachable.

What this does
--------------
Rewrites only `length_scheme` / `counts` / `blank_counts` of each scheme:

  * `orders` is untouched, so the one-scheme-per-order partition, coverage
    (which counts orders sharing a *round*) and continuity (per order, within its
    scheme) are all preserved bit for bit;
  * every order still lands in `[pieces, caps]`, so clause 8 and the
    over-production cap hold;
  * everything else is recomputed from `solver.Model`'s own helpers.

The subproblem.  With `n` rounds that all share one `parallel = p`, order `i`
receives `K_i * p` pieces, where `K_i` is its total of per-bar segments.  The
window `[P_i, C_i]` is independent per order, and the split across the `n` rounds
does not affect the piece count at all, so

    K_i = ceil(P_i / p)     whenever that is <= C_i

is feasible AND knife-minimal for that `p`: knives fall as `p` rises, and the
ceiling is the smallest integer that still clears the demand floor.  The round
count `n` is then free to be whatever keeps every round inside the bed.  So the
whole search is `(blank_type, n, p)` -- at most 5 * 6 * parallel_limit points per
scheme.

Because every valid candidate delivers at least `P_i` pieces, the credited
numerator is `sum(P_i * size_i * mu_int)` for all of them -- a constant.  Yield is
therefore `constant / sum(declared)`, and the plan-level trade-off is purely
bi-criteria: minimise `sum(knives)` against minimise `sum(declared)`.  The caller
picks a point on that frontier with a Lagrange sweep (`--sweep`) and re-measures
with the real `platform_score.evaluate`.

Usage:
    python -X utf8 round_shaper.py runs/semi_v2/result.json --sweep
    python -X utf8 round_shaper.py runs/semi_v2/result.json --out runs/semi_v3/result.json --lam 200
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

from platform_score import evaluate
from solver import Config, load_blanks, load_orders, Model

_EPS = 1e-9
_MU_FALLBACK = 90000.0


def _ceil(x):
    return math.ceil(round(x, 9))


def _split_rounds(ks_total, n, sizes, trim, min_len, cap_len):
    """Split each order's per-bar segment total across `n` rounds, in-bed.

    Returns a list of `n` k-vectors, or None.  Every round must carry every order
    (that is what makes coverage automatic and each order's round run contiguous),
    and must land in `[min_len, cap_len]` metres including the shared trim.
    """
    m = len(ks_total)
    parts = [[ks_total[i] // n + (1 if r < ks_total[i] % n else 0) for r in range(n)]
             for i in range(m)]
    if any(min(p) < 1 for p in parts):
        return None
    lengths = [trim + sum(parts[i][r] * sizes[i] for i in range(m)) for r in range(n)]

    for _ in range(200):
        lo = min(range(n), key=lambda r: lengths[r])
        hi = max(range(n), key=lambda r: lengths[r])
        if lengths[lo] >= min_len - _EPS and lengths[hi] <= cap_len + _EPS:
            return [tuple(parts[i][r] for i in range(m)) for r in range(n)]
        moved = False
        if lengths[hi] > cap_len + _EPS:
            # shed a unit from the overloaded round into a round with headroom
            for i in sorted(range(m), key=lambda j: -sizes[j]):
                if parts[i][hi] <= 1:
                    continue
                for r in sorted(range(n), key=lambda r: lengths[r]):
                    if r == hi:
                        continue
                    if lengths[r] + sizes[i] <= cap_len + _EPS:
                        parts[i][hi] -= 1
                        parts[i][r] += 1
                        lengths[hi] -= sizes[i]
                        lengths[r] += sizes[i]
                        moved = True
                        break
                if moved:
                    break
        else:
            # top up the starved round from a round with slack to give
            for i in sorted(range(m), key=lambda j: -sizes[j]):
                for r in sorted(range(n), key=lambda r: -lengths[r]):
                    if r == lo or parts[i][r] <= 1:
                        continue
                    if lengths[r] - sizes[i] >= min_len - _EPS:
                        parts[i][r] -= 1
                        parts[i][lo] += 1
                        lengths[r] -= sizes[i]
                        lengths[lo] += sizes[i]
                        moved = True
                        break
                if moved:
                    break
        if not moved:
            return None
    return None


def scheme_candidates(ids, model, cfg):
    """Pareto-minimal `(knives, declared)` round plans for one scheme.

    Two independent choices decide the whole scheme:

    * `parallel` -- one shared value `p` for every round.  Order `i` then receives
      `K_i * p` pieces where `K_i` is its total of per-bar segments, so the demand
      window `[P_i, C_i]` fixes `K_i = ceil(P_i / p)` as the smallest legal count;
    * `n` -- how many rounds that per-bar total is split across.  The piece count
      does not depend on the split at all, so `n` only has to keep every round
      inside the bed (`n * min_bed <= total <= n * cap_len`), and the smallest such
      `n` is the knife-minimal one because knives are `sum(K_i) + n`.

    Splitting is what the old solver missed: it delivered a scheme's whole demand
    in one round per piece-vector, so the bed floor forced `sum(K_i)` up.  Here
    `sum(K_i)` falls as `p` rises, and `p` is bounded only by the demand cap,
    the bed floor (`total >= 50`) and the bed weight.
    """
    orders = [model.orders[i] for i in ids]
    sizes = [o.size for o in orders]
    demands = [o.pieces for o in orders]
    caps = [model.caps[i] for i in ids]
    mu = orders[0].linear_weight
    if mu <= 0 or any(p < 1 for p in demands):
        return []
    m = len(ids)
    plimit = model.parallel_limit(ids)
    found = []

    for blank in model.catalogue(ids):
        usable = model.blank_length(ids, blank)
        if usable <= 0:
            continue
        for p in range(1, plimit + 1):
            ks_total, ok = [], True
            for i in range(m):
                k = _ceil(demands[i] / p)
                if k < 1:
                    k = 1
                if k * p > caps[i]:
                    ok = False
                    break
                ks_total.append(k)
            if not ok:
                continue
            # written lengths are what the checker and scorer see
            written = [round(k * s, 9) for k, s in zip(ks_total, sizes)]
            if any(w + cfg.round_trim > usable + _EPS for w in written):
                continue
            cap_len = min(cfg.bed_length, cfg.bed_weight / (p * mu))
            if cap_len < cfg.min_bed_length - _EPS:
                continue
            # Splitting K_i across n rounds puts `S_n = n * round_trim + S` metres on
            # the bed in total, and every round must hold [min_bed, cap_len], so
            #   n * (min_bed - round_trim) <= S <= n * (cap_len - round_trim).
            spread = sum(written)
            n_lo = max(1, _ceil(spread / (cap_len - cfg.round_trim)))
            n_hi = min(cfg.max_rounds,
                       int(math.floor(spread / (cfg.min_bed_length - cfg.round_trim) + _EPS)))
            if n_lo > n_hi:
                continue
            for n in range(n_lo, n_hi + 1):
                split = _split_rounds(ks_total, n, sizes, cfg.round_trim,
                                      cfg.min_bed_length, cap_len)
                if split is None:
                    continue
                rounds, declared, knives = [], 0.0, 0
                for ks in split:
                    lengths = {}
                    for i, k in enumerate(ks):
                        lengths[orders[i].oid] = round(k * sizes[i], 9)
                    length = cfg.round_trim + sum(lengths.values())
                    count = max(1, math.ceil(length * p / usable))
                    declared += count * blank.weight
                    knives += sum(int(v // sizes[i]) for i, v in enumerate(lengths.values())) + 1
                    rounds.append(dict(lengths=lengths, p=p, count=count))
                found.append(dict(knives=knives, declared=declared,
                                  blank_type=blank.bid, rounds=rounds))
    if not found:
        return []
    # Pareto: keep a plan only if no other is at least as good on both axes.
    items = sorted(found, key=lambda c: (c['knives'], c['declared']))
    out, best_declared = [], float('inf')
    for c in items:
        if c['declared'] < best_declared - 1e-6:
            out.append(c)
            best_declared = c['declared']
    return out


def _to_batch(ids, model, cfg, cand):
    orders = model.orders
    return {
        'orders': [orders[i].oid for i in ids],
        'length_scheme': [dict(r['lengths']) for r in cand['rounds']],
        'counts': [r['p'] for r in cand['rounds']],
        'blank_type': cand['blank_type'],
        'blank_counts': [r['count'] for r in cand['rounds']],
    }


def _blank_by_id(model, ids, bid):
    for b in model.catalogue(ids):
        if b.bid == bid:
            return b
    raise KeyError(bid)


def build(plan, model, cfg, *, lam, fallback_lam=0.0):
    """Rewrite `plan` picking, per scheme, `argmin(declared + lam * knives)`."""
    out, stats = [], dict(schemes=0, reshaped=0, no_candidates=0,
                          knives_before=0, knives_after=0,
                          declared_before=0.0, declared_after=0.0)
    for batch in plan:
        oids = batch['orders']
        ids = [model.lookup[o] for o in oids]
        cands = scheme_candidates(ids, model, cfg)
        old_knives = 0
        for scheme, p in zip(batch['length_scheme'], batch['counts']):
            old_knives += sum(int(round(v, 9) // model.orders[i].size)
                              for i, v in ((model.lookup[o], L) for o, L in scheme.items())) + 1
        old_declared = sum(c * _blank_by_id(model, ids, batch['blank_type']).weight
                           for c in batch['blank_counts'])
        stats['schemes'] += 1
        stats['knives_before'] += old_knives
        stats['declared_before'] += old_declared
        if not cands:
            stats['no_candidates'] += 1
            out.append(batch)
            stats['knives_after'] += old_knives
            stats['declared_after'] += old_declared
            continue
        best = min(cands, key=lambda c: c['declared'] + lam * c['knives'])
        out.append(_to_batch(ids, model, cfg, best))
        stats['reshaped'] += 1
        stats['knives_after'] += best['knives']
        stats['declared_after'] += best['declared']
    return out, stats


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('plan', type=Path)
    ap.add_argument('--data', default='data/semi')
    ap.add_argument('--out', type=Path, default=None)
    ap.add_argument('--lam', type=float, default=None,
                    help='kg of declared steel worth one knife; omitted -> --sweep')
    ap.add_argument('--sweep', action='store_true')
    ap.add_argument('--baseline-knives', type=float, default=90000.0)
    ap.add_argument('--limit', type=int, default=None, help='cap schemes processed')
    args = ap.parse_args()

    data = Path(args.data)
    settings = json.loads((data / 'competition.config.json').read_text(encoding='utf-8'))
    settings.update(continuity=True, coverage_shared=True, enforce_order_mass_floor=True,
                    objective='platform_score', baseline_knives=args.baseline_knives)
    cfg = Config(**settings)
    orders = load_orders(str(data / 'orders.normalized.csv'), cfg, skip_invalid=True)
    blanks = load_blanks(str(data / 'blanks.normalized.csv'))
    model = Model(orders, cfg, blanks)
    model.lookup = {o.oid: i for i, o in enumerate(orders)}

    raw = json.loads(args.plan.read_text(encoding='utf-8'))
    plan = raw['plan'] if isinstance(raw, dict) else raw
    if args.limit:
        plan = plan[:args.limit]
    print(f'plan: {args.plan}  schemes={len(plan)}')

    base = evaluate(plan, data, round_name='semi', baseline_knives=args.baseline_knives)
    print(f'BEFORE  knives={base["knives"]:,}  yield={base["yield_rate"]:.4f}  '
          f'coverage={base["coverage"]:.4f}  score={base["score_capped"]:.3f}  '
          f'viol={base["violation_count"]}')

    lams = [args.lam] if args.lam is not None else (
        [0.0, 25.0, 50.0, 100.0, 200.0, 400.0, 800.0, 1600.0, 3200.0] if args.sweep
        else [200.0])
    best = None
    for lam in lams:
        started = time.perf_counter()
        new_plan, stats = build(plan, model, cfg, lam=lam)
        elapsed = time.perf_counter() - started
        try:
            sc = evaluate(new_plan, data, round_name='semi',
                          baseline_knives=args.baseline_knives)
            line = (f'lam={lam:<7g} knives={sc["knives"]:,}  yield={sc["yield_rate"]:.4f}  '
                    f'coverage={sc["coverage"]:.4f}  score={sc["score_capped"]:.3f}  '
                    f'viol={sc["violation_count"]}  '
                    f'reshaped={stats["reshaped"]}/{stats["schemes"]}  ({elapsed:.0f}s)')
        except Exception as exc:                                        # noqa: BLE001
            sc = None
            line = f'lam={lam:<7g} FAILED {type(exc).__name__}: {exc}'
        print(line, flush=True)
        if sc is not None and (best is None or sc['score_capped'] > best[2]['score_capped']):
            best = (lam, new_plan, sc)

    if best is None:
        raise SystemExit('no candidate scored')
    lam, new_plan, sc = best
    print(f'\nBEST lam={lam:g}: score {base["score_capped"]:.3f} -> {sc["score_capped"]:.3f}  '
          f'knives {base["knives"]:,} -> {sc["knives"]:,}  '
          f'yield {base["yield_rate"]:.4f} -> {sc["yield_rate"]:.4f}')
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(new_plan, ensure_ascii=False,
                                       separators=(',', ':')) + '\n', encoding='utf-8')
        print(f'wrote {args.out}')


if __name__ == '__main__':
    main()
