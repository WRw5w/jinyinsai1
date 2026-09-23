"""Validate, combine compatible schemes, and create a one-JSON submission ZIP.

Invalid source orders are reported, never silently repaired or represented by
invented cutting lengths. Packaging success is not official evaluator approval.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import zipfile
from collections import defaultdict
from pathlib import Path

from competition_solver import atomic_json
from solver import Config, load_blanks, load_orders, validate_plan


def merge_compatible(plan, orders, max_rounds):
    """Bin-pack whole existing schemes; every original cutting round is preserved."""
    lookup = {o.oid: o for o in orders}
    groups = defaultdict(list)
    for batch in plan:
        first = lookup[batch['orders'][0]]
        groups[first.steel, first.diameter, batch['blank_type']].append(batch)
    result = []
    for key in sorted(groups):
        bins, sizes = [], []
        for batch in sorted(groups[key], key=lambda b: (-len(b['counts']), b['orders'][0])):
            nr = len(batch['counts'])
            if nr > max_rounds:
                raise ValueError('Source scheme exceeds the round limit')
            target = next((i for i, size in enumerate(sizes) if size + nr <= max_rounds), None)
            if target is None:
                bins.append([batch])
                sizes.append(nr)
            else:
                bins[target].append(batch)
                sizes[target] += nr
        # Prevent an avoidable one-order final bin by moving a whole batch from
        # another bin. This does not alter any physical cutting allocation.
        for i, bucket in enumerate(bins):
            if sum(len(b['orders']) for b in bucket) != 1:
                continue
            moved = False
            for j, donor in enumerate(bins):
                if j == i:
                    continue
                for k, batch in enumerate(donor):
                    if sizes[i] + len(batch['counts']) > max_rounds:
                        continue
                    remaining_orders = sum(len(b['orders']) for b in donor) - len(batch['orders'])
                    if remaining_orders < 2:
                        continue
                    bucket.append(donor.pop(k))
                    sizes[i] += len(batch['counts'])
                    sizes[j] -= len(batch['counts'])
                    moved = True
                    break
                if moved:
                    break
        for bucket in bins:
            merged = {'orders': [], 'length_scheme': [], 'counts': [], 'blank_type': key[2], 'blank_counts': []}
            for batch in bucket:
                for field in ('orders', 'length_scheme', 'counts', 'blank_counts'):
                    merged[field].extend(copy.deepcopy(batch[field]))
            result.append(merged)
    return result


def round_fingerprints(plan):
    rows = []
    for batch in plan:
        for lengths, p, count in zip(batch['length_scheme'], batch['counts'], batch['blank_counts']):
            rows.append(json.dumps([batch['blank_type'], sorted(lengths.items()), p, count], ensure_ascii=False))
    return sorted(rows)


def build(args):
    cfg = Config(**json.loads(Path(args.config).read_text(encoding='utf-8')))
    orders = load_orders(args.orders, cfg)
    blanks = load_blanks(args.blanks)
    source = json.loads(Path(args.input).read_text(encoding='utf-8'))
    before = validate_plan(source, orders, cfg, blanks)
    plan = merge_compatible(source, orders, cfg.max_rounds)
    after = validate_plan(plan, orders, cfg, blanks)
    if round_fingerprints(source) != round_fingerprints(plan):
        raise ValueError('Packaging changed original cutting rounds')
    for key in ('knives', 'rounds', 'finished_weight', 'blank_weight'):
        if not math.isclose(before[key], after[key], rel_tol=1e-12, abs_tol=1e-7):
            raise ValueError(f'Unexpected change to physical metric: {key}')
    if after['coverage'] < before['coverage']:
        raise ValueError('Combination coverage decreased')
    if not args.team or any(c in args.team for c in '<>:"/\\|?*'):
        raise ValueError('Invalid team name for output filename')
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    name = f'初赛结果_{args.team}'
    json_path, zip_path = output / f'{name}.json', output / f'{name}.zip'
    # Keep the exact published schema, with no audit fields inside the solution.
    raw = (json.dumps(plan, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')
    json_path.write_bytes(raw)
    with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(json_path.name, raw)
    # Verify the actual archive bytes and reopen the actual submitted JSON.
    with zipfile.ZipFile(zip_path) as archive:
        if archive.namelist() != [json_path.name] or archive.testzip() is not None:
            raise ValueError('Invalid archive layout or CRC')
        packed = archive.read(json_path.name)
        if packed != raw:
            raise ValueError('Archive content differs from validated JSON')
        validate_plan(json.loads(packed), orders, cfg, blanks)
    audit = json.loads(Path(args.audit).read_text(encoding='utf-8'))
    report = dict(package_created=True, official_acceptance_verified=False,
                  complete_original_order_coverage=not audit.get('rejected'),
                  input_result=str(Path(args.input).resolve()),
                  source_order_count=audit['source_orders'], included_order_count=len(orders),
                  combination_coverage_over_source=round(after['coverage'] * len(orders)) / audit['source_orders'],
                  excluded_orders=audit.get('rejected', []), model_assumptions=audit.get('assumptions', []),
                  before=before, after=after,
                  json_file=str(json_path.resolve()), zip_file=str(zip_path.resolve()),
                  json_sha256=hashlib.sha256(raw).hexdigest(),
                  zip_sha256=hashlib.sha256(zip_path.read_bytes()).hexdigest(),
                  checks=['all valid orders exactly once', 'integer piece delivery and segment lengths',
                          'steel/diameter homogeneity', 'bed length 50..150m', 'bed width <=2m',
                          'bed material <=60000kg', 'round limit from model', 'blank material accounting',
                          'all original physical cutting rounds preserved', 'single JSON at ZIP root', 'ZIP CRC'])
    atomic_json(output / 'validation_report.json', report)
    note = f'''# 提交候选包说明

上传文件：{zip_path.name}，压缩包根目录仅包含同名 JSON。

有效订单：{len(orders)} / {audit['source_orders']}。原始订单 A20260949 的定尺为 -1100 mm，无法用于物理可行的锯切。本包按题面允许的异常数据过滤流程隔离该订单，没有猜测其正确定尺，没有添加虚构订单方案。原始文件未改动。

刀数：{after['knives']}；成材率：{after['yield_rate']:.8%}；组合覆盖率：{after['coverage']:.8%}（分母为有效订单数）；按原始全部订单计的组合覆盖率：{report['combination_coverage_over_source']:.8%}。

本次只合并同钢种、同直径、同坯型的方案，原始每轮的长度、并列支数、钢坯数量完全保留；因此刀数、物料消耗与半小时解一致。每个新方案最多 {cfg.max_rounds} 轮，符合当前模型上限。该上限、切损、计刀与钢坯物料口径等未由四条约束完整规定，详见 validation_report.json。

本地格式、约束和 ZIP 校验通过，不等于官方评分器确认通过。若官方校验要求原始 5000 个订单号全部出现，必须先取得 A20260949 的正确值或官方异常过滤口径；不能用凭空补值来保证通过。本脚本没有执行网页上传。

若需用实际队名命名，运行 `python -X utf8 build_submission.py --team 实际队名`，会同时修改 JSON 和 ZIP 的名称。
'''
    (output / '提交说明.md').write_text(note, encoding='utf-8')
    print(json.dumps({'zip_file': str(zip_path.resolve()), 'metrics': after,
                      'included_orders': len(orders), 'source_orders': audit['source_orders'],
                      'official_acceptance_verified': False}, ensure_ascii=False))
    return report


def parser():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input', default='runs/half_hour_20260915_2114/result.json')
    ap.add_argument('--orders', default='data/orders.normalized.csv')
    ap.add_argument('--blanks', default='data/blanks.normalized.csv')
    ap.add_argument('--config', default='data/competition.config.json')
    ap.add_argument('--audit', default='data/data_audit.json')
    ap.add_argument('--team', default='棒材优化')
    ap.add_argument('--output-dir', default='submission')
    return ap


if __name__ == '__main__':
    build(parser().parse_args())
