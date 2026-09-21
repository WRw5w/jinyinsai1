"""Restore the historical 96.49 package byte-for-byte and record calibration."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
DEST = ROOT / "submission_restore_9649"
JSON_NAME = "初赛结果_棒材优化.json"
ZIP_NAME = "初赛结果_棒材优化.zip"
EXPECTED_JSON = "87d3e646ed0b81b32b2afc30b2c77aa7a22221458f9f3db9180bc7c9556251dd"
EXPECTED_ZIP = "2aa15639806d1d07590c5632ed01e5def764dce86ef222d67d85ae2d9e8ab448"


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    source = ROOT / "submission_optimized"
    archived = HERE / "artifacts" / "official_96_49"
    assert digest(source / JSON_NAME) == EXPECTED_JSON
    assert digest(source / ZIP_NAME) == EXPECTED_ZIP
    assert (source / JSON_NAME).read_bytes() == (archived / JSON_NAME).read_bytes()
    assert (source / ZIP_NAME).read_bytes() == (archived / ZIP_NAME).read_bytes()
    with zipfile.ZipFile(source / ZIP_NAME) as z:
        assert z.testzip() is None
        names = z.namelist()
        assert len(names) == 1 and names[0] == JSON_NAME
        assert z.read(names[0]) == (source / JSON_NAME).read_bytes()
    DEST.mkdir(exist_ok=True)
    for name in (JSON_NAME, ZIP_NAME):
        target = DEST / name
        if target.exists():
            assert target.read_bytes() == (source / name).read_bytes(), "Never replace an unrelated file"
        else:
            shutil.copy2(source / name, target)
    audit = dict(
        restored_at_utc=datetime.now(timezone.utc).isoformat(),
        source_folder="submission_optimized", archive_folder="evidence/20260916/artifacts/official_96_49",
        output_folder="submission_restore_9649", json_sha256=digest(DEST / JSON_NAME),
        zip_sha256=digest(DEST / ZIP_NAME), archive_byte_identity=True,
        zip_crc_passed=True, zip_single_root_json=True, zip_json_byte_identity=True,
        official_score=96.49, official_knives=92913, official_yield_percent=92.51,
        feedback_source="Historical user-reported feedback for the conversation-associated file; no server-side receipt",
        newly_submitted=False,
    )
    write(DEST / "restore_audit.json", audit)
    (DEST / "恢复说明.md").write_text(
        "# 原官方96.49分提交包的原样副本\n\n"
        "本目录中的JSON和ZIP从`submission_optimized`逐字节复制，未重算、重打包或改写。"
        "已验证其与历史证据快照完全相同，ZIP仅有根目录JSON，CRC通过，解压JSON与旁边JSON完全一致。\n\n"
        "历史用户反馈：可行，92913刀，成材率92.51%，覆盖率99.98%，总分96.49。"
        "此分数来自先前用户转述及榜单截图，不是本次新提交结果。本次没有上传或触发平台评测。\n\n"
        "JSON SHA256：`" + EXPECTED_JSON + "`。\n\n"
        "ZIP SHA256：`" + EXPECTED_ZIP + "`。\n\n"
        "用途：恢复到当前已知官方最佳基线；修复计刀模型后再比较新方案。"
        "历史文件与官方反馈的关联仍是会话关联，没有服务器文件回执。\n",
        encoding="utf-8",
    )
    knives = read(ROOT / "diagnostics" / "knife_calibration.json")
    yields = read(ROOT / "diagnostics" / "yield_semantics.json")
    assert len(knives["exact_matches"]) == 1
    assert knives["exact_matches"][0]["predictions"] == [113683, 92913, 119268]
    assert all(c["matches"] for c in yields["official_comparisons"].values())
    # Recompute independently from serialized submissions and the raw CSV.
    import csv
    import math
    with (ROOT / "data" / "orders_quarter.csv").open(encoding="utf-8-sig", newline="") as f:
        orders = {r["订单号"]: r for r in csv.DictReader(f)}
    checks = []
    for case in knives["cases"]:
        name = case["name"]
        path = ROOT / name / JSON_NAME
        plan = read(path)
        k = 0
        mass = 0.0
        blank_mass = 0.0
        for batch in plan:
            for row, p, count in zip(batch["length_scheme"], batch["counts"], batch["blank_counts"]):
                blank_mass += count * {1: 9613.5, 2: 6162.5}[batch["blank_type"]]
                for oid, length in row.items():
                    order = orders[oid]
                    size = float(order["订单定尺(mm)"]) / 1000
                    diameter = int(float(order["订单直径(mm)"])) / 1000
                    k += int(length // size) + 1
                    mass += length * p * math.pi * diameter**2 / 4 * 9860
        prediction = 100 * mass / blank_mass
        expected_yield = yields["official_comparisons"][name]["official_yield_percent"]
        assert digest(path) == case["sha256"]
        assert k == case["official"]
        assert round(prediction, 2) == expected_yield
        checks.append(dict(source=name, json_sha256=digest(path), official_knives=case["official"],
                           independently_recomputed_knives=k, official_yield_percent=expected_yield,
                           independently_recomputed_yield_percent=prediction, displayed_yield_match=True))
    calibration = dict(
        status="All three historical knife counts and displayed material yields independently reproduced",
        source_files=["diagnostics/knife_calibration.json", "diagnostics/yield_semantics.json"],
        verification=checks, private_evaluator_source_available=False,
        implications="Archive is observational calibration, not proof of all hidden evaluator rules",
        deprecated_bound_files=["model_bounds.json", "model_bounds.md", "coupled_bounds.json", "coupled_bounds.md"],
        deprecated_bound_values=[99.18197847, 99.04150070],
        deprecated_bound_reason="Bounds apply only to the superseded per-round-extra-cut and raw-diameter score model; do not use them to bound or optimize official scores",
        restore_audit="../../submission_restore_9649/restore_audit.json",
    )
    write(HERE / "calibration_review.json", calibration)
    (HERE / "calibration_review.md").write_text(
        "# 三份官方反馈的独立复核与旧界停用\n\n"
        "以下是对已序列化JSON、原始订单CSV和用户反馈的复核，不是官方评测源代码。"
        "三组刀数逐刀吻合、成材率按官方显示精度全部吻合；这足以推翻之前的计刀目标。\n\n"
        "| 提交包 | 官方刀数 | 复核刀数 | 官方成材率 | 复核成材率 |\n"
        "| --- | ---: | ---: | ---: | ---: |\n"
        + "".join(f"| {c['source']} | {c['official_knives']} | {c['independently_recomputed_knives']} | {c['official_yield_percent']:.2f}% | {c['independently_recomputed_yield_percent']:.8f}% |\n" for c in checks)
        + "\n计刀复现式：对每个订单条目累计 `int(length_m // (order_length_mm / 1000)) + 1`。"
        "这里是Python浮点`//`，不能与精确整数除法或`int(length / size)`互换。"
        "原代码只每轮加1刀，而反馈所匹配的规则每个订单条目加1刀。\n\n"
        "退步包共有37339个订单条目、4878轮。旧模型的91596刀，加上漏算的32461个条目切分开销，"
        "再扣除实际浮点整除相对数学段数的4789差值，恰为官方119268刀。"
        "所以密集跨订单拼接虽然提高了旧模型成材率，却增加了官方计刀代价。\n\n"
        "材料复现式：成品质量为每项净长度×并列支数×π×`(int(直径mm)/1000)^2/4`×9860，"
        "再除以钢坯总质量；两种钢坯分别9613.5和6162.5kg。"
        "直径的毫米整数截断解释了三个成材率显示值，也解释了旧无效包的395次超重反馈。"
        "物理可行性仍应按原始直径保守校验，不能据此减小真实材料需求。\n\n"
        "详细证据：[刀数诊断](../../diagnostics/knife_calibration.json)、"
        "[材料诊断](../../diagnostics/yield_semantics.json)、[独立复核JSON](calibration_review.json)。\n\n"
        "## 停用旧上界推断\n\n"
        "`model_bounds`中的99.18197847、`coupled_bounds`中的99.04150070，"
        "以及基于旧计刀、旧成材率的99分必要条件，都只适用于现已确认失配的旧本地模型。"
        "它们不能作为官方上界、不能支持官方99可达或不可达结论，也不能继续指导当前搜索。"
        "历史计算文件保留供审计，统一标为旧模型结果；需要对新口径重新建模后才能推导新的界。"
        "此前148/150材料界和99.6总分界同样只是带前提的旧模型推论，尚无官方上界地位。\n\n"
        "当前仍无法用这三个低于100的刀数子分确认刀数子分或最终总分是否封顶。"
        "100分边界实验继续暂停，先完成正常方案与校准评分的验证。\n\n"
        "## 已保留的恢复包\n\n"
        "[恢复ZIP](../../submission_restore_9649/初赛结果_棒材优化.zip)与历史96.49包逐字节相同，"
        "[恢复审计](../../submission_restore_9649/restore_audit.json)记录SHA256、CRC和内容一致性。"
        "这是旧包恢复副本，没有新提交，也没有产生新官方成绩。\n",
        encoding="utf-8",
    )
    md_path = HERE / "EVIDENCE.md"
    md = md_path.read_text(encoding="utf-8-sig")
    md += ("\n## 计刀与材料口径已完成三点复核\n\n"
           "[校准复核与旧界停用说明](calibration_review.md)：三次官方刀数精确吻合、三次成材率显示值吻合。"
           "退步的关键是每个订单条目计切分开销；旧模型只按轮计一次。"
           "99.18198、99.04150及相关99分必要条件均为旧失配模型结果，停止用作官方推断。\n\n"
           "[原官方96.49包恢复副本](../../submission_restore_9649/初赛结果_棒材优化.zip)已经逐字节和ZIP内容审计；"
           "分数来自历史反馈，本次没有提交。\n")
    md_path.write_text(md, encoding="utf-8")
    status_path = HERE / "stage_status.json"
    status = read(status_path)
    status["calibration_review"] = calibration
    status["current_action"] = "Use three-point-calibrated knife/material accounting; retain raw physical validation and official96.49 restoration baseline"
    status["deprecated_model_bounds"] = calibration["deprecated_bound_files"]
    status["bound_usage_policy"] = "Do not apply historical99.18198/99.04150 local bounds or their necessary conditions to the official evaluator"
    status["restoration_package"] = audit
    write(status_path, status)
    print(json.dumps(dict(restored=str(DEST), json_sha256=EXPECTED_JSON, zip_sha256=EXPECTED_ZIP,
                          independent_cases_verified=len(checks), calibration_review=str(HERE / "calibration_review.md")), ensure_ascii=False))


if __name__ == "__main__":
    main()
