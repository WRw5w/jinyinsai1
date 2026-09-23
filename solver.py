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


# How many rounds short of `max_rounds` the greedy hands the remainder to the
# exact `_closing_rounds` search.  The semi-final rule set allows 6 rounds per
# scheme, and the last two are where the exact landing has to be arranged: the
# parallel count of the finishing rounds must divide each order's remainder, so a
# purely throughput-driven greedy reliably strands a few pieces.  A window of 3
# leaves the search room to finish a two-round tail while keeping its frontier
# small.
_CLOSING_LOOKAHEAD = 3

# Upper bound on the round deliveries `_round_moves` will keep.  Enumerating every
# legal round is a product of per-order segment ranges, so it is only affordable
# for the few-order chunks that reach the closing search -- four orders already
# exceed it.  Larger chunks are not a problem in practice because they have more
# rounds to absorb a remainder with, which is why the caller also gates the search
# on the order count.
_MOVE_CAP = 4000

# Largest number of still-owed orders the exact closing search will attempt.
# Two, deliberately.  The search branches over legal rounds, whose count is a
# Cartesian product over orders: the two-order tail of `C60:26.5` resolves in a
# handful of nodes, while the three-order one spent a two-million-node budget and
# forty seconds without a verdict.  Schemes with three or more live orders do not
# need it -- they have the round budget to absorb a remainder in the ordinary
# greedy rounds, and the wall clock is better spent on the many one- and two-order
# schemes the greedy genuinely cannot land alone.
_CLOSING_MAX_ORDERS = 2

# Total states the closing breadth-first sweep may accumulate across all layers.
# The search is a heuristic: on hitting the limit it simply reports failure and the
# scheme keeps the greedy's rounds.  That is much better than letting one hard
# scheme consume the wall-clock allowance the other groups need -- a three-order
# tail can otherwise expand for tens of seconds.  Two orders resolve in a few
# thousand states, and a tight cap is what keeps the worst case predictable.
# (The companion `_CLOSING_WORK_LIMIT` bounds the comparisons rather than the state
# count; both are needed, because a small state count can still be expensive to
# build when the move set is large.)
_CLOSING_NODE_LIMIT = 20000

# WORK budget for one `_closing_rounds` call, counted in (state, move) comparisons.
# `_CLOSING_NODE_LIMIT` and `_CLOSING_STATE_CAP` bound how many states a layer may
# HOLD, but not how much work producing them costs -- and the two are very
# different numbers.  Measured on `NP01:43`'s hopeless pairs: (840, 1399) expands
# 637 + 8,416 + 7,831 states over three layers, i.e. every layer of the third sweep
# is built by comparing ~1,600 move candidates per prior state, and the call ends
# in `None` having spent seconds doing it.  `_construct` then repeats that search
# for all five blanks, so a single pair that cannot close at all -- and (840, 1399)
# cannot, the third layer comes out empty -- costs 13.4 s of a 30-order seed.
#
# The number is chosen by sweeping it against both smoke groups rather than from
# theory.  Pairs of `NP01:43` (3532 orders) and `C60:26.5` (2205, the heaviest
# pieces in the drop) were constructed at 50k / 100k / 250k / 500k / 1M:
#
#     limit    NP01 solved   NP01 time   C60 solved   C60 time
#      50k         7/15         7.95 s       5/10       18.01 s
#     100k         8/15         6.65 s       5/10       13.21 s
#     250k         9/15         9.55 s       5/10       26.02 s
#     500k         9/15        11.88 s       6/10       42.75 s
#       1M         9/15        13.70 s       7/10       66.44 s
#
# 100k is the minimum of BOTH time columns and already reaches the plateau on C60,
# so the extra solves above it are bought at a steep price -- 250k is 43 % slower
# on NP01 for one additional pair, and the pairs that only close at 500k+ are the
# ones whose tail is otherwise absorbed by the greedy's remaining rounds anyway.
# A truncated close is not an error: the caller keeps the greedy's rounds, which
# are already legal and complete, so the quality difference is a partial tail
# rather than an infeasible scheme.
_CLOSING_WORK_LIMIT = 100000

# Memo for concluded `_closing_rounds` calls.  Keyed on everything the search
# reads -- `(ids, blank.bid, remaining, budget)` -- and only NEGATIVE results are
# stored.  A positive result is a round list, and replaying it into a different
# greedy state would be wrong; the negative is a statement about `(blank,
# remaining, budget)` alone, so sharing it is sound and it is the only thing worth
# sharing: the five blanks in `_construct`'s loop probe the same remainders, and on
# the hopeless pairs every one of them concludes `None` at full cost.
_CLOSING_NEG_CACHE = {}

# States allowed per layer of the closing sweep.  The move set is a product of the
# per-order segment ranges, so a layer can in principle hold hundreds of thousands
# of remainders; the cap turns that into a bounded failure.
_CLOSING_STATE_CAP = 60000

# Memo for `_max_delivery_table`.  The key is `(id(order), parallel_limit)`, which
# is safe because the table is a pure function of the order's geometry and the
# group's bar count; `id()` is stable for the lifetime of the loaded order list.
_MAX_DELIVERY_CACHE = {}

# Memo for the legal round shapes of a group.  Keyed on `(ids, blank_id,
# blank_len, parallel_limit)`, i.e. everything the enumeration reads, and the value
# is the untrimmed list; `_round_moves` filters it by the live remainder.  One
# plan's closing searches reuse the same handful of group geometries hundreds of
# times, so this removes the dominant cost of the semi-final construction.
_ROUND_SHAPE_CACHE = {}

# Memo for `_min_rounds_for`.  Keyed on `(id(table), left)`: the table argument is
# one of the cached `_max_delivery_table` lists, so its identity names the order.
_MIN_ROUNDS_CACHE = {}

# Upper bound on the round shapes `_legal_round_shapes` will enumerate.  The
# enumeration is a product of per-order segment ranges, so it is only affordable
# for the small chunks that reach the closing search; truncating is safe because a
# truncated search merely fails to find a closing plan (the candidate is dropped)
# whereas an unbounded one exhausts the whole time budget.
_SHAPE_CAP = 4000

# Upper bound on the closing search's reachable-state frontier.  States are piece
# vectors, so a wide frontier means many near-equivalent partial deliveries; the
# extra breadth buys little once the search is this deep.
_FRONTIER_CAP = 20000


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
    trim: float = 1.0                # m per end; length_mode determines sharing
    length_mode: str = "trimmed_segments"  # legacy, or net_shared_trim (platform)
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
    # Semi-final switches.  `continuity` and `coverage_shared` are modelling
    # switches, not solver heuristics: when set, validate_plan refuses a plan whose
    # order rounds are not adjacent, and counts coverage per shared round, so the
    # search cannot silently emit a violating incumbent.
    continuity: bool = False
    coverage_shared: bool = False
    enforce_order_mass_floor: bool = False   # constraints.txt clause 8 (semi only)

    def validate(self):
        if self.length_mode not in ('trimmed_segments', 'net_shared_trim'):
            raise ModelError('Unknown length_mode')
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
        if self.objective not in ("lex", "score", "platform_score"):
            raise ModelError("objective must be lex, score or platform_score")
        if self.objective in ('score','platform_score') and self.baseline_knives is None:
            raise ModelError("baseline_knives is required for score objectives")

    @property
    def segment_trim(self):
        return 0.0 if self.length_mode == 'net_shared_trim' else 2 * self.trim

    @property
    def round_trim(self):
        return 2 * self.trim if self.length_mode == 'net_shared_trim' else 0.0


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

    @property
    def linear_weight_int(self):
        """Linear weight with the diameter truncated to whole millimetres.

        This is the diameter the platform's yield numerator uses (confirmed by the
        96.29% / 96.42% feedbacks), as opposed to linear_weight which keeps the
        decimals for the physical bed-weight check.
        """
        return math.pi * (int(self.diameter) / 1000) ** 2 / 4 * self.density


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
    # Set only when the semi-final rule set is active: the number of orders that
    # shared at least one cold-bed round.  Under the preliminary rule an order in a
    # multi-order scheme counts regardless of which round it sat in, which is the
    # `covered_flat` fallback used when `covered_shared` is None.
    covered_shared: int | None = None

    @property
    def metrics(self):
        covered = self.covered_shared if self.covered_shared is not None else (
            len(self.ids) if len(self.ids) > 1 else 0)
        return (sum(r.knives for r in self.rounds), sum(r.finished for r in self.rounds),
                sum(r.raw for r in self.rounds), covered)

    @property
    def metrics_prelim_coverage(self):
        """Same metrics under the preliminary coverage rule, for A/B comparisons."""
        return (sum(r.knives for r in self.rounds), sum(r.finished for r in self.rounds),
                sum(r.raw for r in self.rounds), len(self.ids) if len(self.ids) > 1 else 0)


def _pick(row, names, default=None):
    for name in names:
        if row.get(name) not in (None, ""):
            return row[name].strip()
    if default is not None:
        return default
    raise ModelError(f"Missing CSV field: {' / '.join(names)}")


