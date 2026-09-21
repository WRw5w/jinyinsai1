#!/usr/bin/env python3
"""Exact enumeration and time-budgeted bar cutting; standard library only.

Optimality applies to the explicit finite model in README.md. The competition's
constraint.txt is absent; this is not a claim of official competition feasibility.
"""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import random
import sys
import time
from dataclasses import dataclass
from functools import lru_cache


class ModelError(ValueError):
    pass


class InfeasibleError(ModelError):
    """Exhaustive enumeration proves infeasibility in the configured model."""


class ExactLimitError(RuntimeError):
    pass


class SearchTimeout(RuntimeError):
    pass


def _positive(x, name):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or x <= 0:
        raise ModelError(f"{name} must be finite and positive")


def _integer(x, name):
    if type(x) is not int or x <= 0:
        raise ModelError(f"{name} must be a positive integer")


def _ceil(x):
    return math.ceil(x - 1e-10 * max(1, abs(x)))


def _floor(x):
    return math.floor(x + 1e-10 * max(1, abs(x)))


@dataclass
class Config:
    bed_length: float = 120.0        # m, per round
    bed_width: int = 100             # parallel bar count (legacy name)
    bed_width_mm: float | None = None
    bar_gap_mm: float = 0.0
    bed_weight: float = 1e18         # kg actually placed on the bed
    min_bed_length: float = 0.0
    min_bed_weight: float = 0.0
    trim: float = 1.0                # m at EACH end of EACH order segment
    blank_length: float = 120.0      # fallback usable rolled length, m
    blank_weight: float | None = None
    rolling_yield: float = 1.0
    max_rounds: int = 20             # per combination scheme
    weight_scale: float = 1.0        # order CSV: kg=1, tonnes=1000
    max_overproduction_ratio: float = 0.0  # above rounded-up piece demand
    objective: str = "lex"           # lex or score
    baseline_knives: float | None = None
    exact_max_orders: int = 10       # per steel/diameter group
    exact_max_work: int = 1000000    # explicit failure, never false optimality
    search_max_group: int = 8

    def validate(self):
        for name in ("bed_length", "bed_weight", "blank_length", "weight_scale"):
            _positive(getattr(self, name), name)
        for name in ("bed_width", "max_rounds", "exact_max_orders", "exact_max_work", "search_max_group"):
            _integer(getattr(self, name), name)
        for name in ("trim", "bar_gap_mm", "min_bed_length", "min_bed_weight", "max_overproduction_ratio"):
            x = getattr(self, name)
            if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or x < 0:
                raise ModelError(f"{name} must be finite and nonnegative")
        if self.min_bed_length > self.bed_length or self.min_bed_weight > self.bed_weight:
            raise ModelError("Bed lower bound exceeds upper bound")
        for name in ("bed_width_mm", "blank_weight", "baseline_knives"):
            if getattr(self, name) is not None:
                _positive(getattr(self, name), name)
        if not isinstance(self.rolling_yield, (int, float)) or not 0 < self.rolling_yield <= 1:
            raise ModelError("rolling_yield must be in (0, 1]")
        if self.objective not in ("lex", "score"):
            raise ModelError("objective must be lex or score")
        if self.objective == "score" and self.baseline_knives is None:
            raise ModelError("baseline_knives is required for objective=score")


@dataclass(frozen=True)
class Order:
    oid: str
    steel: str
    diameter: float                 # mm
    size: float                     # m
    weight: float                   # kg
    density: float                  # kg/m3
    pieces: int

    @property
    def linear_weight(self):
        return math.pi * (self.diameter / 1000) ** 2 / 4 * self.density


@dataclass(frozen=True)
class Blank:
    bid: int
    weight: float                   # kg before rolling
    usable_length: float | None = None


@dataclass(frozen=True)
class Round:
    ks: tuple[int, ...]             # aligned with Batch.ids
    parallel: int
    blanks: int
    knives: int
    finished: float
    raw: float


@dataclass(frozen=True)
class Batch:
    ids: tuple[int, ...]
    blank_type: int
    rounds: tuple[Round, ...]

    @property
    def metrics(self):
        return (sum(r.knives for r in self.rounds), sum(r.finished for r in self.rounds),
                sum(r.raw for r in self.rounds), len(self.ids) if len(self.ids) > 1 else 0)


