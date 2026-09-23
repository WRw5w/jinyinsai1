# -*- coding: utf-8 -*-
"""复赛交付主驱动 v2：连续性合规（单调阶梯）定形 + 质量均衡装桶。

与 `build_semi_plan.py` 的差别：
  1. 定形器换成 `continuity_shaper`（单调阶梯，保证相邻轮订单集合不同、每单轮次连续）；
  2. **分组方式换掉**：不再沿数据顺序贪心吞并（重载键上会死锁：相邻几单都是
     100t+，任意前缀都超 6 轮容量），改为
       * 按订单质量降序做**容量感知**装桶：每桶的质量上限 = `R(m)·(cap_len-2)·p·μ`，
         其中 `m` 是该桶订单数、`R(m) = min(max_rounds, 2m-1)`；容量随 m 阶梯跳变
         （1/3/5/6 轮 -> 55/164/274/329 t），必须逐桶按 size 算；
       * 桶数 G 由「订单数下界」与「质量下界」共同决定，只在 G0 附近小窗口重试；
       * 某个桶定形失败就把该桶按质量二分递归，保证 clause 12（所有订单均须参加）。

用法：
    python -X utf8 build_semi_plan2.py --out runs/cont_v1 --sweep --cap-ratio 0.02
    python -X utf8 build_semi_plan2.py --out runs/probe --groups NP01:43.0 --limit-orders 40
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import shutil
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import continuity_shaper as cs
from platform_check import check
from platform_score import evaluate
from solver import Config, Model, load_blanks, load_orders, validate_plan

M_MAX = 12          # 单个方案最多订单数（阶梯允许更多，装桶不需要）
START = [time.perf_counter()]   # 进程起始时刻，仅用于进度日志


def group_keys(orders):
    seen = []
    for o in orders:
        key = (o.steel, o.diameter)
        if key not in seen:
            seen.append(key)
    return seen


def order_mass(model, i):
    o = model.orders[i]
    return o.pieces * o.size * o.linear_weight


def solo_candidates(idx, model, cfg):
    """单订单方案（只用于定形失败的孤立订单）：1 轮、只含 1 单。

    1 轮的方案没有相邻轮，天然满足「跨轮接续」判据。
    """
    o = model.orders[idx]
    mu = o.linear_weight
    cap = model.caps[idx]
    out = []
    if mu <= 0 or o.pieces < 1:
        return out
    for blank in model.catalogue([idx]):
        usable = model.blank_length([idx], blank)
        if usable <= 0:
            continue
        for p in range(1, model.parallel_limit([idx]) + 1):
            k = max(1, int(round((o.pieces + p - 1) / p)))
            if k * p > cap:
                continue
            written = round(k * o.size, 9)
            length = cfg.round_trim + written
            upper = min(cfg.bed_length, usable)
            if not (cfg.min_bed_length - 1e-9 <= length <= upper + 1e-9):
                continue
            mass = length * p * mu
            if mass > cfg.bed_weight + 1e-8:
                continue
            count = max(1, math.ceil(length * p / usable))
            out.append(dict(knives=int(written // o.size) + 1,
                            declared=count * blank.weight, blank_type=blank.bid,
                            rounds=[dict(lengths={o.oid: written}, p=p, count=count)]))
    if not out:
        return []
    items = sorted(out, key=lambda c: (c['knives'], c['declared']))
    keep, best_decl = [], float('inf')
    for c in items:
        if c['declared'] < best_decl - 1e-6:
            keep.append(c)
            best_decl = c['declared']
    return keep


def bin_capacity(model, cfg, ids, size=4):
    """一个方案（桶）能承载的最大**申报**质量（kg）：按 `size` 个订单算轮数。

    不能直接用 `max_rounds * bed_weight`：单轮申报长度上限是
    `cap_len = min(bed_length, bed_weight/(p*mu))`，而可用的 p 顶在床宽上
    `p_limit = floor(bed_width_mm/dia)`。当 `p_limit*mu` 不足以让 150 m 顶到 60 t
    （小直径、床宽限制）时，单轮只能装 `cap_len*p_limit*mu < 60 t`。
    再扣掉每轮 2 m 余量，得到真实的单轮申报质量上限。

    ⚠️ 关键：可达轮数取决于**桶内订单数 m**——阶梯族上界 `R <= 2m-1`，再被
    `max_rounds` 顶住（见 `continuity_shaper.max_rounds_for`）。于是
      m=1 -> 1 轮(55t)  m=2 -> 3 轮(164t)  m=3 -> 5 轮(274t)  m>=4 -> 6 轮(329t)。
    容量随 m 阶梯式跳变，这是重载键装桶的核心约束，必须逐桶按 size 算。
    """
    mu = model.orders[ids[0]].linear_weight
    plimit = model.parallel_limit(ids)
    if mu <= 0 or plimit < 1:
        return 0.0
    cap_len = min(cfg.bed_length, cfg.bed_weight / (plimit * mu))
    if cap_len < cfg.min_bed_length:
        return 0.0
    rounds = cs.max_rounds_for(size, cfg.max_rounds)
    return rounds * (cap_len - cfg.round_trim) * plimit * mu


def plan_sizes(n, G):
    """把 n 个订单尽量均匀地分到 G 个桶：先每桶 base 个，余数补到前 rem 个桶。

    n=38, G=10 -> [4]*8 + [3]*2 —— 恰好是容量最大的桶型（8 个 6 轮桶 + 2 个 5 轮桶）。
    """
    base, rem = divmod(n, G)
    return [base + 1] * rem + [base] * (G - rem)


def group_capacity(model, cfg, ids, G):
    """G 个桶（尺寸按 `plan_sizes`）的**总**申报质量上限（kg）。"""
    return sum(bin_capacity(model, cfg, ids, s) for s in plan_sizes(len(ids), G))


def bin_row_capacity(model, cfg, ids, size):
    """桶的**净长**上限（m）：`R(size)·(cap_len - trim)`。

    与 `bin_capacity`（质量口径）并列，因为 `scheme_candidates` 真正卡的是
    **净长**：`spread = Σ ceil(pieces/plimit)·size <= R·(cap_len-trim)`。
    质量口径只是它的近似（差在每个订单的 `ceil` 向上取整），只按质量装桶会造出
    「质量够、净长超」的桶 —— 这正是大键上大量桶定形失败（`dead` 桶）的根源。
    `cap_len` 取床宽允许的最大并行数 `plimit`，此时单轮净长上限最小、最保守。
    """
    mu = model.orders[ids[0]].linear_weight
    plimit = model.parallel_limit(ids)
    if mu <= 0 or plimit < 1:
        return 0.0
    cap_len = min(cfg.bed_length, cfg.bed_weight / (plimit * mu))
    if cap_len < cfg.min_bed_length:
        return 0.0
    return cs.max_rounds_for(size, cfg.max_rounds) * (cap_len - cfg.round_trim)


def cap_aware_bins(model, cfg, ids, G, safety=1.0):
    """按「每桶申报质量上限 + 每桶净长上限」装桶：质量降序，投给**利用率最低**的可容纳桶。

    与朴素 LPT 的三个差别（都是 2026-09-22 实测踩出来的）：
      1. 必须逐桶比对 `load + mass <= cap(size)`——朴素 LPT 不看容量，会把 `m=3`
         的桶（容量只有 `m=4` 的 83%）也塞到同等质量而判死；
      2. 选择准则是**利用率** `load/cap` 而不是剩余绝对容量。桶容量随 m 阶梯跳变，
         按「剩余绝对容量最大」会先把大桶灌到 98%~100%（实测 SALE03:24.0 的两个
         4 单桶恰好 97.6% / 100.0%，把 `distribute` 逼到无解），而小桶还饿着；
         按利用率均衡则全部桶都停在 `total/Σcap` 附近，留出可分配的余量。
      3. **同时**用净长口径把关（`bin_row_capacity`）。只按质量装会产出「质量达标、
         净长却超过 `R·(cap_len-trim)`」的桶，`scheme_candidates` 直接返回空。
    返回桶列表；若某单无处可放则返回 None（调用方换 G 重试）。
    """
    n = len(ids)
    if n <= 0:
        return []
    sizes = plan_sizes(n, G)
    caps = [bin_capacity(model, cfg, ids, s) * safety for s in sizes]
    lcaps = [bin_row_capacity(model, cfg, ids, s) * safety for s in sizes]
    rl = row_len(model, cfg, ids)
    bins = [[] for _ in sizes]
    load = [0.0] * G
    lload = [0.0] * G
    for i in sorted(ids, key=lambda k: -order_mass(model, k)):
        mass = order_mass(model, i)
        li = rl[i]
        best, chosen = None, None
        for b in range(G):
            if len(bins[b]) >= sizes[b]:
                continue
            if load[b] + mass > caps[b] + 1e-6 or lload[b] + li > lcaps[b] + 1e-6:
                continue
            um = load[b] / caps[b] if caps[b] > 0 else 1.0
            ul = lload[b] / lcaps[b] if lcaps[b] > 0 else 1.0
            key = (max(um, ul), len(bins[b]))
            if best is None or key < best:
                best, chosen = key, b
        if chosen is None:
            return None
        bins[chosen].append(i)
        load[chosen] += mass
        lload[chosen] += li
    return [b for b in bins if b]


def row_len(model, cfg, ids):
    """每个订单的「净长需求」= ceil(pieces/p_limit) * size（p 取床宽上限）。"""
    pl = model.parallel_limit(ids)
    return {i: cs._ceil(model.orders[i].pieces / pl) * model.orders[i].size for i in ids}


def balance_bins(model, cfg, ids, G, iters=600, max_pairs=1000000, work_budget=1000000):
    """在 `cap_aware_bins` 的基础上做**桶间订单交换**，压低「最大桶利用率」。

    真正的稀缺资源是每轮净长：单桶上限 = `R(m)·(cap_len-trim)`。质量利用率只是它的
    近似（差在 `ceil(pieces/p)` 的向上取整），所以这里直接按净长做均衡，
    目标是最小化 `max_b load_b / cap_b`（先降最大者，再降超 0.90 的个数）。
    桶尺寸固定，故只用「等量对换」，不改变每桶订单数。

    ⚠️ 2026-09-22 加规模闸门：邻域是 `O(G²·m²)`，大键上 G 可达 300+、单次全扫描
    就上千万次内积，实测 NP01:43（3532 单）在 G=319 时一次均衡要几十分钟，会把整个
    全卷拖死（旧代码没有这个函数，所以 38 s 就跑完了）。现在：
      * 邻域规模 > `max_pairs` 时**直接返回未均衡的 `cap_aware_bins`**；
      * 迭代次数按 `work_budget` 反推上限，保证单次调用总评估量有界。
    均衡只是「锦上添花」，装桶可行性由 `cap_aware_bins` 与结构枚举负责。
    """
    bins = cap_aware_bins(model, cfg, ids, G)
    if not bins:
        return None
    nb = len(bins)
    if nb < 2:
        return bins
    avg_m = sum(len(b) for b in bins) / float(nb)
    hood = nb * (nb - 1) / 2.0 * avg_m * avg_m
    if hood > max_pairs:
        return bins
    iters = max(1, min(iters, int(work_budget // max(1.0, hood))))
    rl = row_len(model, cfg, ids)
    load = [sum(rl[i] for i in b) for b in bins]
    caps = [bin_row_capacity(model, cfg, ids, len(b)) for b in bins]   # 净长口径，与 load 同单位

    def score(ld):
        rr = [ld[b] / caps[b] for b in range(len(bins))]
        return (max(rr), sum(1 for x in rr if x > 0.90))

    cur = score(load)
    for _ in range(iters):
        moved = False
        best = None
        for p in range(len(bins)):
            for q in range(p + 1, len(bins)):
                for a in bins[p]:
                    for c in bins[q]:
                        d = rl[c] - rl[a]
                        if d == 0:
                            continue
                        ld = list(load)
                        ld[p] += d
                        ld[q] -= d
                        sc = score(ld)
                        if sc < cur and (best is None or sc < best[0]):
                            best = (sc, p, q, a, c)
        if best is None:
            break
        sc, p, q, a, c = best
        bins[p].remove(a)
        bins[p].append(c)
        bins[q].remove(c)
        bins[q].append(a)
        load[p] += rl[c] - rl[a]
        load[q] += rl[a] - rl[c]
        cur = sc
        moved = True
        if not moved:
            break
    return bins


def lpt_bins(model, ids, G):
    """质量降序 LPT 装桶：每单投到当前质量最小的桶（不看容量，仅作兜底/对照）。"""
    order = sorted(ids, key=lambda i: -order_mass(model, i))
    bins = [[] for _ in range(G)]
    load = [0.0] * G
    for i in order:
        b = min(range(G), key=lambda k: (load[k], len(bins[k])))
        bins[b].append(i)
        load[b] += order_mass(model, i)
    return [b for b in bins if b]


def shape_bin(model, cfg, ids, depth=0):
    """把一个桶定形；失败则**按质量二分**（LPT 到 2 桶）递归，最坏退化为单订单。

    不用「按位置对半切」：桶内已按质量降序，位置对半会切出「全是重单」的一半，
    那一半的订单数 m 太小（`R <= 2m-1` 放不下），继续退化下去就成孤立重单。
    """
    if not ids:
        return []
    cands = cs.scheme_candidates(ids, model, cfg)
    if cands:
        return [(ids, cands)]
    if len(ids) == 1:
        return [(ids, solo_candidates(ids[0], model, cfg))]
    if depth >= 6:
        return [([i], solo_candidates(i, model, cfg)) for i in ids]
    halves = lpt_bins(model, ids, 2)
    out = []
    for h in halves:
        out.extend(shape_bin(model, cfg, h, depth + 1))
    return out


def pack_sized(model, cfg, ids, size=4):
    """固定桶型兜底：桶数取 ceil(n/size)，再用 `plan_sizes` 均摊（尺寸只落在
    {size, size+1} 内），于是不会留下「尾桶」——尾桶才是容量的最大杀手
    （`m=2` 只有 3 轮 164t、`m=1` 只有 1 轮 55t）。"""
    n = len(ids)
    if n == 0:
        return []
    return cap_aware_bins(model, cfg, ids, max(1, math.ceil(n / size)))


def shape_all(model, cfg, bins):
    groups, bad = [], 0
    for b in bins:
        part = shape_bin(model, cfg, b)
        if not part or any(not c for _ids, c in part):
            bad += 1
        groups.extend(part)
    return groups, bad


def build_groups(model, cfg, ids, log, m_max=M_MAX, target_util=0.93):
    """按「每桶申报质量上限」装桶 + 递归定形。

    桶数 G 这样定：从订单数下界 `ceil(n/m_max)` 往上找**最小的** G 使
    `Σcap(plan_sizes(n,G)) >= total/target_util`，即全键平均利用率压到
    `target_util` 以下。这一步很关键——`distribute` 的实际可达效率约 93%~95%
    （每段长度是离散的 `size_i`，轮长要精确落进 [50,150]），把桶灌到 97%+
    必然判死，而 G 再大一点（桶型从 4 单降到 3 单）容量反而掉一档。
    候选 G 只在主选附近取 4 个，并同时试「容量感知装桶（按利用率均衡）」与
    「朴素质量 LPT」两种分区，取定形失败最少的一版。
    """
    n = len(ids)
    if n == 0:
        return []
    total = sum(order_mass(model, i) for i in ids)
    cap4 = bin_capacity(model, cfg, ids, min(4, n))
    if cap4 <= 0:
        return [([i], solo_candidates(i, model, cfg)) for i in ids]
    G_min = max(1, math.ceil(n / m_max))
    need = total / target_util
    G_main, cap_best, G_best = G_min, -1.0, G_min
    for G in range(G_min, min(n, G_min + 24) + 1):
        cap = group_capacity(model, cfg, ids, G)
        if cap > cap_best:
            G_best, cap_best = G, cap
        if cap >= need:
            G_main = G
            break
    else:
        G_main = G_best
    grid = []
    for d in (0, 1, -1, 2):
        G = G_main + d
        if 1 <= G <= n and G not in grid:
            grid.append(G)

    best, tried = None, 0
    for G in grid:
        packs = []
        ca = cap_aware_bins(model, cfg, ids, G)
        if ca:
            packs.append(ca)
        ba = balance_bins(model, cfg, ids, G)
        if ba and ba != ca:
            packs.append(ba)
        packs.append(lpt_bins(model, ids, G))
        for bins in packs:
            groups, dead_bins = shape_all(model, cfg, bins)
            tried += 1
            if log:
                print('      G=%-3d bins=%-3d shapes=%-4d dead=%-3d  %.0fs'
                      % (G, len(bins), len(groups), dead_bins, time.perf_counter() - START[0]),
                      flush=True)
            if dead_bins == 0:
                if log:
                    print('    cap4=%.1ft G=%d (util~%.3f) -> %d groups (0 shapeless, %d tries)'
                          % (cap4 / 1000.0, G, total / group_capacity(model, cfg, ids, G),
                             len(groups), tried), flush=True)
                return groups
            key = (dead_bins, len(groups))
            if best is None or key < best[0]:
                best = (key, groups, G)
    key, groups, G = best
    if log:
        print('    cap4=%.1ft best G=%s -> %d groups (%d shapeless, %d tries)'
              % (cap4 / 1000.0, G, len(groups), key[0], tried), flush=True)
    return groups


# ---------------------------------------------------------------------------
# 多进程：**按 key 并行**
# ---------------------------------------------------------------------------
# 各钢种/规格键之间完全独立（装桶、定形、候选选择都不跨键），而上游的 `exact_parts`
# DFS 是 CPU 密集的单线程瓶颈（NP01:43.0 单键占全卷绝大部分时间）。本机 16 核，
# 按键并行即可近似线性加速。确定性靠两点保证：
#   1. 每个键的输出只由该键的输入决定（无跨键状态）；
#   2. 汇总时按 `keys` 的**原始顺序**重排（派发顺序按订单数降序，只影响负载均衡）。
_WSTATE = {}


def _init_worker(data_dir, settings):
    """每个子进程只加载一次模型（避免逐任务 pickle 整个 Model）。"""
    cfg = Config(**settings)
    orders = load_orders(str(Path(data_dir) / 'orders.normalized.csv'), cfg, skip_invalid=True)
    blanks = load_blanks(str(Path(data_dir) / 'blanks.normalized.csv'))
    model = Model(orders, cfg, blanks)
    model.lookup = {o.oid: i for i, o in enumerate(orders)}
    _WSTATE['cfg'] = cfg
    _WSTATE['model'] = model


def _work_key(item):
    key, ids, m_max = item
    t0 = time.perf_counter()
    gs = build_groups(_WSTATE['model'], _WSTATE['cfg'], ids, log=False, m_max=m_max)
    dead = sum(1 for _ids, c in gs if not c)
    entry = dict(group='%s:%s' % key, orders=len(ids), groups=len(gs), shapeless=dead,
                 sizes=sorted({len(_ids) for _ids, _c in gs}))
    return '%s:%s' % key, entry, gs, time.perf_counter() - t0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data', default='data/semi')
    ap.add_argument('--out', type=Path, default=Path('runs/cont_v1'))
    ap.add_argument('--lam', type=float, default=None)
    ap.add_argument('--sweep', action='store_true')
    ap.add_argument('--baseline-knives', type=float, default=160000.0)
    ap.add_argument('--cap-ratio', type=float, default=None)
    ap.add_argument('--m-max', type=int, default=M_MAX)
    ap.add_argument('--groups', nargs='*', default=None)
    ap.add_argument('--limit-orders', type=int, default=None)
    ap.add_argument('--dump-groups', type=Path, default=None)
    ap.add_argument('--dump-candidates', type=Path, default=None,
                    help='把每个组的 (knives, declared, batch) 候选表落盘，供 --load-candidates 复用')
    ap.add_argument('--load-candidates', type=Path, default=None,
                    help='跳过装桶+定形，直接用已落盘的候选表重跑 λ 网格')
    ap.add_argument('--jobs', type=int, default=0,
                    help='按 key 并行的进程数；0=自动(取 CPU 核数，最多 16)，1=串行')
    args = ap.parse_args()

    data = Path(args.data)
    settings = json.loads((data / 'competition.config.json').read_text(encoding='utf-8'))
    if args.cap_ratio is not None:
        settings['max_overproduction_ratio'] = args.cap_ratio
    settings.update(continuity=True, coverage_shared=True, enforce_order_mass_floor=True,
                    objective='platform_score', baseline_knives=args.baseline_knives)
    cfg = Config(**settings)
    print('over-production allowance r=%s  m_max=%d  baseline_knives=%g'
          % (cfg.max_overproduction_ratio, args.m_max, cfg.baseline_knives), flush=True)
    orders = load_orders(str(data / 'orders.normalized.csv'), cfg, skip_invalid=True)
    blanks = load_blanks(str(data / 'blanks.normalized.csv'))
    model = Model(orders, cfg, blanks)
    model.lookup = {o.oid: i for i, o in enumerate(orders)}

    keys = group_keys(orders)
    if args.groups:
        want = set()
        for spec in args.groups:
            steel, _, dia = spec.partition(':')
            want.add((steel, float(dia)))
        keys = [k for k in keys if k in want]

    started = time.perf_counter()
    if args.load_candidates:
        # 只重跑 λ 网格：候选表已含每个组的 (knives, declared, batch)，装桶+定形可跳过
        payload = json.loads(Path(args.load_candidates).read_text(encoding='utf-8'))
        per_group = payload['per_group']
        cand_groups = payload['groups']
        print('loaded %d groups of candidates from %s (skipping grouping)'
              % (len([g for g in cand_groups if g]), args.load_candidates), flush=True)
    else:
        all_groups, per_group = [], []
        tasks = []
        for key in keys:
            ids = [i for i, o in enumerate(orders)
                   if o.steel == key[0] and abs(o.diameter - key[1]) < 1e-9]
            if args.limit_orders:
                ids = ids[:args.limit_orders]
            tasks.append((key, ids, args.m_max))
        if not tasks:
            raise SystemExit('no orders matched the requested --groups')

        jobs = args.jobs if args.jobs > 0 else min(16, os.cpu_count() or 1)
        jobs = max(1, min(jobs, len(tasks)))
        if jobs <= 1:
            for key, ids, mm in tasks:
                gs = build_groups(model, cfg, ids, log=len(ids) > 100, m_max=mm)
                dead = sum(1 for _ids, c in gs if not c)
                per_group.append(dict(group='%s:%s' % key, orders=len(ids), groups=len(gs),
                                      shapeless=dead,
                                      sizes=sorted({len(_ids) for _ids, _c in gs})))
                all_groups.extend(gs)
                print('[%s:%s] %d orders -> %d groups (%d shapeless)  %.0fs'
                      % (key[0], key[1], len(ids), len(gs), dead,
                         time.perf_counter() - started), flush=True)
        else:
            print('parallel: %d keys on %d workers' % (len(tasks), jobs), flush=True)
            order_idx = sorted(range(len(tasks)), key=lambda j: -len(tasks[j][1]))
            by_name, done_n = {}, 0
            with ProcessPoolExecutor(max_workers=jobs, initializer=_init_worker,
                                     initargs=(str(Path(args.data).resolve()), settings)) as ex:
                futs = [ex.submit(_work_key, tasks[j]) for j in order_idx]
                for fu in as_completed(futs):
                    gname, entry, gs, dt = fu.result()
                    by_name[gname] = (entry, gs)
                    done_n += 1
                    print('[%s] %d orders -> %d groups (%d shapeless)  %.0fs  (done %d/%d)'
                          % (gname, entry['orders'], entry['groups'], entry['shapeless'],
                             dt, done_n, len(tasks)), flush=True)
            for key in keys:                  # 按原始 key 顺序重排 -> 结果确定
                entry, gs = by_name['%s:%s' % key]
                per_group.append(entry)
                all_groups.extend(gs)
        print('\ncollected %d groups in %.0fs'
              % (len(all_groups), time.perf_counter() - started), flush=True)

        if args.dump_groups:
            args.dump_groups.write_text(json.dumps(
                [[_ids, len(c)] for _ids, c in all_groups], ensure_ascii=False),
                encoding='utf-8')

        # 统一成「每个组一份候选表」=(knives, declared, batch)。这样
        #   * 串行/并行构建（all_groups）与 `--load-candidates`（读盘）走同一条下游；
        #   * λ 只作用在这张表上（`min(declared + lam*knives)`），于是 λ 网格的调整
        #     不需要重跑 20 分钟的装桶+定形 —— 这正是 §6.7「λ 最优点随基准刀数移动」
        #     需要反复重选的地方。`--dump-candidates` 把这张表落盘复用。
        cand_groups = []
        for ids, cands in all_groups:
            if not cands:
                cand_groups.append(None)
            else:
                cand_groups.append([dict(knives=c['knives'], declared=c['declared'],
                                         batch=cs._to_batch(ids, model, cfg, c))
                                    for c in cands])
        if args.dump_candidates:
            args.dump_candidates.parent.mkdir(parents=True, exist_ok=True)
            args.dump_candidates.write_text(
                json.dumps(dict(per_group=per_group, groups=cand_groups),
                           ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
            print('dumped candidates for %d groups to %s'
                  % (len([g for g in cand_groups if g]), args.dump_candidates), flush=True)

    lams = [args.lam] if args.lam is not None else (
        [0.0, 100.0, 200.0, 400.0, 800.0, 1200.0, 1600.0, 2400.0, 3200.0,
         4800.0, 6400.0, 9600.0]
        if args.sweep else [2400.0])

    best = None
    for lam in lams:
        plan = []
        for item in cand_groups:
            if not item:
                continue
            plan.append(copy.deepcopy(
                min(item, key=lambda c: c['declared'] + lam * c['knives'])['batch']))
        sc = evaluate(plan, data, round_name='semi', baseline_knives=args.baseline_knives)
        print('lam=%-7g schemes=%d rounds=%d knives=%d yield=%.4f coverage=%.4f '
              'score=%.3f viol=%d'
              % (lam, len(plan), sum(len(b['length_scheme']) for b in plan),
                 sc['knives'], sc['yield_rate'], sc['coverage'], sc['score_capped'],
                 sc['violation_count']), flush=True)
        if best is None or sc['score_capped'] > best[2]['score_capped']:
            best = (lam, plan, sc)

    lam, plan, sc = best
    print('\nCHOSEN lam=%g: schemes=%d rounds=%d knives=%d yield=%.4f coverage=%.4f score=%.3f'
          % (lam, len(plan), sum(len(b['length_scheme']) for b in plan), sc['knives'],
             sc['yield_rate'], sc['coverage'], sc['score_capped']))

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'result.json').write_text(
        json.dumps(plan, ensure_ascii=False, separators=(',', ':')) + '\n', encoding='utf-8')

    scope = out / 'scoped_data'
    scope.mkdir(parents=True, exist_ok=True)
    placed = {o for b in plan for o in b['orders']}
    kept = []
    with (data / 'orders_semi.csv').open(encoding='gbk', errors='replace') as fh:
        kept.append(fh.readline())
        for line in fh:
            if line.strip() and line.split(',', 1)[0].strip() in placed:
                kept.append(line)
    (scope / 'orders_semi.csv').write_text(''.join(kept), encoding='gbk')
    for name in ('blank_used_finals.csv', 'constraints.txt'):
        shutil.copyfile(data / name, scope / name)
    phys = check(plan, scope, round='semi')
    scope_orders = [o for o in orders if o.oid in placed]
    metrics = validate_plan(plan, scope_orders, cfg, blanks)
    summary = dict(lam=lam, groups=len(plan), m_max=args.m_max, per_group=per_group,
                   orders_placed=len(placed), orders_total=len(orders),
                   rounds=sum(len(b['length_scheme']) for b in plan),
                   knives=sc['knives'], yield_rate=round(sc['yield_rate'], 6),
                   coverage=round(sc['coverage'], 6),
                   violation_count=sc['violation_count'],
                   violations=sc.get('violations'),
                   score_capped=round(sc['score_capped'], 4),
                   score_after_penalty=round(sc.get('score_capped_after_penalty',
                                                    sc['score_capped']), 4),
                   physical_passed=phys['passed'], physical_errors=phys['error_counts'],
                   validator_rounds=metrics['rounds'], validator_knives=metrics['knives'],
                   validator_yield=round(metrics['yield_rate'], 6),
                   validator_coverage=round(metrics['coverage'], 6),
                   elapsed_s=round(time.perf_counter() - started, 1))
    (out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n',
                                      encoding='utf-8')
    print('\nplatform_check passed: %s %s' % (phys['passed'], phys['error_counts']))
    print('validate_plan: rounds=%d knives=%d yield=%.4f coverage=%.4f'
          % (metrics['rounds'], metrics['knives'], metrics['yield_rate'], metrics['coverage']))
    print('wrote %s' % (out / 'result.json'))


if __name__ == '__main__':
    main()