def _detect_encoding(path, candidates=("utf-8-sig", "gbk")):
    """Pick the first encoding that decodes the whole file.

    Three encodings ship with this competition: the preliminary CSVs are UTF-8
    with BOM, the semi-final CSVs are GBK, and the semi-final constraints.txt is
    UTF-8 with BOM.  Detecting beats being told, because a wrong guess here
    silently mis-decodes every Chinese column name.
    """
    with open(path, "rb") as binary:
        raw = binary.read()
    for encoding in candidates:
        try:
            raw.decode(encoding)
            return encoding
        except (UnicodeDecodeError, LookupError):
            continue
    raise ModelError(f"Cannot decode {path} as any of {candidates}")


def load_orders(path: str, cfg: Config, encoding: str | None = None,
                skip_invalid: bool = False) -> list[Order]:
    """Load an order CSV.

    `skip_invalid` isolates rows whose numeric fields are non-positive instead of
    aborting the load.  Both rounds ship exactly one such row (a negative length in
    the preliminary data, a negative weight in the semi-final data) and the rules
    say anomalous rows are dropped during cleaning, so a bulk solver run needs the
    tolerant path.  The strict default is kept because a *malformed* row in a small
    hand-written fixture should still fail loudly.
    """
    cfg.validate()
    orders = []
    if encoding is None:
        encoding = _detect_encoding(path)
    with open(path, encoding=encoding, newline="") as stream:
        for line, row in enumerate(csv.DictReader(stream), 2):
            try:
                oid = _pick(row, ["order_id", "订单号", "订单编号", "id"])
                steel = _pick(row, ["steel", "steel_grade", "钢种", "坯料钢种", "订单钢种"])
                dia = float(_pick(row, ["diameter", "直径", "规格", "订单直径(mm)", "订单直径"]))
                size = float(_pick(row, ["length", "size", "定尺长度", "定尺", "订单定尺(mm)"]))
                weight = float(_pick(row, ["weight", "重量", "需求重量", "订单重量", "订单重量(t)"])) * cfg.weight_scale
                density = float(_pick(row, ["density", "密度"], str(9860)))
                if skip_invalid and min(dia, size, weight, density) <= 0:
                    continue
                for name, value in (("diameter", dia), ("length", size), ("weight", weight), ("density", density)):
                    _positive(value, name)
                pieces = max(1, _ceil(weight / (math.pi * (dia / 1000) ** 2 / 4 * density * size)))
                orders.append(Order(oid, steel, dia, size, weight, density, pieces))
            except (ValueError, TypeError) as exc:
                raise ModelError(f"Order CSV line {line}: {exc}") from exc
    _validate_orders(orders)
    return orders


def load_blanks(path: str, encoding: str | None = None) -> list[Blank]:
    result = []
    if encoding is None:
        encoding = _detect_encoding(path)
    with open(path, encoding=encoding, newline="") as stream:
        for line, row in enumerate(csv.DictReader(stream), 2):
            try:
                bid = int(float(_pick(row, ["blank_type", "blank_id", "id", "坯料", "编号", "钢坯类型"],
                                       str(line - 1))))
                width = float(_pick(row, ["width", "width_mm", "宽度", "钢坯长度"]))
                thickness = float(_pick(row, ["thickness", "thickness_mm", "厚度", "钢坯宽度"]))
                length = float(_pick(row, ["length", "length_m", "长度", "钢坯定尺"]))
                # `钢坯定尺` is in millimetres; `长度mm` is too, while the normalized
                # file already converted to metres.  Normalize on magnitude so both
                # spellings work: a rolled bar is never under 1 m or over 100 m.
                if length > 1000:
                    length = length / 1000.0
                density = float(_pick(row, ["density", "密度"], str(9860)))
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
        lengths = [k * self.orders[i].size + cfg.segment_trim for i, k in zip(ids, ks) if k]
        total = sum(lengths) + cfg.round_trim
        linear = self.orders[ids[0]].linear_weight
        mass = total * parallel * linear
        if not cfg.min_bed_length - 1e-8 <= total <= cfg.bed_length + 1e-8:
            return None
        if not cfg.min_bed_weight - 1e-8 <= mass <= cfg.bed_weight + 1e-8:
            return None
        usable = self.blank_length(ids, blank)
        if max(lengths) + cfg.round_trim > usable + 1e-8:
            return None
        count = max(1, _ceil(total * parallel / usable))
        finished = sum(k * parallel * self.orders[i].size * linear for i, k in zip(ids, ks))
        knives = sum(ks) + 1 if cfg.length_mode == 'net_shared_trim' else sum(k + 1 for k in ks if k)
        return Round(tuple(ks), parallel, count, knives, finished, count * blank.weight)


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
    if cfg.objective == 'platform_score':
        # Weight table of PDF 九: 40 knives / 30 yield / 20 coverage / 10 time.
        # The time term is a constant here because the solver cannot price the
        # official wall-clock subscore, and it does not affect the argmax.
        score = 40 * min(1.0, cfg.baseline_knives / knives) if knives else 0
        score += 30 * yield_rate + 20 * coverage + 10
        return (-round(score, 12),) + lex
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
                bound = min(maxima[pos], max(0, _floor((cfg.bed_length - used - cfg.segment_trim) / o.size)))
                for k in range(bound + 1):
                    enumerate_k(pos + 1, used + (k * o.size + cfg.segment_trim if k else 0), ks + (k,))

            enumerate_k(0, cfg.round_trim, ())
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
    if deadline is not None and time.perf_counter() >= deadline:
        raise SearchTimeout("Time budget exhausted before a complete feasible plan was found")


def _expired(deadline):
    """Soft form of `_deadline`: True when the budget is gone, never raises.

    `None` means "no clock" and is always unexpired.  The seeding phase runs with
    no deadline (see `search_10s`) because clause 12 makes it non-optional, so
    every cooperative check inside `_seed_group` / `_pack_group` has to tolerate
    the sentinel -- comparing against `None` directly raises `TypeError`, which is
    how the sentinel first surfaced.
    """
    return deadline is not None and time.perf_counter() >= deadline


def _close_continuity_gaps(model, ids, ks, remaining, produced, parallel):
    """Force every order with pieces left to appear in this round.

    Clause 6 requires an order split across rounds to run in *adjacent* rounds
    with nothing else in between.  The greedy ranks by remaining volume, so it
    finishes the big orders first and would let a small order drop out of a
    middle round and come back later -- `[0, 3, 4, 5]` in the real NP01:43 run.
    Giving each unfinished order at least one segment per round makes the round
    index list contiguous by construction: an order enters at round `s` and
    leaves at round `e`, with `[s..e]` unbroken.

    Falls back to the caller's `ks` when the gap cannot be closed without
    exceeding the bed, in which case `make_round` rejects the candidate and the
    caller simply skips it.  Returns None when closing the gap would push an
    order past its overproduction cap, so the candidate is dropped rather than
    emitted as an over-producing round.
    """
    ks = list(ks)
    for j, left in enumerate(remaining):
        if left <= 0 or ks[j] > 0:
            continue
        i = ids[j]
        headroom = (model.caps[i] - produced[j]) // max(1, parallel)
        if headroom < 1:
            return None
        ks[j] = min(max(1, _ceil(left / parallel)), headroom)
    return ks


def _min_cuttable_pieces(model, order, parallel):
    """Smallest piece count that can still fill a legal round for one order.

    Each order occupies its own segment of the shared bar path, so an order's
    round is legal when its own segment plus its share of the trim reaches the
    50 m floor:

        k * size + round_trim / parallel >= min_bed_length / parallel

    Rearranged, the minimum segment count is

        k >= (min_bed_length - round_trim) / (parallel * size)

    Leftover pieces below that bound can never be consumed by any later round at
    this parallel count, which is exactly the residual the greedy used to strand.

    `size` is read from the order itself, not from `ids[0]`: orders in one
    (steel, spec) group may still carry different 定尺 lengths.
    """
    cfg = model.cfg
    if parallel < 1:
        return 1
    return max(1, _ceil((cfg.min_bed_length - cfg.round_trim) / (parallel * order.size)))


