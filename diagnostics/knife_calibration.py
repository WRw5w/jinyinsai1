"""Reproduce submitted knife feedback using only physical formula candidates.

No fitted constants: candidates vary entry-vs-row trim cuts, standard integer
rounding, 0/1/2 m trim, and metre-vs-millimetre arithmetic. Three independent
official feedback points must all match. This is evidence about observable
behaviour, not a copy of the private evaluator.
"""
import csv
import hashlib
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CASES = {
    'submission_fixed': 113683,
    'submission_optimized': 92913,
    'submission_normal_985': 119268,
}


def investigate():
    with (ROOT / 'data/orders_quarter.csv').open(encoding='utf-8-sig', newline='') as f:
        sizes = {r['订单号']: float(r['订单定尺(mm)']) for r in csv.DictReader(f)}
    cases = []
    for directory, official in CASES.items():
        path = ROOT / directory / '初赛结果_棒材优化.json'
        raw = path.read_bytes()
        plan = json.loads(raw.decode('utf-8-sig'))
        entries = [(length, sizes[oid]) for b in plan for row in b['length_scheme']
                   for oid, length in row.items()]
        rows = sum(len(b['length_scheme']) for b in plan)
        mathematical_k = sum(round(length / (mm / 1000)) for length, mm in entries)
        floor_k = sum(int(length // (mm / 1000)) for length, mm in entries)
        cases.append(dict(name=directory, official=official, sha256=hashlib.sha256(raw).hexdigest(),
                          entries=entries, rows=rows, mathematical_k=mathematical_k,
                          float_floor_k=floor_k))
    candidates = []
    for units in ('m', 'mm'):
        scale = 1 if units == 'm' else 1000
        for trim_m in (0, 1, 2):
            for rounding in ('round', 'int_div', 'floor_div', 'ceil_div'):
                for add_per_entry in (0, 1, 2):
                    for add_per_row in (0, 1, 2):
                        values = []
                        for case in cases:
                            total = add_per_entry * len(case['entries']) + add_per_row * case['rows']
                            for length, mm in case['entries']:
                                dividend = max(0, (length - trim_m) * scale)
                                divisor = mm / 1000 if units == 'm' else mm
                                value = (int(dividend // divisor) if rounding == 'floor_div'
                                         else {'round': round, 'int_div': int, 'ceil_div': math.ceil}[rounding](dividend / divisor))
                                total += value
                            values.append(total)
                        error = sum(abs(value - case['official']) for value, case in zip(values, cases))
                        candidates.append(dict(units=units, trim_m=trim_m, rounding=rounding,
                                               add_per_entry=add_per_entry, add_per_row=add_per_row,
                                               predictions=values, total_absolute_error=error))
    candidates.sort(key=lambda c: c['total_absolute_error'])
    return dict(
        calibrated_formula='sum(int(length_m // (order_length_mm / 1000)) + 1) over every order entry',
        semantics='One additional cut per order segment; Python floating floor division, independent of parallel count.',
        caveat='Three complete feedback points reproduced exactly; private evaluator code is unavailable.',
        cases=[{k: (len(v) if k == 'entries' else v) for k, v in case.items()} for case in cases],
        exact_matches=[c for c in candidates if not c['total_absolute_error']],
        candidate_count=len(candidates), best_candidates=candidates[:20],
    )


if __name__ == '__main__':
    report = investigate()
    target = Path(__file__).with_suffix('.json')
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