def _pick(row, names, default=None):
    for name in names:
        if row.get(name) not in (None, ""):
            return row[name].strip()
    if default is not None:
        return default
    raise ModelError(f"Missing CSV field: {' / '.join(names)}")


def load_orders(path: str, cfg: Config) -> list[Order]:
    cfg.validate()
    orders = []
    with open(path, encoding="utf-8-sig", newline="") as stream:
        for line, row in enumerate(csv.DictReader(stream), 2):
            try:
                oid = _pick(row, ["order_id", "订单号", "订单编号", "id"])
                steel = _pick(row, ["steel", "steel_grade", "钢种", "订单钢种"])
                dia = float(_pick(row, ["diameter", "直径", "规格", "订单直径"]))
                size = float(_pick(row, ["length", "size", "定尺长度", "定尺"]))
                weight = float(_pick(row, ["weight", "重量", "需求重量", "订单重量"])) * cfg.weight_scale
                density = float(_pick(row, ["density", "密度"]))
                for name, value in (("diameter", dia), ("length", size), ("weight", weight), ("density", density)):
                    _positive(value, name)
                pieces = max(1, _ceil(weight / (math.pi * (dia / 1000) ** 2 / 4 * density * size)))
                orders.append(Order(oid, steel, dia, size, weight, density, pieces))
            except (ValueError, TypeError) as exc:
                raise ModelError(f"Order CSV line {line}: {exc}") from exc
    _validate_orders(orders)
    return orders


def load_blanks(path: str) -> list[Blank]:
    result = []
    with open(path, encoding="utf-8-sig", newline="") as stream:
        for line, row in enumerate(csv.DictReader(stream), 2):
            try:
                bid = int(_pick(row, ["blank_type", "blank_id", "id", "钢坯类型"], str(line - 1)))
                width = float(_pick(row, ["width", "width_mm", "宽度"]))
                thickness = float(_pick(row, ["thickness", "thickness_mm", "厚度"]))
                length = float(_pick(row, ["length", "length_m", "长度"]))
                density = float(_pick(row, ["density", "密度"]))
                for name, value in (("width", width), ("thickness", thickness), ("length", length), ("density", density)):
                    _positive(value, name)
                result.append(Blank(bid, width / 1000 * thickness / 1000 * length * density))
            except (ValueError, TypeError) as exc:
                raise ModelError(f"Blank CSV line {line}: {exc}") from exc
    _validate_blanks(result)
    return result


def _validate_orders(orders):
    seen, densities = set(), {}
    for o in orders:
        if not isinstance(o.oid, str) or not o.oid.strip() or o.oid in seen:
            raise ModelError(f"Empty or duplicate order ID: {o.oid!r}")
        if not isinstance(o.steel, str) or not o.steel.strip():
            raise ModelError(f"Empty steel grade: {o.oid}")
        seen.add(o.oid)
        for name in ("diameter", "size", "weight", "density"):
            _positive(getattr(o, name), f"{o.oid}.{name}")
        _integer(o.pieces, f"{o.oid}.pieces")
        if o.pieces != max(1, _ceil(o.weight / (o.linear_weight * o.size))):
            raise ModelError(f"pieces disagrees with demand weight: {o.oid}")
        key = (o.steel, o.diameter)
        if key in densities and densities[key] != o.density:
            raise ModelError(f"Inconsistent density within steel/diameter group: {key}")
        densities[key] = o.density


def _validate_blanks(blanks):
    if not blanks:
        raise ModelError("Blank catalogue is empty")
    seen = set()
    for b in blanks:
        _integer(b.bid, "blank_type")
        _positive(b.weight, "blank weight")
        if b.bid in seen:
            raise ModelError(f"Duplicate blank_type: {b.bid}")
        if b.usable_length is not None:
            _positive(b.usable_length, "usable_length")
        seen.add(b.bid)


def _groups(orders):
    result = {}
    for i, o in enumerate(orders):
        result.setdefault((o.steel, o.diameter), []).append(i)
    return list(result.values())