def _max_delivery_table(model, ids, order):
    """Largest piece count one round can hand `order`, per parallel count.

    Index `p` holds `kmax * p`, where `kmax` is the largest segment count whose
    shared bar path stays inside the 50-150 m window, under the 60 t ceiling and
    within the blank's reach.  The table depends only on the order and the group's
    parallel limit, never on the remainder, so it is computed once per order and
    reused by every `_min_rounds_for` call -- that call used to rescan the whole
    parallel range each time and dominated the closing search's runtime.

    The result is memoised on `(id(order), p_limit, blank_len)` because the inner
    loops are pure arithmetic over the parallel range: profiling the fixed closing
    search showed 5,370 rebuilds costing 11 of 19 seconds, which is pure waste
    since every rebuild for the same order is identical.
    """
    key = (id(order), model.parallel_limit(ids))
    cached = _MAX_DELIVERY_CACHE.get(key)
    if cached is not None:
        return cached
    cfg = model.cfg
    p_limit = key[1]
    blank_len = model.blank_length(ids, model.catalogue(ids)[0])
    table = [0] * (p_limit + 1)
    k_ceiling = int((cfg.bed_length - cfg.round_trim) // order.size) + 1
    for p in range(1, p_limit + 1):
        kmax = 0
        for k in range(1, k_ceiling + 1):
            segment = k * order.size + cfg.segment_trim
            total = segment + cfg.round_trim
            if total + 1e-8 < cfg.min_bed_length:
                continue
            if total - 1e-8 > cfg.bed_length:
                break
            if total * p * order.linear_weight > cfg.bed_weight + 1e-8:
                break
            if segment + cfg.round_trim > blank_len + 1e-8:
                break
            kmax = k
        table[p] = kmax * p
    _MAX_DELIVERY_CACHE[key] = table
    return table


def _min_rounds_for(model, ids, order, left, budget, table=None):
    """Fewest rounds that can consume exactly `left` pieces of one order.

    A round hands an order `k * parallel` pieces, and the yield numerator credits
    at most the order's demand, so the solver aims to consume the pieces exactly --
    an overshooting plan is legal (constraints.txt clause 8 is a one-sided floor,
    RULES.md 12.5 penalises short delivery only) but the excess is uncredited and
    costs billet weight.  For a fixed `parallel = p` a round carries at most
    `table[p]` pieces, so the order needs `ceil(left / table[p])` rounds and the
    answer is the best over `p`.

    Returns `None` when no parallel count can consume the remainder inside the
    rounds left.  `table` is the precomputed `_max_delivery_table`; callers that
    test many remainders for the same order should pass it.

    This is an optimistic bound, not a certificate: `ceil(left / max_delivery)` can
    be unreachable when the exact-product constraint bites, so a passing verdict
    only means "not obviously impossible".  It is used for pruning, never to
    declare success.
    """
    if left <= 0:
        return 0
    if table is None:
        table = _max_delivery_table(model, ids, order)
    # The answer is a pure function of `(table, left)`, and `feasible_rest` asks for
    # the same remainders over and over across the sweep layers -- 1.5 million calls
    # worth 14 seconds when profiled on `NP01:43`.  `id(table)` identifies the
    # cached `_max_delivery_table` instance, which lives for the whole run.
    cache_key = (id(table), left)
    cached = _MIN_ROUNDS_CACHE.get(cache_key)
    if cached is not None:
        best = cached[0]
        return None if (best is None or (budget is not None and best > budget)) else best
    best = None
    for p in range(1, len(table)):
        per_round = table[p]
        if per_round <= 0:
            continue
        need = _ceil(left / per_round)
        if best is None or need < best:
            best = need
            if best == 1:
                break
    _MIN_ROUNDS_CACHE[cache_key] = (best,)
    if best is not None and budget is not None and best > budget:
        return None
    return best


def _tail_ok(model, ids, blank, remaining, produced):
    """Can the still-unproduced pieces be finished within the remaining rounds?

    A round delivers exactly `k * parallel` pieces to an order, and the platform
    forbids over-production above the rounded-up demand, so the tail must be
    consumed *exactly*.  An earlier version of this guard asked for a *single*
    round that consumes a leftover exactly (`k * parallel == left`), which is far
    too strict: a 946-piece remainder obviously needs many rounds, so the guard
    rejected almost every candidate and the greedy lost its tail gradient
    entirely -- the two-order `C60:26.5` chunk then burned all six rounds and
    stranded two pieces.

    The correct question is the packing one: the leftovers of each order need
    some number of rounds, obtained from `_min_rounds_for`, and the guard passes
    when the orders can share the remaining budget.  Sharing is possible because
    one round may carry segments of several orders at once, so the requirement is
    that the *largest* per-order demand fits in the budget -- plus room for the
    orders that cannot be co-packed because their combined segments would breach
    the 150 m ceiling.

    The guard is deliberately optimistic: a passing verdict is not a promise that
    the greedy will find the completion, only that the state is not obviously
    dead.  `_closing_rounds` is what actually finishes the tail.
    """
    cfg = model.cfg
    pending = [j for j, v in enumerate(remaining) if v > 0]
    if not pending:
        return True
    budget = cfg.max_rounds
    needs = []
    for j in pending:
        need = _min_rounds_for(model, ids, model.orders[ids[j]], remaining[j], budget)
        if need is None:
            return False
        needs.append(need)
    needs.sort(reverse=True)
    # Two orders can travel together whenever their combined segment fits one
    # round; that is the common case in a (steel, spec) group, so the optimistic
    # test is the max, not the sum.  Sum only when the pair demonstrably cannot
    # co-exist -- approximated by the combined minimum segment length.
    total_min = 0.0
    for j in pending:
        order = model.orders[ids[j]]
        # Smallest legal segment for this order's leftover at the widest parallel
        # count it can still use; a lower bound on what the round must carry.
        total_min += _min_cuttable_pieces(model, order, 1) * order.size
    if total_min <= cfg.bed_length:
        return needs[0] <= budget
    return sum(needs) <= budget


def _construct(model, ids, rng, deadline, randomized=False):
    """Pack rounds using several parallel counts and varying order priorities."""
    cfg, ids = model.cfg, tuple(sorted(ids))
    best = None
    # `_closing_rounds` is a pure function of `(ids, blank, remaining, budget)`, but
    # `_construct` reaches the same remainder several times: the greedy retries the
    # whole build once per candidate round shape, and every retry that arrives at the
    # same tail re-runs the identical search.  Measured on `NP01:43`'s (617, 989)
    # pair the search costs 1.67 s and was run five times for the same `(280, 443)`,
    # which was the entire 8.6 s build.
    #
    # `blank.bid` MUST be part of the key, and forgetting it was a real bug: the
    # outer loop tries five blanks in sequence, and a tail cached while building for
    # blank 3 was replayed into a batch declared as blank 2.  The rounds came from
    # `make_round`, so they were internally consistent -- but for the *other* blank,
    # and the exported `blank_counts` then under-declared the steel the platform
    # charges for.  `validate_plan` caught it as "Insufficient blanks for the
    # parallel bars" on 21 of 30 pairs.
    closing_cache = {}
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
            # Candidate parallel counts.  The bed floor of 50 m and the 60 t ceiling
            # interact: a large `parallel` shortens the round below 50 m before it
            # reaches the demand, a small one overruns the weight limit.  The old
            # seed set {1, limit, needed, ceil(needed/kmax)} can miss every feasible
            # point when `max_rounds` is 6, so sweep the whole range -- it is at most
            # ~200 values wide and the per-candidate work is a few divisions.
            choices = set(range(1, limit + 1))
            if randomized:
                choices = set(rng.sample(sorted(choices), min(len(choices), 12)))
                choices.update((1, limit))
            priority = list(range(len(ids)))
            if randomized:
                rng.shuffle(priority)
            else:
                priority.sort(key=lambda j: remaining[j] * model.orders[ids[j]].size, reverse=True)
            candidates = []
            rounds_left = cfg.max_rounds - len(rounds)
            for parallel in sorted(choices):
                _deadline(deadline)
                ks, used = [0] * len(ids), cfg.round_trim
                length_limit = min(cfg.bed_length, cfg.bed_weight / (parallel * model.orders[ids[0]].linear_weight))
                final_round_ok = True
                for j in priority:
                    if remaining[j] <= 0:
                        continue
                    i = ids[j]
                    available = min(length_limit - used, model.blank_length(ids, blank) - cfg.round_trim)
                    kmax = min((model.caps[i] - produced[j]) // parallel, max(0, _floor((available - cfg.segment_trim) / model.orders[i].size)))
                    k = max(1, _ceil(remaining[j] / parallel))
                    if cfg.continuity and rounds_left > 1:
                        # Continuity needs every *live* order present in the round
                        # it is still running through, so the round has to carry a
                        # share of each rather than clearing them one at a time.
                        # Spread what is left over the rounds that remain, but
                        # never below one segment.
                        k = min(k, max(1, _ceil(remaining[j] / parallel / rounds_left)))
                    elif rounds_left == 1:
                        # This is the last round, so aim to deliver exactly what is
                        # owed: no round is left to absorb a remainder, and the yield
                        # numerator credits at most the demand, so over-production
                        # here is pure waste.  That forces `parallel` to divide the
                        # order's remainder (`k = left / p`).  When it does not, this
                        # parallel count cannot finish the order cleanly and the whole
                        # candidate is abandoned -- patching `k` down to `left // p`
                        # only strands the difference forever, which is how the
                        # 946/1156 pair used to end on 2 leftover pieces.  (Abandoning
                        # is safe: the leftovers reach `_solo_scheme`, which may spend
                        # the cap where exactness is impossible.)
                        if remaining[j] % parallel:
                            final_round_ok = False
                            break
                        k = remaining[j] // parallel
                    k = min(k, kmax)
                    if randomized and k > 1 and rng.random() < 0.2:
                        k = rng.randint(1, k)
                    if k > 0:
                        ks[j] = k
                        used += k * model.orders[i].size + cfg.segment_trim
                if not final_round_ok:
                    continue
                if cfg.continuity:
                    # An order that still has pieces left MUST appear in this
                    # round, otherwise its round list gains a hole and clause 6
                    # (adjacent, no skipping) is violated.  Carrying at least one
                    # segment forward keeps every order's rounds contiguous; the
                    # `useful` ranking below then decides how much each gets.
                    ks = _close_continuity_gaps(model, ids, ks, remaining, produced, parallel)
                    if ks is None:
                        continue
                r = model.make_round(ids, ks, parallel, blank)
                if r is not None:
                    # NOTE: the generator variable is named `kj`, never `k`.
                    # Reusing `k` here used to rebind the loop variable from the
                    # shaping block above, which silently corrupted the
                    # `new_remaining` computation three lines below and made the
                    # tail guard reject perfectly good rounds.
                    useful = sum(min(max(0, remaining[j]), kj * parallel) * model.orders[i].size
                                 for j, (i, kj) in enumerate(zip(ids, ks)))
                    # Stranded tail penalty.  `useful` only rewards demand solved
                    # now, so the greedy happily leaves a handful of pieces that
                    # no later round can absorb (they cannot form a legal round)
                    # and then throws the whole construction away.  The tail guard
                    # is what keeps the greedy honest, so it is applied to EVERY
                    # order, not only to the ones short of their demand.
                    new_remaining = [v - kj * parallel for v, kj in zip(remaining, ks)]
                    tail = 0.0
                    if not all(v <= 0 for v in new_remaining):
                        if not _tail_ok(model, ids, blank, new_remaining,
                                        [p + kj * parallel for p, kj in zip(produced, ks)]):
                            tail = 1.0
                        else:
                            # Prefer candidates whose leftovers need the fewest
                            # extra rounds, so the 6-round budget is not spent
                            # early on orders that could have waited.
                            tail = 0.5 * (sum(new_remaining) / max(1.0, sum(remaining)))
                    candidates.append((useful / r.knives, useful / r.raw, -tail, r, tail))
            if not candidates:
                break
            if randomized and rng.random() < 0.25:
                r = rng.choice(candidates)[3]
            else:
                # Prefer a feasible tail, then knives, then material efficiency.
                clean = [c for c in candidates if not c[4]]
                r = max(clean or candidates, key=lambda x: x[:3])[3]
            rounds.append(r)
            for j, k in enumerate(r.ks):
                produced[j] += k * r.parallel
                remaining[j] -= k * r.parallel
            # Hand the tail to the exact search while rounds are still left.  The
            # greedy would otherwise spend the whole budget on throughput and leave
            # nothing for the exact landing, which is how the 946/1156 pair used to
            # end on 85/103 leftover pieces with zero rounds to spare.  Once the
            # remainder is small enough that a joint exact finish is plausible, let
            # the reachability search take over.
            if all(v <= 0 for v in remaining):
                break
            if len(rounds) >= 1 and cfg.max_rounds - len(rounds) <= _CLOSING_LOOKAHEAD:
                key = (blank.bid, tuple(remaining), cfg.max_rounds - len(rounds))
                if key in closing_cache:
                    tail = closing_cache[key]
                else:
                    tail = _closing_rounds(model, ids, blank, remaining, produced, deadline,
                                           cfg.max_rounds - len(rounds))
                    closing_cache[key] = tail
                if tail is not None:
                    rounds.extend(tail)
                    for tr in tail:
                        for j, k in enumerate(tr.ks):
                            produced[j] += k * tr.parallel
                            remaining[j] -= k * tr.parallel
                    break
        if not all(v <= 0 for v in remaining) and len(rounds) < cfg.max_rounds:
            # Closing pass for the rounds the in-loop hand-off did not reach (the
            # greedy finished a round exactly when the lookahead window opened).
            budget = cfg.max_rounds - len(rounds)
            key = (blank.bid, tuple(remaining), budget)
            if key in closing_cache:
                tail = closing_cache[key]
            else:
                tail = _closing_rounds(model, ids, blank, remaining, produced, deadline, budget)
                closing_cache[key] = tail
            if tail is not None:
                rounds.extend(tail)
                for r in tail:
                    for j, k in enumerate(r.ks):
                        produced[j] += k * r.parallel
                        remaining[j] -= k * r.parallel
        if all(v <= 0 for v in remaining):
            candidate = Batch(ids, blank.bid, tuple(rounds))
            if best is None or _key(candidate.metrics, cfg, len(ids)) < _key(best.metrics, cfg, len(ids)):
                best = candidate
    return best


def _round_moves(model, ids, blank, cap):
    """Legal round deliveries for the tail, largest first.

    A move is `(delta, parallel, ks)`: one round that hands each order `ks[j] *
    parallel` pieces.  `cap` bounds what each order may still receive.

    Three properties make the depth-first closing search affordable:

    * **Largest first.**  The moves are sorted by how much of the outstanding
      demand each round clears.  That is not cosmetic -- trying small deliveries
      first sends the search down an exponentially wide tree (53 million nodes for
      `C60:26.5`'s 946/1156 pair), while trying the biggest first makes it dive
      straight to a completion in a handful of nodes.
    * **Single-order-maximal shapes offered explicitly.**  The product enumeration
      visits evenly split rounds first, so a round that loads one order with
      everything the bed will hold has to be offered separately.  Those are exactly
      the rounds a large order needs to land in two rounds.
    * **A hard move cap.**  Beyond it the extra shapes only widen the branch factor.
    """
    cfg = model.cfg
    sizes = [model.orders[i].size for i in ids]
    linear = model.orders[ids[0]].linear_weight
    blank_len = model.blank_length(ids, blank)
    plimit = model.parallel_limit(ids)
    # The legal shapes for a fixed `(ids, blank)` do not depend on the remainder at
    # all -- `cap` only trims which of them are reachable.  Enumerating them is the
    # expensive half (419,190 `offer` calls and 5.2 of 9.7 seconds when profiled on
    # `NP01:43`), and the closing search asks for the same `(ids, blank)` once per
    # blank per retry, so the untrimmed list is built once and filtered after.
    shape_key = (tuple(ids), blank.bid, blank_len, plimit)
    all_shapes = _ROUND_SHAPE_CACHE.get(shape_key)
    if all_shapes is None:
        all_shapes = _enumerate_round_shapes(model, ids, sizes, linear, blank_len, plimit, cfg)
        _ROUND_SHAPE_CACHE[shape_key] = all_shapes
    if not all_shapes:
        return []
    moves = [m for m in all_shapes if all(d <= c for d, c in zip(m[0], cap))]
    if cfg.continuity:
        # Clause 6 revisited at the closing stage.  The greedy guarantees every
        # *live* order appears in each round it builds, but the closing search picks
        # its own rounds, and a move that skips an order which still owes pieces
        # opens a hole in that order's round list.  Measured on `NP01:43`'s pair
        # (24, 25): the sweep returned `[(15, (0, 17)), (9, (9, 0))]`, giving
        # `B20270025` rounds [0,1,2,4] and failing `validate_plan` with a continuity
        # error.  Requiring every order with a positive remainder to carry at least
        # one segment makes the tail contiguous by construction, exactly as
        # `_close_continuity_gaps` does for the greedy.
        live = [j for j, c in enumerate(cap) if c > 0]
        moves = [m for m in moves if all(m[2][j] > 0 for j in live)]
    return moves[:_MOVE_CAP]


def _enumerate_round_shapes(model, ids, sizes, linear, blank_len, plimit, cfg):
    """Every legal round shape for this group, largest total delivery first.

    Split out of `_round_moves` so the result can be cached: the enumeration is a
    product of per-order segment ranges and is the single most expensive step of
    the closing search, yet it depends only on the group's geometry.
    """
    max_seg = [int((cfg.bed_length - cfg.round_trim) // s) + 1 if s else 0 for s in sizes]
    best = {}

    def offer(parallel, ks):
        """Record a candidate round if it is legal; returns whether it was new."""
        lengths = [k * s for k, s in zip(ks, sizes) if k]
        total = sum(lengths) + cfg.round_trim
        if not cfg.min_bed_length - 1e-8 <= total <= cfg.bed_length + 1e-8:
            return False
        if total * parallel * linear > cfg.bed_weight + 1e-8:
            return False
        if max(lengths) + cfg.round_trim > blank_len + 1e-8:
            return False
        delta = tuple(k * parallel for k in ks)
        old = best.get(delta)
        if old is not None and parallel >= old[0]:
            return False
        best[delta] = (parallel, ks)
        return True

    for parallel in range(1, plimit + 1):
        # No `cap` here on purpose: the untrimmed shape set is cached, and the
        # caller filters it by the live remainder.  `max_seg` bounds each order by
        # what the bed can physically hold, which is the only hard ceiling.
        highs = [max(0, max_seg[j]) for j in range(len(ids))]
        if not any(highs):
            continue
        # A round that loads one order with as much as it can take is the shape
        # that lets a big order land in two rounds; a round that splits evenly
        # across orders is the shape that lets several orders share the bed.  Both
        # matter, and the product enumeration below finds the even splits first, so
        # the single-order-maximal shapes are offered explicitly.  Without this the
        # three-order `C60:26.5` chunk never sees order 2's 1800-piece round and
        # the search reports a spurious infeasibility.
        for j in range(len(ids)):
            if highs[j] <= 0:
                continue
            ks = [0] * len(ids)
            ks[j] = highs[j]
            offer(parallel, ks)
        # Then the descending product, which visits the largest combined rounds.
        for ks in itertools.product(*[range(hi, -1, -1) for hi in highs]):
            if not any(ks):
                continue
            offer(parallel, ks)
    if not best:
        return []
    # Pareto cut.  Within one parallel count a move is a `ks` vector, and the
    # delivered pieces are `ks * parallel`, so a move that gives order A at least as
    # much AND order B at least as much as another move makes that other move
    # strictly less useful for reaching a target from above.  Exactness still needs
    # the finer moves, but they are only ever useful as the *last* step, and the
    # breadth-first sweep re-tests every reachable remainder against the per-order
    # delta sets before expanding, so dropping them costs no completeness that the
    # search actually exploits.  Measured on `NP01:43`'s (617, 989) pair: 4,000
    # moves collapse to 547, a 7.3x cut in every layer of the sweep.
    by_parallel = {}
    for delta, parallel, ks in ((d, p, k) for d, (p, k) in best.items()):
        by_parallel.setdefault(parallel, set()).add(tuple(ks))
    pruned = {}
    for parallel, shapes in by_parallel.items():
        # Two-order case is the one the sweep actually exercises, so the frontier is
        # taken on the first coordinate and the running maximum of the rest.
        frontier, best_last = [], None
        for ks in sorted(shapes, key=lambda k: (-k[0], [-v for v in k[1:]])):
            tail = tuple(ks[1:])
            if best_last is None or any(b > a for a, b in zip(best_last, tail)):
                frontier.append(ks)
                best_last = tail if best_last is None else \
                    tuple(max(a, b) for a, b in zip(best_last, tail))
        for ks in frontier:
            delta = tuple(k * parallel for k in ks)
            pruned[delta] = (parallel, list(ks))
    # Always keep the single-order-maximal shapes: they are the whole reason a big
    # order can land in two rounds, and they can sit off the Pareto staircase.
    for parallel, shapes in by_parallel.items():
        for ks in shapes:
            if sum(1 for k in ks if k) == 1:
                delta = tuple(k * parallel for k in ks)
                pruned.setdefault(delta, (parallel, list(ks)))
    moves = [(delta, parallel, ks) for delta, (parallel, ks) in pruned.items()]
    # Rank by total pieces moved.  The single-order-maximal shapes are in the list
    # and several of them score highest for their own order, so this ordering keeps
    # both kinds reachable while making the search dive into the large-delivery
    # branches first.
    moves.sort(key=lambda m: (-sum(m[0]), m[1]))
    return moves[:_MOVE_CAP]


def _reachable_in(deltas, want, rounds):
    """Can `want` be written as a sum of at most `rounds` values from `deltas`?

    A small bounded set-reachability sweep: `deltas` holds the distinct piece
    counts a single round can hand one order, and every round may also contribute
    nothing to that order.  The sweep is capped at `rounds` expansions, so it costs
    at most `rounds * |states| * |deltas|` -- small for a tail of one to three
    rounds, and it prunes remainders that no combination of rounds can land on.
    """
    if want <= 0:
        return True
    if not deltas:
        return False
    if rounds <= 0:
        return False
    states = {0}
    for _ in range(rounds):
        nxt = set(states)
        for value in states:
            for d in deltas:
                total = value + d
                if total == want:
                    return True
                if total < want:
                    nxt.add(total)
        if nxt == states:
            return False                      # no progress left
        states = nxt
    return want in states


def _reachable_sets(deltas, want, rounds):
    """The full reachability frontier per round count, for table-driven lookups.

    `_reachable_in` is cheap, but `_closing_rounds` calls it once per candidate
    remainder and the caller is a breadth-first sweep with tens of thousands of
    candidates.  Measured on `NP01:43` the pair that stalls the sweep made 930,469
    `_reachable_in` calls and spent 66 of its 93 seconds inside them.  The sweep
    only ever asks for `<= rounds` against a fixed `deltas`, so the frontier is
    built once per `rounds` here and every later query is a dictionary lookup.
    """
    levels = []
    states = {0}
    levels.append(states)
    for _ in range(rounds):
        nxt = set(states)
        for value in states:
            for d in deltas:
                total = value + d
                if total <= want:
                    nxt.add(total)
        if nxt == states:
            levels.extend([states] * (rounds - len(levels) + 1))
            break
        states = nxt
        levels.append(states)
    while len(levels) <= rounds:
        levels.append(states)
    return levels


def _closing_rounds(model, ids, blank, remaining, produced, deadline, budget):
    """Finish the tail exactly in at most `budget` rounds, or return None.

    The greedy maximises pieces-per-knife round by round, which is right for the
    bulk of the demand but wrong for the final rounds: the platform forbids
    over-production, so every order must be delivered *exactly*, and that puts the
    last rounds under tight arithmetic constraints.  `C60:26.5`'s first two orders
    are the canonical case -- 946 can only be landed by `parallel` in {11, 22, 43}
    and 1156 only by {17, 34, 68}, whose only common divisor is 2, far too small to
    move 473 segments inside a 150 m round.  No single parallel count can finish
    the pair, so the closing rounds MUST mix parallel counts; the greedy's
    `k = ceil(left / p)` shaping never does.

    This is a breadth-first sweep over ROUND COUNTS, choosing one legal round at a
    time.  Breadth-first matters and the measurement is decisive: `NP01:43`'s pair
    (617, 989) closes in exactly 2 rounds, but the largest-delivery move is
    `p=34 k=(18, 8)`, which leaves `(5, 717)` -- and 5 is below the smallest legal
    delivery, so that subtree is dead.  A depth-first search spends its whole node
    allowance inside it before ever trying a 2-round answer; sweeping round counts
    outward reaches the solution on the second layer for ~4,000 nodes.

    Each layer maps a remainder tuple to the move that produced it, so the first
    layer that reaches all-zero gives the path by walking the parent links.
    """
    if not any(v > 0 for v in remaining):
        return []
    if budget < 1:
        return None
    # Only small schemes get the exact closing search.  Its move set is a product
    # of per-order segment ranges, which is affordable for the 1-3 order schemes
    # that actually strand pieces and hopeless for fourteen-order ones; those
    # larger schemes have plenty of rounds left to absorb a remainder anyway.
    if sum(1 for v in remaining if v > 0) > _CLOSING_MAX_ORDERS:
        return None
    target = tuple(remaining)
    # `produced` is NOT part of the search's input: `target` is what each order
    # still owes, and the search only ever subtracts from it.  That makes the whole
    # function a function of `(ids, blank, target, budget)`, so a negative verdict
    # can be memoised -- see `_CLOSING_NEG_CACHE` for why only negatives are safe
    # to share.
    neg_key = (tuple(ids), blank.bid, target, budget)
    if neg_key in _CLOSING_NEG_CACHE:
        return None
    moves = _round_moves(model, ids, blank, target)
    if not moves:
        _CLOSING_NEG_CACHE[neg_key] = True
        return None

    # Any delivery must still leave every order reachable by the rounds that
    # remain, so a move is only worth trying when each order's remainder after it
    # can still be split into at most the remaining rounds.  `_min_rounds_for`
    # supplies that round-count bound cheaply; `_reachable_in` supplies the
    # arithmetic one.
    pending_meta = []
    for j, left in enumerate(target):
        if left > 0:
            order = model.orders[ids[j]]
            pending_meta.append((j, order, _max_delivery_table(model, ids, order)))
    # Per order, the set of piece counts one round can hand it (excluding zero).
    # This is what `_reachable_in` sweeps, and it is the filter that refuses to
    # explore remainders no combination of rounds can land on.  The sweep is
    # materialised per round count up front (`_reachable_sets`) because the caller
    # probes it once per candidate state.
    order_deltas = [sorted({d[j] for d, _p, _ks in moves if d[j] > 0}) for j in range(len(ids))]
    reach_levels = [_reachable_sets(order_deltas[j], target[j], budget)
                    for j in range(len(ids))]
    # Root gate.  `feasible_rest` only ever judges a *child*, so without this the
    # sweep pays a full expansion to discover that the root itself was never
    # finishable.  The round-count half is what catches the genuinely heavy tails
    # (an order owing more than the remaining rounds can carry); the arithmetic half
    # is cheap insurance and does fire on the `C60:26.5` 946/1156 shape, whose
    # per-order delta sets are disjoint multiples of 2.
    #
    # This gate is NOT sufficient on its own -- measured on `NP01:43`'s (840, 1399)
    # both orders pass it (each is individually reachable; the deltas are dense), and
    # the pair is still unsolvable because the coupling, not the arithmetic, is what
    # fails.  `_CLOSING_WORK_LIMIT` is what bounds that case.
    for j, left in enumerate(target):
        if left <= 0:
            continue
        order = model.orders[ids[j]]
        table = _max_delivery_table(model, ids, order)
        if _min_rounds_for(model, ids, order, left, budget, table) is None:
            _CLOSING_NEG_CACHE[neg_key] = True
            return None
        if left not in (reach_levels[j][budget] if budget < len(reach_levels[j])
                        else reach_levels[j][-1]):
            _CLOSING_NEG_CACHE[neg_key] = True
            return None


    def feasible_rest(rem, rounds_left):
        """Cheap bound: every order must still be finishable in the rounds left.

        Two filters:

        1. Round count -- `_min_rounds_for` gives the fewest rounds that can consume
           an order's remainder, and no order may need more than remain.
        2. Arithmetic -- a round hands order `j` exactly `delta[j]` pieces, so the
           remainder after `r` rounds is `target[j]` minus a sum of `r` of the
           available deltas.  Tested against the precomputed reachability frontier
           per order, which prunes the states where a remainder simply cannot be
           expressed (the 946/1156 pair is exactly that case: their deltas are
           disjoint sets of multiples).
        """
        if rounds_left <= 0:
            return not any(rem)
        for j, order, table in pending_meta:
            left = rem[j]
            if left == 0:
                continue
            need = _min_rounds_for(model, ids, order, left, rounds_left, table)
            if need is None:
                return False
            levels = reach_levels[j]
            if left not in (levels[rounds_left] if rounds_left < len(levels) else levels[-1]):
                return False
        return True

    # `layers[r]` maps each remainder reachable in exactly `r` rounds to
    # `(parallel, ks, parent_remainder)`.  The first layer holding all-zero is the
    # answer; the path is read back through the parent links.
    #
    # The sweep is capped THREE ways, and the work cap is the one that bounds the
    # runtime.  `_CLOSING_STATE_CAP` bounds the states per layer and
    # `_CLOSING_NODE_LIMIT` bounds the total, but neither bounds the *comparisons*:
    # a layer holding 8,416 states is built by testing every one of the 637 moves
    # against every state of the previous layer, and it is that product -- not the
    # layer size -- that costs the seconds.  `work` counts exactly those
    # comparisons and stops the sweep at `_CLOSING_WORK_LIMIT`.  `SearchTimeout` is
    # caught and turned into `None` rather than propagating, because a closing
    # search that runs out of time is not a proof that the scheme is infeasible --
    # the caller keeps the greedy's rounds, which are already legal and complete.
    # Letting the timeout escape aborted the whole construction instead, which is
    # how a single hard pair used to cost the entire scheme.
    layers = [{target: None}]
    work = 0
    for rounds_used in range(1, budget + 1):
        try:
            _deadline(deadline)
        except SearchTimeout:
            return None
        previous = layers[-1]
        current = {}
        hit = None
        exhausted = False
        if sum(len(x) for x in layers) > _CLOSING_NODE_LIMIT:
            return None
        try:
            for rem, _ in previous.items():
                work += len(moves)
                if work > _CLOSING_WORK_LIMIT:
                    exhausted = True
                    break
                for delta, parallel, ks in moves:
                    nxt = tuple(a - b for a, b in zip(rem, delta))
                    if any(v < 0 for v in nxt):
                        continue
                    if nxt in previous or nxt in current:
                        continue
                    if not any(nxt):
                        hit = (rem, parallel, ks)
                        break
                    if feasible_rest(nxt, budget - rounds_used):
                        current[nxt] = (parallel, ks, rem)
                if hit is not None:
                    break
                if len(current) > _CLOSING_STATE_CAP:
                    exhausted = True
                    break
        except SearchTimeout:
            return None
        if exhausted:
            # Out of work, not out of states: the verdict is unknown, so it may NOT
            # be memoised as negative.  The caller treats it the same way as a
            # timeout -- keep the greedy's rounds and move on.
            return None
        if hit is not None:
            rem, parallel, ks = hit
            path = [(parallel, ks)]
            for back in range(rounds_used - 1, 0, -1):
                parallel, ks, rem = layers[back][rem]
                path.append((parallel, ks))
            rounds = []
            for par, kk in reversed(path):
                got = model.make_round(ids, kk, par, blank)
                if got is None:            # cannot happen: moves were vetted
                    return None
                rounds.append(got)
            return rounds or None
        if not current:
            # The layer came out empty, so no remainder survives this round count
            # and the sweep has PROVEN infeasibility within the budget.  This is the
            # one negative worth caching, and it is why the hopeless pairs collapse:
            # (840, 1399) reaches this line for every one of the five blanks, so the
            # first blank pays the work cap and the other four return instantly.
            _CLOSING_NEG_CACHE[neg_key] = True
            return None
        layers.append(current)
    # Fell out of the round-count loop with live states still pending: every round
    # count up to the budget was swept and none reached all-zero, so the tail cannot
    # be closed in this budget at all.  Also a proof, also cacheable.
    _CLOSING_NEG_CACHE[neg_key] = True
    return None


def _pack_group(model, group, singles, construct, rng, deadline, cfg):
    """Merge single-order schemes into shared schemes so coverage can score.

    `_seed_group` builds one scheme per order, which is optimal for a metric that
    only counts included orders.  The semi-final replaces that metric: an order
    earns coverage only when it shares a *round* with another order, so a plan of
    solo schemes scores `coverage = 0`.  Merging is therefore mandatory, yet it
    fights two hard limits measured on the real drop:

    * `max_rounds` is a per-scheme budget, not a per-order one, so a heavy order
      can consume all six rounds alone; and
    * a shared scheme must close *every* member inside the same six rounds and land
      each one inside the over-production cap (`max_overproduction_ratio`, 2% for
      the semi-final).  The packing still TARGETS exact delivery -- the excess is
      uncredited by the yield numerator and costs billet weight in the denominator
      -- but exactness is no longer a validity requirement: constraints.txt clause 8
      is a one-sided floor and RULES.md 12.5 penalises only short delivery.

    The strategy is neighbour-first and width-limited: try to merge the current
    order with the next `w-1` orders, and on failure bisect the window down to a
    pair.  Whatever still refuses to merge is returned as a singleton so the
    caller can emit a solo scheme (clause 12 completeness beats coverage).
    """
    pending = list(group)
    merged, leftovers = [], []
    # Pairs only, and this is a measured ceiling rather than a taste.  The exact
    # closure (`_closing_rounds`) refuses tails of more than `_CLOSING_MAX_ORDERS`
    # orders because its move set is the product of the per-order segment ranges,
    # and the greedy alone cannot land a multi-order tail exactly.  Measured on
    # `NP01:43` for the first 20 orders: pairs construct 10/10 in 3.9 s, while
    # three- and four-order chunks construct 0/10 and 0/6 -- every attempt runs to
    # its deadline and only delays the orders that would otherwise be placed.  So a
    # wider window is strictly worse, not merely slower.
    window = 2
    index = 0
    while index < len(pending):
        # A chunk is only worth attempting when there is time to finish it AND the
        # orders can plausibly share.  `rounds_min(a) + rounds_min(b) > max_rounds`
        # is a hard refusal: each order needs that many rounds on its own, and a
        # shared scheme spends the same per-scheme budget on all of them.  Testing
        # it before calling `construct` is what keeps a 2,205-order group from
        # spending its budget on pairs that cannot possibly pack.
        if _expired(deadline):
            break
        width = min(window, len(pending) - index)
        placed = False
        while width >= 2:
            if _expired(deadline):
                break
            chunk = tuple(pending[index:index + width])
            if not _can_share(model, chunk):
                width //= 2
                continue
            combined = None
            for attempt in range(4):
                if _expired(deadline):
                    break
                try:
                    combined = construct(model, chunk, rng, deadline, attempt > 0)
                except SearchTimeout:
                    combined = None
                    break
                if combined is not None:
                    break
            if combined is not None:
                merged.append(combined)
                index += width
                placed = True
                break
            width //= 2
        if placed:
            continue
        # A single order that no neighbour will share six rounds with.  Keep it
        # alone; `_seed_group` turns unmergeable leftovers into solo schemes.
        oid = pending[index]
        leftovers.append((oid,))
        index += 1
    # Orders the deadline cut off keep their own single-order schemes, so the plan
    # is still complete (clause 12) even when the packing pass ran out of time.
    for rest in pending[index:]:
        leftovers.append((rest,))
    return merged, leftovers


def _can_share(model, chunk):
    """Cheap refusal for a chunk that cannot fit one scheme's round budget.

    `max_rounds` is per scheme, not per order, and a shared scheme serves every
    member in every round (continuity).  So when the per-order minimum round counts
    already exceed the budget together, no scheme can hold them and `_construct`
    would only burn time discovering that.  Measured on `NP01:43`, most of the
    adjacent pairs pass this test, but the heavy ones do not -- and skipping them
    saves the closing search from running at all.
    """
    budget = model.cfg.max_rounds
    total = 0
    for i in chunk:
        order = model.orders[i]
        need = _min_rounds_for(model, (i,), order, order.pieces, budget)
        if need is None:
            return False
        total += need
        if total > budget:
            return False
    return True


def _seed_group(model, group, construct, rng, deadline, orders, cfg):
    """Split one (steel, diameter) group into constructible schemes.

    The preliminary round's dataset let every order stand alone: one order was
    small enough to fill a legal 50-150 m round by itself.  The semi-final data
    breaks that assumption in two ways, both measured on the real drop:

    * an order may be too *heavy* for one round -- 989 pieces of 43 mm bar weigh
      69.4 t, above the 60 t ceiling, so it needs at least two rounds; and
    * an order may be too *light* to reach the 50 m floor at a parallel count
      that still delivers its pieces -- 265 pieces at p=46 need only 28.7 m.

    Requiring `construct` to succeed per single order therefore empties `singles`
    for almost every large group and falls through to constructing the *entire*
    group in one scheme, which cannot fit in `max_rounds`.  The fix is to seed
    with sub-groups of at most `search_max_group` orders: try singles first (the
    fast, high-quality path), and only when a single is infeasible pack it with
    its neighbours into one small scheme.
    """
    singles, fallback = [], []
    for i in group:
        if _expired(deadline):
            # Out of time while seeding.  The remaining orders still need a scheme
            # for clause 12, so they go through the `_solo_scheme` path below rather
            # than being silently dropped.
            fallback.append(i)
            continue
        try:
            single = construct(model, (i,), rng, deadline)
        except SearchTimeout:
            single = None
        if single is None:
            fallback.append(i)
        else:
            singles.append(single)
    if cfg.coverage_shared or cfg.continuity:
        # Scoring coverage under the semi-final rule requires two different orders
        # to appear in the SAME round (`platform_score.evaluate`, coverage_shared
        # branch).  A plan of one-order schemes therefore scores coverage = 0 no
        # matter how good each scheme is, so grouping is not a quality knob here --
        # it is the objective.  `_pack_group` merges neighbours first and hands back
        # whatever it could not merge.
        merged, leftovers = _pack_group(model, group, singles, construct, rng, deadline, cfg)
        fallback = [i for pair in leftovers for i in pair]
        singles = merged
    if not fallback:
        return singles
    # Re-pack the orders that could not stand alone.  Measured on the real
    # semi-final drop, `max_rounds` is a *per-scheme* budget and the heavy orders
    # exhaust it on their own: at C60:26.5 an order worth 6,520 pieces needs >= 3
    # rounds (best single round delivers 2,442), so two of them cannot share a
    # 6-round scheme at all -- `_construct` correctly returns None.  Chunking is
    # therefore attempted, but a failed chunk is split rather than merged, and an
    # order that still cannot share gets a solo scheme of its own.  That keeps the
    # plan complete (clause 12) while never exceeding the per-scheme round budget.
    # Pairs only here too, for the same reason as in `_pack_group`: three-order
    # chunks never construct under the semi-final rules.
    window = 2
    index = 0
    while index < len(fallback):
        if _expired(deadline):
            break
        chunk = tuple(fallback[index:index + window])
        combined = None
        for attempt in range(8):
            if _expired(deadline):
                break
            try:
                combined = construct(model, chunk, rng, deadline, attempt > 0)
            except SearchTimeout:
                combined = None
                break
            if combined is not None:
                break
        if combined is not None:
            singles.append(combined)
            index += window
            continue
        if window > 2:
            window = max(2, window // 2)
            continue
        # Even a two-order window fails, so the two orders are genuinely unable to
        # share six rounds.  Emit each as its own scheme (`_solo_scheme` widens the
        # parallel sweep and the segment shape until the round budget fits).
        for i in chunk:
            solo = None
            for blank in model.catalogue((i,)):
                try:
                    solo = _solo_scheme(model, (i,), blank, rng, deadline, randomized=True)
                except SearchTimeout:
                    solo = None
                    break
                if solo is not None:
                    break
            if solo is None:
                raise ModelError(
                    f"Search found no feasible scheme for {orders[i].oid}"
                    f"; not a proof of infeasibility")
            singles.append(solo)
        index += window
    # Anything the deadline cut off still needs a scheme: clause 12 requires every
    # order to appear in the plan, so fall back to the dedicated solo builder, which
    # is a direct construction rather than a search and does not consult the clock
    # per candidate.  Only if even that fails is the order genuinely unpackable.
    for i in fallback[index:]:
        solo = None
        for blank in model.catalogue((i,)):
            solo = _solo_scheme(model, (i,), blank, rng, None, randomized=True)
            if solo is not None:
                break
        if solo is None:
            raise ModelError(
                f"Search found no feasible scheme for {orders[i].oid}"
                f"; not a proof of infeasibility")
        singles.append(solo)
    return singles


def _solo_scheme(model, ids, blank, rng, deadline, randomized=False):
    """Build a scheme for orders that no other order can share rounds with.

    `_construct` spreads each order's demand over ALL remaining rounds, which is
    right for a shared scheme (continuity forces every order into every round) but
    wrong for a solo order: the spread can overshoot the 6-round budget or leave a
    tail that no 50 m round can absorb.  Here the round count is chosen first, then
    the pieces are divided to fit it.

    The legal shapes are enumerated analytically.  A round's segment count `k` is
    legal exactly when

        min_bed_length <= k * size + segment_trim + round_trim <= min(bed_length, usable)

    and the round's mass `(k * size + segment_trim + round_trim) * parallel * linear`
    stays inside the 60 t ceiling, so for a fixed `parallel` the legal `k` are a
    contiguous range `[k_lo, k_hi]`.  Over `n` rounds the fewest segments that can
    carry the demand is therefore

        total_k = max(n * k_lo, ceil(pieces / parallel))

    any value up to `n * k_hi` that the over-production cap admits is walkable
    (a contiguous range sums to every value in between), and even division of
    `total_k` over `n` rounds keeps every `k` inside `[k_lo, k_hi]`.  So this is
    O(n * parallel_limit) arithmetic, then one `make_round` per round to build it.

    The equality that used to be here -- `total_k * parallel == pieces`, i.e.
    `parallel` must divide the demand -- is gone, and it was the wrong constraint.
    It left 3,304 of the 9,999 semi-final orders with NO legal scheme at all
    (measured with `diagnostics/semi_overproduction_probe.py`): the demand is often
    prime, and `parallel = 1`
    cannot reach the 50 m floor for a heavy order, so no `(n, p)` landed them.
    `_solo_scheme` returned None, `_seed_group` raised `ModelError`, and the whole
    chunk was lost.  The equality had been added to stop over-delivery, but that
    premise is wrong for the semi-final: constraints.txt clause 8 is a one-sided
    floor ("每订单冷床分配总重量须不低于该订单重量") and RULES.md 12.5 penalises SHORT
    delivery only -- over-production is uncredited, not forbidden (see
    `platform_score.Rules.numerator_capped_by_demand`).  `model.caps` is the real
    ceiling, and inside it the search now minimises the excess instead of refusing.

    Ranking: `total_k + n` is the scheme's knife count (`sum(ks) + 1` per round), and
    `parallel * total_k` is the over-delivery the yield numerator will not credit, so
    candidates are ordered by `(total_k + n, parallel * total_k)`.  Fewer rounds wins
    first, which is also why `n` is swept ascending: a small `n` needs fewer segments
    overall, so it improves both terms at once.
    """
    cfg = model.cfg
    order = model.orders[ids[0]]
    limit = model.parallel_limit(ids)
    size, linear = order.size, order.linear_weight
    tr = cfg.segment_trim + cfg.round_trim
    cap = model.caps[ids[0]]

    k_lo = max(1, _ceil((cfg.min_bed_length - tr) / size))
    len_hi = min(cfg.bed_length, model.blank_length(ids, blank))

    def k_hi_for(parallel):
        k_hi = _floor((len_hi - tr) / size)
        if parallel * linear > 0:
            k_hi = min(k_hi, _floor((cfg.bed_weight / (parallel * linear) - tr) / size))
        return k_hi

    def build(parallel, total_k, n):
        per, extra = divmod(total_k, n)
        if per < 1:
            return None
        ks = [per + 1] * extra + [per] * (n - extra)
        rounds = []
        for k in ks:
            r = model.make_round(ids, [k], parallel, blank)
            if r is None:
                return None
            rounds.append(r)
        return Batch(ids, blank.bid, tuple(rounds))

    for n in range(1, min(cfg.max_rounds, order.pieces) + 1):
        _deadline(deadline)
        candidates = []
        for parallel in range(1, limit + 1):
            _deadline(deadline)
            k_hi = k_hi_for(parallel)
            if k_hi < k_lo:
                continue
            total_k = max(n * k_lo, _ceil(order.pieces / parallel))
            if total_k > n * k_hi or parallel * total_k > cap:
                continue
            candidates.append((total_k + n, parallel * total_k, parallel, total_k))
        if not candidates:
            continue
        candidates.sort()
        if randomized and len(candidates) > 1:
            # Keep the knife/yield ranking, vary only among equally good shapes so
            # the annealer still sees a different incumbent on each restart.
            tied = [c for c in candidates if c[:2] == candidates[0][:2]]
            rng.shuffle(tied)
            candidates = tied + candidates[len(tied):]
        for _, _, parallel, total_k in candidates:
            got = build(parallel, total_k, n)
            if got is not None:
                return got
    return None


def search_10s(orders: list[Order], cfg: Config, seconds=10.0, seed=1, blanks=None,
               initial_plan=None, constructor=None, checkpoint=None, checkpoint_interval=60.0,
               stats=None) -> list[dict]:
    """Feasible incumbent + merge/split/move/swap/repack + annealing.

    Budget includes initialization, with a size-based reserve for export and
    validation. Cooperative checks are not an OS hard real-time guarantee.

    Seeding and annealing are budgeted SEPARATELY, and that separation is a fix
    rather than tidiness.  Seeding is not optional work: clause 12 requires every
    order to appear in a scheme, so `_seed_group` must run to completion or the
    plan is invalid -- it cannot be truncated to respect a deadline the way the
    annealing neighbourhood can.  Sharing one deadline between them made the
    caller's `seconds` unpredictable: on the real semi-final drop, `NP01:43`'s
    first 60 orders need ~36 s to seed, so any `seconds` below that expired the
    budget DURING seeding, `_seed_group` stopped early, and the `_deadline` check
    right after it raised `SearchTimeout` -- reporting an infeasible instance
    where the solver had simply been given less time than construction costs.
    (Measured: `search_10s(60 orders, seconds=25)` always raised, while
    `seconds=50` on the same 60 orders succeeded.)

    So the seed phase is allowed the time it needs and the annealing phase gets
    whatever remains of `seconds`; `reserve` is held back for export/validation.
    A seed phase that alone overruns `seconds` still yields a valid incumbent
    (the greedy rounds are complete), it just leaves no time to improve it.
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
        # `deadline=None` means "no clock": seeding must produce a complete set of
        # schemes (clause 12), so it is not interruptible.  See the docstring for
        # why sharing the annealing budget here was a bug.  `_seed_group` still
        # degrades gracefully inside -- a hard closing search falls back to
        # `_solo_scheme`, which is a direct construction -- so the phase terminates
        # on the problem's own difficulty rather than on a timer.
        bucket = _seed_group(model, group, construct, rng, None, orders, cfg)
        current_ids = []
        for single in bucket:
            current[next_id] = single
            current_ids.append(next_id)
            next_id += 1
        buckets.append(current_ids)
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
            ks = tuple(round((lengths[model.orders[i].oid] - model.cfg.segment_trim) / model.orders[i].size)
                       if model.orders[i].oid in lengths else 0 for i in ids)
            r = model.make_round(ids, ks, p, blank)
            rounds.append(Round(r.ks, r.parallel, count, r.knives, r.finished, count * blank.weight))
        output.append(Batch(ids, blank.bid, tuple(rounds)))
    return output


def _export(batches, orders, cfg):
    return [{"orders": [orders[i].oid for i in b.ids],
             "length_scheme": [{orders[i].oid: round(k * orders[i].size + cfg.segment_trim, 9) for i, k in zip(b.ids, r.ks) if k} for r in b.rounds],
             "counts": [r.parallel for r in b.rounds], "blank_type": b.blank_type,
             "blank_counts": [r.blanks for r in b.rounds]}
            for b in sorted(batches, key=lambda b: b.ids)]


def validate_plan(plan, orders, cfg, blanks=None, blank_rule='per_round'):
    """Recompute constraints and costs from public JSON, independently of make_round.

    blank_rule decides the minimum declared steel:
      'per_round'          every round must carry its own (net + 2m) physical mass
                           (historical behaviour, kept as the default)
      'aggregate'          only the plan total must cover sum((net + 2m) * p * mu)
      'aggregate_no_trim'  only the plan total must cover sum(net * p * mu)
      'finished_floor'     only the plan total must cover the finished product mass
                           itself, i.e. sum(k * p * mu_int) -- the bare physical
                           floor, raw >= finished
    The three looser modes exist to run the discriminating experiment described in
    runs/acceptance/ACCEPTANCE_REPORT.md; they are not claims about the platform.
    Note that `total` inside the round loop already includes the shared trim, which
    is why the no-trim flavour has to subtract it back out.
    """
    if blank_rule not in ('per_round', 'aggregate', 'aggregate_no_trim', 'finished_floor'):
        raise ModelError('unknown blank_rule: %r' % (blank_rule,))
    model = Model(orders, cfg, blanks)
    lookup = {o.oid: i for i, o in enumerate(orders)}
    seen, knives, finished, raw, covered, rounds = set(), 0, 0.0, 0.0, 0, 0
    finished_int_total = 0.0
    declared_total, required_total = 0.0, 0.0
    order_rounds = {}          # oid -> list of round indices, for the continuity rule
    allocated = {}             # oid -> allocated physical mass, for the clause 8 floor
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
        shared_round_orders = set()
        for j, (scheme, parallel, blank_count) in enumerate(zip(schemes, counts, blank_counts)):
            _integer(parallel, "counts")
            _integer(blank_count, "blank_counts")
            if parallel > cfg.bed_width:
                raise ModelError("Parallel count exceeds bed_width")
            if cfg.bed_width_mm is not None and parallel * orders[ids[0]].diameter + (parallel - 1) * cfg.bar_gap_mm > cfg.bed_width_mm + 1e-8:
                raise ModelError("Physical bed width exceeded")
            if not isinstance(scheme, dict) or not scheme or any(n not in produced for n in scheme):
                raise ModelError("Invalid length_scheme order")
            # Independent interpretation of the public schema, not make_round.
            shared = cfg.length_mode == 'net_shared_trim'
            total = 2 * cfg.trim if shared else 0.0
            finished_int_delta = 0.0
            if shared:
                knives += 1
            if len(set(scheme)) > 1:
                shared_round_orders.update(scheme)
            for name, length in scheme.items():
                _positive(length, "segment length")
                o = orders[lookup[name]]
                ratio = (length if shared else length - 2 * cfg.trim) / o.size
                k = round(ratio)
                if k < 1 or not math.isclose(ratio, k, rel_tol=1e-10, abs_tol=1e-8):
                    raise ModelError("Segment is not an integer multiple under the configured length_mode")
                if length + (2 * cfg.trim if shared else 0) > usable + 1e-8:
                    raise ModelError("Segment exceeds usable length from one blank")
                produced[name] += k * parallel
                total += length
                finished += k * parallel * o.size * linear
                finished_int_delta += k * parallel * o.size * o.linear_weight_int
                knives += k if shared else k + 1
                order_rounds.setdefault(name, []).append(j)
                # Mass this round allocates to this order.  Every one of the
                # `parallel` bars is cut into this order's share, so the delivered
                # mass is k pieces of `size` metres on each bar: the same reading the
                # platform's yield numerator uses (`length * parallel * linear_weight`
                # in platform_score.row_metrics).  Clause 8 is then normally
                # satisfied whenever the piece demand is met, because
                # pieces = ceil(weight / (size * linear)) <= k * parallel implies
                # k * size * linear * parallel >= weight.  It still has teeth for a
                # plan whose lengths and counts disagree, which is why it is checked
                # on the exports and in the independent checker rather than assumed.
                allocated[name] = allocated.get(name, 0.0) + k * o.size * parallel * linear
            finished_int_total += finished_int_delta
            mass = total * parallel * linear
            if not cfg.min_bed_length - 1e-8 <= total <= cfg.bed_length + 1e-8:
                raise ModelError("Bed length violation")
            if not cfg.min_bed_weight - 1e-8 <= mass <= cfg.bed_weight + 1e-8:
                raise ModelError("Bed weight violation")
            if blank_rule == 'per_round':
                if blank_count * usable + 1e-8 < total * parallel:
                    raise ModelError("Insufficient blanks for the parallel bars")
            else:
                declared_total += blank_count * blank.weight
                if blank_rule == 'aggregate':
                    required_total += total * parallel * linear
                elif blank_rule == 'aggregate_no_trim':
                    required_total += (total - (2 * cfg.trim if shared else 0.0)) * parallel * linear
                else:                              # 'finished_floor'
                    required_total += finished_int_delta
            raw += blank_count * blank.weight
            rounds += 1
        # Structural semi-final rules first, so a plan that both under-delivers and
        # breaks continuity reports the more specific defect.
        if cfg.continuity:
            for name in names:
                js = sorted(set(order_rounds.get(name, ())))
                if js and js != list(range(js[0], js[0] + len(js))):
                    raise ModelError(
                        f"Continuity violation for {name}: rounds {js} are not adjacent")
        if cfg.enforce_order_mass_floor:
            for name in names:
                if allocated.get(name, 0.0) + 1e-6 < orders[lookup[name]].weight:
                    raise ModelError(
                        f"Order mass floor violated for {name}: allocated "
                        f"{allocated.get(name, 0.0):.3f} kg < required {orders[lookup[name]].weight:.3f} kg")
        for name, count in produced.items():
            i = lookup[name]
            if not orders[i].pieces <= count <= model.caps[i]:
                raise ModelError(f"Demand/overproduction violation for {name}: {count}, allowed [{orders[i].pieces}, {model.caps[i]}]")
        # Coverage rule.  The semi-final rule counts only orders that shared a
        # cold-bed round; the preliminary rule counted every order of a multi-order
        # scheme regardless of which round it sat in.  `coverage_shared` names the
        # choice explicitly, so the two readings can be compared without also
        # flipping continuity (they are separate sentences in the rule files).
        covered += len(shared_round_orders) if cfg.coverage_shared else (
            len(names) if len(names) > 1 else 0)
    if seen != set(lookup):
        raise ModelError(f"Missing orders: {sorted(set(lookup) - seen)[:10]}")
    if blank_rule != 'per_round' and declared_total + 1e-3 < required_total:
        raise ModelError("Aggregate declared steel below the total %s physical mass"
                         % {'aggregate': '(net+2m)', 'aggregate_no_trim': 'net',
                            'finished_floor': 'finished'}[blank_rule])
    if blank_rule == 'finished_floor':
        # The probe deliberately declares less steel than the true physical product
        # mass: that IS the hypothesis under test. The only floor left is the
        # platform's own accounting, which uses the diameter truncated to whole
        # millimetres. Keep that guard so the probe cannot claim raw < finished.
        if finished_int_total > raw + 1e-7 * max(1, raw):
            raise ModelError("Finished (integer-diameter) mass exceeds raw mass")
    elif finished > raw + 1e-7 * max(1, raw):
        raise ModelError("Finished mass exceeds raw mass")
    result = {"orders": len(orders), "plans": len(plan), "rounds": rounds, "knives": knives,
              "finished_weight": finished, "blank_weight": raw, "yield_rate": finished / raw if raw else 0.0,
              "coverage": covered / len(orders) if orders else 0.0, "objective": cfg.objective,
              "length_mode": cfg.length_mode}
    if cfg.objective == 'platform_score':
        result['platform_score_estimate'] = -_key((knives,finished,raw,covered),cfg,len(orders))[0]
    elif cfg.baseline_knives is not None:
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
    ap.add_argument("--objective", choices=["lex", "score", "platform_score"])
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
