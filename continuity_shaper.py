# -*- coding: utf-8 -*-
"""连续性合规的定形器：一般单调阶梯（staircase）轮次结构。

背景（为什么需要它）
--------------------
`round_shaper.py` 把方案内**每个订单放进每一轮**，因此同一方案内所有轮的
订单集合完全相同。这在复赛的「跨轮接续」约束下被判违规（2026-09-22 平台反馈：
不可行，跨轮接续不连续 7559 条）。

反推官方口径：违规单位 = (方案, 批)，判据 = 第 j 轮的订单集合与第 j+1 轮**完全相同**。
老交付包上按此判据计得 7559 条，与官方串逐项吻合（含「方案1 恰好缺 批3」这一
不可伪造的指纹：批3/批4 之间正是 `merge_compatible` 的拼接缝，两侧集合不同）。
详见 `diagnostics/semi_continuity_audit.py`。

于是合规结构必须同时满足：
  1. 每个订单出现的轮次是**连续的一段**（clause 6 后半句：不得跳轮）；
  2. 相邻两轮的订单集合**不同**（clause 6 前半句 + 官方判据）。
注意：官方题面 PDF 的输出示例 `{A20260104,A20260105} -> {A20260104}` 说明
**单订单轮次是合法的**（因此每轮最少 1 单，不是 2 单）；同时也说明相邻两轮
**可以共用订单**（示例两轮都含 A20260104）。被排除的读法是「相邻轮必须无交集」，
因为 28.6% 的订单单轮超过 60t 承重上限，必须跨轮，那样连官方示例都不合法。

结构族：**单调阶梯**。轮次 r 的订单集合 = 订单序列上的连续窗口 [a_r, b_r]，
且 a_r、b_r 单调不减。性质：
  * 每个订单的窗口成员期天然连续（满足 1）；
  * 每个轮次边界都有订单进出，故相邻窗口必然不同（满足 2）；
  * 覆盖性：a_1 = 1、b_R = m，于是并集恰好是 1..m。

给定 (m 个订单, R 轮)，令 B = R-1 个边界，需要把
`need_b = m - 1` 次「右端推进」（b: 1 -> m）与 `need_a = aR - 1` 次「左端推进」
（a: 1 -> aR）摊到 B 个边界上，每个边界至少 1 次推进。可达范围：
`1 <= R <= 2m - 1`（aR 取到 m）。
`aR` 越小左端越晚滑动、早期订单成员期越长；本文件按「成员期谱最大」自动挑 aR。

⚠️ 为什么必须把首窗取到 `(1,1)`（`need_b = m-1`，b 从 1 起）：
2026-09-22 那版把 b 从 2 起（`need_b = m-2`），于是 `R <= 2m-2`，`m=2` 只能 2 轮、
`m=3` 只能 4 轮。桶的申报质量上限正比于可达轮数，结果 SALE03:24.0 这类「单订单
质量很大、桶内订单数少」的重载键上，容量凭空少掉 25%~33%，20 个订单无处安放
（clause 12）。`(1,1)` 是合法窗（官方示例里单订单轮就是合法的），把它纳入后
`m=2 -> 3 轮`、`m=3 -> 5 轮`，容量恢复。这是纯粹的容量损失修复，不改变合规性。

订单→位置的指派：窗口成员期越长的位置分给需求（质量）越大的订单。

用法
----
    python -X utf8 continuity_shaper.py --selftest
"""
from __future__ import annotations

import argparse
import itertools
import math
import os

from platform_score import row_segments
from solver import Config, Model, load_blanks, load_orders

_EPS = 1e-9
R_MAX = 6               # constraints.txt 第 4 条：每个组合方案 <= 6 轮
M_MAX = 24              # 阶梯本身允许任意 m；此上限只用于防止病态输入


def _ceil(x):
    return math.ceil(round(x, 9))


# ---------------------------------------------------------------------------
# 一般单调阶梯
# ---------------------------------------------------------------------------
def _schedule(m, R, aR):
    """把 need_b 次右推进（前载）与 need_a 次左推进（后载）摊到 R-1 个边界。

    返回 [(a_r, b_r)]（1-indexed，含端点）或 None。
    """
    if m < 1 or R < 1:
        return None
    if R == 1:
        return [(1, m)]
    B = R - 1
    need_b, need_a = m - 1, aR - 1
    if need_b < 0 or need_a < 0:
        return None
    if need_b + need_a < B:
        return None
    # 先保证每个边界至少 1 次推进：前 nb 个边界由右推进覆盖，其余由左推进覆盖
    nb = min(need_b, B)
    dl = [1] * nb + [0] * (B - nb)
    el = [0] * nb + [1] * (B - nb)
    extra_b = need_b - nb
    extra_a = need_a - (B - nb)
    if extra_b < 0 or extra_a < 0:
        return None
    q, r = divmod(extra_b, B)                 # 多出来的右推进前载
    if q or r:
        for i in range(B):
            dl[i] += q + (1 if i < r else 0)
    q, r = divmod(extra_a, B)                 # 多出来的左推进后载
    if q or r:
        for i in range(B):
            el[i] += q + (1 if i >= B - r else 0) if r else q
    wins, a, b = [], 1, 1
    for i in range(B):
        wins.append((a, b))
        b += dl[i]
        a += el[i]
    wins.append((a, b))
    if b != m or wins[0][0] != 1:
        return None
    if any(bb < aa for aa, bb in wins):
        return None
    seen = set()
    for aa, bb in wins:
        seen.update(range(aa, bb + 1))
    if seen != set(range(1, m + 1)):
        return None
    return wins