class Model:
    def __init__(self, orders, cfg, blanks=None):
        cfg.validate()
        _validate_orders(orders)
        if blanks is not None:
            _validate_blanks(blanks)
        self.orders, self.cfg, self.blanks = orders, cfg, blanks
        self.caps = [o.pieces + _floor(o.pieces * cfg.max_overproduction_ratio) for o in orders]

    def catalogue(self, ids):
        if self.blanks is not None:
            return self.blanks
        linear = self.orders[ids[0]].linear_weight
        weight = self.cfg.blank_weight or self.cfg.blank_length * linear / self.cfg.rolling_yield
        return [Blank(1, weight, self.cfg.blank_length)]

    def parallel_limit(self, ids):
        cfg, limit = self.cfg, self.cfg.bed_width
        if cfg.bed_width_mm is not None:
            dia = self.orders[ids[0]].diameter
            limit = min(limit, _floor((cfg.bed_width_mm + cfg.bar_gap_mm) / (dia + cfg.bar_gap_mm)))
        return limit

    def blank_length(self, ids, blank):
        length = blank.weight * self.cfg.rolling_yield / self.orders[ids[0]].linear_weight
        return min(length, blank.usable_length) if blank.usable_length is not None else length

    def make_round(self, ids, ks, parallel, blank):
        cfg = self.cfg
        if not any(ks) or parallel < 1 or parallel > self.parallel_limit(ids):
            return None
        lengths = [k * self.orders[i].size + 2 * cfg.trim for i, k in zip(ids, ks) if k]
        total = sum(lengths)
        linear = self.orders[ids[0]].linear_weight
        mass = total * parallel * linear
        if not cfg.min_bed_length - 1e-8 <= total <= cfg.bed_length + 1e-8:
            return None
        if not cfg.min_bed_weight - 1e-8 <= mass <= cfg.bed_weight + 1e-8:
            return None
        usable = self.blank_length(ids, blank)
        if max(lengths) > usable + 1e-8:
            return None
        count = max(1, _ceil(total * parallel / usable))
        finished = sum(k * parallel * self.orders[i].size * linear for i, k in zip(ids, ks))
        # Each segment is trimmed independently: k-1 separation cuts + 2 trims.
        return Round(tuple(ks), parallel, count, sum(k + 1 for k in ks if k), finished, count * blank.weight)


def _add(a, b):
    return tuple(x + y for x, y in zip(a, b))


def _key(metrics, cfg, n):
    knives, finished, raw, covered = metrics
    # Do not turn floating-point summation noise into a better yield/score.
    yield_rate = round(finished / raw, 12) if raw else 0.0
    coverage = covered / n if n else 0.0
    lex = (knives, -yield_rate, -coverage)
    if cfg.objective == "lex":
        return lex
    score = (40 * cfg.baseline_knives / knives if knives else 0) + 40 * yield_rate + 20 * coverage
    return (-round(score, 12),) + lex


def _totals(batches):
    result = (0, 0.0, 0.0, 0)
    for batch in batches:
        result = _add(result, batch.metrics)
    return result


def _dominates(a, b):
    return a[0] <= b[0] and a[1] >= b[1] and a[2] <= b[2] and a[3] >= b[3]


def _insert(frontier, item, metrics):
    value = metrics(item)
    if any(_dominates(metrics(old), value) for old in frontier):
        return
    frontier[:] = [old for old in frontier if not _dominates(value, metrics(old))]
    frontier.append(item)


class WorkLimit:
    def __init__(self, limit):
        self.limit, self.work = limit, 0

    def tick(self):
        self.work += 1
        if self.work > self.limit:
            raise ExactLimitError(f"Exact work limit {self.limit} exceeded; no optimality claim. Increase exact_max_work or use search.")


