# -*- coding: utf-8 -*-
"""复赛「跨轮接续不连续」官方口径审计器（2026-09-22 反推 + 可行性裁剪）。

官方反馈（用户转述，复赛第 1 次提交，被判不可行）：
    不可行，总分=0；违规次数：7559条，累计扣分37795分；
    跨轮接续不连续(7559条)：方案0批0、方案0批1、方案0批2、方案0批3、方案0批4、
    方案1批0、方案1批1、方案1批2、方案1批4、方案2批0、方案2批1、方案2批2…等7559处

反推
----
违规定位单位是 **(方案, 批)**（不是订单：官方串给的全是 `方案a批j` 对）。

在旧交付包上（`round_shaper` 把方案内**每个订单放进每一轮**，故同一方案内所有轮
订单集合完全相同），两个候选判据给出**同一个数 7559**：

    B  相邻两轮订单集合**有交集**      -> 7559   （集合相同必然有交集，故与 C 重合）
    C  相邻两轮订单集合**完全相同**    -> 7559

且排序后前 12 项 = 方案0批0..批4、方案1批0,1,2,4、方案2批0,1,2，与官方串**逐字一致**
——含「方案1 恰好缺 批3」这一**不可伪造的指纹**：旧包 scheme1 在批3/批4 之间正是
`merge_compatible` 的拼接缝，缝两侧来自不同源方案、订单集合不同，故该边界不计违规。
「批 = 轮边界」这一点同时排除了「按订单计」的读法（那会给出 `Σ m_s(R_s-1)`，
比 7559 大一个量级）。

判据 B 与 C 在旧包上重合，必须另找判别证据，而证据指向 **C**：
  * 官方题面 PDF 的**输出示例本身**在相邻两轮共享订单——方案 2 第 1 轮
    `{A20260105:109.25, A20260104:25.6}`、第 2 轮 `{A20260104:70.4}`，两轮都含
    A20260104。若「有交集」是违规，官方示例自己就不合法，故 B 不是判据。
  * 该示例恰好是 C 合规的最小演示：集合由 `{104,105}` 变为 `{104}`，
    且 A20260104 的出现轮次连续。它还顺带证明**单订单轮次合法**（第 2 轮只有 1 单）。

所以判据是 **C（相邻两轮订单集合不得完全相同）**，并配合 clause 6 后半句
「同一订单的出现轮次必须连续（不得跳轮）」。本审计器两条都查。

用法
----
    python -X utf8 diagnostics/semi_continuity_audit.py                  # 审计旧交付包
    python -X utf8 diagnostics/semi_continuity_audit.py <result.json>
    python -X utf8 diagnostics/semi_continuity_audit.py --selftest       # 合成样例自检
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "deliverables_semi" / "复赛结果_棒材优化.json"
OFFICIAL_COUNT = 7559
OFFICIAL_HEAD = [(0, 0), (0, 1), (0, 2), (0, 3), (0, 4),
                 (1, 0), (1, 1), (1, 2), (1, 4),
                 (2, 0), (2, 1), (2, 2)]


def audit(plan):
    """返回 (统计 dict, 判据 B 明细, 判据 C 明细, 跳轮明细)。"""
    shared, identical, changed, incomparable = [], [], [], []
    for a, batch in enumerate(plan):
        rounds = [set(s) for s in (batch.get("length_scheme") or [])]
        for j in range(len(rounds) - 1):
            x, y = rounds[j], rounds[j + 1]
            if x & y:
                shared.append((a, j))
            if x == y:
                identical.append((a, j))
            else:
                changed.append((a, j))
            if not (x <= y or y <= x):
                incomparable.append((a, j))

    spans = {}
    for a, batch in enumerate(plan):
        for j, sc in enumerate(batch.get("length_scheme") or []):
            for oid in sc:
                spans.setdefault((a, oid), []).append(j)
    multi = {k: sorted(v) for k, v in spans.items() if len(v) > 1}
    skipped = {k: v for k, v in multi.items()
               if v != list(range(v[0], v[0] + len(v)))}

    res = {
        "schemes": len(plan),
        "rounds": sum(len(b.get("length_scheme") or []) for b in plan),
        "B_shared_adjacent": len(shared),
        "C_equal_adjacent": len(identical),
        "A_rounds_minus_fragments": None,
        "incomparable_adjacent": len(incomparable),
        "changed_adjacent": len(changed),
        "orders_spanning_rounds": len(multi),
        "orders_with_skips": len(skipped),
    }
    boundaries = sum(1 for b in plan
                     for j in range(1, len(b.get("length_scheme") or []))
                     if set(b["length_scheme"][j]) != set(b["length_scheme"][j - 1]))
    res["A_rounds_minus_fragments"] = res["rounds"] - (res["schemes"] + boundaries)
    return res, shared, identical, skipped, incomparable


def report(tag, plan, expect_official=False):
    res, shared, identical, skipped, incomparable = audit(plan)
    print("=" * 78)
    print("### %s" % tag)
    print("    方案数=%(schemes)d  轮数=%(rounds)d" % res)
    print("    跨轮订单(出现在>=2轮)=%(orders_spanning_rounds)d  "
          "其中跳轮(轮次不连续)=%(orders_with_skips)d" % res)
    print("    候选判据计数：")
    print("      A 片段内相邻对数(轮数-片段数) = %(A_rounds_minus_fragments)d" % res)
    print("      B 相邻两轮集合【有交集】      = %(B_shared_adjacent)d  "
          "（已证不可行，非官方判据）" % res)
    print("      C 相邻两轮集合【完全相同】    = %(C_equal_adjacent)d   <== 采用的判据"
          % res)
    print("      参考：相邻两轮集合互不嵌套    = %(incomparable_adjacent)d ；集合发生变化 = "
          "%(changed_adjacent)d" % res)
    print("    扣分 = C x 5 = %d ；跳轮另计，每单 5 分 = %d"
          % (5 * res["C_equal_adjacent"], 5 * res["orders_with_skips"]))
    if expect_official:
        head = sorted(identical)[:12]
        print("    与官方条数 %d 一致: %s ； 前 12 项逐字一致: %s"
              % (OFFICIAL_COUNT, res["A_rounds_minus_fragments"] == OFFICIAL_COUNT
                 and res["B_shared_adjacent"] == OFFICIAL_COUNT,
                 head == OFFICIAL_HEAD))
    ok = res["C_equal_adjacent"] == 0 and res["orders_with_skips"] == 0
    print("    结论: %s" % ("PASS（0 条，可提交）" if ok
                           else "FAIL（会被官方判跨轮接续不连续）"))
    return res


def selftest():
    print("=" * 78)
    print("### 自检：违规结构 vs 合规结构")
    bad = [{"orders": ["X", "Y"],
            "length_scheme": [{"X": 30.0, "Y": 20.0}] * 6,
            "counts": [44] * 6, "blank_type": 1, "blank_counts": [4] * 6}]
    report("违规样例  同一对订单重复 6 轮（旧交付包的做法）", bad)

    good = [{"orders": ["A", "B", "C"],
             "length_scheme": [{"A": 20.0}, {"A": 20.0, "B": 20.0}, {"B": 20.0, "C": 20.0},
                               {"C": 20.0}],
             "counts": [40] * 4, "blank_type": 1, "blank_counts": [4] * 4}]
    report("合规样例  单调阶梯（相邻集合不同、每单轮次连续）", good)

    skip = [{"orders": ["X", "Y", "Z"],
             "length_scheme": [{"X": 20.0, "Y": 20.0}, {"Y": 20.0, "Z": 20.0},
                               {"X": 20.0, "Z": 20.0}],
             "counts": [40] * 3, "blank_type": 1, "blank_counts": [4] * 3}]
    report("跳轮样例  X 在第1、3轮出现而第2轮缺失（clause 6 后半句）", skip)

    demo = [{"orders": ["A20260104", "A20260105"],
             "length_scheme": [{"A20260105": 109.25, "A20260104": 25.6},
                               {"A20260104": 70.4}],
             "counts": [1] * 2, "blank_type": 1, "blank_counts": [1] * 2}]
    report("官方 PDF 输出示例（方案2）——相邻两轮共享 A20260104，但集合不同，合规", demo)

    print("=" * 78)
    print("要点：本审计器以 C（相邻两轮集合不得完全相同）+ 无跳轮 作为判定；")
    print("      「有交集」不是判据——官方 PDF 输出示例的相邻两轮就共享订单。")
    return


def main():
    args = list(sys.argv[1:])
    if args and args[0] == "--selftest":
        selftest()
        return
    path = Path(args[0]) if args else DEFAULT
    plan = json.loads(path.read_text(encoding="utf-8"))
    report("待审计包 %s" % path.name, plan,
           expect_official=(path.resolve() == DEFAULT.resolve()))


if __name__ == "__main__":
    main()