_STAIR = {}
_STAIR_ALL = {}
_STRUCT_CACHE = {}


def max_rounds_for(m, r_max=R_MAX):
    """`m` 个订单的桶最多能排多少轮（阶梯族的结构上界）: min(r_max, 2m-1)。

    m=1 -> 1 轮，m=2 -> 3 轮，m=3 -> 5 轮，m>=4 -> 6 轮（被 r_max 顶住）。
    桶的申报质量上限正比于这个轮数，装桶时必须用它算容量，不能再假设恒定 6 轮。
    """
    if m <= 0:
        return 0
    return min(int(r_max), 2 * m - 1)


def _stair_key(m, wins):
    sp = _spans(wins, m)
    return (min(sp),) + tuple(sorted(sp, reverse=True))


def _struct_key(supports):
    """结构偏好：先最大化「最小成员期」，再让最长成员期尽量短，再最大化总成员期。"""
    lens = [len(s) for s in supports]
    return (min(lens), -max(lens), sum(lens))


def _sup_from_intervals(combo, m, R):
    """由「每个位置一段连续区间」还原出 sup[pos]（轮次升序），非法返回 None。

    合法性 = constraints.txt 第 6 条：每轮非空、相邻两轮集合不同、每单连续。
    """
    sets = []
    for r in range(1, R + 1):
        st = frozenset(i for i, (s, e) in enumerate(combo) if s <= r <= e)
        if not st:
            return None
        sets.append(st)
    for j in range(R - 1):
        if sets[j] == sets[j + 1]:
            return None
    return tuple(tuple(j for j in range(R) if i in sets[j]) for i in range(m))


def _even_pick(items, k):
    """从 `items` 里**均匀**取 k 条，保证覆盖整条偏好谱而不是只有头部。

    紧桶往往需要的正是「低偏好」的结构（某些轮订单很少、便于把该轮填满），
    只取偏好头部会把它们全部丢掉。
    """
    if len(items) <= k:
        return list(items)
    return [items[min(len(items) - 1, int(i * len(items) / k))] for i in range(k)]


def _structs_sample(m, R, want, seed=20260922):
    """随机采样合法结构（固定种子，可复现）。

    对每个订单独立抽一段区间 [s,e] ⊆ [1,R]，再用 `_sup_from_intervals` 验合法性
    （每轮非空、相邻轮集合不同、每单连续）。比逐轮 DFS 快好几个数量级，且天然
    覆盖「稀疏~稠密」全谱，正好补上窗口族够不到的紧桶形状。
    """
    import random
    rng = random.Random(seed)
    out, seen = [], set()
    tries, cap_tries = 0, max(4000, want * 60)
    while len(out) < want and tries < cap_tries:
        tries += 1
        combo = []
        for _ in range(m):
            s = rng.randint(1, R)
            e = rng.randint(s, R)
            combo.append((s, e))
        sup = _sup_from_intervals(combo, m, R)
        if sup is None or sup in seen:
            continue
        seen.add(sup)
        out.append((_struct_key(sup), sup))
    return out


def structures(m, R, limit=24, combo_cap=2000000, sample_n=600):
    """所有（或前 limit 条最有代表性的）合法区间结构，**保留订单↔区间的配对**。

    一个「结构」= 给每个订单指定它出现的连续轮次区间，要求：每轮非空、
    相邻两轮集合不同（官方「跨轮接续」判据）、每个订单至少出现 1 轮。
    等价于 constraints.txt 第 6 条（连续锯切、不得跳轮）。

    ⚠️ 2026-09-22：旧版只有一个 `staircase_all`（单调窗口族），(m=3,R=5) 只有 1 条，
    而真实合法结构有 10 条。SALE03:24.0 的紧桶里，正是**非窗口族**的那些结构可行
    —— 例如 `((0,1,2),(1,2,3,4),(2,3))`（中间订单跨 4 轮）与 `((0,1,2),(1,),(3,4))`。
    只试窗口族曾把 13 个桶里的 7 个误判为不可行，是重载键丢单的真正原因。

    生成策略：
      * `len(ivs)^m <= combo_cap` 时**穷举**（覆盖 (3,5)=3375、(4,6)=194k、(5,5)=759k
        等同口径的小桶，本文交付里装桶尺寸几乎都在此域内）；
      * 否则用 `_structs_sample` 随机采样合法结构。
      两条路的结果都再并入**全部单调窗口族**（保证旧版的「均衡形状」不丢），
      窗口族排在最前，其余按 `_struct_key` 降序并用 `_even_pick` 均匀截到 `limit` 条，
      保证紧桶需要的低偏好形状不被挤掉。
    """
    key = (m, R, limit)
    hit = _STRUCT_CACHE.get(key)
    if hit is not None:
        return hit
    if R < 1 or m < 1 or R > max_rounds_for(m):
        _STRUCT_CACHE[key] = []
        return []

    ivs = [(s, e) for s in range(1, R + 1) for e in range(s, R + 1)]
    if len(ivs) ** m <= combo_cap:
        cands = []
        for combo in itertools.product(ivs, repeat=m):
            sup = _sup_from_intervals(combo, m, R)
            if sup is not None:
                cands.append((_struct_key(sup), sup))
    else:
        cands = _structs_sample(m, R, sample_n)

    # 窗口族（单调阶梯）**永远优先入选**：它是「均衡形状」，本文交付里除 SALE03:24.0
    # 之外的所有键都靠它 0 失败通过；绝不能让随机采样把它挤出 limit。
    wins_sups, wset = [], set()
    for wins in staircase_all(m, R):
        present, _l = _membership(wins, m, R)
        sup = tuple(tuple(r for r in range(R) if present[p][r]) for p in range(1, m + 1))
        if sup not in wset:
            wset.add(sup)
            wins_sups.append(sup)
    cands.sort(key=lambda x: x[0], reverse=True)
    rest, seen = [], set(wins_sups)
    for _k, sup in cands:
        if sup not in seen:
            seen.add(sup)
            rest.append(sup)
    out_sups = wins_sups + _even_pick(rest, max(0, limit - len(wins_sups)))
    out = [[list(s) for s in sup] for sup in out_sups]
    _STRUCT_CACHE[key] = out
    return out