def _exact_batches(model, ids, work):
    """All patterns, then all production states up to max_rounds.

    Equal (round count, production vector) labels are pruned only when both
    knives and raw mass are no better. Finished mass is fixed for such a state.
    """
    cfg = model.cfg
    caps = tuple(model.caps[i] for i in ids)
    demand = tuple(model.orders[i].pieces for i in ids)
    output = []
    for blank in model.catalogue(ids):
        patterns = []
        for parallel in range(1, min(model.parallel_limit(ids), max(caps)) + 1):
            maxima = [min(cap // parallel, max(0, _floor((min(cfg.bed_length, model.blank_length(ids, blank)) - 2 * cfg.trim) / model.orders[i].size))) for i, cap in zip(ids, caps)]

            def enumerate_k(pos, used, ks):
                work.tick()
                if pos == len(ids):
                    r = model.make_round(ids, ks, parallel, blank)
                    if r is not None:
                        patterns.append(r)
                    return
                o = model.orders[ids[pos]]
                bound = min(maxima[pos], max(0, _floor((cfg.bed_length - used - 2 * cfg.trim) / o.size)))
                for k in range(bound + 1):
                    enumerate_k(pos + 1, used + (k * o.size + 2 * cfg.trim if k else 0), ks + (k,))

            enumerate_k(0, 0.0, ())
        # Label: (knives, finished, raw, coverage=0, tuple of rounds).
        states = {tuple(0 for _ in ids): [(0, 0.0, 0.0, 0, ())]}
        for _ in range(min(cfg.max_rounds, sum(caps))):
            next_states = {}
            for produced, labels in states.items():
                for r in patterns:
                    work.tick()
                    new = tuple(v + k * r.parallel for v, k in zip(produced, r.ks))
                    if any(v > cap for v, cap in zip(new, caps)):
                        continue
                    bucket = next_states.setdefault(new, [])
                    for label in labels:
                        work.tick()
                        finished = sum(v * model.orders[i].size * model.orders[i].linear_weight for i, v in zip(ids, new))
                        item = (label[0] + r.knives, finished, label[2] + r.raw, 0, label[4] + (r,))
                        _insert(bucket, item, lambda x: x[:4])
            for produced, labels in next_states.items():
                if all(v >= d for v, d in zip(produced, demand)):
                    for label in labels:
                        _insert(output, Batch(ids, blank.bid, label[4]), lambda x: x.metrics)
            states = next_states
            if not states:
                break
    return output


def brute_force(orders: list[Order], cfg: Config, blanks=None) -> list[dict]:
    """Optimal within the configured model, or an explicit error.

    Enumerates rounds, production states and all partitions. Retains global
    Pareto alternatives: independently optimizing groups is wrong for a ratio.
    """
    model = Model(orders, cfg, blanks)
    work = WorkLimit(cfg.exact_max_work)
    global_frontier = [((0, 0.0, 0.0, 0), ())]
    for group in _groups(orders):
        if len(group) > cfg.exact_max_orders:
            raise ExactLimitError(f"Group has {len(group)} orders; exact_max_orders={cfg.exact_max_orders}")

        @lru_cache(None)
        def batches(mask):
            return _exact_batches(model, tuple(group[j] for j in range(len(group)) if mask >> j & 1), work)

        @lru_cache(None)
        def partition(mask):
            if not mask:
                return [((0, 0.0, 0.0, 0), ())]
            anchor = mask & -mask
            subset, frontier = mask, []
            while subset:
                work.tick()
                if subset & anchor:
                    for batch in batches(subset):
                        for metrics, rest in partition(mask ^ subset):
                            work.tick()
                            _insert(frontier, (_add(batch.metrics, metrics), (batch,) + rest), lambda x: x[0])
                subset = (subset - 1) & mask
            return frontier

        group_frontier = partition((1 << len(group)) - 1)
        if not group_frontier:
            raise InfeasibleError(f"No feasible plan in configured model: {[orders[i].oid for i in group]}")
        combined = []
        for left, right in itertools.product(global_frontier, group_frontier):
            work.tick()
            _insert(combined, (_add(left[0], right[0]), left[1] + right[1]), lambda x: x[0])
        global_frontier = combined
    result = min(global_frontier, key=lambda x: _key(x[0], cfg, len(orders)))[1]
    plan = _export(result, orders, cfg)
    validate_plan(plan, orders, cfg, blanks)
    return plan


def _deadline(deadline):
    if time.perf_counter() >= deadline:
        raise SearchTimeout("Time budget exhausted before a complete feasible plan was found")


def _construct(model, ids, rng, deadline, randomized=False):
    """Pack rounds using several parallel counts and varying order priorities."""
    cfg, ids = model.cfg, tuple(sorted(ids))
    best = None
    for blank in model.catalogue(ids):
        _deadline(deadline)
        remaining = [model.orders[i].pieces for i in ids]
        produced, rounds = [0] * len(ids), []
        for _ in range(cfg.max_rounds):
            _deadline(deadline)
            if not any(v > 0 for v in remaining):
                break
            limit = min(model.parallel_limit(ids), max(model.caps[i] - v for i, v in zip(ids, produced)))
            if limit <= 0:
                break
            choices = {1, limit}
            for i, needed in zip(ids, remaining):
                if needed > 0:
                    kmax = max(1, _floor((min(cfg.bed_length, model.blank_length(ids, blank)) - 2 * cfg.trim) / model.orders[i].size))
                    choices.update((min(limit, needed), min(limit, max(1, _ceil(needed / kmax)))))
            if randomized:
                choices.add(rng.randint(1, limit))
            priority = list(range(len(ids)))
            if randomized:
                rng.shuffle(priority)
            else:
                priority.sort(key=lambda j: remaining[j] * model.orders[ids[j]].size, reverse=True)
            candidates = []
            for parallel in sorted(choices):
                _deadline(deadline)
                ks, used = [0] * len(ids), 0.0
                length_limit = min(cfg.bed_length, cfg.bed_weight / (parallel * model.orders[ids[0]].linear_weight))
                for j in priority:
                    if remaining[j] <= 0:
                        continue
                    i = ids[j]
                    available = min(length_limit - used, model.blank_length(ids, blank))
                    kmax = min((model.caps[i] - produced[j]) // parallel, max(0, _floor((available - 2 * cfg.trim) / model.orders[i].size)))
                    k = min(kmax, max(1, _ceil(remaining[j] / parallel)))
                    if randomized and k > 1 and rng.random() < 0.2:
                        k = rng.randint(1, k)
                    if k > 0:
                        ks[j] = k
                        used += k * model.orders[i].size + 2 * cfg.trim
                r = model.make_round(ids, ks, parallel, blank)
                if r is not None:
                    useful = sum(min(max(0, remaining[j]), k * parallel) * model.orders[i].size for j, (i, k) in enumerate(zip(ids, ks)))
                    candidates.append((useful / r.knives, useful / r.raw, r))
            if not candidates:
                break
            r = rng.choice(candidates)[2] if randomized and rng.random() < 0.25 else max(candidates, key=lambda x: x[:2])[2]
            rounds.append(r)
            for j, k in enumerate(r.ks):
                produced[j] += k * r.parallel
                remaining[j] -= k * r.parallel
        if all(v <= 0 for v in remaining):
            candidate = Batch(ids, blank.bid, tuple(rounds))
            if best is None or _key(candidate.metrics, cfg, len(ids)) < _key(best.metrics, cfg, len(ids)):
                best = candidate
    return best


def search_10s(orders: list[Order], cfg: Config, seconds=10.0, seed=1, blanks=None,
               initial_plan=None, constructor=None, checkpoint=None, checkpoint_interval=60.0,
               stats=None) -> list[dict]:
    """Feasible incumbent + merge/split/move/swap/repack + annealing.

    Budget includes initialization, with a size-based reserve for export and
    validation. Cooperative checks are not an OS hard real-time guarantee.
    """
    started = time.perf_counter()
    _positive(seconds, "seconds")
    reserve = min(seconds * 0.2, 0.01 + len(orders) * 0.00005)
    deadline = started + seconds - reserve
    model = Model(orders, cfg, blanks)
    if not orders:
        return []
    rng = random.Random(seed)
    construct = constructor or _construct
    buckets, current, next_id = [], {}, 0
    initial_batches = _import_plan(initial_plan, model) if initial_plan is not None else None
    if initial_batches is not None:
        by_group = {}
        for batch in initial_batches:
            o = orders[batch.ids[0]]
            by_group.setdefault((o.steel, o.diameter), []).append(batch)
        for values in by_group.values():
            bucket = []
            for batch in values:
                current[next_id] = batch
                bucket.append(next_id)
                next_id += 1
            buckets.append(bucket)
    for group in ([] if initial_batches is not None else _groups(orders)):
        bucket, singles = [], []
        for i in group:
            _deadline(deadline)
            single = construct(model, (i,), rng, deadline)
            if single is None:
                singles = []
                break
            singles.append(single)
        if not singles:
            combined = None
            if len(group) <= cfg.search_max_group:
                for attempt in range(32):
                    combined = construct(model, tuple(group), rng, deadline, attempt > 0)
                    if combined is not None:
                        break
            if combined is None:
                raise ModelError(f"Search found no feasible initial group: {[orders[i].oid for i in group[:8]]}; not a proof of infeasibility")
            singles = [combined]
        for single in singles:
            current[next_id] = single
            bucket.append(next_id)
            next_id += 1
        buckets.append(bucket)
    current_metrics = _totals(current.values())
    best_metrics, best = current_metrics, dict(current)
    # Accumulate edits since the last incumbent instead of copying/scoring 20k
    # batches whenever a two-batch neighborhood improves the plan.
    pending = {}
    iterations = 0
    next_checkpoint = time.perf_counter() + checkpoint_interval
    if checkpoint:
        checkpoint(_export(best.values(), orders, cfg), time.perf_counter() - started, iterations)
    try:
        while True:
            _deadline(deadline)
            iterations += 1
            if checkpoint and time.perf_counter() >= next_checkpoint:
                checkpoint(_export(best.values(), orders, cfg), time.perf_counter() - started, iterations)
                next_checkpoint = time.perf_counter() + checkpoint_interval
            bucket = rng.choice(buckets)
            a_pos = rng.randrange(len(bucket))
            positions = [a_pos]
            a = current[bucket[a_pos]]
            move = rng.randrange(5)
            if len(bucket) > 1 and move in (0, 2, 3):
                b_pos = rng.randrange(len(bucket) - 1)
                if b_pos >= a_pos:
                    b_pos += 1
                b = current[bucket[b_pos]]
                positions.append(b_pos)
                if move == 0:
                    parts = [a.ids + b.ids]
                elif move == 2:
                    left, right = list(a.ids), list(b.ids)
                    right.append(left.pop(rng.randrange(len(left))))
                    parts = [p for p in (left, right) if p]
                else:
                    left, right = list(a.ids), list(b.ids)
                    j, k = rng.randrange(len(left)), rng.randrange(len(right))
                    left[j], right[k] = right[k], left[j]
                    parts = [left, right]
            elif move == 1 and len(a.ids) > 1:
                union = list(a.ids)
                rng.shuffle(union)
                cut = rng.randrange(1, len(union))
                parts = [union[:cut], union[cut:]]
            else:
                parts = [a.ids]
            if any(len(part) > cfg.search_max_group for part in parts):
                continue
            replacements = [construct(model, tuple(part), rng, deadline, True) for part in parts]
            if any(batch is None for batch in replacements):
                continue
            before = _totals(current[bucket[pos]] for pos in positions)
            after = _totals(replacements)
            proposed = tuple(x - y + z for x, y, z in zip(current_metrics, before, after))
            key = _key(proposed, cfg, len(orders))
            improved = key < _key(current_metrics, cfg, len(orders))
            temperature = max(0.05, 2 * (deadline - time.perf_counter()) / seconds)
            loss = max(0, proposed[0] - current_metrics[0]) if cfg.objective == "lex" else max(0, key[0] - _key(current_metrics, cfg, len(orders))[0])
            if improved or rng.random() < 0.04 * math.exp(-loss / temperature):
                for pos in sorted(positions, reverse=True):
                    removed_id = bucket[pos]
                    del current[removed_id]
                    if removed_id in best:
                        pending[removed_id] = None
                    else:
                        pending.pop(removed_id, None)
                    bucket[pos] = bucket[-1]
                    bucket.pop()
                for batch in replacements:
                    current[next_id] = batch
                    pending[next_id] = batch
                    bucket.append(next_id)
                    next_id += 1
                current_metrics = proposed
                if _key(current_metrics, cfg, len(orders)) < _key(best_metrics, cfg, len(orders)):
                    for changed_id, batch in pending.items():
                        if batch is None:
                            best.pop(changed_id, None)
                        else:
                            best[changed_id] = batch
                    pending.clear()
                    best_metrics = current_metrics
    except SearchTimeout:
        pass
    plan = _export(best.values(), orders, cfg)
    validate_plan(plan, orders, cfg, blanks)
    if stats is not None:
        stats.update(iterations=iterations, elapsed_seconds=time.perf_counter() - started)
    return plan


def _import_plan(plan, model):
    validate_plan(plan, model.orders, model.cfg, model.blanks)
    lookup = {o.oid: i for i, o in enumerate(model.orders)}
    output = []
    for entry in plan:
        ids = tuple(lookup[n] for n in entry['orders'])
        blank = next(b for b in model.catalogue(ids) if b.bid == entry['blank_type'])
        rounds = []
        for lengths, p, count in zip(entry['length_scheme'], entry['counts'], entry['blank_counts']):
            ks = tuple(round((lengths[model.orders[i].oid] - 2 * model.cfg.trim) / model.orders[i].size)
                       if model.orders[i].oid in lengths else 0 for i in ids)
            r = model.make_round(ids, ks, p, blank)
            rounds.append(Round(r.ks, r.parallel, count, r.knives, r.finished, count * blank.weight))
        output.append(Batch(ids, blank.bid, tuple(rounds)))
    return output


def _export(batches, orders, cfg):
    return [{"orders": [orders[i].oid for i in b.ids],
             "length_scheme": [{orders[i].oid: k * orders[i].size + 2 * cfg.trim for i, k in zip(b.ids, r.ks) if k} for r in b.rounds],
             "counts": [r.parallel for r in b.rounds], "blank_type": b.blank_type,
             "blank_counts": [r.blanks for r in b.rounds]}
            for b in sorted(batches, key=lambda b: b.ids)]


def validate_plan(plan, orders, cfg, blanks=None):
    """Recompute constraints and costs from public JSON, independently of make_round."""
    model = Model(orders, cfg, blanks)
    lookup = {o.oid: i for i, o in enumerate(orders)}
    seen, knives, finished, raw, covered, rounds = set(), 0, 0.0, 0.0, 0, 0
    if not isinstance(plan, list):
        raise ModelError("Plan must be a JSON array")
    for batch in plan:
        if not isinstance(batch, dict) or set(batch) != {"orders", "length_scheme", "counts", "blank_type", "blank_counts"}:
            raise ModelError("Invalid batch fields")
        names = batch["orders"]
        if not isinstance(names, list) or not names or any(not isinstance(n, str) for n in names):
            raise ModelError("orders must be a nonempty string list")
        if len(set(names)) != len(names) or any(n not in lookup or n in seen for n in names):
            raise ModelError("Unknown, duplicate or cross-scheme order")
        seen.update(names)
        ids = tuple(lookup[n] for n in names)
        if len({(orders[i].steel, orders[i].diameter) for i in ids}) != 1:
            raise ModelError("Mixed steel/diameter in a batch")
        schemes, counts, blank_counts = (batch[k] for k in ("length_scheme", "counts", "blank_counts"))
        if not all(isinstance(x, list) for x in (schemes, counts, blank_counts)):
            raise ModelError("Round fields must be lists")
        if not 1 <= len(schemes) <= cfg.max_rounds or not len(schemes) == len(counts) == len(blank_counts):
            raise ModelError("Invalid round count or mismatched arrays")
        _integer(batch["blank_type"], "blank_type")
        blank = next((b for b in model.catalogue(ids) if b.bid == batch["blank_type"]), None)
        if blank is None:
            raise ModelError("Unknown blank_type")
        produced = {n: 0 for n in names}
        linear = orders[ids[0]].linear_weight
        usable = blank.weight * cfg.rolling_yield / linear
        if blank.usable_length is not None:
            usable = min(usable, blank.usable_length)
        for scheme, parallel, blank_count in zip(schemes, counts, blank_counts):
            _integer(parallel, "counts")
            _integer(blank_count, "blank_counts")
            if parallel > cfg.bed_width:
                raise ModelError("Parallel count exceeds bed_width")
            if cfg.bed_width_mm is not None and parallel * orders[ids[0]].diameter + (parallel - 1) * cfg.bar_gap_mm > cfg.bed_width_mm + 1e-8:
                raise ModelError("Physical bed width exceeded")
            if not isinstance(scheme, dict) or not scheme or any(n not in produced for n in scheme):
                raise ModelError("Invalid length_scheme order")
            total = 0.0
            for name, length in scheme.items():
                _positive(length, "segment length")
                o = orders[lookup[name]]
                ratio = (length - 2 * cfg.trim) / o.size
                k = round(ratio)
                if k < 1 or not math.isclose(ratio, k, rel_tol=1e-10, abs_tol=1e-8):
                    raise ModelError("Segment is not an integer multiple plus trims")
                if length > usable + 1e-8:
                    raise ModelError("Segment exceeds usable length from one blank")
                produced[name] += k * parallel
                total += length
                finished += k * parallel * o.size * linear
                knives += k + 1
            mass = total * parallel * linear
            if not cfg.min_bed_length - 1e-8 <= total <= cfg.bed_length + 1e-8:
                raise ModelError("Bed length violation")
            if not cfg.min_bed_weight - 1e-8 <= mass <= cfg.bed_weight + 1e-8:
                raise ModelError("Bed weight violation")
            if blank_count * usable + 1e-8 < total * parallel:
                raise ModelError("Insufficient blanks for the parallel bars")
            raw += blank_count * blank.weight
            rounds += 1
        for name, count in produced.items():
            i = lookup[name]
            if not orders[i].pieces <= count <= model.caps[i]:
                raise ModelError(f"Demand/overproduction violation for {name}: {count}, allowed [{orders[i].pieces}, {model.caps[i]}]")
        covered += len(names) if len(names) > 1 else 0
    if seen != set(lookup):
        raise ModelError(f"Missing orders: {sorted(set(lookup) - seen)[:10]}")
    if finished > raw + 1e-7 * max(1, raw):
        raise ModelError("Finished mass exceeds raw mass")
    result = {"orders": len(orders), "plans": len(plan), "rounds": rounds, "knives": knives,
              "finished_weight": finished, "blank_weight": raw, "yield_rate": finished / raw if raw else 0.0,
              "coverage": covered / len(orders) if orders else 0.0, "objective": cfg.objective}
    if cfg.baseline_knives is not None:
        result["pdf_score"] = (40 * cfg.baseline_knives / knives if knives else 0) + 40 * result["yield_rate"] + 20 * result["coverage"]
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--orders", required=True)
    ap.add_argument("--blanks", help="Blank CSV: width/thickness mm, length m, density kg/m3")
    ap.add_argument("--output", default="result.json")
    ap.add_argument("--mode", choices=["brute", "search"], default="search")
    ap.add_argument("--seconds", type=float, default=10)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--config")
    ap.add_argument("--objective", choices=["lex", "score"])
    ap.add_argument("--baseline-knives", type=float)
    ap.add_argument("--validate", metavar="RESULT_JSON", help="Validate without optimizing")
    ap.add_argument("--metrics", help="Optional validation metrics JSON")
    args = ap.parse_args()
    try:
        settings = {}
        if args.config:
            with open(args.config, encoding="utf-8-sig") as stream:
                settings = json.load(stream)
        if args.objective:
            settings["objective"] = args.objective
        if args.baseline_knives is not None:
            settings["baseline_knives"] = args.baseline_knives
        cfg = Config(**settings)
        orders = load_orders(args.orders, cfg)
        blanks = load_blanks(args.blanks) if args.blanks else None
        start = time.perf_counter()
        if args.validate:
            with open(args.validate, encoding="utf-8-sig") as stream:
                plan = json.load(stream)
        elif args.mode == "brute":
            plan = brute_force(orders, cfg, blanks)
        else:
            plan = search_10s(orders, cfg, args.seconds, args.seed, blanks)
        elapsed = time.perf_counter() - start
        metrics = validate_plan(plan, orders, cfg, blanks)
        metrics.update(elapsed_seconds=elapsed, optimality="not_certified" if args.validate or args.mode == "search" else "proven_in_configured_model")
        if not args.validate:
            with open(args.output, "w", encoding="utf-8") as stream:
                json.dump(plan, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write("\n")
        if args.metrics:
            with open(args.metrics, "w", encoding="utf-8") as stream:
                json.dump(metrics, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write("\n")
        print(json.dumps(metrics, ensure_ascii=False, allow_nan=False))
    except (ModelError, ExactLimitError, SearchTimeout, OSError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
