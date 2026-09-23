"""Read-only solver diagnostics; writes only this evidence directory."""
import bisect
from collections import defaultdict
from functools import reduce
from hashlib import sha256
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from solver import Config, Model, load_orders, load_blanks, validate_plan


def ceil_bound(x):
    # Slightly relax the lower bound to avoid increasing it due to floating error.
    return math.ceil(x - 1e-8)


def main():
    evidence = Path(__file__).resolve().parent
    cfg = Config(**json.loads((ROOT / "runs/normal_heterogeneous/config.json").read_text()))
    orders = load_orders(str(ROOT / "data/orders.normalized.csv"), cfg)
    blanks = load_blanks(str(ROOT / "data/blanks.normalized.csv"))
    model = Model(orders, cfg, blanks)
    groups = defaultdict(list)
    for i, order in enumerate(orders):
        groups[order.steel, order.diameter].append(i)
    group_bounds = []
    for key, ids in sorted(groups.items()):
        rho = orders[ids[0]].linear_weight
        parallel_max = model.parallel_limit(ids)
        demand = sum(orders[i].pieces * orders[i].size * rho for i in ids)
        cap = sum(model.caps[i] * orders[i].size * rho for i in ids)
        segment_lower = sum(ceil_bound(orders[i].pieces / parallel_max) for i in ids)
        sequential_length_lower = sum(ceil_bound(orders[i].pieces / parallel_max) * orders[i].size for i in ids)
        sizes_mm = sorted(set(round(orders[i].size * 1000) for i in ids))
        assert all(abs(orders[i].size * 1000 - round(orders[i].size * 1000)) < 1e-7 for i in ids)
        gcd_mm = reduce(math.gcd, sizes_mm)
        reachable = bytearray(148000 // gcd_mm + 1)
        reachable[0] = 1
        # Unbounded integer size combinations relax order quantities and one-blank
        # segment constraints: every valid row is still included in this superset.
        for unit in [s // gcd_mm for s in sizes_mm]:
            for length in range(unit, len(reachable)):
                reachable[length] |= reachable[length - unit]
        lengths = [i * gcd_mm / 1000 for i, ok in enumerate(reachable) if ok and i * gcd_mm >= 48000]
        def snap_down(length):
            index = bisect.bisect_right(lengths, length + 1e-9) - 1
            return lengths[index] if index >= 0 else None
        max_mass = max_yield = 0.0
        max_mass_choice = max_yield_choice = None
        max_mass_continuous = max(min(148 * p * rho, 60000 - 2 * p * rho) for p in range(1, parallel_max + 1))
        for p in range(1, parallel_max + 1):
            length_limit = min(148, 60000 / (p * rho) - 2)
            length = snap_down(length_limit)
            if length is None:
                continue
            finished = length * p * rho
            if finished > max_mass:
                max_mass = finished
                max_mass_choice = {"parallel": p, "net_length_m": length}
            for blank in blanks:
                for count in range(1, math.ceil(60000 / blank.weight) + 1):
                    raw = count * blank.weight
                    length = snap_down(min(length_limit, raw / (p * rho) - 2))
                    if length is None:
                        continue
                    value = length * p * rho / raw
                    if value > max_yield:
                        max_yield = value
                        max_yield_choice = {"parallel": p, "net_length_m": length, "blank_type": blank.bid, "blank_count": count, "raw_kg": raw}
        mass_round_lb = ceil_bound(demand / max_mass)
        length_round_lb = ceil_bound(sequential_length_lower / 148)
        rounds_lb = max(mass_round_lb, length_round_lb)
        # Net product <=148*p*rho per row implies a material loss of at least
        # 2*rho*ceil(total_required_net_length/148) over the group.
        min_parallel_sum = ceil_bound(demand / (148 * rho))
        trim_lb = 2 * rho * min_parallel_sum
        group_bounds.append({
            "steel": key[0], "diameter_mm": key[1], "order_count":len(ids), "linear_weight_kg_per_m":rho,
            "max_parallel":parallel_max, "mandatory_product_kg":demand,"maximum_product_under_5pct_cap_kg":cap,
            "size_gcd_mm":gcd_mm, "mandatory_internal_segments_lower_bound":segment_lower,
            "sequential_net_length_lower_bound_m":sequential_length_lower,
            "one_round_product_upper_bound_continuous_kg":max_mass_continuous,
            "one_round_product_upper_bound_discrete_kg":max_mass,"max_product_relaxed_row":max_mass_choice,
            "rounds_lower_bound_mass":mass_round_lb,"rounds_lower_bound_sequential_length":length_round_lb,
            "rounds_lower_bound":rounds_lb,"knives_lower_bound":segment_lower+rounds_lb,
            "minimum_shared_trim_kg":trim_lb,
            "one_round_yield_upper_bound_discrete":max_yield,"max_yield_relaxed_row":max_yield_choice,
        })
    segments = sum(g["mandatory_internal_segments_lower_bound"] for g in group_bounds)
    rounds = sum(g["rounds_lower_bound"] for g in group_bounds)
    knife_lb = segments + rounds
    # Max total yield over demand<=F_g<=cap, raw_g>=F_g/upper_yield_g.
    # Parametric fractional optimization: ratio>=t iff max sum F_g*(1-t/y_g)>=0.
    low, high = 0.0, 1.0
    for _ in range(70):
        mid = (low + high) / 2
        test = sum((g["maximum_product_under_5pct_cap_kg"] if 1-mid/g["one_round_yield_upper_bound_discrete"] > 0 else g["mandatory_product_kg"]) * (1-mid/g["one_round_yield_upper_bound_discrete"]) for g in group_bounds)
        if test >= 0:
            low = mid
        else:
            high = mid
    yield_ub = high + 1e-12
    # Global coverage bound permits all valid orders in multi-order schemes;
    # candidates' actual coverage is also retained as a separate conditional case.
    source_cases = [
        ("continued", "submission_continued/初赛结果_棒材优化.json"),
        ("heterogeneous", "runs/normal_heterogeneous/result.json"),
        ("normal_985", "submission_normal_985/初赛结果_棒材优化.json"),
    ]
    candidate_records = []
    for name, source in source_cases:
        path = ROOT / source
        if not path.exists():
            continue
        plan = json.loads(path.read_text(encoding="utf-8-sig"))
        metrics = validate_plan(plan,orders,cfg,blanks)
        trim = sum(2 * b["counts"][r] * orders[next(i for i,o in enumerate(orders) if o.oid == b["orders"][0])].linear_weight for b in plan for r in range(len(b["counts"])))
        rounding = metrics["blank_weight"]-metrics["finished_weight"]-trim
        score_losses = {
            "knife_component":40*(1-min(1,90000/metrics["knives"])),
            "shared_trim_material_component":30*trim/metrics["blank_weight"],
            "integer_blank_rounding_material_component":30*rounding/metrics["blank_weight"],
            "coverage_component":20*(1-metrics["coverage"]),
            "time_component_assumed":0,
        }
        rows=[]
        for target in (98.5,99.0):
            current_k,current_y,cov=metrics["knives"],metrics["yield_rate"],metrics["coverage"]
            required_y=(target-3600000/current_k-20*cov-10)/30
            fixed_y_denominator=target-30*current_y-20*cov-10
            global_y_denominator=target-30*yield_ub-20*cov-10
            rows.append({
                "target":target,"gap_from_current_score":max(0,target-metrics["platform_score_estimate"]),
                "required_yield_if_knives_unchanged":required_y,
                "yield_only_route_ruled_out_by_bound":required_y>yield_ub,
                "maximum_knives_if_yield_unchanged":math.floor(3600000/fixed_y_denominator+1e-8),
                "knife_only_route_ruled_out_by_bound":math.floor(3600000/fixed_y_denominator+1e-8)<knife_lb,
                "necessary_max_knives_even_at_yield_upper_bound":math.floor(3600000/global_y_denominator+1e-8),
                "necessary_min_yield_even_at_knife_lower_bound":(target-3600000/knife_lb-20*cov-10)/30,
                "necessary_conditions_not_joint_sufficiency":True,
            })
        candidate_records.append({"id":name,"path":source,"json_sha256":sha256(path.read_bytes()).hexdigest(),"local_metrics":metrics,"internal_segments":metrics["knives"]-metrics["rounds"],"excess_segments_above_integer_width_bound":metrics["knives"]-metrics["rounds"]-segments,"shared_trim_kg":trim,"integer_blank_rounding_kg":rounding,"losses_in_score_points":score_losses,"targets":rows})
    bound_score = 40*min(1,90000/knife_lb)+30*yield_ub+30
    result = {
        "scope":"Conditional bounds for current local net_shared_trim model and inferred capped40/30/20/10 score. NOT official evaluator bounds.",
        "assumptions":{"orders":4999,"homogeneous_groups":36,"round_physical_length_m":[50,150],"shared_trim_m":2,"round_weight_cap_kg":60000,"bed_width_mm":2000,"knife_formula":"sum_i k_i +1 per physical round","blank_weights_kg":[b.weight for b in blanks],"max_overproduction_ratio":.05,"score":"40*min(1,90000/K)+30*yield+20*coverage+10","time_score_assumed":100,"valid_order_coverage_upper_bound":1},
        "bounds":{
            "mandatory_internal_segments_lower_bound":segments,"rounds_lower_bound":rounds,"knives_lower_bound":knife_lb,
            "physical_length_only_yield_upper_bound":148/150,"discrete_blank_and_size_yield_upper_bound":yield_ub,
            "minimum_shared_trim_kg":sum(g["minimum_shared_trim_kg"] for g in group_bounds),
            "score_upper_bound_at_coverage1":bound_score,
            "best_verified_local_score_lower_bound":max(r["local_metrics"]["platform_score_estimate"] for r in candidate_records),
            "bounds_can_be_simultaneously_loose":"The best knife count and best material yield may be incompatible; combined upper bound is optimistic, not evidence that99 is achievable.",
        },
        "proof_notes":[
            "Each order's q_i pieces needs at least ceil(q_i/Pmax) internal size segments because each segment delivers at most Pmax pieces. Summing is valid because an exported segment belongs to exactly one order.",
            "For parallel p, maximum net length is min(148,60000/(p*rho)-2). Relaxed unbounded combinations of group sizes bound feasible net length above; ignoring order caps or per-blank segment lengths enlarges feasible set, preserving an upper bound.",
            "Every group must deliver its mandatory product mass. Dividing by largest relaxed single-round finished mass and rounding up lower-bounds the number of rounds. Groups cannot share rounds.",
            "Every round contributes one additional local knife; therefore K>=sum(group internal-segment lower bound + group round lower bound).",
            "A row's attainable yield is bounded by enumerating integer p and blank count/type, then taking largest relaxed length allowed by material and bed constraints. Any valid row belongs to this superset; group aggregate yield cannot exceed its largest row yield.",
            "Aggregate yield upper bound permits each group's produced mass to vary continuously between mandatory demand and5% per-order piece caps, with raw>=finished/group_max_yield. This is a further relaxation.",
            "All ceiling lower bounds subtract a tiny tolerance; yield upper bound adds a tiny tolerance. Floating arithmetic is not a formal interval proof, but directions were chosen conservatively and the inputs are far from relevant integer boundaries.",
        ],
        "group_bounds":group_bounds,"candidates":candidate_records,
        "official_boundary_analysis":"Deferred. No network and no platform submissions performed by this diagnostic.",
        "recommendation":"98.5 local target reached; request/await official feedback for the specific candidate before treating local<99 as official<99. Local99 demands joint knife+yield gains and is close to this optimistic bound.",
    }
    (evidence/"model_bounds.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    lines=["# 当前正常模型的条件界与99分必要条件","","以下仅适用于当前本地模型及推测评分公式，**不是官方上界**。采用整数段数、每轮2m头尾、50–150m物理长度、60t承重、2m床宽、同钢种同直径组合，以及5%单订单超产上限。", "", "## 加强后的界", "", f"- 逐订单内部定尺段数至少 **{segments:,}**。",f"- 按36个不可混合组、整数并列支数和可达定尺长度分别求单轮容量，至少 **{rounds:,}轮**。",f"- 本地刀数因此至少 **{knife_lb:,}**（每轮另计1刀）。",f"- 加入整数钢坯质量和离散定尺，放宽问题的成材率上界 **{100*yield_ub:.8f}%**；单纯148/150给出98.66666667%。",f"- 乐观本地评分上界 **{bound_score:.8f}**（覆盖率放宽到100%）。",f"- 已验证候选给出的本地最优值下界 **{result['bounds']['best_verified_local_score_lower_bound']:.8f}**。","","以上刀数最小与成材率最大不保证可同时达到，因此不能据上界大于99就宣布99可达。当前界仍没有排除99。", "", "## 当前候选的评分损失", "", "| 版本 | 本地刀数 | 内部段数超出下界 | 成材率 | 本地估分 | 刀数扣分 | 头尾扣分 | 钢坯取整扣分 |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for r in candidate_records:
        m=r["local_metrics"];l=r["losses_in_score_points"]
        lines.append(f"| {r['id']} | {m['knives']} | {r['excess_segments_above_integer_width_bound']} | {100*m['yield_rate']:.6f}% | {m['platform_score_estimate']:.8f} | {l['knife_component']:.6f} | {l['shared_trim_material_component']:.6f} | {l['integer_blank_rounding_material_component']:.6f} |")
    newest=candidate_records[-1]
    t=next(t for t in newest["targets"] if t["target"]==99)
    lines += ["", "## 最新98.5以上候选达到99的必要条件", "", f"保持刀数不变，需要成材率 **{100*t['required_yield_if_knives_unchanged']:.6f}%**，已超过本地物理界；只改材料不能达到99。", "",f"保持成材率不变，需要刀数≤**{t['maximum_knives_if_yield_unchanged']}**，低于条件刀数下界；只降刀也不能达到99。", "", f"即使采用最乐观另一指标，仍分别需要刀数≤**{t['necessary_max_knives_even_at_yield_upper_bound']}**、成材率≥**{100*t['necessary_min_yield_even_at_knife_lower_bound']:.6f}%**。二者是分别必要条件，不是一个已证明可行的组合。", "", "本地98.5目标已达到，应先用当前文件哈希取得官方反馈，不能把本地98.56自动解读为官方未达99。官方刀数与成材率口径尚未校准，继续无限搜索可能追逐错误目标。评分边界分析仍延期。", "", "完整逐组界、候选SHA256、98.5/99目标的必要条件和推导说明见[model_bounds.json](model_bounds.json)。"]
    (evidence/"model_bounds.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps({"bounds":result["bounds"],"latest_targets":newest["targets"],"latest_score_losses":newest["losses_in_score_points"]},ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