def staircase_all(m, R):
    """单调窗口族的 (m, R) 阶梯（按偏好降序）。保留给自检与对照使用。"""
    key = (m, R)
    if key in _STAIR_ALL:
        return _STAIR_ALL[key]
    out = []
    if 1 <= R <= max_rounds_for(m):
        for aR in range(1, m + 1):
            w = _schedule(m, R, aR)
            if w is not None:
                out.append(w)
        out.sort(key=lambda w: _stair_key(m, w), reverse=True)
    _STAIR_ALL[key] = out
    return out


def staircase(m, R):
    """(m, R) 的最佳单调阶梯（按成员期谱字典序最大挑 aR）或 None。"""
    key = (m, R)
    if key in _STAIR:
        return _STAIR[key]
    out = None
    if m >= 1 and 1 <= R <= max_rounds_for(m):
        best_key = None
        for aR in range(1, m + 1):
            w = _schedule(m, R, aR)
            if w is None:
                continue
            sp = _spans(w, m)
            # 先最大化「最小成员期」（避免出现只出现 1 轮、必须一轮吃完全部料的
            # 位置），再按成员期谱字典序取最大。
            k = (min(sp),) + tuple(sorted(sp, reverse=True))
            if best_key is None or k > best_key:
                best_key, out = k, w
    _STAIR[key] = out
    return out


def _spans(wins, m):
    """每个位置（1..m）被多少个窗口覆盖 = 该订单的成员期轮数。"""
    sp = [0] * (m + 1)
    for aa, bb in wins:
        for p in range(aa, bb + 1):
            sp[p] += 1
    return sp[1:]


def _membership(wins, m, R):
    """由窗口序列给出 present[pos][r] 与每个位置的成员期长度。"""
    present = [[False] * R for _ in range(m + 1)]
    for r, (a, b) in enumerate(wins):
        for p in range(a, b + 1):
            present[p][r] = True
    lens = [0] + [sum(present[p]) for p in range(1, m + 1)]
    return present, lens


def shape_membership(m, R):
    """返回 present[pos][r]（pos 为 1..m）与每个位置的成员期长度。"""
    wins = staircase(m, R)
    if wins is None:
        return None, None
    return _membership(wins, m, R)


# ---------------------------------------------------------------------------
# 每轮 k 的分配（带支撑集）
# ---------------------------------------------------------------------------
_EXACT_CACHE = {}


_SCALE = 1000            # 净长量化到毫米（size 只保留到 0.001 m）


