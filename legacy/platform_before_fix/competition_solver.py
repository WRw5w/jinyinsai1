"""Real-data construction, warm starts and durable checkpoints for timed search."""
import argparse
import json
import math
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path

from solver import (Batch, Config, Model, ModelError, Round, _construct, _deadline,
                    _floor, _key, load_blanks, load_orders, search_10s, validate_plan)


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    os.replace(temp, path)


def now():
    return datetime.now(timezone.utc).isoformat()


class Factory:
    """Exact piece delivery using full rounds plus a one/two-round remainder.

    Only the construction is specialized. Every candidate is still checked by
    the same model, and the public JSON is independently validated on saving.
    """
    def __init__(self):
        self.pattern_cache = {}
        self.single_cache = {}

    def patterns(self, model, i, blank, deadline):
        o, cfg = model.orders[i], model.cfg
        key = (o.diameter, o.density, o.size, blank.bid)
        if key in self.pattern_cache:
            return self.pattern_cache[key]
        pool = {}
        kmin = max(1, math.ceil((cfg.min_bed_length - 2 * cfg.trim) / o.size - 1e-9))
        kmax = _floor((min(cfg.bed_length, model.blank_length((i,), blank)) - 2 * cfg.trim) / o.size)
        for k in range(kmin, kmax + 1):
            _deadline(deadline)
            length = k * o.size + 2 * cfg.trim
            pmax = min(model.parallel_limit((i,)), _floor(cfg.bed_weight / (length * o.linear_weight)))
            for p in range(1, pmax + 1):
                r = model.make_round((i,), (k,), p, blank)
                if r is None:
                    continue
                n = k * p
                if n not in pool or (r.knives, r.raw) < (pool[n].knives, pool[n].raw):
                    pool[n] = r
        # Favor high pieces/cut; material efficiency breaks ties.
        anchors = sorted(pool, key=lambda n: (-n / pool[n].knives, pool[n].raw / n))
        self.pattern_cache[key] = pool, anchors
        return pool, anchors

    def single(self, model, i, blank, rng, deadline, randomized):
        cache_key = (i, blank.bid)
        if not randomized and cache_key in self.single_cache:
            return self.single_cache[cache_key]
        pool, anchors = self.patterns(model, i, blank, deadline)
        if not pool:
            return None
        q = model.orders[i].pieces
        eligible = [n for n in anchors if n <= q]
        if not eligible:
            return None
        anchor = eligible[0] if not randomized else rng.choice(eligible[:max(1, len(eligible) // 3)])
        full, remainder = divmod(q, anchor)
        head = pool[anchor]

        def tail(need):
            if need == 0:
                return ()
            if need in pool:
                return (pool[need],)
            best, best_key = None, None
            # Integer lookup gives all two-round complements in O(patterns).
            for pos, n in enumerate(eligible):
                if pos % 64 == 0:
                    _deadline(deadline)
                other = need - n
                if other in pool:
                    rows = (pool[n], pool[other])
                    key = (sum(r.knives for r in rows), sum(r.raw for r in rows))
                    if best_key is None or key < best_key:
                        best, best_key = rows, key
            return best

        ending = tail(remainder)
        if ending is None and full:
            full -= 1
            ending = tail(remainder + anchor)
        if ending is None:
            # Small exact quantities can always use p=1 when they lie in the
            # sum of legal contiguous k intervals; avoid an invalid tiny tail.
            o, cfg = model.orders[i], model.cfg
            lo = max(1, math.ceil((cfg.min_bed_length - 2 * cfg.trim) / o.size - 1e-9))
            hi = _floor((min(cfg.bed_length, model.blank_length((i,), blank)) - 2 * cfg.trim) / o.size)
            nr = math.ceil(q / hi) if hi > 0 else cfg.max_rounds + 1
            if nr > cfg.max_rounds or nr * lo > q:
                return None
            base, extra = divmod(q, nr)
            ending = tuple(model.make_round((i,), (base + (j < extra),), 1, blank) for j in range(nr))
            if any(r is None for r in ending):
                return None
            full = 0
        rows = (head,) * full + ending
        if len(rows) > model.cfg.max_rounds:
            return None
        batch = Batch((i,), blank.bid, rows)
        old = self.single_cache.get(cache_key)
        if old is None or _key(batch.metrics, model.cfg, 1) < _key(old.metrics, model.cfg, 1):
            self.single_cache[cache_key] = batch
        return batch

    def __call__(self, model, ids, rng, deadline, randomized=False):
        ids = tuple(sorted(ids))
        candidates = []
        for blank in model.catalogue(ids):
            _deadline(deadline)
            singles = [self.single(model, i, blank, rng, deadline, randomized) for i in ids]
            if any(b is None for b in singles):
                continue
            # Fuse rows with equal parallel counts. Combining two segments of
            # the same order also removes their redundant trim allowance.
            rows = []
            for j, batch in enumerate(singles):
                for r in batch.rounds:
                    ks = tuple(r.ks[0] if pos == j else 0 for pos in range(len(ids)))
                    nr = Round(ks, r.parallel, r.blanks, r.knives, r.finished, r.raw)
                    merged = False
                    for pos, old in enumerate(rows):
                        if old.parallel == nr.parallel:
                            joint = model.make_round(ids, tuple(a + b for a, b in zip(old.ks, nr.ks)), nr.parallel, blank)
                            if joint is not None:
                                rows[pos] = joint
                                merged = True
                                break
                    if not merged:
                        rows.append(nr)
            if len(rows) <= model.cfg.max_rounds:
                candidates.append(Batch(ids, blank.bid, tuple(rows)))
        if len(ids) > 1 and randomized and rng.random() < 0.3:
            trial = _construct(model, ids, rng, deadline, True)
            if trial is not None:
                candidates.append(trial)
        return min(candidates, key=lambda b: _key(b.metrics, model.cfg, len(ids))) if candidates else None


def run(args):
    cfg = Config(**json.loads(Path(args.config).read_text(encoding='utf-8')))
    orders = load_orders(args.orders, cfg)
    blanks = load_blanks(args.blanks)
    initial = json.loads(Path(args.initial).read_text(encoding='utf-8')) if args.initial else None
    factory = Factory()
    started = time.perf_counter()
    output = Path(args.output)
    audit = json.loads(Path(args.audit).read_text(encoding='utf-8')) if args.audit else {}

    def save(plan, elapsed, iterations, state='running'):
        metrics = validate_plan(plan, orders, cfg, blanks)
        metrics.update(state=state, elapsed_seconds=elapsed, budget_seconds=args.seconds,
                       iterations=iterations, updated_at=now(), pid=os.getpid(),
                       original_orders=audit.get('source_orders', len(orders)),
                       rejected_orders=[r['order_id'] for r in audit.get('rejected', [])],
                       submission_ready=False, optimality='not_certified',
                       note='Validated for supplied constraints and documented model assumptions; unresolved source anomaly/unspecified process rules remain.')
        atomic_json(output, plan)
        atomic_json(output.with_suffix('.metrics.json'), metrics)
        print(json.dumps(metrics, ensure_ascii=False), flush=True)

    # Warm-start file is already a usable result, even before the first search checkpoint.
    if initial is not None:
        save(initial, 0.0, 0)
    latest_iterations = 0
    stats = {}

    def checkpoint(plan, elapsed, iterations):
        nonlocal latest_iterations
        latest_iterations = iterations
        save(plan, elapsed, iterations)

    plan = search_10s(orders, cfg, seconds=args.seconds, seed=args.seed, blanks=blanks,
                      initial_plan=initial, constructor=factory, checkpoint=checkpoint,
                      checkpoint_interval=args.checkpoint_seconds, stats=stats)
    save(plan, time.perf_counter() - started, stats.get('iterations', latest_iterations), 'completed')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--orders', default='data/orders.normalized.csv')
    ap.add_argument('--blanks', default='data/blanks.normalized.csv')
    ap.add_argument('--config', default='data/competition.config.json')
    ap.add_argument('--audit', default='data/data_audit.json')
    ap.add_argument('--seconds', type=float, default=10)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--initial')
    ap.add_argument('--output', default='runs/quick_10s/result.json')
    ap.add_argument('--checkpoint-seconds', type=float, default=60)
    args = ap.parse_args()
    run(args)


if __name__ == '__main__':
    main()
