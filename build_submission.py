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
from platform_check import check
from platform_score import evaluate as evaluate_platform
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
    if cfg.length_mode != 'net_shared_trim':
        raise ValueError('Competition ZIP requires net_shared_trim; legacy trimmed exports are invalid')
    round_name = getattr(args, 'round', 'prelim')
    orders = load_orders(args.orders, cfg, skip_invalid=True)
    blanks = load_blanks(args.blanks)
    source = json.loads(Path(args.input).read_text(encoding='utf-8'))
    mode = getattr(args, 'weight_mode', 'strict')
    rule = 'per_round' if mode == 'strict' else mode
    before = validate_plan(source, orders, cfg, blanks, blank_rule=rule)
    if getattr(args, 'no_merge', False):
        # The source plan is already the final scheme set: its rounds were shaped
        # to satisfy clause 6 (no skipped rounds, adjacent round sets differ).
        # Concatenating rounds from different schemes (merge_compatible) would
        # splice unrelated round sets together and reintroduce the continuity
        # violation -- so packaging must be byte-for-byte identity here.
        plan = copy.deepcopy(source)
    else:
        plan = merge_compatible(source, orders, cfg.max_rounds)
    after = validate_plan(plan, orders, cfg, blanks, blank_rule=rule)
    independent = check(plan, data=Path(getattr(args, 'data', 'data')), weight_mode=mode,
                        round=round_name)
    if not independent['passed']:
        raise ValueError(f"Independent platform-contract check failed: {independent['error_counts']}")
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
    prefix = '复赛结果' if round_name == 'semi' else '初赛结果'
    name = f'{prefix}_{args.team}'
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
        validate_plan(json.loads(packed), orders, cfg, blanks, blank_rule=rule)
        if not check(json.loads(packed), data=Path(getattr(args, 'data', 'data')),
                     weight_mode=mode, round=round_name)['passed']:
            raise ValueError('Packed JSON failed independent validation')
    audit = json.loads(Path(args.audit).read_text(encoding='utf-8'))
    # Knife baseline B.  The semi-final value is NOT in any official written rule:
    # 160000 comes only from the group Q&A archived in
    # evidence/rules/BASELINE_160000_SOURCE.md, while 90000 is the preliminary
    # value RULES.md records and the default baked into platform_score.Rules.
    # The driver build_semi_plan2.py optimises at 160000, so silently inheriting
    # platform_score's 90000 default here would make the packaged report disagree
    # with the plan it packages and with 复赛技术报告.md §6.7.  Make the choice
    # explicit and report BOTH readings, so no single one is presented as settled.
    default_baseline = 160000.0 if round_name == 'semi' else 90000.0
    baseline = default_baseline if args.baseline_knives is None else args.baseline_knives
    prediction = evaluate_platform(plan, data=Path(getattr(args, 'data', 'data')),
                                   round_name=round_name, baseline_knives=baseline)
    anchor = None
    if baseline != 90000.0:
        anchor = evaluate_platform(plan, data=Path(getattr(args, 'data', 'data')),
                                   round_name=round_name, baseline_knives=90000.0)
    delivered = defaultdict(int)
    lookup = {o.oid: o for o in orders}
    for batch in plan:
        for lengths, parallel in zip(batch['length_scheme'], batch['counts']):
            for oid, length in lengths.items():
                delivered[oid] += round(length / lookup[oid].size) * parallel
    demand_mass = sum(o.pieces * o.size * o.linear_weight for o in orders)
    production = dict(per_order_extra_limit=cfg.max_overproduction_ratio,
                      orders_with_extra_pieces=sum(delivered[o.oid] > o.pieces for o in orders),
                      max_actual_extra_ratio=max(delivered[o.oid] / o.pieces - 1 for o in orders),
                      rounded_demand_kg=demand_mass,
                      extra_kg=after['finished_weight'] - demand_mass,
                      mass_weighted_extra_ratio=after['finished_weight'] / demand_mass - 1)
    report = dict(package_created=True, official_acceptance_verified=False,
                  complete_original_order_coverage=not audit.get('rejected'),
                  input_result=str(Path(args.input).resolve()),
                  source_order_count=audit['source_orders'], included_order_count=len(orders),
                  combination_coverage_over_source=round(after['coverage'] * len(orders)) / audit['source_orders'],
                  excluded_orders=audit.get('rejected', []), model_assumptions=audit.get('assumptions', []),
                  before=before, after=after, production=production,
                  before_after_are_legacy_physical_model_not_official_score=True,
                  calibrated_prediction=prediction,
                  calibrated_prediction_at_90000_anchor=anchor,
                  calibrated_prediction_baseline_knives=baseline,
                  independent_platform_check=independent,
                  blank_weight_mode=mode,
                  json_file=str(json_path.resolve()), zip_file=str(zip_path.resolve()),
                  json_sha256=hashlib.sha256(raw).hexdigest(),
                  zip_sha256=hashlib.sha256(zip_path.read_bytes()).hexdigest(),
                  round=round_name,
                  checks=['all valid orders exactly once', 'integer piece delivery and segment lengths',
                          'steel/diameter homogeneity', 'bed length 50..150m', 'bed width <=2m',
                          'bed material <=60000kg', 'round limit from model', 'blank material accounting',
                          'all original physical cutting rounds preserved', 'single JSON at ZIP root', 'ZIP CRC']
                         + (['semi: <=6 rounds per scheme', 'semi: adjacent rounds per order',
                             'semi: per-order allocated mass >= order weight'] if round_name == 'semi' else []))
    atomic_json(output / 'validation_report.json', report)
    if round_name == 'semi':
        anomaly_note = ('原始订单 B20270281 的订单重量为 -25.418 t（非正重量），无法给出物理可行的锯切方案。'
                        '本包按题面允许的异常数据过滤流程隔离该订单，没有猜测其正确重量，没有添加虚构订单方案。原始文件未改动。')
        knife_note = '按复赛口径（每轮一组头尾，刀数 = 段数 + 1）计算的预测刀数：'
        calibration_note = ('复赛未公布基准刀数与时间子分口径，以上总分为本地模型结果，'
                            '不代表已取得官方评分器源码或穷尽全部规则。')
        tail_note = ('本地格式、约束和 ZIP 校验通过，不等于官方评分器确认通过。'
                     f"若官方校验要求原始 {audit['source_orders']} 个订单号全部出现，"
                     '必须先取得 B20270281 的正确值或官方异常过滤口径；不能用凭空补值来保证通过。'
                     '本脚本没有执行网页上传。')
    else:
        anomaly_note = ('原始订单 A20260949 的定尺为 -1100 mm，无法用于物理可行的锯切。'
                        '本包按题面允许的异常数据过滤流程隔离该订单，没有猜测其正确定尺，没有添加虚构订单方案。原始文件未改动。')
        knife_note = '按七个官方校准点复现的预测刀数：'
        calibration_note = ('七个历史官方校准点的刀数（及展示成材率）均已复现，'
                            '不代表已取得评分器源码或穷尽全部规则。')
        tail_note = ('本地格式、约束和 ZIP 校验通过，不等于官方评分器确认通过。'
                     '若官方校验要求原始 5000 个订单号全部出现，必须先取得 A20260949 的正确值'
                     '或官方异常过滤口径；不能用凭空补值来保证通过。本脚本没有执行网页上传。')
    if getattr(args, 'no_merge', False):
        merge_note = ('本打包步骤**不做任何轮次合并**：源方案已由 `continuity_shaper` 按 clause 6 定形'
                      '（同一订单的轮次连续、相邻两轮订单集合不同），逐字节原样写入，'
                      '因此「校验的对象」与「提交的对象」完全相同。')
    else:
        merge_note = '本打包步骤只合并同钢种、同直径、同坯型的方案，保留每轮参数。'
    if anchor is None:
        score_note = (f"预测总分（基准刀数 B={baseline:g}）：刀数子分封顶假设下 "
                      f"{prediction['score_capped']:.6f}；不封顶假设下 {prediction['score_uncapped']:.6f}。"
                      "新包仍需官方实测。")
        baseline_note = f"基准刀数取 B={baseline:g}。"
    else:
        score_note = (f"预测总分（**同时给出两个基准刀数，不取其一**）："
                      f"B=90000（初赛值 / `RULES.md` 所记 / `platform_score` 默认）时，"
                      f"刀数子分封顶假设下 {anchor['score_capped']:.6f}，不封顶 {anchor['score_uncapped']:.6f}；"
                      f"B={baseline:g}（群答疑给出的复赛值，档案 "
                      f"`evidence/rules/BASELINE_160000_SOURCE.md`）时，"
                      f"封顶 {prediction['score_capped']:.6f}，不封顶 {prediction['score_uncapped']:.6f}。"
                      "新包仍需官方实测。")
        baseline_note = ("基准刀数 B 无官方书面文件：复赛值 160000 只见于群答疑，90000 是初赛值，"
                         "本包两个都算，不把任一读法当作已确认。")
    note = f'''# 提交候选包说明

上传文件：{zip_path.name}，压缩包根目录仅包含同名 JSON。

有效订单：{len(orders)} / {audit['source_orders']}。{anomaly_note}

{knife_note}{prediction['knives']}；预测成材率：{prediction['yield_rate']:.8%}；组合覆盖率：{prediction['coverage']:.8%}（分母为有效订单数）；按原始全部订单计的组合覆盖率：{report['combination_coverage_over_source']:.8%}。

{score_note}{calibration_note}

输入方案：{Path(args.input).resolve()}。length_scheme 只写净定尺整数倍，整轮统一加 2m 余量计算长度和承重。{merge_note}每个新方案最多 {cfg.max_rounds} 轮，符合当前模型上限。详见 validation_report.json。

超产口径：以每单向上取整的需求支数为基准，额外支数不超过需求支数的 {cfg.max_overproduction_ratio:.2%}（向下取整）；实际有 {production['orders_with_extra_pieces']} 单额外交付，单单最大比例 {production['max_actual_extra_ratio']:.4%}，按重量汇总额外产量 {production['extra_kg']:.3f} kg（{production['mass_weighted_extra_ratio']:.4%}）。题面四条设备约束未列出超产上限，此上限为求解设置，尚未获官方单独确认。

评分预测按每个订单段 `int(length // size) + 1` 计刀数，并按截成整数毫米的直径计算成材质量；物理可行性仍按原始小数直径保守校验。总分使用 40/30/20/10 权重与时间满分；{baseline_note}validation_report.json 的 before/after 保留旧物理模型统计用于审计，其中旧 platform_score_estimate 已失准；请使用 calibrated_prediction，不能再按旧估分选提交。

独立校验器直接读取原始 CSV 和提交 JSON：整数倍、含余量的长度/承重、宽度、交付数量及物料检查均通过。已复现旧包 11315 条整数倍错误与 44 条长度错误；重量采用比反馈数量更保守的检查，不声称已拿到官方评分器源码。

{tail_note}

若需按队名重新打包，请在原打包命令中添加 `--team 实际队名`，同时保留原 --input、--config、--audit、--output-dir 参数，避免误选旧方案。
'''
    if args.extra_note:
        note += '\n' + Path(args.extra_note).read_text(encoding='utf-8').rstrip() + '\n'
    (output / '提交说明.md').write_text(note, encoding='utf-8')
    print(json.dumps({'zip_file': str(zip_path.resolve()), 'calibrated_prediction': {
                          key:prediction[key] for key in ('knives','yield_rate','coverage','score_capped','score_uncapped')},
                      'included_orders': len(orders), 'source_orders': audit['source_orders'],
                      'official_acceptance_verified': False}, ensure_ascii=False))
    return report