def _round_reach(sizes_in_round, cap_one, l_r, h_r, _cache={}):
    """轮内可达净长里有没有落在 `[l_r, h_r]` 的值（大整数 bit-set 有界背包）。

    只看**单轮**、忽略轮间耦合，因此结果集是真实可达集的**超集**——用它剪枝是
    安全的必要条件。`x_i` 取 0 也被允许（更保守），所以只会少砍、不会误砍。
    """
    lim = int(round(h_r * _SCALE))
    lower = int(math.ceil(l_r * _SCALE - 1e-6))
    if lower > lim:
        return False
    key = (tuple(sizes_in_round), tuple(cap_one), lim)
    reach = _cache.get(key)
    if reach is None:
        mask_all = (1 << (lim + 1)) - 1
        reach = 1
        for si, c in zip(sizes_in_round, cap_one):
            step = int(round(si * _SCALE))
            if step <= 0:
                continue
            cnt = min(c, lim // step)
            blk = 1
            while cnt > 0:
                take = min(blk, cnt)
                reach |= (reach << (take * step)) & mask_all
                cnt -= take
                blk <<= 1
        _cache[key] = reach
    window = ((1 << (lim - lower + 1)) - 1) << lower
    return (reach & window) != 0


def _round_bounds(k_tot, supports, sizes, cap_net, min_net):
    """每轮 / 轮间的廉价必要条件；不符直接判不可行。

    2026-09-22 加，原因：`exact_parts` 的 DFS 是唯一性能瓶颈（SALE03:24.0 单键
    7700 万次递归 60 s；NP01:43 前 300 单 1.02 亿次 133 s），而其中**全部**慢调用
    都是在给「一眼就不适配」的紧桶证伪。这几条 O(m·R)~O(m·R·log) 的判据能瞬间
    砍掉它们：

      1. `lo[r] = Σ_{i∈r} size_i <= cap_net`：每个订单在**每个**支撑轮至少放 1 段。
      2. `hi[r] = Σ_{i∈r} (k_i - |sup_i| + 1)·size_i >= min_net`：单轮上界取紧
         ——订单 i 要给其它 `|sup_i|-1` 个支撑轮各留 ≥1 段，故单轮最多
         `k_i - |sup_i| + 1` 段（用松界 `Σ k_i·size_i` 几乎砍不掉东西）。
      3. **轮间耦合界**：其余各轮都被 `[min_net, cap_net]` 夹住，于是
         `L[r] ∈ [total-(n-1)·cap_net, total-(n-1)·min_net]`。总长顶到
         `n·min_net` 或 `n·cap_net` 时这条界把窗口压成一点，配 (4) 立刻判死。
      4. **单轮可达值背包**：窗口较窄时用大整数 bit-set 做一次有界背包，检查窗口内
         是否存在 `Σ x_i·size_i`。紧桶的不可行性几乎都来自「窗口里没有可达值」。
      5. `k_i <= Σ_{r∈sup_i} floor((cap_net-(lo[r]-size_i))/size_i)`：扣掉同轮他单
         保底占用后，单轮能盛下订单 i 的最大段数。
    """
    m = len(k_tot)
    n = max(max(s) for s in supports) + 1
    lo = [0.0] * n
    hi = [0.0] * n
    cap_one = [0] * m
    total = 0.0
    for i in range(m):
        si, nsup, ki = sizes[i], len(supports[i]), k_tot[i]
        if ki < nsup:
            return False
        cap_one[i] = ki - nsup + 1
        total += ki * si
        c = cap_one[i] * si
        for r in supports[i]:
            lo[r] += si
            hi[r] += c
    if total > n * cap_net + _EPS or total < n * min_net - _EPS:
        return False
    g_lo = total - (n - 1) * cap_net          # L[r] 的下界（其它轮不可能占更多）
    g_hi = total - (n - 1) * min_net          # L[r] 的上界（其它轮不可能占更少）
    if g_lo > g_hi + _EPS:
        return False
    # ---- (6) 轮长窗口的**和**必须覆盖 total ----
    # Σ_r L[r] = total 且 L[r] ∈ [l_r, h_r]，故 Σ l_r <= total <= Σ h_r 是必要条件。
    # 紧桶上这条极强：当多数订单 `nsup_i = n`（每轮都出现、每轮至少 1 段）时
    # `hi[r] ≈ lo[r]`，窗口近乎退化为点，Σ h_r 一旦低于 total 立刻判死。
    # 旧版只逐轮检查 `hi[r] < l_r`，不做这个求和检查，于是这类「一眼不可行」的紧桶
    # 全部漏进 `exact_parts` 的 DFS，在 200 万节点预算里空转到 0.5~1.0 s 才返回
    # —— 这就是「78 次 exact_parts 吃掉 35.5 s / 占全卷 87%」的来源。
    l_r_all = [max(lo[r], g_lo, min_net) for r in range(n)]
    h_r_all = [min(cap_net, g_hi, hi[r]) for r in range(n)]
    if sum(l_r_all) > total + _EPS or sum(h_r_all) < total - _EPS:
        return False
    wide = 3.0 * max(sizes)
    for r in range(n):
        l_r, h_r = l_r_all[r], h_r_all[r]
        if l_r > h_r + _EPS or hi[r] < l_r - _EPS or lo[r] > h_r + _EPS:
            return False
        if h_r - l_r <= wide:
            in_round = [(sizes[i], cap_one[i]) for i in range(m) if r in supports[i]]
            if not in_round:
                return False
            if not _round_reach([x[0] for x in in_round], [x[1] for x in in_round],
                                l_r, h_r):
                return False
    for i in range(m):
        si = sizes[i]
        room = 0
        for r in supports[i]:
            room += math.floor((cap_net - (lo[r] - si)) / si + 1e-9)
        if k_tot[i] > room:
            return False
    # ---- (7) 单订单视角的**上界** ----
    # 轮 r 里其它订单至少占 `lo[r]-s_i`，故订单 i 在该轮最多 `h_r - (lo[r]-s_i)`
    # 段（`h_r` 已含 cap_net / g_hi / hi[r] 三重收紧）。累加得 `k_i·s_i` 的必要上界。
    # ⚠️ 只做上界：下界 `Σ_{r∈sup_i} l_r` **不是**必要条件——其它订单可以把某轮填到
    # 远超订单 i 自身段长之和，于是 `Σ l_r` 完全可能大于 `k_i·s_i` 而实例仍可行，
    # 写成下界会误杀可行桶（= clause 12 丢单）。2026-09-22 推导时踩过这个坑。
    for i in range(m):
        si = sizes[i]
        hi_i = 0.0
        for r in supports[i]:
            hi_i += max(0.0, h_r_all[r] - (lo[r] - si))
        if k_tot[i] * si > hi_i + _EPS:
            return False
    return True


def _default_budget():
    """`exact_parts` 的 DFS 节点预算（可用环境变量 `CS_EXACT_BUDGET` 覆盖）。

    预算决定「放弃一个结构」的代价：实测 NP01:43.0 前 200 单里 77 次
    `exact_parts` 调用**每次都把 200 万预算跑满**（≈0.46 s），合计 35.6 s，
    占 `build_groups` 的 87%。这些实例既没在预算内找到解、也没在预算内证伪。

    取 400,000 的依据（2026-09-22 实测，NP01:43.0 前 200 单，最重键的最重子集）：
        预算 2,000,000 -> 41.2 s / 29 组 / 0 shapeless
        预算 1,000,000 -> 23.8 s / 29 组 / 0 shapeless
        预算   500,000 -> 16.5 s / 29 组 / 0 shapeless
        预算   200,000 -> 10.3 s / 29 组 / 0 shapeless
    即在这条最难路径上，预算从 20 万提到 200 万**不改变任何结果**（组数、丢单数
    都相同）。取 40 万 = 已证无损点的 2 倍余量，同时保留 5 倍加速。

    ⚠️ 预算耗尽的语义是「**没搜完**」，调用方按保守处理（放弃该结构，去试别的
    结构/轮数），因此它**不会**产出非法方案（不会假阳性），只可能在极端情况下
    多退化为二分桶（更多方案、刀数略升）。全量构建以「所有键 0 shapeless + 连续性
    审计 PASS」为验收闸门，若某键出现丢单即应上调此预算。
    """
    try:
        return int(os.environ.get('CS_EXACT_BUDGET', '') or 400000)
    except ValueError:
        return 400000


def exact_parts(k_tot, supports, sizes, cap_net, min_net, budget=None):
    """精确 DFS：判断是否存在满足「每轮净长 ∈ [min_net, cap_net]」的整数分配。

    逐订单、逐支撑轮地**增量**枚举段数，每一层用「该轮剩余容量还能放几段」剪枝；
    并额外用**后缀上下界**剪枝：把 t 之后所有订单都尽量塞进某轮能得到该轮的最大
    可达净长 `L[r] + suffix_up[r][t]`，若小于 min_net 就整支砍掉（对称地，
    `L[r] + suffix_lo[r][t] > cap_net` 也砍）。没有这两条，k~70 的桶要搜几百万
    个分支；加上后写实例上通常几千次即可穷尽。

    ⚠️ 2026-09-22 修正：旧版把「超出 budget」直接当成不可行返回 None，于是
    `distribute` 在重载键上把**可行**的桶误判为不可行（SALE03:24.0 大面积丢单的
    真凶）。现在 budget 只是保护阈值（默认 400 万，配合剪枝基本不会触发），
    触发时返回 None 仅代表「没搜完」，调用方仍需按保守处理。
    """
    # ⚠️ 缓存键**必须**含 cap_net / min_net：它们随并行支数 p 变化，而不同的 p
    # 完全可能给出同一个 k_tot（需求支数少时 ceil(pieces/p) 对 p=1,2 相同）。
    # 旧键漏了它们，于是 p=1 的结果会被 p=2 复用——假可行会让轮长越界（提交作废），
    # 假不可行会白丢容量（clause 12 丢单）。2026-09-22 修。
    key = (tuple(k_tot), tuple(tuple(s) for s in supports), tuple(sizes),
           round(cap_net, 9), round(min_net, 9))
    if budget is None:
        budget = _default_budget()
    hit = _EXACT_CACHE.get(key)
    if hit is not None:
        return [list(row) for row in hit] if hit is not False else None

    m = len(k_tot)
    n = max(max(s) for s in supports) + 1
    sup_idx = [list(s) for s in supports]
    sup_set = [set(s) for s in sup_idx]
    if not _round_bounds(k_tot, supports, sizes, cap_net, min_net):
        _EXACT_CACHE[key] = False
        return None
    total = sum(k * s for k, s in zip(k_tot, sizes))
    lo_sum = sum(len(sup_idx[i]) * sizes[i] for i in range(m))
    hi_sum = 0.0                                   # 每轮上界取紧（见 _round_bounds）
    for r in range(n):
        hi_sum += sum((k_tot[i] - len(sup_idx[i]) + 1) * sizes[i]
                      for i in range(m) if r in sup_set[i])
    if total > n * cap_net + _EPS or total < n * min_net - _EPS \
            or lo_sum > n * cap_net + _EPS or hi_sum < n * min_net - _EPS:
        _EXACT_CACHE[key] = False
        return None

    order = sorted(range(m), key=lambda i: (len(sup_idx[i]), -k_tot[i] * sizes[i]))
    # suffix_up[r][t] / suffix_lo[r][t]：order[t:] 这些订单在轮 r 上的可达上/下界
    suffix_up = [[0.0] * n for _ in range(m + 1)]
    suffix_lo = [[0.0] * n for _ in range(m + 1)]
    for t in range(m - 1, -1, -1):
        i = order[t]
        su, sl = suffix_up[t + 1], suffix_lo[t + 1]
        up_i = (k_tot[i] - len(sup_idx[i]) + 1) * sizes[i]   # 单轮最多贡献（取紧）
        for r in range(n):
            if r in sup_set[i]:
                suffix_up[t][r] = su[r] + up_i
                suffix_lo[t][r] = sl[r] + sizes[i]
            else:
                suffix_up[t][r] = su[r]
                suffix_lo[t][r] = sl[r]

    parts = [[0] * n for _ in range(m)]
    L = [0.0] * n
    work = [0]

    def dfs(t):
        work[0] += 1
        if t == m:
            return all(min_net - _EPS <= L[r] <= cap_net + _EPS for r in range(n))
        if work[0] > budget:
            return False
        su, sl = suffix_up[t], suffix_lo[t]
        for r in range(n):
            if L[r] + su[r] < min_net - _EPS:
                return False
            if L[r] + sl[r] > cap_net + _EPS:
                return False
        i = order[t]
        sup = sup_idx[i]
        s = sizes[i]
        nsup = len(sup)

        def choose(j, left):
            work[0] += 1
            if work[0] > budget:
                return False
            r = sup[j]
            cap_rows = int(math.floor((cap_net - L[r]) / s + 1e-9))
            if j == nsup - 1:
                if 1 <= left <= cap_rows:
                    L[r] += left * s
                    parts[i][r] = left
                    if dfs(t + 1):
                        return True
                    L[r] -= left * s
                    parts[i][r] = 0
                return False
            hi = min(left - (nsup - j - 1), cap_rows)
            for x in range(1, hi + 1):
                L[r] += x * s
                parts[i][r] = x
                if choose(j + 1, left - x):
                    return True
                L[r] -= x * s
                parts[i][r] = 0
            return False

        return choose(0, k_tot[i])

    if not dfs(0):
        _EXACT_CACHE[key] = False
        return None
    _EXACT_CACHE[key] = [list(row) for row in parts]
    return parts


def distribute(k_tot, supports, sizes, trim, min_len, cap_len, iters=800):
    """把各订单的段数摊到它自己的支撑轮上，使每轮长度落在 [min_len, cap_len]。

    supports[i] 是订单 i 出现的轮次（升序）；每个支撑轮至少 1 段。
    返回 parts[i][r]（缺失轮为 0）或 None。

    两步走（2026-09-22 重写三版；旧版在重载键上大面积误判不可行）：
      1. **受限轮优先 + 预留**：按「该轮可选订单数」升序处理轮次（首轮/末轮可选订单
         最少，先定），每轮先给每个可选订单保底 1 段，再用「最大可放入段」把该轮
         填到净长上限；填的同时给该订单**尚未处理的支撑轮各留 1 段**。
         这正是精确解的结构：以 SALE03 k=[70,48,20] 为例，精确解是
         `{0:30,1:12,2:28}/{1:18,2:1,3:29}/{2:1,3:1,4:18}`——末轮（只含 1 单）
         先被填到 18 段并给前两轮各留 1 段。旧版按「当前最短轮」注水会先把中段
         顶穿到 240 m，再靠单步修复永远救不回来。
      2. **局部搜索**：以 `viol(x) = max(0,x-cap_net) + max(0,min_net-x)` 之和为
         目标做下降，邻域含「单搬」与「等量对换」。对换是必需的：当所有轮同侧
         越界时单搬没有落脚点。
    """
    m = len(k_tot)
    n = max(max(s) for s in supports) + 1
    cap_net = cap_len - trim
    min_net = min_len - trim
    sup_idx = [list(s) for s in supports]
    sup_set = [set(s) for s in sup_idx]
    if not _round_bounds(k_tot, supports, sizes, cap_net, min_net):
        return None
    for i in range(m):
        if k_tot[i] < len(sup_idx[i]):
            return None
    parts = [[0] * n for _ in range(m)]
    rem = list(k_tot)
    seen = [0] * m
    L = [0.0] * n

    # ---- 1) 受限轮优先的前向构造 ----
    order = sorted(range(n), key=lambda r: sum(1 for i in range(m) if r in sup_set[i]))
    for r in order:
        elig = [i for i in range(m) if r in sup_set[i]]
        if not elig:
            return None
        for i in elig:
            if rem[i] < 1:
                return None
            parts[i][r] += 1
            rem[i] -= 1
            L[r] += sizes[i]
            seen[i] += 1
        while True:
            left = cap_net - L[r]
            if left <= _EPS:
                break
            cand = [i for i in elig
                    if rem[i] - (len(sup_idx[i]) - seen[i]) > 0 and sizes[i] <= left + _EPS]
            if not cand:
                break
            i = max(cand, key=lambda x: sizes[x])
            parts[i][r] += 1
            rem[i] -= 1
            L[r] += sizes[i]
    # 仍未收下的段：塞进该单还有余量的支撑轮
    for i in range(m):
        while rem[i] > 0:
            slot = [r for r in sup_idx[i] if L[r] + sizes[i] <= cap_net + _EPS]
            if not slot:
                return exact_parts(k_tot, supports, sizes, cap_net, min_net)
            r = max(slot, key=lambda x: L[x])
            parts[i][r] += 1
            rem[i] -= 1
            L[r] += sizes[i]

    # ---- 2) 局部搜索 ----
    def viol(x):
        return max(0.0, x - cap_net) + max(0.0, min_net - x)

    net = [L[r] for r in range(n)]
    cur = sum(viol(x) for x in net)
    for _ in range(iters):
        if cur <= _EPS:
            return parts
        r_hi = max(range(n), key=lambda r: net[r])
        r_lo = min(range(n), key=lambda r: net[r])
        best = None
        for i in range(m):
            if parts[i][r_hi] <= 1 or r_hi not in sup_set[i]:
                continue
            for r2 in sup_idx[i]:
                if r2 == r_hi:
                    continue
                d = (viol(net[r_hi] - sizes[i]) + viol(net[r2] + sizes[i])) \
                    - (viol(net[r_hi]) + viol(net[r2]))
                if d < -_EPS and (best is None or d < best[0]):
                    best = (d, i, -1, r_hi, r2)
        if best is None:
            for i in range(m):
                if parts[i][r_hi] <= 1 or r_hi not in sup_set[i] or r_lo not in sup_set[i]:
                    continue
                for j in range(m):
                    if i == j or parts[j][r_lo] <= 1:
                        continue
                    if r_hi not in sup_set[j] or r_lo not in sup_set[j]:
                        continue
                    a = net[r_hi] - sizes[i] + sizes[j]
                    b = net[r_lo] + sizes[i] - sizes[j]
                    d = (viol(a) + viol(b)) - (viol(net[r_hi]) + viol(net[r_lo]))
                    if d < -_EPS and (best is None or d < best[0]):
                        best = (d, i, j, r_hi, r_lo)
        if best is None:
            return exact_parts(k_tot, supports, sizes, cap_net, min_net)
        _, i, j, r1, r2 = best
        if j < 0:
            parts[i][r1] -= 1
            parts[i][r2] += 1
            net[r1] -= sizes[i]
            net[r2] += sizes[i]
        else:
            parts[i][r1] -= 1
            parts[i][r2] += 1
            parts[j][r2] -= 1
            parts[j][r1] += 1
            net[r1] += sizes[j] - sizes[i]
            net[r2] += sizes[i] - sizes[j]
        cur = sum(viol(x) for x in net)
    return exact_parts(k_tot, supports, sizes, cap_net, min_net)


def _assign(m, lens, masses, cache, R):
    """需求越大 -> 成员期越长的位置。返回 pos_of[idx] = 位置(1..m)。"""
    key = (m, R, tuple(lens[1:]))
    hit = cache.get(key)
    if hit is not None:
        return hit
    rank_pos = sorted(range(1, m + 1), key=lambda p: -lens[p])
    rank_ord = sorted(range(m), key=lambda i: -masses[i])
    pos_of = [0] * m
    for k, idx in enumerate(rank_ord):
        pos_of[idx] = rank_pos[k]
    cache[key] = pos_of
    return pos_of


# ---------------------------------------------------------------------------
# 候选枚举
# ---------------------------------------------------------------------------
def scheme_candidates(ids, model, cfg, *, r_max=R_MAX):
    """一个订单组（同钢种同规格）的 Pareto (knives, declared) 合规候选。

    ids 内部顺序无关；返回的 round 结构保证：
      * 相邻两轮订单集合不同（官方「跨轮接续不连续」判据 = 0）；
      * 每个订单的轮次连续（跳轮 = 0）；
      * 每轮至少 1 个订单（官方示例即含单订单轮）。
    """
    orders = [model.orders[i] for i in ids]
    m = len(ids)
    if m < 1 or m > M_MAX:
        return []
    sizes = [o.size for o in orders]
    demands = [o.pieces for o in orders]
    caps = [model.caps[i] for i in ids]
    masses = [o.pieces * o.size * o.linear_weight for o in orders]
    mu = orders[0].linear_weight
    if mu <= 0 or any(p < 1 for p in demands):
        return []
    tr = cfg.round_trim

    pos_cache = {}
    found = []
    # ⚠️ 2026-09-22 性能重排（第 4 版）：**坯型循环外提**。
    # 旧版循环序是 坯型 -> p -> R -> 结构，而 `distribute(k_tot, supports, sizes,
    # tr, min_len, cap_len)` 与坯型**完全无关**（k_tot 只由 p 与需求支数决定，
    # cap_len 只由 p 与 μ 决定），只有「申报支数 = ceil(轮长*p/usable)」才用到坯型。
    # 于是同一 (p, R, 结构) 被 5 个坯型各算一次 `distribute`（= 5 倍冗余）。
    # 实测 NP01:43.0 前 200 单：2 076 次 distribute / 35.9 s，占 `build_groups`
    # 总耗时 87%。改成 p -> R -> 结构 -> 坯型 后，每个结构只算一次 distribute，
    # 而**结构枚举一条不少**（SALE03:24.0 的 0 丢单修复完全保留）。
    for p in range(1, model.parallel_limit(ids) + 1):
        cap_len = min(cfg.bed_length, cfg.bed_weight / (p * mu))
        if cap_len < cfg.min_bed_length - _EPS:
            continue
        k_tot, ok = [], True
        for i in range(m):
            k = max(1, _ceil(demands[i] / p))
            if k * p > caps[i]:
                ok = False
                break
            k_tot.append(k)
        if not ok:
            continue
        written = [round(k * s, 9) for k, s in zip(k_tot, sizes)]
        # `written[i]` is order i's TOTAL net length across every round it
        # appears in, so it is bounded by one billet's rolled length
        # (`usable`), NOT by the 150 m bed: a single round carries only a
        # split of it, and `cap_len` already enforces the bed bounds below.
        need_len = max(written) + tr
        avail = []
        for blank in model.catalogue(ids):
            usable = model.blank_length(ids, blank)
            if usable > 0 and need_len <= usable + _EPS:
                avail.append((blank, usable))
        if not avail:
            continue
        spread = sum(written)
        r_lo = max(1, _ceil(spread / (cap_len - tr)))
        r_hi = min(r_max, max_rounds_for(m),
                   int(math.floor(spread / (cfg.min_bed_length - tr) + _EPS)))
        if r_lo > r_hi:
            continue
        for R in range(r_lo, r_hi + 1):
            if spread > R * (cap_len - tr) + _EPS:
                continue
            # 同一个 (m, R) 有多条合法「区间结构」（每单一段连续轮次、每轮非空、
            # 相邻两轮集合不同）。旧版只枚举单调窗口族 `staircase_all`：
            # (m=3,R=5) 只有 1 条，而真实有 10 条；重载键的紧桶恰恰是**非窗口族**
            # 可行（如 ((0,1,2),(1,2,3,4),(2,3))）。这是 SALE03:24.0 丢单真因。
            # 现按偏好顺序遍历全部结构，逐条试 `distribute`；一旦某结构可行，
            # 同一 (p,R) 下**所有可用坯型**都由它派生候选（轮长与刀数与坯型无关），
            # 故立即跳出结构循环。
            for sup in structures(m, R):
                lens = [0] * (m + 1)
                for idx in range(m):
                    lens[idx + 1] = len(sup[idx])
                pos_of = _assign(m, lens, masses, pos_cache, R)
                supports = [list(sup[pos_of[idx] - 1]) for idx in range(m)]
                parts = distribute(k_tot, supports, sizes, tr,
                                   cfg.min_bed_length, cap_len)
                if parts is None:
                    continue
                rounds, knives = [], 0
                for r in range(R):
                    lengths, seg = {}, 0
                    for idx in range(m):
                        if parts[idx][r]:
                            lengths[orders[idx].oid] = round(parts[idx][r] * sizes[idx], 9)
                            seg += row_segments(lengths[orders[idx].oid], sizes[idx], 'floor')
                    length = tr + sum(lengths.values())
                    if not (cfg.min_bed_length - _EPS <= length <= cfg.bed_length + _EPS):
                        rounds = None
                        break
                    knives += seg + 1
                    rounds.append((length, lengths))
                if rounds is None:
                    continue
                for blank, usable in avail:
                    declared = 0.0
                    rds = []
                    for length, lengths in rounds:
                        count = max(1, math.ceil(length * p / usable))
                        declared += count * blank.weight
                        rds.append(dict(lengths=lengths, p=p, count=count))
                    found.append(dict(knives=knives, declared=declared,
                                      blank_type=blank.bid, rounds=rds))
                break
    if not found:
        return []
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


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------
def selftest(verbose=True):
    bad = 0
    feasible = 0
    if verbose:
        print('=' * 78)
        print('### 阶梯族形状自检  (m: 订单数, R: 轮数, 成员期谱)')
    for m in range(2, 13):
        for R in range(1, 7):
            wins = staircase(m, R)
            if wins is None:
                continue
            feasible += 1
            present, lens = shape_membership(m, R)
            sets = [frozenset(p for p in range(1, m + 1) if present[p][r]) for r in range(R)]
            for r in range(R - 1):
                if sets[r] == sets[r + 1]:
                    print('  !! (m=%d,R=%d) 相邻集合相同 @%d' % (m, R, r))
                    bad += 1
            for p in range(1, m + 1):
                rs = [r for r in range(R) if present[p][r]]
                if not rs:
                    print('  !! (m=%d,R=%d) 订单 %d 从未出现' % (m, R, p))
                    bad += 1
                elif rs != list(range(rs[0], rs[0] + len(rs))):
                    print('  !! (m=%d,R=%d) 订单 %d 跳轮 %s' % (m, R, p, rs))
                    bad += 1
            if any(len(s) < 1 for s in sets):
                print('  !! (m=%d,R=%d) 存在空轮' % (m, R))
                bad += 1
            if verbose and m <= 10:
                print('  m=%2d R=%d  %-34s 大小=%s'
                      % (m, R, wins, [b - a + 1 for a, b in wins]))
    print('  (m,R) 可行组合 = %d，违规形状 = %d（应为 0）' % (feasible, bad))
    print('=' * 78)
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default='data/semi')
    ap.add_argument('--selftest', action='store_true')
    args = ap.parse_args()
    if args.selftest or True:
        selftest()


if __name__ == '__main__':
    main()
