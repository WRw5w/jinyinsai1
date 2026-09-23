"""Independent submission audit using net lengths and a shared 2m round trim.

Reads the raw competition CSVs directly; deliberately does not import solver.py.
This reconstructs the observed platform length contract, not its private code.

Two rule sets are supported, matching platform_score.Rules:

* ``'prelim'`` -- the four-clause preliminary constraints.txt.  No round cap, no
  continuity, and overproduction is the documented failure mode.
* ``'semi'``   -- the twelve-clause semi-final constraints.txt.  Adds the six-round
  cap, adjacent-round continuity, the per-order allocated-mass floor, and turns
  the delivery failure from over- to under-production.

Two floors were already here and stay in both modes, because they are the same
sentences in both files: clause "每轮冷床铸坯总重须不低于本轮棒材重" (per-round
declared mass >= physical round mass) and "所有订单均须参加锯切计算" (every valid
order appears exactly once).
"""
import argparse
import csv
import json
import math
import zipfile
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_CEILING
from pathlib import Path

ROUND_FILES = {
    'prelim': dict(
        orders=('orders_quarter.csv', 'utf-8-sig',
                dict(oid='订单号', size='订单定尺(mm)', diameter='订单直径(mm)',
                     weight='订单重量(t)', steel='坯料钢种')),
        blanks=('blank_used.csv', 'utf-8-sig',
                dict(bid='坯料', a='宽度mm', b='厚度mm', length='长度mm')),
        max_rounds=None, continuity=False,
    ),
    'semi': dict(
        orders=('orders_semi.csv', 'gbk',
                dict(oid='订单号', size='定尺长度', diameter='规格',
                     weight='订单重量', steel='钢种')),
        blanks=('blank_used_finals.csv', 'gbk',
                dict(bid='编号', a='钢坯长度', b='钢坯宽度', length='钢坯定尺')),
        max_rounds=6, continuity=True,
    ),
}


def load_orders_and_blanks(data, round):
    """Parse either round's CSVs into Decimal orders and blank masses."""
    D = Decimal
    spec = ROUND_FILES[round]
    ofile, oenc, ocols = spec['orders']
    orders, excluded = {}, []
    with (data / ofile).open(encoding=oenc, newline='') as f:
        for row in csv.DictReader(f):
            oid = row[ocols['oid']].strip()
            size = D(row[ocols['size']]) / 1000
            dia = D(row[ocols['diameter']].strip())
            weight = D(row[ocols['weight']]) * 1000
            # Generalized anomaly predicate across both rounds.
            if size <= 0 or dia <= 0 or weight <= 0:
                excluded.append(oid)
                continue
            linear = D(str(math.pi)) * (dia / 1000) ** 2 / 4 * D(9860)
            # the platform's yield numerator truncates the diameter to whole mm
            linear_int = D(str(math.pi)) * (D(int(dia)) / 1000) ** 2 / 4 * D(9860)
            orders[oid] = dict(size=size, dia=dia, linear=linear, linear_int=linear_int,
                               weight=weight, steel=row[ocols['steel']].strip(),
                               pieces=int((weight / (linear * size) - D('1e-8'))
                                          .to_integral_value(rounding=ROUND_CEILING)))
    bfile, benc, bcols = spec['blanks']
    blanks = {}
    with (data / bfile).open(encoding=benc, newline='') as f:
        for row in csv.DictReader(f):
            blanks[int(float(row[bcols['bid']]))] = (
                D(row[bcols['a']]) * D(row[bcols['b']]) * D(row[bcols['length']])
                * D(9860) / D(10) ** 9)
    return orders, blanks, excluded, spec


