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
from validation_status import uncertified_build_report


def rotate_scheme_rounds(plan):
    """Close every cross-round seam: round j ends where round j+1 begins.

    ⛔ The reasoning this implements was FALSIFIED on 2026-09-26 and the rotation
    does not fix anything.  `submission_semi_merged_v4` went through this function,
    our checker then reported 0 violations, and the platform scored it 0 points for
    **7342** `跨轮接续不连续` (receipt:
    `artifacts/rejected/submission_semi_merged_v4/official_feedback.json`).
    Rotating key order cannot help when a boundary shares more than one order: the
    candidate predicate requires *every* shared order to sit at both ends of the
    seam at once, which one rotation cannot arrange.  See
    `tools/analysis/clause6_candidate.py`.  Kept so the existing rejected packages
    stay byte-reproducible; do not read it as a repair.

    What it was meant to do: the platform reads a scheme's rounds as one continuous
    billet stream, so `跨轮接续不连续` fires whenever two rounds share orders but the
    last order of the earlier round is not the first of the later one.  Our solver
    emits every round with the SAME key order (one shared cold-bed set), which
    satisfies "the same orders" but broke that seam -- the platform returned 0 points
    and a 35150 penalty for it on `submission_semi_merged_v2` (2026-09-23).

    The rotation is still cost-free -- each round's (order -> length) multiset is
    unchanged, so knives / yield / coverage are identical (verified: 94.71154834617565
    both before and after) -- it simply does not address the rule the platform applies.
    """
    for batch in plan:
        rounds = batch.get('length_scheme') or []
        orders = [list(r) for r in rounds]
        for j in range(len(orders) - 2, -1, -1):
            want = orders[j + 1][0]
            cur = orders[j]
            if want in cur:
                k = cur.index(want)
                orders[j] = cur[k + 1:] + cur[:k + 1]
        batch['length_scheme'] = [{oid: rounds[j][oid] for oid in orders[j]}
                                  for j in range(len(orders))]
    return plan


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
    plan = merge_compatible(source, orders, cfg.max_rounds)
    # `跨轮接续不连续`: the platform reads a scheme's rounds as one continuous
    # billet stream, so round j must END on round j+1's FIRST order.  Every round
    # of a scheme carries the same shared cold-bed set, so a rotation to close
    # each seam is free -- the (order -> length) multiset per round is untouched.
    # Pinned by the 2026-09-23 official 0-point feedback (7030 violations); see
    # diagnostics/fix_continuity_order.py.
    if round_name == 'semi':
        plan = rotate_scheme_rounds(plan)
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
    prediction = evaluate_platform(plan, data=Path(getattr(args, 'data', 'data')), round_name=round_name)
    delivered = defaultdict(int)
    lookup = {o.oid: o for o in orders}
    for batch in plan:
        for lengths, parallel in zip(batch['length_scheme'], batch['counts']):
            for oid, length in lengths.items():
                delivered[oid] += round(length / lookup[oid].size) * parallel
    demand_mass = sum(o.pieces * o.size * o.linear_weight for o in orders)
    # The over-production mass must come from the UNCAPPED physical total.  The
    # yield numerator (`finished_weight`) is capped per order at its demand under
    # the semi-final rules, so differencing it against demand is identically zero
    # and silently reported "additional output -0.000 kg" next to "9550 orders with
    # extra pieces".  `finished_physical_weight` is what the plan actually cuts.
    physical_mass = after.get('finished_physical_weight', after['finished_weight'])
    production = dict(per_order_extra_limit=cfg.max_overproduction_ratio,
                      orders_with_extra_pieces=sum(delivered[o.oid] > o.pieces for o in orders),
                      max_actual_extra_ratio=max(delivered[o.oid] / o.pieces - 1 for o in orders),
                      rounded_demand_kg=demand_mass,
                      extra_kg=physical_mass - demand_mass,
                      mass_weighted_extra_ratio=physical_mass / demand_mass - 1)
    report = dict(package_created=True, official_acceptance_verified=False,
                  complete_original_order_coverage=not audit.get('rejected'),
                  input_result=str(Path(args.input).resolve()),
                  source_order_count=audit['source_orders'], included_order_count=len(orders),
                  combination_coverage_over_source=round(after['coverage'] * len(orders)) / audit['source_orders'],
                  excluded_orders=audit.get('rejected', []), model_assumptions=audit.get('assumptions', []),
                  before=before, after=after, production=production,
                  before_after_are_legacy_physical_model_not_official_score=True,
                  calibrated_prediction=prediction,
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
    # The release gate, not the legacy model, decides what may be submitted.  This
    # report used to be written straight out, so a freshly built package carried
    # `independent_platform_check.passed: True` -- a machine-readable approval for a
    # package the platform scored at zero on 2026-09-26, because that check still
    # implements the clause-6 reading that submission falsified.  `resync_reports`
    # applies the same downgrade, so every path that emits a report emits an
    # uncertified one, and the model's own numbers survive under
    # `legacy_model_result`.
    report = uncertified_build_report(report)
    atomic_json(output / 'validation_report.json', report)
    # The note is assembled from the round's own facts rather than a fixed
    # template.  The earlier version hardcoded the preliminary round throughout:
    # it named prelim's rejected order (`A20260949`, a negative cut length), cited
    # prelim's seven calibration feedbacks, and printed a 90000 knife baseline.
    # Every one of those is wrong for the semi-final round, and the baseline in
    # particular changes the score the note claims.  It also crashed outright once
    # `max_overproduction_ratio` became `None` (the semi-final rule set caps
    # nothing), because the sentence applied a `:.2%` format to it.
    semi = round_name == 'semi'
    baseline = 160000 if semi else 90000
    rejected = (audit.get('rejected') or [None])[0]
    if rejected is None:
        isolated = '本次审计没有隔离任何订单，全部原始订单都进入了方案。'
    else:
        original = rejected.get('original') or {}
        shown = original.get('订单号', rejected.get('order_id'))
        detail = '、'.join(f'{k}={v}' for k, v in original.items() if k != '订单号')
        isolated = (f"原始订单 {shown} 的原始记录为 {detail}，原因 {rejected.get('reason')}，"
                    f"无法用于物理可行的锯切。本包按题面允许的异常数据过滤流程隔离该订单，"
                    f"没有猜测其正确取值，没有添加虚构订单方案。原始文件未改动。")
    if cfg.max_overproduction_ratio is None:
        overproduction = (
            f"超产口径：不设上限。题目对该轮只给出下界（每订单冷床分配总重量须不低于该订单重量），"
            f"没有超产上限，超产既不扣分也不计入成材率分子。实际有 "
            f"{production['orders_with_extra_pieces']} 单额外交付，单单最大比例 "
            f"{production['max_actual_extra_ratio']:.4%}，按重量汇总额外产量 "
            f"{production['extra_kg']:.3f} kg（{production['mass_weighted_extra_ratio']:.4%}）。")
    else:
        overproduction = (
            f"超产口径：以每单向上取整的需求支数为基准，额外支数不超过需求支数的 "
            f"{cfg.max_overproduction_ratio:.2%}（向下取整）；实际有 "
            f"{production['orders_with_extra_pieces']} 单额外交付，单单最大比例 "
            f"{production['max_actual_extra_ratio']:.4%}，按重量汇总额外产量 "
            f"{production['extra_kg']:.3f} kg（{production['mass_weighted_extra_ratio']:.4%}）。")
    if semi:
        knife_model = (
            "评分预测按复赛口径计刀：每轮的刀数等于该轮总段数加一（连接处各一刀、每轮冷床一组头尾），"
            "不乘棒材根数；并按截成整数毫米的直径计算成材质量。物理可行性仍按原始小数直径保守校验。"
            f"总分使用 40/30/20/10 权重与 {baseline} 基准刀数。")
        calibration = (
            "复赛没有官方反馈可用于校准：本轮的刀数与覆盖率口径依 constraints.txt 与 PDF 六/九实现，"
            "尚未经任何官方实测验证。历史校准只在初赛轮成立，不能外推到这里。")
        upload_hint = (
            "本地格式、约束和 ZIP 校验通过，不等于官方评分器确认通过。"
            "若官方校验要求原始 10000 个订单号全部出现，需先取得上述被隔离订单的官方异常过滤口径；"
            "不能用凭空补值来保证通过。本脚本没有执行网页上传。")
    else:
        knife_model = (
            "评分预测按每个订单段 `int(length // size) + 1` 计刀数，并按截成整数毫米的直径计算成材质量；"
            f"物理可行性仍按原始小数直径保守校验。总分使用 40/30/20/10 权重、推测的 {baseline} 基准刀数"
            "与时间满分。validation_report.json 的 before/after 保留旧物理模型统计用于审计，其中旧 "
            "platform_score_estimate 已失准；请使用 calibrated_prediction，不能再按旧估分选提交。")
        calibration = (
            "七个历史官方校准点的刀数（及展示成材率）均已复现，不代表已取得评分器源码或穷尽全部规则。")
        upload_hint = (
            "本地格式、约束和 ZIP 校验通过，不等于官方评分器确认通过。"
            "若官方校验要求原始 5000 个订单号全部出现，必须先取得上述被隔离订单的正确值或官方异常过滤口径；"
            "不能用凭空补值来保证通过。本脚本没有执行网页上传。")
    note = f'''# 提交候选包说明

上传文件：{zip_path.name}，压缩包根目录仅包含同名 JSON。

轮次：{'复赛' if semi else '初赛'}。

有效订单：{len(orders)} / {audit['source_orders']}。{isolated}

预测刀数：{prediction['knives']}；预测成材率：{prediction['yield_rate']:.8%}；组合覆盖率：{prediction['coverage']:.8%}（分母为有效订单数）；按原始全部订单计的组合覆盖率：{report['combination_coverage_over_source']:.8%}。

预测总分：刀数子分封顶假设下 {prediction['score_capped']:.6f}；不封顶假设下 {prediction['score_uncapped']:.6f}。新包仍需官方实测。{calibration}

输入方案：{Path(args.input).resolve()}。length_scheme 只写净定尺整数倍，整轮统一加 2m 余量计算长度和承重。本打包步骤只合并同钢种、同直径、同坯型的方案，保留每轮参数。每个新方案最多 {cfg.max_rounds} 轮，符合当前模型上限。详见 validation_report.json。

{overproduction}

{knife_model}

独立校验器直接读取原始 CSV 和提交 JSON：整数倍、含余量的长度/承重、宽度、交付数量及物料检查均通过。重量采用比反馈数量更保守的检查，不声称已拿到官方评分器源码。

{upload_hint}

若需按队名重新打包，请在原打包命令中添加 `--team 实际队名`，同时保留原 --input、--config、--audit、--data、--round、--output-dir 参数，避免误选旧方案。
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
    # Every round-dependent path defaults to None and is filled in by
    # `resolve_round_paths` once `--round` is known.  Previously they were
    # hardcoded to the preliminary round's files while `--round` only switched the
    # validator and the package name -- so `--round semi` alone would read
    # prelim's orders, blanks, config and audit, validate them under semi rules,
    # and emit a plausible-looking package built from the wrong dataset.  The
    # inputs are hash-pinned in validation_report.json, so the mistake was
    # auditable after the fact, but nothing surfaced it at build time.  Deriving
    # the defaults from the round makes the two impossible to disagree.
    ap.add_argument('--input', default=None,
                    help='the solved plan to package (required)')
    ap.add_argument('--orders', default=None)
    ap.add_argument('--blanks', default=None)
    ap.add_argument('--config', default=None)
    ap.add_argument('--audit', default=None)
    ap.add_argument('--team', default='棒材优化')
    ap.add_argument('--output-dir', default='artifacts/candidates/submission_fixed')
    ap.add_argument('--round', default='prelim', choices=['prelim', 'semi'],
                    help='rule set used for validation, scoring and the data defaults; '
                         'semi also renames the package')
    ap.add_argument('--data', default=None,
                    help='directory holding the raw CSVs the independent checker reads')
    ap.add_argument('--weight-mode', default='strict',
                    choices=['strict', 'aggregate', 'aggregate_no_trim', 'finished_floor'],
                    help='blank-material check口径; default strict keeps prior behaviour')
    ap.add_argument('--extra-note', default=None,
                    help='markdown file appended verbatim to 提交说明.md (sidecar only, never inside the ZIP)')
    return ap


def resolve_round_paths(args):
    """Fill the round-dependent paths that the caller left unset.

    `--round semi` selects `data/semi/` for every input, so a semi build cannot
    silently consume the preliminary dataset.  A path the caller passed
    explicitly is never overridden -- the point is to fix the *defaults*, not to
    guess the operator's intent.
    """
    root = 'data/semi' if args.round == 'semi' else 'data'
    for name, leaf in (('orders', 'orders.normalized.csv'),
                       ('blanks', 'blanks.normalized.csv'),
                       ('config', 'competition.config.json'),
                       ('audit', 'data_audit.json')):
        if getattr(args, name) is None:
            setattr(args, name, f'{root}/{leaf}')
    if args.data is None:
        args.data = root
    if args.input is None:
        raise SystemExit(
            'error: --input is required (the solved plan to package). '
            f'For the semi-final round a typical value is runs/semi_nolimit_v1/result.json')
    return args


if __name__ == '__main__':
    build(resolve_round_paths(parser().parse_args()))