def parser():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input', default='runs/platform_fix/result.json')
    ap.add_argument('--orders', default='data/orders.normalized.csv')
    ap.add_argument('--blanks', default='data/blanks.normalized.csv')
    ap.add_argument('--config', default='data/competition.config.json')
    ap.add_argument('--audit', default='data/data_audit.json')
    ap.add_argument('--team', default='棒材优化')
    ap.add_argument('--output-dir', default='submission_fixed')
    ap.add_argument('--round', default='prelim', choices=['prelim', 'semi'],
                    help='rule set used for validation and scoring; semi also renames the package')
    ap.add_argument('--data', default='data',
                    help='directory holding the raw CSVs the independent checker reads')
    ap.add_argument('--weight-mode', default='strict',
                    choices=['strict', 'aggregate', 'aggregate_no_trim', 'finished_floor'],
                    help='blank-material check口径; default strict keeps prior behaviour')
    ap.add_argument('--extra-note', default=None,
                    help='markdown file appended verbatim to 提交说明.md (sidecar only, never inside the ZIP)')
    ap.add_argument('--baseline-knives', type=float, default=None,
                    help='knife subscore baseline B for the reported prediction; '
                         'default: 160000 for semi (the value build_semi_plan2.py optimises at), '
                         '90000 for prelim. When it differs from 90000 the report and 提交说明.md '
                         'carry both readings.')
    ap.add_argument('--no-merge', action='store_true',
                    help='skip merge_compatible; use when the source plan is already the '
                         'final scheme set (semi-final continuity-shaped plans)')
    return ap


if __name__ == '__main__':
    build(parser().parse_args())