def check(plan, data=Path('data'), check_delivery=True, weight_mode='strict',
          round='prelim'):
    """Audit a plan.

    weight_mode decides how the declared blank material is checked:
      'strict'            every round must carry its own (net + 2 m) physical mass
                          (our own conservative reading; never falsified either)
      'aggregate'         only the plan total must cover sum((net + 2 m) * p * mu)
      'aggregate_no_trim' only the plan total must cover sum(net * p * mu)
      'finished_floor'    only the plan total must cover the finished product mass
                          sum(k * p * mu_int), i.e. the bare raw >= finished floor

    The official rule for the minimum declared steel has never been observed --
    every package we ever submitted declared far more than any candidate lower
    bound -- and the problem statement only says "成材率 = 成材总重量/钢坯总重量".
    The three aggregate modes exist to run that discriminating experiment; they are
    NOT a claim about the platform.
    """
    if weight_mode not in ('strict', 'aggregate', 'aggregate_no_trim', 'finished_floor'):
        raise ValueError('unknown weight_mode: %r' % (weight_mode,))
    if round not in ROUND_FILES:
        raise ValueError('unknown round: %r' % (round,))
    D = Decimal
    orders, blanks, excluded, spec = load_orders_and_blanks(data, round)
    errors = defaultdict(list)
    produced, used = Counter(), Counter()
    length_min, length_max, mass_max = D('Infinity'), D(0), D(0)
    physical_rows = 0
    declared_total, required_total = D(0), D(0)
    # per-order physical mass actually allocated, for constraints.txt clause 8
    allocated = defaultdict(lambda: D(0))
    order_rounds = defaultdict(list)

    def error(kind, a=None, j=None, **extra):
        # `scheme` and `round` are reserved: they carry the location, so callers
        # must not also pass them through **extra.
        extra.pop('scheme', None)
        extra.pop('round', None)
        errors[kind].append(dict(scheme=a, round=j, **extra))

    if not isinstance(plan, list):
        raise ValueError('Solution must be an array')
    for a, batch in enumerate(plan):
        if not isinstance(batch, dict) or set(batch) != {'orders', 'length_scheme', 'counts', 'blank_type', 'blank_counts'}:
            error('schema', a)
            continue
        names = batch['orders']
        if not isinstance(names, list) or not names or any(not isinstance(n, str) or n not in orders for n in names):
            error('order_identity', a)
            continue
        used.update(names)
        if len({(orders[n]['steel'], orders[n]['dia']) for n in names}) != 1:
            error('homogeneity', a)
            continue
        fields = [batch[k] for k in ['length_scheme', 'counts', 'blank_counts']]
        if not all(isinstance(v, list) for v in fields) or not fields[0] or len({len(v) for v in fields}) != 1:
            error('round_arrays', a)
            continue
        # constraints.txt clause 4 (semi-final only): at most six cold-bed rounds.
        if spec['max_rounds'] is not None and len(fields[0]) > spec['max_rounds']:
            error('round_cap', a, rounds=len(fields[0]), limit=spec['max_rounds'])
        if type(batch['blank_type']) is not int or batch['blank_type'] not in blanks:
            error('blank_type', a)
            continue
        linear, dia = orders[names[0]]['linear'], orders[names[0]]['dia']
        for j, (scheme, count, stock) in enumerate(zip(*fields)):
            if not isinstance(scheme, dict) or not scheme or any(n not in names for n in scheme):
                error('round_orders', a, j)
                continue
            if type(count) is not int or count <= 0 or type(stock) is not int or stock <= 0:
                error('integer_counts', a, j)
                continue
            for oid in scheme:
                order_rounds[oid].append((a, j))
            net = D(0)
            finished_int = D(0)
            for oid, length in scheme.items():
                if isinstance(length, bool) or not isinstance(length, (int, float)) or not math.isfinite(length) or length <= 0:
                    error('invalid_length', a, j, order=oid)
                    continue
                value = D(str(length))
                net += value
                ratio = value / orders[oid]['size']
                k = int(ratio.to_integral_value())
                if k <= 0 or abs(ratio - k) > D('1e-8'):
                    error('noninteger_multiple', a, j, order=oid)
                else:
                    produced[oid] += k * count
                    # k pieces of `size` metres each, `count` bars in parallel.  The
                    # mass the round hands this order therefore scales with `count`,
                    # matching the platform's own yield numerator.
                    piece_mass = k * orders[oid]['size'] * count * orders[oid]['linear']
                    finished_int += k * orders[oid]['size'] * count * orders[oid]['linear_int']
                    allocated[oid] += piece_mass
            physical = net + D(2)
            mass = physical * count * linear
            physical_rows += 1
            length_min, length_max = min(length_min, physical), max(length_max, physical)
            mass_max = max(mass_max, mass)
            if physical < 50 - D('1e-8') or physical > 150 + D('1e-8'):
                error('bed_length', a, j, physical_length_m=float(physical))
            if mass > 60000 + D('1e-7'):
                error('bed_weight', a, j, weight_kg=float(mass))
            if dia * count > 2000 + D('1e-8'):
                error('bed_width', a, j)
            declared = stock * blanks[batch['blank_type']]
            if weight_mode == 'strict':
                if declared + D('1e-7') < mass:
                    error('blank_material', a, j)
            else:
                if weight_mode == 'aggregate':
                    required = mass
                elif weight_mode == 'aggregate_no_trim':
                    required = net * count * linear
                else:
                    required = finished_int
                declared_total += declared
                required_total += required
    if weight_mode != 'strict' and declared_total + D('1e-5') < required_total:
        error('blank_material_total', declared_kg=float(declared_total),
              required_kg=float(required_total))
    # constraints.txt clause 6 (semi-final only): each order's rounds must be adjacent.
    if spec['continuity']:
        for oid, places in order_rounds.items():
            by_scheme = defaultdict(list)
            for a, j in places:
                by_scheme[a].append(j)
            for a, js in by_scheme.items():
                js = sorted(js)
                if len(js) > 1 and js != list(range(js[0], js[0] + len(js))):
                    error('continuity', order=oid, scheme=a, rounds=js)
        # `跨轮接续不连续` -- STRICTER than round adjacency, pinned by the
        # 2026-09-23 official feedback on submission_semi_merged_v2 (0 points,
        # 7030 violations, 35150 penalty).  The platform reads a scheme's rounds
        # as one continuous billet stream, so the LAST order cut in round j must
        # be the FIRST order cut in round j+1.  Emitting the same key order in
        # every round satisfies "same orders" but breaks this seam whenever two
        # rounds share orders.  The count here must reproduce the official 7030
        # on that ZIP.
        for a, batch in enumerate(plan if isinstance(plan, list) else []):
            rounds = batch.get('length_scheme') or []
            for j, (left, right) in enumerate(zip(rounds, rounds[1:])):
                if left and right and set(left) & set(right) \
                        and next(reversed(left)) != next(iter(right)):
                    error('continuity_seam', scheme=a, round=j,
                          left_last=next(reversed(left)), right_first=next(iter(right)))
    for oid, order in orders.items():
        if used[oid] != 1:
            error('order_coverage', order=oid, occurrences=used[oid])
        # Delivery floor, both rounds: piece demand is a one-sided floor here.  The
        # preliminary scripts' over-production penalty was documented as broken and
        # the semi-final round penalises under-production instead, so neither round
        # reports an overshoot as a violation of the *checker*.  Overshoot still
        # costs score, because the yield numerator stops at the demanded mass.
        if check_delivery and produced[oid] < order['pieces']:
            error('short_delivery', order=oid, actual=produced[oid], required=order['pieces'])
    # constraints.txt clause 8 (semi-final only): the mass allocated on the cold beds
    # for an order must reach that order's weight.  It is a separate sentence from
    # the per-round mass floor above, and it is the one constraint the preliminary
    # data could never exercise because the preliminary clause list omits it.
    if spec['max_rounds'] is not None:
        for oid, order in orders.items():
            if used[oid] == 1 and allocated[oid] + D('1e-6') < order['weight']:
                error('order_mass_floor', order=oid, allocated_kg=float(allocated[oid]),
                      required_kg=float(order['weight']))
    return dict(passed=not errors, error_counts={k: len(v) for k, v in errors.items()},
                errors=dict(errors), valid_orders=len(orders), excluded_invalid_orders=excluded,
                source_orders=len(orders) + len(excluded), round_count=physical_rows,
                min_physical_length_m=float(length_min) if physical_rows else None,
                max_physical_length_m=float(length_max), max_round_weight_kg=float(mass_max),
                official_acceptance_verified=False, weight_mode=weight_mode, round=round,
                declared_blank_kg=float(declared_total) if weight_mode != 'strict' else None,
                required_blank_kg=float(required_total) if weight_mode != 'strict' else None,
                weight_check={
                    'strict': 'conservative pi-based physical round mass including 2m shared trim',
                    'aggregate': 'plan-total declared steel vs total (net+2m) physical mass',
                    'aggregate_no_trim': 'plan-total declared steel vs total net mass',
                    'finished_floor': 'plan-total declared steel vs finished product mass only',
                }[weight_mode])


def read_plan(path):
    path = Path(path)
    if path.suffix.lower() == '.zip':
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if len(names) != 1 or '/' in names[0] or '\\' in names[0] or not names[0].endswith('.json') or z.testzip():
                raise ValueError('ZIP must contain one JSON at the root with valid CRC')
            return json.loads(z.read(names[0]))
    return json.loads(path.read_text(encoding='utf-8-sig'))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('file')
    ap.add_argument('--data', default='data')
    ap.add_argument('--round', default='prelim', choices=['prelim', 'semi'])
    ap.add_argument('--report')
    ap.add_argument('--weight-mode', default='strict',
                    choices=['strict', 'aggregate', 'aggregate_no_trim', 'finished_floor'])
    args = ap.parse_args()
    result = check(read_plan(args.file), Path(args.data), weight_mode=args.weight_mode,
                   round=args.round)
    if args.report:
        Path(args.report).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'errors'}, ensure_ascii=False))
    raise SystemExit(0 if result['passed'] else 2)
